#!/usr/bin/env python3
"""Install stock Codex instructions plus a small personal execution block."""
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


def merge_execution_rules(existing, rules):
    if BEGIN not in existing and END not in existing:
        return existing + ('\n\n' if existing else '') + rules
    if existing.count(BEGIN) != 1 or existing.count(END) != 1:
        raise ValueError('Malformed execution-rule markers; no files changed.')
    before, rest = existing.split(BEGIN, 1)
    _, after = rest.split(END, 1)
    return before + rules.rstrip() + after


def main():
    source = Path(__file__).resolve().parent
    codex_home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
    config = codex_home / 'config.toml'
    agents = codex_home / 'AGENTS.md'
    text = config.read_text() if config.exists() else ''
    parsed = tomllib.loads(text)
    rules = (source / 'execution-rules.md').read_text().strip()
    if rules.count(BEGIN) != 1 or rules.count(END) != 1:
        raise SystemExit('Invalid execution rules; no files changed.')
    developer = merge_execution_rules(parsed.get('developer_instructions', ''), rules)

    # Keep each model's native stock base and preserve unrelated developer text.
    root_end = re.search(r'^[ \t]*\[', text, re.MULTILINE)
    offset = root_end.start() if root_end else len(text)
    root = re.sub(r'^[ \t]*(?:model_instructions_file|instructions|developer_instructions)\s*=.*(?:\n|$)', '',
                  text[:offset], flags=re.MULTILINE)
    updated_config = 'developer_instructions = ' + json.dumps(developer, ensure_ascii=False) + '\n' + root + text[offset:]
    expected = dict(parsed)
    expected.pop('instructions', None)
    expected.pop('model_instructions_file', None)
    expected['developer_instructions'] = developer
    if tomllib.loads(updated_config) != expected:
        raise SystemExit('Cannot update the prompt without changing other settings; no files changed.')

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
    if not agents.exists():
        atomic_write(agents, personal)
    if text != updated_config:
        atomic_write(config, updated_config)
    print(json.dumps({'agents': str(agents), 'stock_base_prompt': True,
                      'personal_execution_rules': True,
                      'model_instructions_file': None, 'existing_agents_preserved': True,
                      'backups_created': False}))


if __name__ == '__main__':
    main()
