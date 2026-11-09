#!/usr/bin/env python3
"""Verify independent Daybreak extra-high defaults with disposable localhost sessions."""
import argparse
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import threading
import time
import tomllib


spec = importlib.util.spec_from_file_location(
    'terminal_qa', Path(__file__).with_name('terminal-qa.py'))
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)


def until(term, condition, description, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        term.read(.15)
        if condition():
            return
        if term.process.poll() is not None:
            break
    raise AssertionError(description + '\n' + term.text())


def footer(term, value):
    return any(value in line and ' · ' in line for line in term.screen.display)


def expect_settings(term, model, effort, daybreak):
    until(term, lambda: footer(term, f'{model} {effort}')
          and footer(term, 'EL2 Control')
          and footer(term, 'Daybreak ' + ('on' if daybreak else 'off')),
          f'Expected {model} {effort}, EL2 Control and Daybreak {daybreak}.')


def user_text(request):
    for item in reversed(request.get('input', [])):
        if isinstance(item, dict) and item.get('role') == 'user':
            content = item.get('content', [])
            if isinstance(content, str):
                return content
            return ' '.join(part.get('text', '') for part in content
                            if isinstance(part, dict))
    return ''


def request_for(term, marker):
    term.command(marker)
    until(term, lambda: any(user_text(r) == marker for r in qa.Responses.requests),
          'The localhost fixture did not receive ' + marker)
    term.read(1)
    matches = [r for r in qa.Responses.requests if user_text(r) == marker]
    assert len(matches) == 1, f'Expected one request for {marker}, got {len(matches)}.'
    return matches[0]


def assert_request(request, model, effort, program):
    assert request['model'] == model, request.get('model')
    assert request.get('reasoning', {}).get('effort') == effort, request.get('reasoning')
    actual = request.get('access_programs', {}).get('cyber')
    assert actual == program, (actual, program)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ.pop('NO_COLOR', None)
    profile, workspace = args.output / 'profile', args.output / 'workspace'
    profile.mkdir()
    workspace.mkdir()
    models = json.loads((Path.home() / '.codex/models_cache.json').read_text())['models']
    selected = [m for m in models if m['slug'] in ('gpt-6.1-sol', 'gpt-6-sol')]
    assert {m['slug'] for m in selected} == {'gpt-6.1-sol', 'gpt-6-sol'}
    # This localhost fixture represents a Blue-entitled account. The bundled
    # catalog does not include account-specific access-program discovery.
    for model in selected:
        model['available_access_programs'] = {
            'cyber': ['standard', 'daybreak_blue']
            if model['slug'] == 'gpt-6-sol' else ['standard']}
    (profile / 'models.json').write_text(json.dumps({'models': selected}))
    (profile / 'auth.json').write_text(json.dumps({
        'auth_mode': 'apikey', 'OPENAI_API_KEY': 'synthetic-local-daybreak-defaults'}))
    selections = profile / 'state/owner-el2-selection'
    selections.mkdir(parents=True)
    (selections / 'default.json').write_text('{"enabled":true}\n')
    qa.Responses.requests = []
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), qa.Responses)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    config_path = profile / 'config.toml'
    config_path.write_text(f'''
model = "gpt-6.1-sol"
model_reasoning_effort = "xhigh"
daybreak = false
model_catalog_json = {json.dumps(str(profile / 'models.json'))}
openai_base_url = "http://127.0.0.1:{server.server_port}/v1"
check_for_update_on_startup = false
approval_policy = "never"
approvals_reviewer = "user"
sandbox_mode = "danger-full-access"
default_permissions = ":danger-full-access"
thread_unload_delay_secs = 0
[features]
cli_daybreak = true
api_key_cyber_access_programs = true
api_key_model_discovery = false
memories = false
chronicle = false
plugins = false
[tui]
animations = false
notifications = false
screen_reader_detection_done = true
status_line = ["model-with-reasoning", "permissions", "daybreak", "thread-id"]
[projects.{json.dumps(str(workspace))}]
trust_level = "trusted"
''')
    baseline = tomllib.loads(config_path.read_text())
    def check_defaults():
        saved = tomllib.loads(config_path.read_text())
        for key in ('model', 'model_reasoning_effort', 'daybreak',
                    'sandbox_mode', 'approval_policy', 'default_permissions'):
            assert saved.get(key) == baseline[key], (key, saved.get(key), baseline[key])
        assert json.loads((selections / 'default.json').read_text()) == {'enabled': True}

    with args.executable.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    report = {'checks': [], 'executable_sha256': digest, 'real_model_requests': 0}
    terms = []
    try:
        term = qa.Terminal(args.executable, profile, workspace)
        terms.append(term)
        expect_settings(term, 'GPT-6.1-Sol', 'xhigh', False)
        assert not qa.Responses.requests, 'Startup unexpectedly sent a model request.'
        report['checks'].append('Fresh CLI uses normal Sol6.1 xhigh, EL2 and Daybreak off.')
        term.command('/daybreak')
        expect_settings(term, 'GPT-6-Sol', 'xhigh', True)
        check_defaults()
        request = request_for(term, 'DAYBREAK_XHIGH_DEFAULT_FIXTURE')
        assert_request(request, 'gpt-6-sol', 'xhigh', 'daybreak_blue')
        report['checks'].append('Daybreak selects Sol6 xhigh, preserves EL2, and sends Blue to localhost.')

        # A second launch while Daybreak is active catches accidental global pollution.
        fresh = qa.Terminal(args.executable, profile, workspace)
        terms.append(fresh)
        expect_settings(fresh, 'GPT-6.1-Sol', 'xhigh', False)
        fresh.close()
        terms.remove(fresh)
        report['checks'].append('An independent CLI remains normal while a Daybreak session is active.')

        # Saved Daybreak metadata and the normal return pair survive closing the frontend.
        returns = list((profile / 'state/daybreak-return').glob('*.json'))
        assert len(returns) == 1, 'Expected one saved Daybreak return selection.'
        thread_id = returns[0].stem
        term.close()
        terms.remove(term)
        term = qa.Terminal(args.executable, profile, workspace, resume_thread=thread_id)
        terms.append(term)
        expect_settings(term, 'GPT-6-Sol', 'xhigh', True)
        request = request_for(term, 'DAYBREAK_RESUMED_XHIGH_FIXTURE')
        assert_request(request, 'gpt-6-sol', 'xhigh', 'daybreak_blue')
        report['checks'].append('Resumed Daybreak retains Sol6 xhigh, Blue routing and EL2.')

        term.command('/daybreak')
        expect_settings(term, 'GPT-6.1-Sol', 'xhigh', False)
        request = request_for(term, 'DAYBREAK_OFF_RESTORE_FIXTURE')
        assert_request(request, 'gpt-6.1-sol', 'xhigh', 'standard')
        check_defaults()
        report['checks'].append('Disabling Daybreak restores Sol6.1 xhigh, EL2 and standard routing.')
        (args.output / 'restored-screen.txt').write_text(term.text())
        term.close()
        terms.remove(term)

        # A compatible model must get the Daybreak xhigh default, not inherit high.
        compatible = qa.Terminal(args.executable, profile, workspace,
                                 extra_args=['--model', 'gpt-6-sol',
                                             '-c', 'model_reasoning_effort="high"'])
        terms.append(compatible)
        expect_settings(compatible, 'GPT-6-Sol', 'high', False)
        compatible.command('/daybreak')
        expect_settings(compatible, 'GPT-6-Sol', 'xhigh', True)
        request = request_for(compatible, 'DAYBREAK_COMPATIBLE_XHIGH_FIXTURE')
        assert_request(request, 'gpt-6-sol', 'xhigh', 'daybreak_blue')
        compatible.command('/daybreak')
        expect_settings(compatible, 'GPT-6-Sol', 'high', False)
        check_defaults()
        report['checks'].append('Already-compatible Sol6 also switches high to xhigh and restores high.')
        report['synthetic_model_requests'] = len(qa.Responses.requests)
        report['passed'] = True
    finally:
        for index, terminal in enumerate(terms):
            (args.output / f'last-screen-{index}.txt').write_text(terminal.text())
            (args.output / f'last-screen-{index}.ansi').write_bytes(terminal.output)
            terminal.close()
        server.shutdown()
        server.server_close()
        (args.output / 'request-settings.json').write_text(json.dumps([
            {key: request.get(key) for key in ('model', 'reasoning', 'access_programs')}
            for request in qa.Responses.requests], indent=2) + '\n')
        (args.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
