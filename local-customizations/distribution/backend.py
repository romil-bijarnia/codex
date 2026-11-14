#!/usr/bin/env python3
"""Roll the persistent backend forward only after observed idle time."""
import importlib.util
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from common import atomic_write, sha256, source_root


def acquire_rollout_lock(home):
    path = home/'state/codex-customizations/backend.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    return lock


def running_backend(home):
    """Read the loaded executable, not the selected path or a rollout receipt."""
    record = json.loads((home/'app-server-daemon/daemon.pid').read_text())
    pid = int(record['pid'])
    if pid <= 0:
        raise RuntimeError('Invalid daemon PID.')
    proc = Path(f'/proc/{pid}')
    identity = record.get('processIdentity', record.get('linuxProcessIdentity'))
    if identity is not None:
        fields = (proc/'stat').read_text().rsplit(')', 1)[1].split()
        if (identity.get('bootId') != Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                or identity.get('startTicks') != int(fields[19]) or fields[0] == 'Z'):
            raise RuntimeError('The daemon PID record is stale.')
    executable = proc/'exe'
    digest = sha256(executable)
    path = Path(os.readlink(executable))
    source = None
    try:
        manifest = json.loads((path.parent.parent/'custom-build.json').read_text())
        if manifest['binary_sha256']['codex'] == digest:
            source = manifest.get('source_commit')
    except (OSError, ValueError, KeyError):
        pass
    return dict(pid=pid, executable=str(path), sha256=digest, source_commit=source)


def backend_status(home, package):
    result = {'selected_package': str(package)}
    try:
        manifest = json.loads((package/'custom-build.json').read_text())
        result.update(selected_source_commit=manifest.get('source_commit'),
                      expected_sha256=manifest['binary_sha256']['codex'])
        running = running_backend(home)
        result.update(running=running, matches_selected=running['sha256'] == result['expected_sha256'])
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        result.update(matches_selected=None, error=str(error))
    return result


def active_threads(socket_path):
    spec = importlib.util.spec_from_file_location('local_rpc', source_root() / 'local-customizations/prompts/refresh-loaded-chats.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    # Open via a short fd-relative Unix path when the resolved home path is long.
    path = Path(socket_path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    client = None
    try:
        client = module.Client(Path(f'/proc/self/fd/{directory}/{path.name}'))
        page = client.rpc('thread/loaded/list', {})
        active = []
        for thread in page['data']:
            state = client.rpc('thread/read', {'threadId':thread,'includeTurns':False})['thread']
            if state['status']['type'] == 'active':active.append(thread)
        if page.get('nextCursor'):
            raise RuntimeError('Loaded thread list was paginated; refusing to infer idle from an incomplete observation.')
        return active
    finally:
        if client is not None:client.sock.close()
        os.close(directory)


def rollout(package, home, inspect=False):
    state = home/'state/codex-customizations/backend.json'
    env = {k:v for k,v in os.environ.items() if not k.startswith(('CODEX_','OPENAI_','CHATGPT_'))}
    env['CODEX_HOME'] = str(home)
    def report(status, **extra):
        atomic_write(state,json.dumps(dict(status=status,package=str(package),source_commit=manifest.get('source_commit'),pid=os.getpid(),expected_sha256=expected,**extra),indent=2)+'\n')
    idle = None
    candidate = None
    while True:
        # One worker follows the latest selection; a superseded worker must not
        # leave a newer update without an idle observer.
        selected = (home/'packages/standalone/current').resolve(strict=True)
        if candidate != selected:
            package = selected
            manifest = json.loads((package/'custom-build.json').read_text())
            expected = manifest['binary_sha256']['codex']
            binary = package/'bin/codex'
            if sha256(binary) != expected:raise RuntimeError('Backend candidate changed after verification.')
            candidate = selected
            idle = None
        try:
            result=subprocess.run([str(binary),'app-server','daemon','version'],env=env,capture_output=True,text=True,check=True,timeout=20)
            if not inspect:
                running = running_backend(home)
                if running['sha256'] == expected:
                    report('complete',running_pid=running['pid'],running_source_commit=running['source_commit'])
                    return package
            active=active_threads(json.loads(result.stdout)['socketPath'])
            if inspect:
                print(json.dumps({'active_count':len(active),'package':str(package),'service_changed':False}));return
            if active:idle=None
            elif idle is None:idle=time.monotonic()
            report('waiting_for_idle',active_count=len(active))
            if idle is not None and time.monotonic()-idle >= 15:
                # Never install a package superseded during the observation.
                if (home/'packages/standalone/current').resolve() != package:
                    continue
                break
        except Exception as error:
            if inspect:raise
            report('waiting_for_observation',error=str(error)[:500]);idle=None
        time.sleep(5)
    if sha256(binary) != expected:raise RuntimeError('Backend candidate changed while waiting.')
    report('installing')
    result=subprocess.run([str(binary),'app-server','daemon','update','--from-cli','--yes'],env=env,capture_output=True,text=True,timeout=180)
    if result.returncode:
        report('failed',error=result.stderr[-1500:]);raise SystemExit(1)
    running = running_backend(home)
    if running['sha256'] != expected:
        report('failed',error='The daemon did not load the verified executable.');raise SystemExit(1)
    report('complete',running_pid=running['pid'],running_source_commit=running['source_commit'])
    return package


def main():
    package = Path(sys.argv[1]).resolve(strict=True)
    home = Path(os.environ.get('CODEX_HOME', Path.home()/'.codex'))
    if '--inspect' in sys.argv[2:]:
        rollout(package, home, inspect=True)
        return
    if '--lock-fd' in sys.argv[2:]:
        lock = os.fdopen(int(sys.argv[sys.argv.index('--lock-fd') + 1]), 'a')
    else:
        lock = acquire_rollout_lock(home)
    if lock is None:
        return
    with lock:
        while True:
            package = rollout(package, home)
            # Serialize the final selection check and worker-lock release with
            # update(). Otherwise an update could see an exiting worker's lock
            # and leave the new selection without a rollout worker.
            with (home/'state/codex-customizations/update.lock').open('a') as update_lock:
                fcntl.flock(update_lock, fcntl.LOCK_EX)
                if (home/'packages/standalone/current').resolve() == package:
                    lock.close()
                    return


if __name__=='__main__':main()
