#!/usr/bin/env python3
"""Check native /fork window handoff against a localhost model and fake launcher."""

import argparse
import http.server
import importlib.util
import json
from pathlib import Path
import sys
import threading


def load_terminal_qa():
    spec = importlib.util.spec_from_file_location(
        "terminal_qa", Path(__file__).with_name("terminal-qa.py"))
    qa = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qa)
    return qa


def sessions(profile):
    result = {}
    for path in (profile / "sessions").rglob("*.jsonl"):
        with path.open() as stream:
            first = json.loads(stream.readline())
        if first.get("type") == "session_meta":
            result[first["payload"]["id"]] = (path, first["payload"])
    return result


def assert_parent_recorded(path, message):
    with path.open() as stream:
        records = [json.loads(line) for line in stream]
    assert any(record.get("type") == "response_item"
               and record["payload"].get("type") == "message"
               and record["payload"].get("role") == "user"
               and any(part.get("type") == "input_text"
                       and part.get("text") == message
                       for part in record["payload"].get("content", []))
               for record in records), "The original chat did not receive the next message"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    executable = args.executable.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    profile = output / "profile"
    workspace = output / "project with spaces"
    (profile / "bin").mkdir(parents=True)
    workspace.mkdir()
    catalog = json.loads((Path.home() / ".codex/models_cache.json").read_text())
    models = [model for model in catalog["models"] if model["slug"] == "gpt-6.1-sol"]
    assert models, "Fixture model missing from model catalog"
    (profile / "models.json").write_text(json.dumps({"models": models}))
    (profile / "auth.json").write_text(json.dumps({
        "auth_mode": "apikey", "OPENAI_API_KEY": "synthetic-local-fork-test",
    }))
    # The native frontend must discover and execute this exact helper. Capture
    # arguments without creating windows, connecting to a daemon, or using any
    # user profile. A marker makes the second invocation fail deterministically.
    helper = profile / "bin/codex-fork-window"
    helper.write_text(f"#!{sys.executable}\n" + '''import json
import os
from pathlib import Path
import sys
profile = Path(os.environ["CODEX_HOME"])
with (profile / "launcher-calls.jsonl").open("a") as stream:
    stream.write(json.dumps({"argv": sys.argv[1:], "codex_home": str(profile)}) + "\\n")
if (profile / "fail-launch").exists():
    print("synthetic launcher failure", file=sys.stderr)
    sys.exit(23)
''')
    helper.chmod(0o755)
    qa = load_terminal_qa()
    qa.Responses.requests = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), qa.Responses)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (profile / "config.toml").write_text(f'''model = "gpt-6.1-sol"
model_reasoning_effort = "xhigh"
model_catalog_json = {json.dumps(str(profile / "models.json"))}
openai_base_url = "http://127.0.0.1:{server.server_port}/v1"
approval_policy = "never"
sandbox_mode = "read-only"
check_for_update_on_startup = false
web_search = "disabled"
[features]
api_key_model_discovery = false
memories = false
plugins = false
chronicle = false
[tui]
animations = false
notifications = false
screen_reader_detection_done = true
status_line = ["thread-id"]
[projects.{json.dumps(str(workspace))}]
trust_level = "trusted"
''')
    term = None
    checks = []
    try:
        term = qa.Terminal(executable, profile, workspace)
        term.wait("OpenAI Codex")
        term.command("fork parent fixture")
        term.wait("DAYBREAK_QA_OK")
        term.read(.5)
        initial = sessions(profile)
        assert len(initial) == 1, initial.keys()
        source = next(iter(initial))
        source_path = initial[source][0]
        for fail in (False, True):
            if fail:
                (profile / "fail-launch").touch()
            before = sessions(profile)
            term.command("/fork")
            term.wait("was created, but its window could not open" if fail
                      else "opened in a new Terminal window.", timeout=35)
            after = sessions(profile)
            children = set(after) - set(before)
            assert len(children) == 1, children
            child = children.pop()
            assert after[child][1].get("forked_from_id") == source, after[child][1]
            calls = [json.loads(line) for line in
                     (profile / "launcher-calls.jsonl").read_text().splitlines()]
            assert len(calls) == (2 if fail else 1), calls
            assert calls[-1] == {
                "argv": ["--resume", child, "--workdir", str(workspace), "--no-daemon"],
                "codex_home": str(profile),
            }, calls[-1]
            assert child in term.text(), "Handoff result omitted the saved child's ID"
            phase = "failure" if fail else "success"
            (output / f"handoff-{phase}.txt").write_text(term.text())
            term.command("/status")
            term.read(1)
            assert source in term.text(), "Fork replaced the original selected chat"
            message = f"parent after handoff {phase}"
            term.command(message)
            # Wait for the submitted user message as well as its response, so
            # a reply still visible from the previous turn cannot pass the test.
            term.wait(message)
            term.wait("DAYBREAK_QA_OK")
            term.read(.5)
            assert_parent_recorded(source_path, message)
            checks.append(f"{phase}: child saved, helper called once, parent remains usable")
        report = {"passed": checks, "real_model_requests": 0,
                  "gui_required": False, "binary": str(executable)}
        (output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    finally:
        if term:
            (output / "terminal.txt").write_text(term.text())
            (output / "terminal.raw").write_bytes(term.output)
            term.close()
        (output / "fixture-requests.json").write_text(
            json.dumps(qa.Responses.requests, indent=2) + "\n")
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
