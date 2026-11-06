#!/usr/bin/env python3
"""Move the retired Sara base override into global AGENTS.md, without backups."""
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib

BEGIN = '<!-- BEGIN SARA MIGRATED SYSTEM GUIDANCE -->'
END = '<!-- END SARA MIGRATED SYSTEM GUIDANCE -->'


def split_guidance(text):
    if BEGIN not in text and END not in text:
        return '', text
    if text.count(BEGIN) != 1 or text.count(END) != 1:
        raise ValueError('Malformed Sara guidance markers; no files changed.')
    before, rest = text.split(BEGIN, 1)
    guidance, after = rest.split(END, 1)
    return guidance.strip(), (before + after).strip()


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
    legacy = codex_home / 'prompts/sara-system.md'
    text = config.read_text() if config.exists() else ''
    parsed = tomllib.loads(text)
    selected = parsed.get('model_instructions_file')
    if selected and Path(selected).expanduser().resolve() != legacy.resolve():
        raise SystemExit('A different base-prompt override is selected; no files changed.')
    if selected and not legacy.is_file():
        raise SystemExit('The selected Sara prompt is missing; no files changed.')

    # Remove only the root-level selector, preserving all other config bytes.
    root_end = re.search(r'^[ \t]*\[', text, re.MULTILINE)
    offset = root_end.start() if root_end else len(text)
    root = re.sub(r'^[ \t]*model_instructions_file\s*=.*(?:\n|$)', '',
                  text[:offset], flags=re.MULTILINE)
    updated_config = root + text[offset:]
    expected = dict(parsed)
    expected.pop('model_instructions_file', None)
    if tomllib.loads(updated_config) != expected:
        raise SystemExit('Cannot remove the selector without changing other settings; no files changed.')

    template = source / 'AGENTS.md'
    default = template.read_text() if template.is_file() else ''
    default_guidance, default_personal = split_guidance(default)
    current = agents.read_text() if agents.exists() else ''
    guidance, personal = split_guidance(current)
    old_prompt = legacy.read_text().strip() if legacy.exists() else ''
    if old_prompt and old_prompt != guidance:
        guidance = (guidance + '\n\n' + old_prompt).strip()
    guidance = guidance or default_guidance
    if default_personal.strip() and default_personal.strip() not in personal:
        personal = (personal.rstrip() + '\n\n' + default_personal.strip()).strip()
    merged = ((BEGIN + '\n' + guidance + '\n' + END + '\n\n') if guidance else '')
    merged += personal.strip() + '\n'
    if not guidance and not personal.strip():
        raise SystemExit('No instructions found; restore AGENTS.md from private GRID first.')
    if len(merged.encode()) > 32768:
        raise SystemExit('Merged instructions exceed the default 32 KiB limit; no files changed.')

    # Preserve the guidance first, then disable and remove the retired override.
    if current != merged:
        atomic_write(agents, merged)
    if text != updated_config:
        atomic_write(config, updated_config)
    legacy.unlink(missing_ok=True)
    print(json.dumps({'agents': str(agents), 'custom_base_prompt': False,
                      'legacy_prompt_removed': not legacy.exists(),
                      'agents_bytes': len(merged.encode()), 'backups_created': False}))


if __name__ == '__main__':
    main()
