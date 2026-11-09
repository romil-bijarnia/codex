#!/usr/bin/env python3
"""Install the compact stock-derived base, or restore stock with --stock."""
import argparse
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib

BEGIN = '<personal_execution_rules>'
END = '</personal_execution_rules>'


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


def remove_execution_rules(existing):
    if BEGIN not in existing and END not in existing:
        return existing
    if existing.count(BEGIN) != 1 or existing.count(END) != 1:
        raise ValueError('Malformed execution-rule markers; no files changed.')
    before, rest = existing.split(BEGIN, 1)
    _, after = rest.split(END, 1)
    return (before + after).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', action='store_true')
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    codex_home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
    config = codex_home / 'config.toml'
    agents = codex_home / 'AGENTS.md'
    destination = codex_home / 'prompts/sara-system.md'
    text = config.read_text() if config.exists() else ''
    parsed = tomllib.loads(text)
    developer = remove_execution_rules(parsed.get('developer_instructions', ''))
    compact = (source / 'compact-base.md').read_text()
    if not compact.strip():
        raise SystemExit('Compact base is empty; no files changed.')

    root_end = re.search(r'^[ \t]*\[', text, re.MULTILINE)
    offset = root_end.start() if root_end else len(text)
    root = re.sub(r'^[ \t]*(?:model_instructions_file|instructions|developer_instructions)\s*=.*(?:\n|$)', '',
                  text[:offset], flags=re.MULTILINE)
    prefix = ''
    expected = dict(parsed)
    for key in ('instructions', 'model_instructions_file', 'developer_instructions'):
        expected.pop(key, None)
    if not args.stock:
        prefix += 'model_instructions_file = ' + json.dumps(str(destination)) + '\n'
        expected['model_instructions_file'] = str(destination)
    if developer:
        prefix += 'developer_instructions = ' + json.dumps(developer, ensure_ascii=False) + '\n'
        expected['developer_instructions'] = developer
    updated_config = prefix + root + text[offset:]
    if tomllib.loads(updated_config) != expected:
        raise SystemExit('Cannot update the base without changing other settings; no files changed.')

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
        raise SystemExit('Review the retired copied base block in AGENTS.md first; no files changed.')

    machine_source = source / 'MACHINE.md'
    machine_destination = codex_home / 'MACHINE.md'
    if machine_source.is_file() and not machine_destination.exists():
        atomic_write(machine_destination, machine_source.read_text())
    if not agents.exists():
        atomic_write(agents, personal)
    if not args.stock and (not destination.exists() or destination.read_text() != compact):
        atomic_write(destination, compact)
    if text != updated_config:
        atomic_write(config, updated_config)
    print(json.dumps({'base_mode': 'stock' if args.stock else 'compact',
                      'base_file': None if args.stock else str(destination),
                      'separate_execution_block': False, 'agents': str(agents),
                      'existing_agents_preserved': True, 'backups_created': False}))


if __name__ == '__main__':
    main()
