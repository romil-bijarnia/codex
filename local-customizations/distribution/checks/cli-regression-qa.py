#!/usr/bin/env python3
"""Verify literal pastes and API overrides with disposable, local fixtures."""
import argparse
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    binary = args.binary.resolve(strict=True)
    spec = importlib.util.spec_from_file_location('terminal_qa', Path(__file__).with_name('terminal-qa.py'))
    qa = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qa)
    report = {'checks': [], 'real_model_requests': 0}
    with binary.open('rb') as stream:
        report['executable_sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()

    # Run the real API helper under a disposable HOME; its native child only captures arguments.
    fixture = args.output / 'api'
    profile = fixture / 'profile'
    profile.mkdir(parents=True)
    helper = fixture / '.local/bin/codex-api-chat'
    helper.parent.mkdir(parents=True)
    shutil.copy2(args.source / 'local-customizations/api-cli/codex-api-chat.py', helper)
    (profile / 'auth.json').write_text('{"auth_mode":"apikey","OPENAI_API_KEY":"local-fixture"}')
    capture = fixture / 'capture.py'
    capture.write_text('#!/usr/bin/python3\nimport sys,json\nprint(json.dumps(sys.argv[1:]))\n')
    capture.chmod(0o700)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('CODEX_', 'OPENAI_', 'CHATGPT_'))}
    env.update(HOME=str(fixture), CODEX_API_HOME=str(profile), CODEX_API_BINARY=str(capture))
    invocations = [
        (['-c', 'model="root value"', 'api', 'exec', 'literal $(value)'],
         ['-c', 'model="root value"', 'exec', 'literal $(value)']),
        (['api', '-c', 'model="root value"', 'exec', 'literal $(value)'],
         ['-c', 'model="root value"', 'exec', 'literal $(value)']),
        (['-c', 'model="root"', 'api', '-c', 'model="child"', 'exec', 'fixture'],
         ['-c', 'model="root"', '-c', 'model="child"', 'exec', 'fixture']),
    ]
    for invocation, expected in invocations:
        result = subprocess.run([str(binary), *invocation], env=env, text=True,
                                capture_output=True, timeout=15, check=True)
        actual = json.loads(result.stdout)
        # The helper always adds its own credential-store override first.
        assert actual[2:] == expected, (invocation, actual)
    (profile / 'auth.json').unlink()
    result = subprocess.run([str(binary), '-c', 'model="login value"', 'api',
                             'login', '--with-api-key'], env=env, text=True,
                            capture_output=True, timeout=15, check=True)
    assert json.loads(result.stdout)[2:] == ['-c', 'model="login value"', 'login', '--with-api-key']
    report['checks'].append('Root and child API overrides retain exact values, precedence and login dispatch.')

    profile, workspace = args.output / 'profile', args.output / 'workspace'
    profile.mkdir()
    workspace.mkdir()
    models = json.loads((Path.home() / '.codex/models_cache.json').read_text())['models']
    (profile / 'models.json').write_text(json.dumps({'models': [m for m in models if m['slug'] == 'gpt-6.1-sol']}))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), qa.Responses)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (profile / 'config.toml').write_text(f'''
model = "gpt-6.1-sol"
model_provider = "local-fixture"
model_catalog_json = {json.dumps(str(profile / 'models.json'))}
approval_policy = "never"
sandbox_mode = "read-only"
check_for_update_on_startup = false
web_search = "disabled"
[model_providers.local-fixture]
name = "Local regression fixture"
base_url = "http://127.0.0.1:{server.server_port}/v1"
wire_api = "responses"
requires_openai_auth = false
[features]
steer_only = true
memories = false
chronicle = false
plugins = false
[tui]
animations = false
notifications = false
screen_reader_detection_done = true
[tui.keymap.composer]
queue = []
[projects.{json.dumps(str(workspace))}]
trust_level = "trusted"
''')
    term = qa.Terminal(binary, profile, workspace)
    payload = '!AUDIT_LITERAL_PASTE_MARKER_' + 'a' * 4096
    try:
        term.wait('OpenAI Codex')
        term.read(1)
        term.send('\x1b[200~' + payload + '\x1b[201~')
        assert '[Pasted Content' in term.text(), term.text()
        term.send('\r')
        term.wait('DAYBREAK_QA_OK')
        texts = []
        for request in qa.Responses.requests:
            users = [item for item in request.get('input', []) if item.get('role') == 'user']
            if users:
                content = users[-1].get('content', [])
                texts.append(content if isinstance(content, str) else
                             '\n'.join(part.get('text', '') for part in content))
        assert texts.count(payload) == 1, [text[:100] for text in texts]
        assert '[Pasted Content 4124 chars]' not in texts
        report['checks'].append('A 4,124-character literal paste reaches the model once with its complete text.')
        report['passed'] = True
    finally:
        (args.output / 'terminal.txt').write_text(term.text())
        term.close()
        server.shutdown()
        (args.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
