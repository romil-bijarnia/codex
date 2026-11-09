#!/usr/bin/env python3
"""Exercise personal terminal integrations using isolated local fixtures."""
import argparse
import base64
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import tomllib

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("daybreak_qa", Path(__file__).with_name('terminal-qa.py'))
daybreak_qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daybreak_qa)
Terminal = daybreak_qa.Terminal


def run(command, env=None):
    result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, (command, result.returncode, result.stdout, result.stderr)
    return result


def helper_checks(binary, output, render_saved_file=True):
    results = []
    response = run([str(binary), "--help"])
    assert "visualize" in response.stdout and "api" in response.stdout
    for name, expected in [("visualize", "usage: codex visualize"), ("api", "Usage: codex api")]:
        response = run([str(binary), name, "--help"])
        assert expected in response.stdout, response.stdout
        results.append(name + " help reaches the local helper")
    response = run([str(binary), "api", "--version"])
    native_version = run([str(binary), "--version"]).stdout.strip()
    assert response.stdout.strip() == native_version, (response.stdout, native_version)
    results.append("API profile invokes this native version without logging in")
    if render_saved_file:
        fragment = output / "test fragment.html"
        fragment.write_text('<main><h1>Local visualization check</h1><p>Saved viewer.</p></main>\n')
        response = run([str(binary), "visualize", str(fragment), "--no-open", "--json"])
        viewer = json.loads(response.stdout)
        data = Path(viewer["viewer"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == viewer["sha256"]
        assert viewer["opened"] is False and viewer["server_required"] is False
        assert b"Local visualization check" in data
        results.append("visualization renders a real saved file and preserves its hash")
        (output / "viewer.json").write_text(json.dumps(viewer, indent=2) + "\n")
    fixture = output / "helper argument fixture"
    arguments = ["exec", "-c", 'model="name with spaces"', "--", "literal $(value)"]
    script = 'import json,sys; print(json.dumps(sys.argv[1:])); sys.exit(23)\n'
    for name, target in [("api", fixture / ".local/bin/codex-api-chat"),
                         ("visualize", fixture / "local-overrides/visualize-cli/visualize.py")]:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(script)
        env = dict(os.environ)
        env["HOME" if name == "api" else "CODEX_HOME"] = str(fixture)
        response = subprocess.run([str(binary), name, *arguments], env=env, text=True,
                                  capture_output=True, timeout=20)
        assert response.returncode == 23, (name, response.returncode, response.stderr)
        assert json.loads(response.stdout) == arguments, response.stdout
        results.append(name + " preserves quoted arguments and helper exit status")
    return results


def profile_setup(output, name, extra=""):
    root = output / name
    root.mkdir()
    profile = root / "profile"
    workspace = root / "workspace"
    profile.mkdir()
    workspace.mkdir()
    config = f'''
model = "gpt-6.1-sol"
model_provider = "local-qa"
approval_policy = "never"
sandbox_mode = "read-only"
check_for_update_on_startup = false
web_search = "disabled"
{extra}
[model_providers.local-qa]
name = "Local QA without inference"
base_url = "http://127.0.0.1:9/v1"
wire_api = "responses"
requires_openai_auth = false
[features]
memories = false
[tui]
animations = false
notifications = false
screen_reader_detection_done = true
[projects.{json.dumps(str(workspace))}]
trust_level = "trusted"
'''
    (profile / "config.toml").write_text(config)
    return root, profile, workspace


def voice_checks(binary, output):
    root, profile, workspace = profile_setup(output, "voices")
    terminal = Terminal(binary, profile, workspace)
    checks = []
    try:
        terminal.wait("OpenAI Codex")
        terminal.read(1)
        terminal.command("/voice settings")
        terminal.wait("Choose a voice")
        terminal.send("\x1b[B")
        terminal.send("\r")
        terminal.wait("Select voice")
        terminal.send("\x1b[B")
        terminal.wait("aube")
        assert "No results" not in terminal.text(), terminal.text()
        (root / "aube-picker.txt").write_text(terminal.text())
        terminal.send("\r")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            saved = tomllib.loads((profile / "config.toml").read_text())
            if saved.get("realtime", {}).get("voice") == "aube":
                break
            terminal.read()
        assert saved.get("realtime", {}).get("voice") == "aube", saved.get("realtime")
        checks.append("Aube is visible and selection persists in the isolated profile")
        terminal.command("/voice settings")
        terminal.wait("Choose a voice")
        terminal.send("\x1b[B")
        terminal.send("\r")
        terminal.wait("Select voice")
        terminal.send("\x1b[B")
        terminal.wait("bubbie")
        assert "No results" not in terminal.text(), terminal.text()
        (root / "bubbie-picker.txt").write_text(terminal.text())
        terminal.send("\x1b")
        terminal.read(.5)
        saved = tomllib.loads((profile / "config.toml").read_text())
        assert saved["realtime"]["voice"] == "aube"
        checks.append("Bubbie is visible and cancelling keeps the saved voice")
        return checks
    finally:
        (root / "terminal.txt").write_text(terminal.text())
        (root / "terminal.raw").write_bytes(terminal.output)
        terminal.close()


class ReminderServer(http.server.BaseHTTPRequestHandler):
    paths = []

    def log_message(self, *_):
        pass

    def do_GET(self):
        self.paths.append(self.path)
        if self.path.startswith("/api/codex/accounts/check"):
            response = {"accounts": [{"id": "local-qa-account", "plan_type": "pro",
                "can_access_with_session": True, "workspace_backend_origin": "https://chatgpt.com",
                "account_routing_override": "NO_CONSTRAINT"}]}
        elif "/wham/security-setup" in self.path:
            response = {"notice": {"title": "Preference QA reminder", "description": "Local fixture only.",
                "action": {"label": "View", "url": "https://chatgpt.com/cyber"}}}
        else:
            response = {"data": [], "plugins": [], "has_more": False}
        body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def jwt(payload):
    encode = lambda data: base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    return encode({"alg": "none"}) + "." + encode(payload) + ".local-fixture"


def reminder_checks(binary, output):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ReminderServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    try:
        for opted_in in [False, True]:
            name = "reminder-opt-in" if opted_in else "reminder-default"
            root, profile, workspace = profile_setup(output, name)
            catalog = json.loads((Path.home() / ".codex/models_cache.json").read_text())
            (profile / "models.json").write_text(json.dumps({"models": catalog["models"]}))
            config = (profile / "config.toml").read_text().replace('model_provider = "local-qa"', f'''model_provider = "openai"
cli_auth_credentials_store = "file"
model_catalog_json = {json.dumps(str(profile / "models.json"))}
chatgpt_base_url = "http://127.0.0.1:{server.server_port}"
openai_base_url = "http://127.0.0.1:{server.server_port}/v1"''')
            (profile / "config.toml").write_text(config)
            token = jwt({"exp": int(time.time()) + 86400, "iat": int(time.time()),
                "https://api.openai.com/auth": {"chatgpt_user_id": "local-qa-user",
                    "chatgpt_account_id": "local-qa-account", "chatgpt_plan_type": "pro"}})
            (profile / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {
                "id_token": token, "access_token": token, "refresh_token": "local-fixture", "account_id": "local-qa-account"},
                "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
            if opted_in:
                marker = profile / "local-overrides/security-setup-reminder/disabled"
                marker.parent.mkdir(parents=True)
                marker.write_text("Use the ordinary optional reminder in this isolated fixture.\n")
            ReminderServer.paths.clear()
            old_log = os.environ.get("RUST_LOG")
            old_proxy = {k: os.environ.get(k) for k in ["HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy"]}
            os.environ["RUST_LOG"] = "codex_tui::security_setup=debug"
            os.environ.update(HTTPS_PROXY="http://127.0.0.1:9", https_proxy="http://127.0.0.1:9",
                              NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
            terminal = Terminal(binary, profile, workspace)
            try:
                terminal.wait("OpenAI Codex")
                terminal.read(4)
                paths = list(ReminderServer.paths)
                (root / "requests.json").write_text(json.dumps(paths, indent=2) + "\n")
                (root / "terminal.txt").write_text(terminal.text())
                requested = any("/wham/security-setup" in path for path in paths)
                assert requested == opted_in, (opted_in, paths, terminal.text())
                checks.append("reminder opt-in restores the standard request" if opted_in else "default reminder suppression skips the request")
            finally:
                terminal.close()
                if old_log is None:
                    os.environ.pop("RUST_LOG", None)
                else:
                    os.environ["RUST_LOG"] = old_log
                for key, value in old_proxy.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
        return checks
    finally:
        server.shutdown()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--only", choices=["helpers", "voices", "reminders"])
    parser.add_argument("--without-bundled-renderer", action="store_true", help="Defer the installed-plugin renderer check to the target machine")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    checks = []
    skipped = []
    with args.binary.open("rb") as stream:
        binary_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    complete = False
    try:
        for name, function in [("helpers", helper_checks), ("voices", voice_checks), ("reminders", reminder_checks)]:
            if args.only is None or args.only == name:
                if name == "helpers" and args.without_bundled_renderer:
                    results = helper_checks(args.binary, args.output, render_saved_file=False)
                    skipped.append("bundled visualization renderer: validated on target machine before installation")
                else:
                    results = function(args.binary, args.output)
                checks.extend(results)
                print(json.dumps({"passed": results}), flush=True)
        with args.binary.open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == binary_hash, "The selected executable changed during verification"
        complete = True
    finally:
        (args.output / "results.json").write_text(json.dumps({"binary": str(args.binary), "binary_sha256": binary_hash,
            "complete": complete, "passed": checks, "skipped": skipped}, indent=2) + "\n")


if __name__ == "__main__":
    main()
