#!/usr/bin/python3
"""Check the installed terminal picker order and its EL2 shortcut in a PTY."""
import argparse
import hashlib
import http.server
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import threading

ROOT = Path(__file__).resolve().parent
helper = ROOT / 'qa.py'
if not helper.exists():
    helper = ROOT / 'el2-qa.py'
spec = importlib.util.spec_from_file_location('el2_qa', helper)
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)
EXPECTED = ['EL2 Control', 'Full Access', 'Approve for me', 'Ask for approval', 'Read Only']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('executable', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--display-only', action='store_true', help='Check menu order without accessing an EL2 kernel interface')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    profile, workspace = args.output / 'profile', args.output / 'workspace'
    profile.mkdir()
    workspace.mkdir()
    models = json.loads((Path.home() / '.codex/models_cache.json').read_text())['models']
    (profile / 'models.json').write_text(json.dumps({'models': [m for m in models if m['slug'] == 'gpt-6.1-sol']}))
    (profile / 'auth.json').write_text(json.dumps({'auth_mode': 'apikey', 'OPENAI_API_KEY': 'synthetic-local-picker-order-test'}))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), qa.Responses)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (profile / 'config.toml').write_text(f'''
model = "gpt-6.1-sol"
model_catalog_json = {json.dumps(str(profile / 'models.json'))}
openai_base_url = "http://127.0.0.1:{server.server_port}/v1"
approval_policy = "on-request"
approvals_reviewer = "auto_review"
default_permissions = ":danger-full-access"
thread_unload_delay_secs = 0
[features]
api_key_model_discovery = false
memories = false
[tui]
animations = false
notifications = false
screen_reader_detection_done = true
status_line = ["permissions", "approval-mode", "model-with-reasoning"]
[projects.{json.dumps(str(workspace))}]
trust_level = "trusted"
''')
    with args.executable.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    report = {'executable': str(args.executable), 'executable_sha256': digest, 'expected_order': EXPECTED}
    term = None
    try:
        if not args.display_only:
            info = json.loads(subprocess.check_output(['/usr/local/bin/el2-control', 'info'], text=True))
            assert info['exception_level'] == 2
        term = qa.qa.Terminal(args.executable, profile, workspace)
        term.wait('OpenAI Codex')
        term.read(1)
        term.command('/permissions')
        term.wait('Read Only')
        qa.snapshot(term, args.output, 'picker')
        names = '|'.join(re.escape(name) for name in EXPECTED)
        rows = re.findall(r'(?m)^\s*[\u203a>]?\s*(\d+)\.\s+(' + names + r')\b', term.text())
        assert rows == [(str(i), name) for i, name in enumerate(EXPECTED, 1)], rows
        report['displayed_order'] = [name for _, name in rows]
        if args.display_only:
            report.update(passed=True, kernel_activation_verified=False, real_model_requests=0)
            return
        term.send('1')
        qa.until(term, lambda: qa.el2_footer_visible(term), 'Shortcut 1 did not activate EL2 Control.')
        qa.snapshot(term, args.output, 'selected')
        selected = list((profile / 'state/owner-el2-selection').glob('*.json'))
        assert len(selected) == 1
        activation = json.loads(selected[0].read_text())
        assert activation['exception_level'] == 2 and activation['native_execution_verified']
        term.command('/permissions')
        term.wait('Update Model Permissions')
        qa.snapshot(term, args.output, 'selected-picker')
        assert re.search(r'1\.\s+EL2 Control\s+\(current\)', term.text()), term.text()
        assert not qa.Responses.requests, 'Picker navigation unexpectedly made a model request.'
        term.send('\x1b')
        term.command('OWNER_PERMISSIONS_FIXTURE')
        term.wait('DAYBREAK_QA_OK')
        term.read(1)
        contexts = []
        for path in (profile / 'sessions').rglob('*.jsonl'):
            for line in path.read_text().splitlines():
                event = json.loads(line)
                if event.get('type') == 'turn_context':
                    contexts.append(event['payload'])
        assert contexts, 'The fixture did not record an actual turn context.'
        context = contexts[-1]
        assert context['approval_policy'] == 'never', context
        assert context['approvals_reviewer'] == 'auto_review', context
        assert context['permission_profile']['type'] == 'disabled', context
        report['effective_permissions'] = {
            key: context[key] for key in
            ('approval_policy', 'approvals_reviewer', 'permission_profile')
        }
        report['synthetic_model_requests'] = len(qa.Responses.requests)
        report.update(passed=True, first_shortcut_activates_el2=True, current_selection_preserved=True, real_model_requests=0)
    finally:
        if term is not None:
            term.close()
        server.shutdown()
        (args.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
