#!/usr/bin/env python3
"""Roll the persistent backend forward only after observed idle time."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from common import atomic_write, sha256, source_root


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


def main():
    package = Path(sys.argv[1]).resolve(strict=True)
    home = Path(os.environ.get('CODEX_HOME', Path.home()/'.codex'))
    state = home/'state/codex-customizations/backend.json'
    manifest = json.loads((package/'custom-build.json').read_text())
    expected = manifest['binary_sha256']['codex']
    binary = package/'bin/codex'
    if sha256(binary) != expected:raise RuntimeError('Backend candidate changed after verification.')
    env = {k:v for k,v in os.environ.items() if not k.startswith(('CODEX_','OPENAI_','CHATGPT_'))}
    env['CODEX_HOME'] = str(home)
    def report(status, **extra):
        atomic_write(state,json.dumps(dict(status=status,package=str(package),pid=os.getpid(),expected_sha256=expected,**extra),indent=2)+'\n')
    idle = None
    while True:
        if (home/'packages/standalone/current').resolve() != package:
            report('superseded');return
        try:
            result=subprocess.run([str(binary),'app-server','daemon','version'],env=env,capture_output=True,text=True,check=True,timeout=20)
            active=active_threads(json.loads(result.stdout)['socketPath'])
            if '--inspect' in sys.argv[2:]:
                print(json.dumps({'active_count':len(active),'package':str(package),'service_changed':False}));return
            if active:idle=None
            elif idle is None:idle=time.monotonic()
            report('waiting_for_idle',active_count=len(active))
            if idle is not None and time.monotonic()-idle >= 15:break
        except Exception as error:
            if '--inspect' in sys.argv[2:]:raise
            report('waiting_for_observation',error=str(error)[:500]);idle=None
        time.sleep(5)
    if sha256(binary) != expected:raise RuntimeError('Backend candidate changed while waiting.')
    report('installing')
    result=subprocess.run([str(binary),'app-server','daemon','update','--from-cli','--yes'],env=env,capture_output=True,text=True,timeout=180)
    if result.returncode:
        report('failed',error=result.stderr[-1500:]);raise SystemExit(1)
    pid=json.loads((home/'app-server-daemon/daemon.pid').read_text())['pid']
    if sha256(Path(f'/proc/{pid}/exe')) != expected:
        report('failed',error='The daemon did not load the verified executable.');raise SystemExit(1)
    report('complete',running_pid=pid)


if __name__=='__main__':main()
