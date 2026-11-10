#!/usr/bin/env python3
"""Exercise strict MCP approval through a real binary and disposable localhost fixtures.

No browser runtime, real account, credential store or paid model is used. The
MCP child below is a synthetic Python server named node_repl; it requests only
the approval metadata contract, never JavaScript or computer control.
"""
import argparse
import copy
import hashlib
import http.server
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time


CASES = ("full-access", "auto-allow", "auto-deny", "malformed-strict",
         "human-input", "cancel-review")
PARENT_MODEL = "mcp-review-parent-fixture"
REVIEW_MODEL = "mcp-review-guardian-fixture"


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def fixture_meta(case):
    return {
        "codex_request_type": "approval_request",
        "codex_approval_kind": "mcp_tool_call",
        "tool_name": "js",
        "connector_id": "node_repl",
        "tool_params": {},
        "codex_strict_auto_review": "invalid" if case == "malformed-strict" else True,
        **({"codex_requires_user_input": True} if case == "human-input" else {}),
    }


def mcp_fixture(case, evidence):
    """Minimal newline JSON-RPC MCP server; only an accepted response marks execution."""
    pending = None

    def send(value):
        print(json.dumps({"jsonrpc": "2.0", **value}), flush=True)

    for line in sys.stdin:
        request = json.loads(line)
        method = request.get("method")
        if method == "initialize":
            result = {"protocolVersion": request["params"]["protocolVersion"],
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "synthetic-mcp-review-qa", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "js", "description": "Synthetic approval fixture; no code executes.",
                      "inputSchema": {"type": "object", "properties": {}},
                      "annotations": {"readOnlyHint": True}}]}
        elif method == "tools/call":
            pending = request["id"]
            meta = fixture_meta(case)
            call_meta = request.get("params", {}).get("_meta", {})
            if "callId" in call_meta:
                meta["callId"] = call_meta["callId"]
            schema = {"type": "object", "properties": {}}
            if case == "human-input":
                schema.update(properties={"answer": {"type": "string"}}, required=["answer"])
            write_json(evidence, {"elicitation_issued": True, "executed": False})
            send({"id": "synthetic-approval", "method": "elicitation/create", "params": {
                "message": "Approve this synthetic local test action?",
                "requestedSchema": schema, "_meta": meta}})
            continue
        elif method is None and request.get("id") == "synthetic-approval":
            response = request.get("result", {})
            result_meta = response.get("_meta", {}) or {}
            observed = {"elicitation_issued": True,
                        "action": response.get("action"),
                        "content": response.get("content"),
                        "reviewer_tag": result_meta.get("approvals_reviewer"),
                        "executed": response.get("action") == "accept"}
            write_json(evidence, observed)
            send({"id": pending, "result": {"content": [{"type": "text", "text":
                  "SYNTHETIC_APPROVAL_RESULT " + json.dumps(observed)}]}})
            continue
        elif method == "notifications/cancelled":
            write_json(evidence, {"elicitation_issued": True, "executed": False,
                                  "cancel_notification": True})
            continue
        elif method in ("resources/list", "resources/templates/list"):
            result = {"resources": []} if method == "resources/list" else {"resourceTemplates": []}
        elif "id" not in request:
            continue
        else:
            result = {}
        send({"id": request["id"], "result": result})


class Responses(http.server.BaseHTTPRequestHandler):
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
        metadata = request.get("client_metadata", {})
        guardian = metadata.get("x-openai-subagent") == "guardian"
        try:
            turn_metadata = json.loads(metadata.get("x-codex-turn-metadata", "{}"))
        except (TypeError, ValueError):
            turn_metadata = {}
        auxiliary = turn_metadata.get("thread_source") == "thread_title"
        with state["lock"]:
            state["requests"].append({"guardian": guardian, "auxiliary": auxiliary,
                                      "model": request.get("model"), "path": self.path})
            index = len(state["requests"])
            send_tool = not guardian and not auxiliary and not state["tool_sent"]
            if send_tool:
                state["tool_sent"] = True
        if guardian:
            state["guardian_started"].set()
            if state["case"] == "cancel-review":
                state["release_guardian"].wait(20)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def event(kind, **value):
            self.wfile.write(("event: " + kind + "\ndata: " +
                             json.dumps({"type": kind, **value}) + "\n\n").encode())
            self.wfile.flush()

        try:
            response_id = "fixture-response-" + str(index)
            event("response.created", response={"id": response_id})
            if guardian:
                deny = state["case"] == "auto-deny"
                text = json.dumps({"risk_level": "high" if deny else "low",
                                   "user_authorization": "low" if deny else "high",
                                   "outcome": "deny" if deny else "allow",
                                   "rationale": "Deterministic synthetic localhost QA decision."})
                item = {"type": "message", "role": "assistant", "id": "fixture-review",
                        "content": [{"type": "output_text", "text": text}]}
            elif send_tool:
                item = {"type": "function_call", "call_id": "synthetic-mcp-call",
                        "namespace": "mcp__node_repl", "name": "js", "arguments": "{}"}
            else:
                item = {"type": "message", "role": "assistant", "id": "fixture-complete",
                        "content": [{"type": "output_text", "text":
                                     '{"title":"Synthetic QA"}' if auxiliary else "MCP_REVIEW_QA_DONE"}]}
            event("response.output_item.done", item=item)
            event("response.completed", response={"id": response_id, "usage": {
                "input_tokens": 1, "output_tokens": 1, "total_tokens": 2}})
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            if guardian:
                state["guardian_finished"].set()


class AppServer:
    def __init__(self, binary, profile, workspace, log_path):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("CODEX_", "OPENAI_", "CHATGPT_", "AZURE_OPENAI_"))}
        env.update(HOME=str(profile.parent), CODEX_HOME=str(profile),
                   XDG_CONFIG_HOME=str(profile / "xdg-config"),
                   XDG_DATA_HOME=str(profile / "xdg-data"),
                   XDG_STATE_HOME=str(profile / "xdg-state"),
                   CODEX_APP_SERVER_DISABLE_MANAGED_CONFIG="1",
                   HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9",
                   NO_PROXY="127.0.0.1,localhost")
        self.log = log_path.open("w")
        self.process = subprocess.Popen([str(binary), "app-server"], cwd=workspace,
                                        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.log, text=True, bufsize=1)
        self.messages = queue.Queue()
        self.pending = []
        self.request_id = 0
        self.unexpected_requests = []

        def read():
            for line in self.process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except ValueError:
                    self.messages.put({"protocol_error": "non-JSON app-server output"})
            self.messages.put({"closed": True})

        threading.Thread(target=read, daemon=True).start()

    def send(self, method, params=None):
        self.request_id += 1
        value = {"id": self.request_id, "method": method}
        if params is not None:
            value["params"] = params
        self.write(value)
        return self.request_id

    def write(self, value):
        self.process.stdin.write(json.dumps(value) + "\n")
        self.process.stdin.flush()

    def read(self, timeout):
        try:
            message = self.messages.get(timeout=max(.01, timeout))
        except queue.Empty as exc:
            raise TimeoutError("app-server emitted no response before the deadline") from exc
        if message.get("closed"):
            raise RuntimeError("fixture app-server exited with code " + str(self.process.poll()))
        if message.get("protocol_error"):
            raise RuntimeError(message["protocol_error"])
        if "id" in message and "method" in message:
            # No synthetic test can obtain human approval. Decline any unexpected
            # input request rather than silently granting or answering a form.
            self.unexpected_requests.append(message["method"])
            self.write({"id": message["id"], "result": {"action": "decline"}})
        return message

    def response(self, request_id, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self.read(deadline - time.monotonic())
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    raise RuntimeError("app-server RPC error: " + json.dumps(message["error"])[:600])
                return message.get("result", {})
            self.pending.append(message)
        raise TimeoutError("app-server response deadline expired")

    def call(self, method, params=None, timeout=20):
        return self.response(self.send(method, params), timeout)

    def turn_completed(self, thread_id, turn_id, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for i, message in enumerate(self.pending):
                params = message.get("params", {})
                if (message.get("method") == "turn/completed" and
                        params.get("threadId") == thread_id and
                        params.get("turn", {}).get("id") == turn_id):
                    return self.pending.pop(i)["params"]
            message = self.read(deadline - time.monotonic())
            self.pending.append(message)
        raise TimeoutError("synthetic turn did not complete")

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=5)
        except (BrokenPipeError, subprocess.TimeoutExpired):
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.log.close()


def write_config(profile, workspace, port, case, evidence):
    source = Path(__file__).resolve().parents[3] / "codex-rs/models-manager/models.json"
    catalog = json.loads(source.read_text())["models"]
    parent = copy.deepcopy(next(m for m in catalog if m["slug"] == "gpt-5.5"))
    parent.update(slug=PARENT_MODEL, display_name="Synthetic MCP parent",
                  tool_mode="direct", use_responses_lite=False, prefer_websockets=False,
                  auto_review_model_override=REVIEW_MODEL)
    reviewer = copy.deepcopy(parent)
    reviewer.update(slug=REVIEW_MODEL, display_name="Synthetic MCP reviewer",
                    auto_review_model_override=None)
    write_json(profile / "models.json", {"models": [parent, reviewer]})
    full = case in ("full-access", "malformed-strict", "human-input")
    (profile / "config.toml").write_text(f'''
model = {json.dumps(PARENT_MODEL)}
model_provider = "local-mcp-fixture"
model_catalog_json = {json.dumps(str(profile / "models.json"))}
approval_policy = "{'never' if full else 'on-request'}"
approvals_reviewer = "{'user' if full else 'auto_review'}"
sandbox_mode = "{'danger-full-access' if full else 'workspace-write'}"
check_for_update_on_startup = false
web_search = "disabled"
[model_providers.local-mcp-fixture]
name = "Synthetic localhost MCP review fixture"
base_url = "http://127.0.0.1:{port}/v1"
wire_api = "responses"
requires_openai_auth = false
[features]
guardian_approval = true
memories = false
chronicle = false
plugins = false
apps = false
[analytics]
enabled = false
[mcp_servers.node_repl]
command = {json.dumps(sys.executable)}
args = {json.dumps([str(Path(__file__).resolve()), '--mcp-fixture', case, str(evidence)])}
default_tools_approval_mode = "approve"
startup_timeout_sec = 10
tool_timeout_sec = 30
[projects.{json.dumps(str(workspace))}]
trust_level = "trusted"
''')


def run_case(binary, output, case, timeout):
    root = output / case
    profile, workspace = root / "profile", root / "workspace"
    profile.mkdir(parents=True)
    workspace.mkdir()
    evidence = root / "elicitation.json"
    state = {"case": case, "requests": [], "tool_sent": False, "lock": threading.Lock(),
             "guardian_started": threading.Event(), "guardian_finished": threading.Event(),
             "release_guardian": threading.Event()}
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Responses)
    server.daemon_threads = True
    server.state = state
    threading.Thread(target=server.serve_forever, daemon=True).start()
    write_config(profile, workspace, server.server_port, case, evidence)
    app = AppServer(binary, profile, workspace, root / "app-server.log")
    result = {"case": case, "passed": False, "real_model_requests": 0}
    try:
        app.call("initialize", {"clientInfo": {"name": "mcp-review-qa", "version": "1"},
                 "capabilities": {"experimentalApi": True}})
        app.write({"method": "initialized"})
        thread = app.call("thread/start", {"model": PARENT_MODEL, "cwd": str(workspace),
                                          "ephemeral": True})["thread"]
        turn = app.call("turn/start", {"threadId": thread["id"], "input": [{
            "type": "text", "text": "Run the synthetic js approval fixture once."}]})["turn"]
        if case == "cancel-review":
            assert state["guardian_started"].wait(timeout), "automatic review never started"
            app.call("turn/interrupt", {"threadId": thread["id"], "turnId": turn["id"]})
        completed = app.turn_completed(thread["id"], turn["id"], timeout)
        if case == "cancel-review":
            # Release the deliberately late allow response before checking the
            # execution marker. Cancellation must reject late results too.
            state["release_guardian"].set()
            assert state["guardian_finished"].wait(3), "delayed reviewer fixture did not finish"
            time.sleep(.2)
        observed = json.loads(evidence.read_text()) if evidence.exists() else {}
        guardian_requests = sum(r["guardian"] for r in state["requests"])
        result.update(elicitation=observed, guardian_requests=guardian_requests,
                      parent_requests=sum(not r["guardian"] and not r["auxiliary"] for r in state["requests"]),
                      human_requests=app.unexpected_requests,
                      turn_status=completed.get("turn", {}).get("status"))
        assert observed.get("elicitation_issued"), "native turn never reached the synthetic MCP server"
        if case in ("full-access", "auto-allow"):
            assert observed.get("action") == "accept", "strict approval did not accept"
            assert observed.get("content") == {}, "strict acceptance was not canonical empty content"
            assert observed.get("reviewer_tag") == "auto_review", "canonical reviewer tag missing"
            assert observed.get("executed") is True, "approved synthetic action did not run"
        else:
            assert observed.get("executed") is False, "unapproved synthetic action ran"
            assert observed.get("action") != "accept", "negative case auto-accepted"
        if case == "full-access":
            assert guardian_requests == 0, "Full Access unexpectedly ran reviewer inference"
            assert not app.unexpected_requests, "Full Access unexpectedly asked for input"
        if case in ("auto-allow", "auto-deny", "cancel-review"):
            assert guardian_requests >= 1, "selected automatic review never reached the local reviewer fixture"
        if case in ("malformed-strict", "human-input"):
            assert guardian_requests == 0, "invalid or human-input form was reviewed as an executable action"
        if case == "auto-deny":
            assert observed.get("action") == "decline", "reviewer denial was not enforced"
        if case == "cancel-review":
            assert result["turn_status"] == "interrupted", "parent turn did not stop on cancellation"
        result["passed"] = True
    except Exception as exc:
        result["error"] = str(exc)[:800]
    finally:
        state["release_guardian"].set()
        app.close()
        server.shutdown()
        result.setdefault("guardian_requests", sum(r["guardian"] for r in state["requests"]))
        write_json(root / "result.json", result)
        write_json(root / "model-requests.json", state["requests"])
    return result


def self_test():
    """Check only the synthetic fixture protocol; no Codex binary or model is run."""
    with tempfile.TemporaryDirectory(prefix="mcp-review-fixture-") as directory:
        for action in ("accept", "decline", "cancel"):
            path = Path(directory) / (action + ".json")
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                      "--mcp-fixture", "full-access", str(path)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                for request in (
                    {"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                    {"id": 2, "method": "tools/list"},
                    {"id": 3, "method": "tools/call", "params": {"name": "js", "arguments": {}}},
                ):
                    child.stdin.write(json.dumps(request) + "\n"); child.stdin.flush()
                    response = json.loads(child.stdout.readline())
                assert response["method"] == "elicitation/create"
                assert response["params"]["_meta"]["codex_strict_auto_review"] is True
                child.stdin.write(json.dumps({"id": "synthetic-approval", "result": {
                    "action": action, "content": {} if action == "accept" else None,
                    "_meta": {"approvals_reviewer": "auto_review"}}}) + "\n")
                child.stdin.flush()
                assert json.loads(child.stdout.readline())["id"] == 3
                assert json.loads(path.read_text())["executed"] == (action == "accept")
            finally:
                child.stdin.close(); child.wait(timeout=3)
    print(json.dumps({"fixture_self_test": "passed", "checks": 3, "real_model_requests": 0}))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--mcp-fixture":
        mcp_fixture(sys.argv[2], Path(sys.argv[3])); return
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        self_test(); return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--cases", nargs="+", choices=CASES, default=CASES)
    parser.add_argument("--timeout", type=float, default=35)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    args.output.mkdir(parents=True, exist_ok=False)
    with binary.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    report = {"binary_sha256": digest, "real_model_requests": 0, "cases": []}
    try:
        for case in args.cases:
            result = run_case(binary, args.output.resolve(), case, args.timeout)
            report["cases"].append(result)
            print(json.dumps(result), flush=True)
        report["passed"] = all(case["passed"] for case in report["cases"])
    finally:
        write_json(args.output / "results.json", report)
    if not report.get("passed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
