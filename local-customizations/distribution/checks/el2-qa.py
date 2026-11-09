#!/usr/bin/python3
"""Exercise the real permissions picker against disposable local Codex sessions."""
import argparse
import http.server
import importlib.util
import hashlib
import json
from pathlib import Path
import re
import subprocess
import threading
import time

spec = importlib.util.spec_from_file_location('terminal_qa', Path(__file__).with_name('terminal-qa.py'))
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)


class Responses(qa.Responses):
    requests = []
    paths = []

    def do_POST(self):
        self.paths.append(self.path)
        super().do_POST()


def last_user_text(request):
    for item in reversed(request.get('input', [])):
        if isinstance(item, dict) and item.get('role') == 'user':
            content = item.get('content', [])
            if isinstance(content, str):
                return content
            return ' '.join(part.get('text', '') for part in content if isinstance(part, dict))
    return ''


def until(term, condition, description, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        term.read(.15)
        if condition():
            return
    raise AssertionError(description + '\n' + term.text())


def snapshot(term, root, name):
    (root / (name + '.txt')).write_text(term.text())
    (root / (name + '.ansi')).write_bytes(term.output)


def el2_footer_visible(term):
    return any(line.lstrip().startswith('EL2 Control ·') for line in term.screen.display)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('executable', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--start-workspace', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    profile, workspace = args.output / 'profile', args.output / 'workspace'
    profile.mkdir()
    workspace.mkdir()
    models = json.loads((Path.home() / '.codex/models_cache.json').read_text())['models']
    (profile / 'models.json').write_text(json.dumps({'models': [m for m in models if m['slug'] == 'gpt-6.1-sol']}))
    (profile / 'auth.json').write_text(json.dumps({'auth_mode': 'apikey', 'OPENAI_API_KEY': 'synthetic-local-el2-mode-test'}))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Responses)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (profile / 'config.toml').write_text(f'''
model = "gpt-6.1-sol"
model_reasoning_effort = "xhigh"
model_catalog_json = {json.dumps(str(profile / 'models.json'))}
openai_base_url = "http://127.0.0.1:{server.server_port}/v1"
approval_policy = "on-request"
approvals_reviewer = "user"
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
    terms = []
    with args.executable.open('rb') as executable_file:
        digest = hashlib.file_digest(executable_file, 'sha256').hexdigest()
    report = {'checks': [], 'executable': str(args.executable), 'executable_sha256': digest,
              'initial_profile': 'workspace-write' if args.start_workspace else 'full-access'}
    try:
        term = qa.Terminal(args.executable, profile, workspace,
                           extra_args=['--sandbox', 'workspace-write'] if args.start_workspace else [])
        terms.append(term)
        term.wait('OpenAI Codex')
        term.read(2)
        term.command('/permissions')
        term.wait('EL2 Control')
        snapshot(term, args.output, 'picker')
        match = re.search(r'(\d+)\.\s+EL2 Control', term.text())
        if not match:
            raise AssertionError('Could not find the EL2 picker shortcut.\n' + term.text())
        subprocess.run(['sudo', '-n', 'systemctl', 'stop', 'owner-el2.service'], check=True)
        if Path('/dev/owner_el2').exists():
            raise AssertionError('The service did not remove the device.')
        term.send(match.group(1))
        until(term, lambda: el2_footer_visible(term), 'The footer did not adopt EL2 Control.')
        snapshot(term, args.output, 'selected')
        active = subprocess.check_output(['systemctl', 'is-active', 'owner-el2.service'], text=True).strip()
        if active != 'active':
            raise AssertionError('The picker did not start the service.')
        saved = list((profile / 'state/owner-el2-selection').glob('*.json'))
        if len(saved) != 1:
            raise AssertionError('The selected mode was not saved per conversation.')
        activation = json.loads(saved[0].read_text())
        if activation['exception_level'] != 2 or not activation['native_execution_verified']:
            raise AssertionError('The selection was saved without native EL2 proof.')
        report['checks'].append('Selecting EL2 Control starts a stopped service and verifies execution at EL2.')
        report['thread_id'] = saved[0].stem
        report['activation'] = activation
        term.command('/permissions')
        term.wait('Update Model Permissions')
        snapshot(term, args.output, 'selected-picker')
        term.send('\x1b')
        if Responses.requests:
            raise AssertionError('Permission activation unexpectedly submitted a model request.')
        # Codex discards completely blank sessions. Materialize this disposable
        # thread using the localhost-only Responses fixture before testing resume.
        term.command('EL2_MODE_PERSISTENCE_FIXTURE')
        term.wait('DAYBREAK_QA_OK')
        term.read(1)
        snapshot(term, args.output, 'materialized')
        term.close()
        terms.remove(term)
        term = qa.Terminal(args.executable, profile, workspace, resume_thread=report['thread_id'])
        terms.append(term)
        term.wait('OpenAI Codex')
        until(term, lambda: el2_footer_visible(term), 'The selected mode did not restore on resume.')
        snapshot(term, args.output, 'resumed')
        report['checks'].append('Resuming the conversation restores the selected mode after verifying the interface.')
        term.command('/permissions el2-control')
        term.wait('EL2 Control enabled')
        snapshot(term, args.output, 'inline')
        report['checks'].append('The inline /permissions el2-control command activates the same mode.')
        if not Responses.requests or any(last_user_text(request).lstrip().startswith('/permissions') for request in Responses.requests):
            raise AssertionError('A permission command reached the model fixture.')
        report['synthetic_model_requests'] = len(Responses.requests)
        report['fixture_requests'] = [
            {'model': request.get('model'), 'last_user_text': last_user_text(request)[:160]}
            for request in Responses.requests
        ]
        report['fixture_paths'] = Responses.paths
        report['real_model_requests'] = 0
        report['passed'] = True
    finally:
        for term in terms:
            term.close()
        subprocess.run(['sudo', '-n', 'systemctl', 'start', 'owner-el2.service'], check=True)
        server.shutdown()
        (args.output / 'result.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
