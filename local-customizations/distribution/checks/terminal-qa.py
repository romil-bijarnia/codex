#!/usr/bin/env python3
"""Exercise /daybreak in the real terminal UI against a local Responses fixture."""

import argparse
import codecs
import fcntl
import http.server
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import termios
import threading
import time

import pyte


class Responses(http.server.BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *_):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append(request)
        events = [
            {"type": "response.created", "response": {"id": "daybreak_qa"}},
            {"type": "response.output_item.done", "item": {
                "type": "message", "role": "assistant", "id": "daybreak_qa_message",
                "content": [{"type": "output_text", "text": "DAYBREAK_QA_OK"}],
            }},
            {"type": "response.completed", "response": {"id": "daybreak_qa", "usage": {
                "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
            }}},
        ]
        body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Terminal:
    def __init__(self, executable, profile, workspace, use_daemon=False,
                 resume_thread=None, extra_args=()):
        self.screen = pyte.Screen(110, 35)
        self.stream = pyte.Stream(self.screen)
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.master, self.slave = pty.openpty()
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", 35, 110, 0, 0))
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("CODEX_", "OPENAI_", "CHATGPT_"))}
        env.update(CODEX_HOME=str(profile), TERM="xterm-256color", COLORTERM="truecolor")
        self.env = env
        self.executable = executable
        self.use_daemon = use_daemon
        self.process = subprocess.Popen(
            [str(executable), *([] if use_daemon else ["--no-daemon"]),
             "--no-alt-screen", "-C", str(workspace), *extra_args,
             *(["resume", resume_thread] if resume_thread else [])],
            env=env, stdin=self.slave, stdout=self.slave, stderr=self.slave,
            cwd=workspace, start_new_session=True,
        )
        self.output = b""

    def read(self, duration=0.3):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            if not select.select([self.master], [], [], .02)[0]:
                continue
            try:
                data = os.read(self.master, 65536)
            except OSError:
                break
            if not data:
                break
            self.output += data
            self.stream.feed(self.decoder.decode(data))
            replies = []
            if b"\x1b[6n" in data:
                replies.append(b"\x1b[1;1R")
            if b"\x1b[c" in data or b"\x1b[0c" in data:
                replies.append(b"\x1b[?62;4;6;22c")
            if b"\x1b[?u" in data:
                replies.append(b"\x1b[?0u")
            if b"\x1b[>q" in data:
                replies.append(b"\x1bP>|foot(1.28.0)\x1b\\")
            if replies:
                os.write(self.master, b"".join(replies))

    def text(self):
        return "\n".join(self.screen.display)

    def wait(self, text, timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.read()
            if text in self.text():
                return
            if self.process.poll() is not None:
                break
        raise AssertionError(f"Did not see {text!r}:\n{self.text()}")

    def send(self, value):
        os.write(self.master, value.encode())
        self.read()

    def command(self, value):
        self.send(value)
        self.send("\r")

    def close(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        os.close(self.master)
        os.close(self.slave)
        if self.use_daemon:
            subprocess.run([str(self.executable), "app-server", "daemon", "stop"],
                           env=self.env, capture_output=True, timeout=15, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--daemon", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    profile = args.output / "profile"
    workspace = args.output / "workspace"
    profile.mkdir()
    workspace.mkdir()
    catalog = json.loads((Path.home() / ".codex/models_cache.json").read_text())
    models = [m for m in catalog["models"] if m["slug"] in ["gpt-6.1-sol", "gpt-6-sol"]]
    assert len(models) == 2
    (profile / "models.json").write_text(json.dumps({"models": models}))
    (profile / "auth.json").write_text(json.dumps({
        "auth_mode": "apikey", "OPENAI_API_KEY": "synthetic-local-daybreak-test",
    }))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Responses)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (profile / "config.toml").write_text(f'''
model = "gpt-6.1-sol"
model_reasoning_effort = "xhigh"
daybreak = false
model_catalog_json = {json.dumps(str(profile / "models.json"))}
openai_base_url = "http://127.0.0.1:{server.server_port}/v1"
approval_policy = "never"
sandbox_mode = "read-only"
[features]
cli_daybreak = true
api_key_cyber_access_programs = true
api_key_model_discovery = false
memories = false
[tui]
animations = false
notifications = false
screen_reader_detection_done = true
[projects.{json.dumps(str(workspace))}]
trust_level = "trusted"
''')
    term = Terminal(args.executable, profile, workspace, use_daemon=args.daemon)
    checks = []
    try:
        term.wait("OpenAI Codex")
        term.read(2)
        term.command("/daybreak")
        term.wait("Select a model for Daybreak")
        (args.output / "picker.txt").write_text(term.text())
        assert "GPT-6-Sol" in term.text()
        assert "Daybreak on." not in term.text()
        checks.append("incompatible model opens compatible picker before enabling")
        term.send("\x1b")
        import tomllib
        saved = tomllib.loads((profile / "config.toml").read_text())
        assert saved["daybreak"] is False and saved["model"] == "gpt-6.1-sol"
        checks.append("cancelling preserves model and Daybreak setting")
        term.command("/daybreak")
        term.wait("Select a model for Daybreak")
        term.send("\r")
        term.wait("Daybreak on.")
        saved = tomllib.loads((profile / "config.toml").read_text())
        assert saved["daybreak"] is True and saved["model"] == "gpt-6-sol"
        assert saved["model_reasoning_effort"] == "xhigh"
        checks.append("selection changes model, preserves supported effort, and enables Daybreak")
        term.command("hello")
        term.wait("DAYBREAK_QA_OK")
        turns = [r for r in Responses.requests if r.get("access_programs")]
        assert turns[-1]["model"] == "gpt-6-sol"
        assert turns[-1]["access_programs"]["cyber"] == "daybreak_blue"
        checks.append("next request uses selected model and Daybreak Blue")
        term.command("/daybreak")
        term.wait("Daybreak off.")
        term.command("hello again")
        deadline = time.monotonic() + 20
        while len([r for r in Responses.requests if r.get("access_programs")]) < 2 and time.monotonic() < deadline:
            term.read()
        turns = [r for r in Responses.requests if r.get("access_programs")]
        assert len(turns) == 2
        assert turns[-1]["model"] == "gpt-6-sol"
        assert turns[-1]["access_programs"]["cyber"] == "standard"
        checks.append("toggle off keeps the chosen model and sends standard access")
        print(json.dumps({"passed": checks}, indent=2))
        (args.output / "results.json").write_text(json.dumps({"passed": checks}, indent=2))
    finally:
        (args.output / "terminal.txt").write_text(term.text())
        (args.output / "terminal.raw").write_bytes(term.output)
        (args.output / "request-programs.json").write_text(json.dumps([
            {k: r.get(k) for k in ["model", "access_programs"]}
            for r in Responses.requests
        ], indent=2))
        term.close()
        server.shutdown()


if __name__ == "__main__":
    main()
