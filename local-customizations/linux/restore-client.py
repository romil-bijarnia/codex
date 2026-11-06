#!/usr/bin/env python3
"""Select an existing verified personal client without rebuilding or restarting chats."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib


def atomic_write(path, content):
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        if path.exists():
            os.fchmod(fd, path.stat().st_mode & 0o777)
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def restore(home, package):
    home = home.expanduser().resolve(strict=True)
    root = home / 'packages/standalone'
    package = package.expanduser().resolve(strict=True)
    if package.parent != (root / 'releases').resolve(strict=True):
        raise ValueError('Select an installed package directly under standalone/releases.')
    receipt = json.loads((package / 'romil-build.json').read_text())
    with (package / 'bin/codex').open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if (digest != receipt['executable_sha256']
            or receipt['suites_passed'] != ['defaults', 'picker', 'integrations']):
        raise ValueError('Package hash or complete personal-client verification does not match.')
    state = home / 'app-server-daemon'
    state.mkdir(exist_ok=True)
    with (state / 'daemon.lock').open('a') as daemon_lock, (root / 'install.lock').open('a') as install_lock:
        fcntl.flock(daemon_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(install_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config_path, settings_path = home / 'config.toml', state / 'settings.json'
        config = config_path.read_text() if config_path.exists() else ''
        before = tomllib.loads(config)
        # Only the root table may contain this setting; preserve all other TOML.
        section = re.search(r'^\s*\[', config, re.MULTILINE)
        split = section.start() if section else len(config)
        head, tail = config[:split], config[split:]
        setting = 'check_for_update_on_startup = false\n'
        if re.search(r'^\s*check_for_update_on_startup\s*=', head, re.MULTILINE):
            head = re.sub(r'^[ \t]*check_for_update_on_startup\s*=[^\n]*(?:\n|$)',
                          setting, head, count=1, flags=re.MULTILINE)
        else:
            head = setting + head
        updated = head + tail
        expected = dict(before, check_for_update_on_startup=False)
        if tomllib.loads(updated) != expected:
            raise ValueError('Refusing to change unrelated TOML settings.')
        settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
        settings.setdefault('updater', {})['autoUpdateEnabled'] = False
        previous = str((root / 'current').resolve(strict=True))
        # Disable both daemon updates and the separate terminal update popup.
        atomic_write(settings_path, json.dumps(settings, indent=2) + '\n')
        atomic_write(config_path, updated)
        (root / 'auto-update-version').unlink(missing_ok=True)
        if (root / 'current').resolve() != package:
            link = root / f'.current.restore-{os.getpid()}'
            try:
                link.symlink_to(package)
                os.replace(link, root / 'current')
            finally:
                link.unlink(missing_ok=True)
    return {'package': str(package), 'sha256': digest, 'previous_package': previous,
            'automatic_updates_enabled': False, 'startup_update_checks': False,
            'background_processes_added': 0, 'running_sessions_restarted': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--codex-home', type=Path,
                        default=Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))))
    args = parser.parse_args()
    print(json.dumps(restore(args.codex_home, args.package), indent=2))
