#!/usr/bin/python3
"""Verify CLI startup defaults and per-thread overrides using a local fixture."""
import argparse
import hashlib
import http.server
import importlib.util
import json
import os
import re
from pathlib import Path
import threading

spec = importlib.util.spec_from_file_location(
    'el2_qa', Path(__file__).with_name('el2-qa.py'))
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)


def footer(term, text):
    return any(text in line and ' · ' in line for line in term.screen.display)


def main():
    os.environ.pop('NO_COLOR', None)
    parser = argparse.ArgumentParser()
    parser.add_argument('executable', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    profile, workspace = args.output / 'profile', args.output / 'workspace'
    profile.mkdir()
    workspace.mkdir()
    models = json.loads((Path.home() / '.codex/models_cache.json').read_text())['models']
    (profile / 'models.json').write_text(json.dumps({
        'models': [m for m in models if m['slug'] == 'gpt-6.1-sol']}))
    (profile / 'auth.json').write_text(json.dumps({
        'auth_mode': 'apikey', 'OPENAI_API_KEY': 'synthetic-local-defaults-test'}))
    selection_dir = profile / 'state/owner-el2-selection'
    selection_dir.mkdir(parents=True)
    (selection_dir / 'default.json').write_text('{"enabled":true}\n')
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), qa.Responses)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (profile / 'config.toml').write_text(f'''
model = "gpt-6.1-sol"
model_reasoning_effort = "xhigh"
daybreak = false
model_catalog_json = {json.dumps(str(profile / 'models.json'))}
openai_base_url = "http://127.0.0.1:{server.server_port}/v1"
approval_policy = "on-request"
approvals_reviewer = "user"
default_permissions = ":danger-full-access"
thread_unload_delay_secs = 0
[features]
cli_daybreak = true
api_key_model_discovery = false
memories = false
[tui]
animations = false
notifications = false
screen_reader_detection_done = true
status_line_use_colors = true
status_line = ["model-with-reasoning", "permissions", "daybreak", "thread-id"]
[projects.{json.dumps(str(workspace))}]
trust_level = "trusted"
''')
    with args.executable.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    report = {'checks': [], 'executable_sha256': digest, 'real_model_requests': 0}
    term = None
    try:
        term = qa.qa.Terminal(args.executable, profile, workspace)
        term.wait('OpenAI Codex')
        qa.until(term, lambda: footer(term, 'EL2 Control'), 'EL2 default did not activate.')
        qa.until(term, lambda: footer(term, 'GPT-6.1-Sol xhigh'),
                 'Configured model and reasoning defaults did not activate.')
        qa.until(term, lambda: footer(term, 'Daybreak off'),
                 'Configured Daybreak default did not activate.')
        qa.snapshot(term, args.output, 'fresh-defaults')
        row = next(i for i, line in enumerate(term.screen.display)
                   if 'EL2 Control' in line and ' · ' in line)
        column = term.screen.display[row].index('EL2 Control')
        colours = {term.screen.buffer[row][i].fg
                   for i in range(column, column + len('EL2 Control'))
                   if not term.screen.buffer[row][i].data.isspace()}
        assert colours == {'ff5f6d'}, colours
        report['el2_footer_colour'] = '#FF5F6D'
        markers = [p for p in selection_dir.glob('*.json') if p.name != 'default.json']
        assert not markers, 'Startup must restore the selection without running activation.'
        first_id = re.search(r'\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b', term.text()).group(0)
        selection_path = selection_dir / f'{first_id}.json'
        assert not qa.Responses.requests, 'Startup sent a model request.'
        report['checks'].append('Fresh CLI restores Sol xhigh, EL2 and Daybreak off without bridge activation or a model request.')
        term.command('DEFAULTS_PERSISTENCE_FIXTURE')
        term.wait('DAYBREAK_QA_OK')
        term.read(1)
        request = next(r for r in qa.Responses.requests if qa.last_user_text(r) == 'DEFAULTS_PERSISTENCE_FIXTURE')
        assert request['model'] == 'gpt-6.1-sol'
        assert request['reasoning']['effort'] == 'xhigh'
        assert request.get('access_programs', {}).get('cyber') in (None, 'standard'), request.get('access_programs')
        report['checks'].append('A localhost fixture request carries Sol, xhigh and no Daybreak access program.')
        term.command('/permissions :danger-full-access')
        term.wait('Enable full access?')
        term.send('1')
        qa.until(term, lambda: selection_path.exists() and json.loads(selection_path.read_text()) == {'enabled': False}, 'Explicit Full Access did not clear EL2.')
        assert json.loads(selection_path.read_text()) == {'enabled': False}
        term.close()
        term = qa.qa.Terminal(args.executable, profile, workspace, resume_thread=first_id)
        term.wait('OpenAI Codex')
        term.read(2)
        assert not footer(term, 'EL2 Control'), term.text()
        qa.snapshot(term, args.output, 'explicit-choice-resumed')
        report['checks'].append('An explicit per-thread Full Access choice survives resume despite the EL2 default.')
        term.command('/new')
        qa.until(term, lambda: footer(term, 'EL2 Control'), 'New conversation did not apply EL2 default.')
        qa.until(term, lambda: footer(term, 'GPT-6.1-Sol xhigh'),
                 'New conversation did not apply model and reasoning defaults.')
        qa.until(term, lambda: footer(term, 'Daybreak off'),
                 'New conversation did not apply the Daybreak default.')
        qa.snapshot(term, args.output, 'new-defaults')
        report['checks'].append('The /new command reapplies all four defaults.')
        term.close()
        term = qa.qa.Terminal(args.executable, profile, workspace,
                              extra_args=['--sandbox', 'workspace-write'])
        term.wait('OpenAI Codex')
        term.read(2)
        assert not footer(term, 'EL2 Control'), term.text()
        qa.snapshot(term, args.output, 'workspace-override')
        report['checks'].append('An explicit workspace sandbox is respected.')
        report['startup_bridge_activation'] = False
        report['synthetic_model_requests'] = len(qa.Responses.requests)
        report['passed'] = True
    finally:
        if term is not None:
            qa.snapshot(term, args.output, 'last-screen')
            term.close()
        server.shutdown()
        (args.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
