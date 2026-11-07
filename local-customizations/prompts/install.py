#!/usr/bin/env python3
"""Restore the selected model's stock base and retain global AGENTS.md."""
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as target:
        target.write(text)
        temporary = Path(target.name)
    try:
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    source = Path(__file__).resolve().parent
    codex_home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
    config = codex_home / 'config.toml'
    agents = codex_home / 'AGENTS.md'
    text = config.read_text() if config.exists() else ''
    parsed = tomllib.loads(text)

    # Remove only root-level base overrides; model and permission settings stay.
    root_end = re.search(r'^[ \t]*\[', text, re.MULTILINE)
    offset = root_end.start() if root_end else len(text)
    root = re.sub(r'^[ \t]*(?:model_instructions_file|instructions)\s*=.*(?:\n|$)', '',
                  text[:offset], flags=re.MULTILINE)
    updated_config = root + text[offset:]
    expected = dict(parsed)
    expected.pop('instructions', None)
    expected.pop('model_instructions_file', None)
    if tomllib.loads(updated_config) != expected:
        raise SystemExit('Cannot restore stock without changing other settings; no files changed.')

    # Restore the private template only for a new profile, never overwrite edits.
    template = source / 'AGENTS.md'
    if agents.exists():
        personal = agents.read_text()
    elif template.is_file():
        personal = template.read_text()
    else:
        raise SystemExit('Restore personal AGENTS.md from private GRID first; no files changed.')
    if not personal.strip() or len(personal.encode()) > 32768:
        raise SystemExit('AGENTS.md must be nonempty and within 32 KiB; no files changed.')
    if '<!-- BEGIN SARA MIGRATED SYSTEM GUIDANCE -->' in personal:
        raise SystemExit('Review the retired copied base block in AGENTS.md before restoring stock; no files changed.')

    if not agents.exists():
        atomic_write(agents, personal)
    if text != updated_config:
        atomic_write(config, updated_config)
    print(json.dumps({'agents': str(agents), 'stock_base_prompt': True,
                      'base_override_removed': True, 'existing_agents_preserved': True,
                      'agents_bytes': len(personal.encode()), 'backups_created': False}))


if __name__ == '__main__':
    main()
