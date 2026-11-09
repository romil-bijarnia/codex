#!/usr/bin/env python3
"""Check terminal steering against a localhost Responses fixture, without paid inference."""
import argparse
import http.server
import importlib.util
import json
import os
import pathlib
import subprocess
import threading
import time

spec = importlib.util.spec_from_file_location(
    "terminal_qa", pathlib.Path(__file__).with_name('terminal-qa.py')
)
terminal_qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(terminal_qa)


class Fixture(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"data":[],"plugins":[],"has_more":false}')

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        state = self.server.state
        metadata = json.loads(request.get("client_metadata", {}).get("x-codex-turn-metadata", "{}"))
        auxiliary = metadata.get("thread_source") == "thread_title"
        with state["lock"]:
            index = len(state["requests"])
            if not auxiliary:
                state["requests"].append({"path": self.path, "body": request})
        blocked = not auxiliary and index == state["block_index"]
        identifier = f"response-{index}"
        item_id = f"message-{index}"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def event(kind, **payload):
            data = {"type": kind, **payload}
            self.wfile.write(f"event: {kind}\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()

        try:
            event("response.created", response={"id": identifier})
            event("response.output_item.added", output_index=0, item={
                "type": "message", "id": item_id, "role": "assistant", "content": []
            })
            if blocked:
                text = ("<proposed_plan>\n- PLAN_STREAM_ACTIVE\n"
                        if state["scenario"] == "plan" else "WORK_IN_PROGRESS")
                event("response.output_text.delta", item_id=item_id,
                      output_index=0, content_index=0, delta=text)
                state["blocked"].set()
                state["release"].wait(40)
                text += "</proposed_plan>\n" if state["scenario"] == "plan" else " FINISHED_OLD_WORK"
            else:
                text = ('{"title":"Local QA"}' if auxiliary else
                        "STEER_APPLIED" if index > state["block_index"] else "BASELINE_READY")
                event("response.output_text.delta", item_id=item_id,
                      output_index=0, content_index=0, delta=text)
            event("response.output_item.done", output_index=0, item={
                "type": "message", "role": "assistant", "id": item_id,
                "content": [{"type": "output_text", "text": text}],
            })
            event("response.completed", response={"id": identifier, "usage": {
                "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
            }})
        except (BrokenPipeError, ConnectionResetError):
            pass


def run_case(binary, output, scenario, backend=None):
    root = output / scenario
    root.mkdir(parents=True)
    profile, workspace = root / "profile", root / "workspace"
    profile.mkdir()
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    (workspace / "example.txt").write_text("original\n")
    subprocess.run(["git", "-C", str(workspace), "add", "."], check=True)
    subprocess.run(["git", "-C", str(workspace), "-c", "user.name=Local QA", "-c",
                    "user.email=qa@localhost", "commit", "-qm", "fixture"], check=True)
    (workspace / "example.txt").write_text("changed\n")
    state = {"scenario": scenario, "block_index": 1 if scenario == "compact" else 0,
             "requests": [], "lock": threading.Lock(),
             "blocked": threading.Event(), "release": threading.Event()}
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    server.daemon_threads = True
    server.state = state
    threading.Thread(target=server.serve_forever, daemon=True).start()
    catalog = json.loads((pathlib.Path.home() / ".codex/models_cache.json").read_text())
    models = [m for m in catalog["models"] if m["slug"] == "gpt-6.1-sol"]
    assert len(models) == 1
    models[0]["use_responses_lite"] = False
    (profile / "models.json").write_text(json.dumps({"models": models}))
    (profile / "config.toml").write_text(f'''
model = "gpt-6.1-sol"
model_provider = "local-fixture"
model_catalog_json = {json.dumps(str(profile / "models.json"))}
approval_policy = "never"
sandbox_mode = "read-only"
check_for_update_on_startup = false
web_search = "disabled"
[model_providers.local-fixture]
name = "Local steering fixture"
base_url = "http://127.0.0.1:{server.server_port}/v1"
wire_api = "responses"
requires_openai_auth = false
[features]
steer_only = true
instant_interrupt = true
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
    backend_process = None
    backend_log = None
    extra_args = ()
    if backend:
        socket_path = root / "backend.sock"
        endpoint = "unix://" + str(socket_path)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("CODEX_", "OPENAI_", "CHATGPT_"))}
        env["CODEX_HOME"] = str(profile)
        backend_log = (root / "backend.log").open("w")
        backend_process = subprocess.Popen(
            [str(backend), "app-server", "--listen", endpoint], env=env,
            stdin=subprocess.DEVNULL, stdout=backend_log, stderr=backend_log,
        )
        deadline = time.monotonic() + 15
        while not socket_path.exists() and time.monotonic() < deadline:
            assert backend_process.poll() is None, "Fixture backend exited"
            time.sleep(.05)
        assert socket_path.exists(), "Fixture backend did not start"
        extra_args = ("--remote", endpoint)
    terminal = terminal_qa.Terminal(
        binary, profile, workspace, use_daemon=bool(backend), extra_args=extra_args
    )
    # This fixture owns the standalone backend, rather than a managed daemon.
    if backend:
        terminal.use_daemon = False
    try:
        terminal.wait("OpenAI Codex")
        terminal.read(1)
        if scenario == "plan":
            terminal.command("/plan")
            terminal.wait("Plan mode")
        if scenario == "compact":
            terminal.command("Baseline request")
            terminal.wait("BASELINE_READY")
            terminal.read(.5)
            terminal.command("/compact")
        elif scenario == "review":
            terminal.command("/review")
            terminal.wait("Select a review preset")
            terminal.command("2")
        else:
            terminal.command("Initial task")
        deadline = time.monotonic() + 20
        while not state["blocked"].is_set() and time.monotonic() < deadline:
            terminal.read(.1)
        assert state["blocked"].is_set(), terminal.text()
        terminal.read(.5)
        if scenario == "plan":
            terminal.wait("PLAN_STREAM_ACTIVE")
        terminal.send("CORRECT_NOW")
        if scenario == "tab":
            terminal.send("\t")
            terminal.read(.8)
            assert len(state["requests"]) == 1, "Tab submitted the draft"
            assert "to queue" not in terminal.text(), terminal.text()
        terminal.send("\r")
        deadline = time.monotonic() + 20
        while len(state["requests"]) <= state["block_index"] + 1 and time.monotonic() < deadline:
            terminal.read(.1)
        assert len(state["requests"]) > state["block_index"] + 1, (
            "Message waited behind the active work", terminal.text())
        new_request = state["requests"][state["block_index"] + 1]
        assert "CORRECT_NOW" in json.dumps(new_request["body"]), "Follow-up request omitted the correction"
        assert not state["release"].is_set(), "Old work finished before steering"
        terminal.wait("STEER_APPLIED")
        # Finish the retired HTTP handler and ensure it cannot produce a second submission.
        state["release"].set()
        terminal.read(1)
        assert len(state["requests"]) == state["block_index"] + 2, "Unexpected duplicate request"
        return {"scenario": scenario, "passed": True, "requests": len(state["requests"]),
                "steered_before_old_work_finished": True, "duplicate_submissions": False}
    finally:
        state["release"].set()
        (root / "terminal.txt").write_text(terminal.text())
        (root / "terminal.ansi").write_bytes(terminal.output)
        (root / "requests.json").write_text(json.dumps(state["requests"], indent=2))
        terminal.close()
        if backend_process:
            backend_process.terminate()
            try:
                backend_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                backend_process.kill()
                backend_process.wait()
            backend_log.close()
        server.shutdown()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    parser.add_argument("--cases", nargs="+", default=["regular", "plan", "review", "compact", "tab"])
    parser.add_argument("--backend", type=pathlib.Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    results = []
    try:
        for scenario in args.cases:
            result = run_case(args.binary, args.output, scenario, args.backend)
            results.append(result)
            print(json.dumps(result), flush=True)
    finally:
        (args.output / "results.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
