from contextlib import redirect_stdout, redirect_stderr
from html.parser import HTMLParser
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('visualize_cli', ROOT / 'visualize.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
CODEX_HOME = Path.home() / '.codex'


class ShellParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.frames = []

    def handle_starttag(self, tag, attrs):
        if tag == 'iframe':
            self.frames.append(dict(attrs))


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / 'chart with spaces.html'
        self.source.write_text('<div id="counter">0</div><script>document.getElementById("counter").textContent="1";</script>')
        self.viewers = self.root / 'viewers'

    def render(self):
        return module.make_viewer(self.source, codex_home=CODEX_HOME, viewer_root=self.viewers)

    def test_persistent_file_and_browser_sandbox(self):
        result = self.render()
        document = Path(result['viewer']).read_text()
        parser = ShellParser()
        parser.feed(document)
        self.assertEqual(len(parser.frames), 1)
        self.assertEqual(parser.frames[0]['sandbox'], 'allow-scripts')
        self.assertIn("connect-src blob: data:", parser.frames[0]['data-srcdoc'])
        self.assertIn('id="counter"', parser.frames[0]['data-srcdoc'])
        self.assertTrue(result['url'].startswith('file:///'))
        self.assertFalse(result['server_required'])
        self.source.unlink()
        self.assertTrue(Path(result['viewer']).is_file())

    def test_repeat_is_stable_and_content_change_is_versioned(self):
        first = self.render()
        self.assertEqual(first, self.render())
        self.source.write_text('<div>changed</div>')
        second = self.render()
        self.assertNotEqual(first['viewer'], second['viewer'])
        self.assertTrue(Path(first['viewer']).is_file())

    def test_modified_viewer_is_not_overwritten(self):
        result = self.render()
        destination = Path(result['viewer'])
        destination.write_text('preserve this change')
        with self.assertRaisesRegex(ValueError, 'refusing to overwrite'):
            self.render()
        self.assertEqual(destination.read_text(), 'preserve this change')

    def test_symlink_destination_is_rejected(self):
        result = self.render()
        destination = Path(result['viewer'])
        destination.unlink()
        destination.symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, 'symbolic link'):
            self.render()

    def test_symlink_viewer_directory_is_rejected(self):
        real = self.root / 'real'
        real.mkdir()
        self.viewers.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symbolic link'):
            self.render()

    def test_full_document_is_rejected(self):
        self.source.write_text('<!doctype html><html><body>not a fragment</body></html>')
        with self.assertRaises(ValueError):
            self.render()

    def test_oversized_input_is_rejected(self):
        self.source.write_bytes(b'x' * (module.MAX_BYTES + 1))
        with self.assertRaisesRegex(ValueError, '1 MB'):
            self.render()

    def test_non_html_input_is_rejected(self):
        self.source = self.root / 'not-html.txt'
        self.source.write_text('data')
        with self.assertRaisesRegex(ValueError, '.html'):
            self.render()

    def test_missing_input_is_rejected(self):
        self.source.unlink()
        with self.assertRaises(FileNotFoundError):
            self.render()

    def test_invalid_encoding_is_rejected(self):
        self.source.write_bytes(b'\xff')
        with self.assertRaises(UnicodeError):
            self.render()

    def test_fragment_cannot_break_out_of_iframe_attribute(self):
        self.source.write_text('<div>\"</iframe><iframe sandbox="allow-same-origin">attempt</iframe></div>')
        result = self.render()
        parser = ShellParser()
        parser.feed(Path(result['viewer']).read_text())
        self.assertEqual(len(parser.frames), 1)
        self.assertEqual(parser.frames[0]['sandbox'], 'allow-scripts')

    def test_renderer_discovery_is_semantic_and_ignores_unrelated_paths(self):
        base = self.root / 'plugins/cache/openai-bundled/visualize'
        for version in ['1.0.9', '1.0.66', 'untrusted-name']:
            script = base / version / 'skills/visualize/scripts/render.py'
            script.parent.mkdir(parents=True)
            script.write_text('test fixture')
        self.assertEqual(module.find_renderer(self.root), base / '1.0.66/skills/visualize/scripts/render.py')

    def test_json_no_open_does_not_launch_any_process(self):
        output = io.StringIO()
        with patch.object(module, 'ROOT', self.root), patch.object(module.subprocess, 'run') as launch, redirect_stdout(output):
            status = module.main(['--no-open', '--json', str(self.source)])
        self.assertEqual(status, 0)
        launch.assert_not_called()
        self.assertFalse(json.loads(output.getvalue())['opened'])

    def test_open_uses_structured_arguments_and_propagates_failure(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(module, 'ROOT', self.root), patch.object(module.sys, 'platform', 'darwin'), patch.object(module.subprocess, 'run') as launch, redirect_stdout(output):
            status = module.main(['--json', str(self.source)])
        self.assertEqual(status, 0)
        result = json.loads(output.getvalue())
        launch.assert_called_once_with(['/usr/bin/open', '-b', 'com.apple.Safari', result['viewer']], check=True)
        with patch.object(module, 'ROOT', self.root), patch.object(module.sys, 'platform', 'darwin'), patch.object(module.subprocess, 'run', side_effect=OSError('opener failed')), redirect_stderr(errors):
            self.assertEqual(module.main([str(self.source)]), 1)
        self.assertIn('opener failed', errors.getvalue())

    def test_linux_open_uses_default_browser_and_propagates_failure(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(module, 'ROOT', self.root), patch.object(module.sys, 'platform', 'linux'), patch.object(module.subprocess, 'run') as launch, redirect_stdout(output):
            self.assertEqual(module.main(['--json', str(self.source)]), 0)
        result = json.loads(output.getvalue())
        launch.assert_called_once_with(['xdg-open', result['url']], check=True)
        self.assertTrue(result['opened'])
        with patch.object(module, 'ROOT', self.root), patch.object(module.sys, 'platform', 'linux'), patch.object(module.subprocess, 'run', side_effect=OSError('browser failed')), redirect_stderr(errors):
            self.assertEqual(module.main([str(self.source)]), 1)
        self.assertIn('browser failed', errors.getvalue())


if __name__ == '__main__':
    unittest.main()
