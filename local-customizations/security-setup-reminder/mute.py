#!/usr/bin/python3
"""Prepare a cached CLI copy without the optional setup reminder."""
from pathlib import Path
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / 'cache'
ORIGINAL_URL = b'/wham/security-setup'
MUTED_URL = b'/wham/reminder-muted'


def save_json(path, value):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as out:
        json.dump(value, out, indent=2)
        out.write('\n')
        temporary = Path(out.name)
    os.replace(temporary, path)


def ensure_package_layout(native, executable):
    """Keep the bundled runtime beside the reminder-muted executable."""
    if executable == native:
        return executable
    source = native.parent.parent
    if not (source / 'codex-package.json').is_file():
        return executable
    if executable.parent.name == 'bin':
        return executable

    package = executable.parent / 'package'
    chosen = package / 'bin' / native.name
    if chosen.is_file() and (package / 'codex-resources/voice/bin/codex-voice-host').is_file():
        return chosen

    staging = Path(tempfile.mkdtemp(prefix='package-stage-', dir=executable.parent))
    try:
        staged_package = staging / 'package'
        copied = subprocess.run(['/bin/cp', '-cR', str(source), str(staged_package)],
                                capture_output=True)
        if copied.returncode:
            shutil.rmtree(staged_package, ignore_errors=True)
            shutil.copytree(source, staged_package)
        shutil.copy2(executable, staged_package / 'bin' / native.name)
        os.replace(staged_package, package)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return chosen


def prepare(native):
    native = native.resolve(strict=True)
    stat = native.stat()
    fingerprint = [str(native), stat.st_dev, stat.st_ino, stat.st_size,
                   stat.st_mtime_ns, stat.st_ctime_ns]
    launcher_fingerprint = [str(value) for value in fingerprint]
    CACHE.mkdir(mode=0o700, exist_ok=True)
    with (CACHE / 'build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state_path = CACHE / 'current.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        if state.get('fingerprint') == fingerprint:
            chosen = Path(state['executable'])
            if chosen.is_file():
                chosen = ensure_package_layout(native, chosen)
                if (state['executable'] != str(chosen)
                        or state.get('launcher_fingerprint') != launcher_fingerprint):
                    state['executable'] = str(chosen)
                    state['launcher_fingerprint'] = launcher_fingerprint
                    save_json(state_path, state)
                return chosen

        data = native.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        count = data.count(ORIGINAL_URL)
        if count == 0:
            save_json(state_path, {'fingerprint': fingerprint,
                                  'launcher_fingerprint': launcher_fingerprint,
                                  'executable': str(native),
                                  'status': 'reminder-path-not-present', 'source_sha256': digest})
            return native
        if count != 1:
            raise RuntimeError('The updated CLI changed its reminder implementation; running the original CLI.')

        assert len(ORIGINAL_URL) == len(MUTED_URL)
        directory = CACHE / digest
        directory.mkdir(mode=0o700, exist_ok=True)
        quiet = directory / 'codex'
        metadata_path = directory / 'patch.json'
        if not quiet.exists() or not metadata_path.exists():
            offset = data.index(ORIGINAL_URL)
            patched = data[:offset] + MUTED_URL + data[offset + len(ORIGINAL_URL):]
            assert patched[:offset] == data[:offset]
            assert patched[offset + len(ORIGINAL_URL):] == data[offset + len(ORIGINAL_URL):]
            version = subprocess.check_output([str(native), '--version'], text=True).strip()
            temporary = directory / 'codex.new'
            try:
                temporary.write_bytes(patched)
                shutil.copymode(native, temporary)
                subprocess.run(['codesign', '--force', '--sign', '-', str(temporary)],
                               check=True, capture_output=True)
                subprocess.run(['codesign', '--verify', '--strict', str(temporary)],
                               check=True, capture_output=True)
                assert subprocess.check_output([str(temporary), '--version'], text=True).strip() == version
                os.replace(temporary, quiet)
            finally:
                temporary.unlink(missing_ok=True)
            assert hashlib.sha256(native.read_bytes()).hexdigest() == digest
            save_json(metadata_path, {'version': version, 'original_binary': str(native),
                                     'source_sha256': digest, 'url_literal_offset': offset,
                                     'quiet_sha256': hashlib.sha256(quiet.read_bytes()).hexdigest()})
        quiet = ensure_package_layout(native, quiet)
        save_json(state_path, {'fingerprint': fingerprint,
                              'launcher_fingerprint': launcher_fingerprint,
                              'executable': str(quiet),
                              'status': 'muted', 'source_sha256': digest})
        return quiet


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2:
            raise RuntimeError('This helper is run automatically by the Codex launcher.')
        print(prepare(Path(sys.argv[1])))
    except Exception as error:
        print('Codex reminder mute: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
