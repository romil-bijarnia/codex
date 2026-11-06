#!/usr/bin/env python3
"""Install the complete personal base prompt using Codex's supported file setting."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
import tomllib

source = Path(__file__).resolve().parent / 'sara-system.md'
codex_home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
config = codex_home / 'config.toml'
destination = codex_home / 'prompts/sara-system.md'
text = config.read_text() if config.exists() else ''
parsed = tomllib.loads(text)
if source.exists():
    prompt = source.read_bytes()
else:
    catalog = json.loads((codex_home / 'models_cache.json').read_text())
    model = next(item for item in catalog['models'] if item['slug'] == parsed.get('model'))
    base = model['model_messages']['instructions_template']
    policy = (source.parent / 'repository-preservation.md').read_text()
    prompt = (base.rstrip() + '\n\n' + policy).encode()
existing = parsed.get('model_instructions_file')
if existing and Path(existing) != destination:
    raise SystemExit('A different base-prompt file is already selected; preserve that override first.')
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
backup = codex_home / 'backups' / ('repository-prompt-' + stamp)
backup.mkdir(parents=True, exist_ok=False)
if config.exists():
    shutil.copy2(config, backup / 'config.toml')
if destination.exists():
    shutil.copy2(destination, backup / 'sara-system.md')
destination.parent.mkdir(parents=True, exist_ok=True)
with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as target:
    target.write(prompt)
    temporary = Path(target.name)
temporary.chmod(0o600)
os.replace(temporary, destination)
if not existing:
    text = 'model_instructions_file = ' + json.dumps(str(destination)) + '\n' + text
    assert tomllib.loads(text)['model_instructions_file'] == str(destination)
    with tempfile.NamedTemporaryFile(mode='w', dir=config.parent, delete=False) as target:
        target.write(text)
        temporary = Path(target.name)
    temporary.chmod(config.stat().st_mode & 0o777 if config.exists() else 0o600)
    os.replace(temporary, config)
print(json.dumps({'base_prompt': str(destination), 'backup': str(backup)}))
