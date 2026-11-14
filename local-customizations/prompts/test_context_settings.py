import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest


SOURCE = Path(__file__).resolve().parent
INSTALLER = SOURCE / 'install.py'
TODO = 'https://chatgpt.com/space/page_example'
CONFIG = '''model = "keep-model"
model_reasoning_effort = "xhigh"
[features]
memories = true
steer_only = true
[memories]
generate_memories = true
use_memories = true # formerly injected automatically
extract_model = "keep-extractor"
[skills]
config = [{name = "unrelated", enabled = false}]
[plugins."mission-control@personal-local"]
enabled = true
'''


class ContextSettingsTests(unittest.TestCase):
    def invoke(self, home, *args):
        return subprocess.run(
            [sys.executable, str(INSTALLER), *args],
            env={**os.environ, 'CODEX_HOME': str(home)},
            capture_output=True, text=True, check=True,
        )

    def fixture(self, home, text=CONFIG):
        (home / 'config.toml').write_text(text)
        agents = 'Keep these instructions. Shared to-do: ' + TODO + '\n'
        (home / 'AGENTS.md').write_text(agents)
        memory = home / 'memories'
        memory.mkdir()
        (memory / 'MEMORY.md').write_text('Private memory registry stays intact.\n')
        (memory / 'memory_summary.md').write_text('Private memory summary stays intact.\n')
        return agents

    def test_only_two_context_settings_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self.fixture(home)
            expected = tomllib.loads(CONFIG)
            expected['model_instructions_file'] = str(home / 'prompts/sara-system.md')
            expected['features']['omit_skill_descriptions'] = True
            expected['memories']['use_memories'] = False
            self.invoke(home)
            self.assertEqual(tomllib.loads((home / 'config.toml').read_text()), expected)

    def test_preserves_memory_todo_skills_and_plugins(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            agents = self.fixture(home)
            before = {p.name: p.read_bytes() for p in (home / 'memories').iterdir()}
            self.invoke(home)
            self.assertEqual((home / 'AGENTS.md').read_text(), agents)
            self.assertEqual({p.name: p.read_bytes() for p in (home / 'memories').iterdir()}, before)
            self.assertIn(TODO, (home / 'AGENTS.md').read_text())
            self.assertFalse((home / 'backups').exists())
            prompt = (home / 'prompts/sara-system.md').read_text()
            self.assertIn('memory-lookup.md', prompt)
            self.assertIn('read their full SKILL.md', prompt)
            self.assertEqual((home / 'prompts/memory-lookup.md').read_bytes(),
                             (SOURCE / 'memory-lookup.md').read_bytes())

    def test_idempotent_without_backups(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self.fixture(home)
            self.invoke(home)
            files = [p for p in home.rglob('*') if p.is_file()]
            before = {p.relative_to(home): (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
            self.invoke(home)
            after = {p.relative_to(home): (p.read_bytes(), p.stat().st_mtime_ns)
                     for p in home.rglob('*') if p.is_file()}
            self.assertEqual(after, before)

    def test_missing_sections_and_stock_preservation(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self.fixture(home, 'model = "keep-model"\n')
            self.invoke(home)
            parsed = tomllib.loads((home / 'config.toml').read_text())
            self.assertFalse(parsed['memories']['use_memories'])
            self.assertTrue(parsed['features']['omit_skill_descriptions'])
            self.invoke(home, '--stock')
            parsed.pop('model_instructions_file')
            self.assertEqual(tomllib.loads((home / 'config.toml').read_text()), parsed)

    def test_origin_hash_and_no_injected_summary(self):
        import json
        provenance = json.loads((SOURCE / 'origin.json').read_text())
        self.assertEqual(provenance['compact_sha256'],
                         hashlib.sha256((SOURCE / 'compact-base.md').read_bytes()).hexdigest())
        guide = (SOURCE / 'memory-lookup.md').read_text()
        self.assertNotIn('{{ memory_summary }}', guide)
        self.assertNotIn('MEMORY_SUMMARY BEGINS', guide)
        self.assertIn('<oai-mem-citation>', guide)


if __name__ == '__main__':
    unittest.main()
