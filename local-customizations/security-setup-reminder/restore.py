#!/usr/bin/python3
"""Disable the persistent reminder mute and restore the previous entrypoint."""
from pathlib import Path
import json
import os

root = Path(__file__).resolve().parent
metadata = json.loads((root / 'installation.json').read_text())
(root / 'disabled').touch()
command = Path(metadata['command_path'])
wrapper = root / 'codex-launcher.cjs'
if command.is_symlink() and Path(os.readlink(command)) == wrapper:
    command.unlink()
    command.symlink_to(metadata['previous_symlink'])
shell = Path(metadata['shell_file'])
source = shell.read_text()
if source.count(metadata['new_dispatch']) == 1:
    shell.write_text(source.replace(metadata['new_dispatch'], metadata['old_dispatch'], 1))
print('Reminder mute disabled. Open a new terminal to restore the previous shell entrypoint.')
