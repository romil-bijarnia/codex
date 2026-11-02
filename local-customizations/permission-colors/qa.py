#!/usr/bin/env python3
"""Inspect real TUI colors in isolated local-only sessions, without model requests."""

import argparse
import json
import os
from pathlib import Path
import re
import tempfile
import time

import pexpect
import pyte


def scenario(binary, sandbox, colors=True, light=False):
    with tempfile.TemporaryDirectory(prefix="codex-permission-colors-qa-") as temporary:
        home = Path(temporary)
        workspace = home / "workspace"
        workspace.mkdir()
        config = (
            'model = "gpt-6.1-sol"\n'
            'model_provider = "local-qa"\n'
            'approval_policy = "never"\n'
            f'sandbox_mode = {json.dumps(sandbox)}\n'
            'check_for_update_on_startup = false\n'
            'web_search = "disabled"\n'
            '[model_providers.local-qa]\n'
            'name = "Local QA (no inference)"\n'
            'base_url = "http://127.0.0.1:9/v1"\n'
            'wire_api = "responses"\n'
            'requires_openai_auth = false\n'
            f'[projects.{json.dumps(str(workspace))}]\n'
            'trust_level = "trusted"\n'
            '[tui]\n'
            'theme = "catppuccin-mocha"\n'
            'status_line = ["model-with-reasoning", "permissions", "context-window-size"]\n'
            f'status_line_use_colors = {str(colors).lower()}\n'
            'screen_reader_detection_done = true\n'
        )
        (home / "config.toml").write_text(config)
        env = dict(os.environ)
        env.update({
            "CODEX_HOME": str(home),
            "TERM": "xterm-256color",
            "COLORTERM": "truecolor",
            "FORCE_COLOR": "3",
            "NO_PROXY": "127.0.0.1,localhost",
        })
        env.pop("NO_COLOR", None)
        screen = pyte.Screen(140, 40)
        stream = pyte.Stream(screen)
        child = pexpect.spawn(
            str(binary), ["--no-daemon", "--no-alt-screen", "--cd", str(workspace)],
            env=env, encoding="utf-8", codec_errors="replace", dimensions=(40, 140), timeout=1,
        )
        raw = []

        def pump(seconds):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                try:
                    data = child.read_nonblocking(65536, timeout=0.1)
                except pexpect.TIMEOUT:
                    continue
                except pexpect.EOF:
                    raise RuntimeError("CLI exited unexpectedly\n" + "\n".join(screen.display))
                raw.append(data)
                if "\x1b[6n" in data:
                    child.send("\x1b[1;1R")
                if "\x1b]10;?" in data:
                    child.send("\x1b]10;rgb:1e1e/1e1e/1e1e\x07" if light else "\x1b]10;rgb:dede/dede/dede\x07")
                if "\x1b]11;?" in data:
                    child.send("\x1b]11;rgb:ffff/ffff/ffff\x07" if light else "\x1b]11;rgb:0000/0000/0000\x07")
                stream.feed(data)

        label = {"danger-full-access": "Full Access", "workspace-write": "Workspace", "read-only": "Read Only"}[sandbox]
        try:
            deadline = time.monotonic() + 30
            match = None
            while time.monotonic() < deadline:
                pump(0.25)
                for y, line in enumerate(screen.display):
                    if label in line and " · " in line:
                        match = (y, line.index(label), line)
                if match is not None:
                    break
            if match is None:
                raise RuntimeError("No permission footer\n" + "\n".join(screen.display))
            y, x, line = match
            fg = screen.buffer[y][x].fg
            assert all(screen.buffer[y][x + offset].fg == fg for offset in range(len(label)))
            model_x = len(line) - len(line.lstrip())
            result = {
                "label": label, "colors": colors, "light": light,
                "foreground": fg, "model_foreground": screen.buffer[y][model_x].fg,
                "footer": line.strip(),
            }
            # Verify this is still a functioning native UI, not a printed imitation.
            child.send("/permissions")
            pump(0.3)
            child.send("\r")
            pump(0.7)
            menu = "\n".join(screen.display)
            if not re.search(r"Ask for approval|Approve for me", menu):
                raise RuntimeError("Native permission selector did not open\n" + menu)
            child.send("\x1b")
            pump(0.25)
            if sandbox == "danger-full-access" and colors and not light:
                child.send("/permissions :ask-for-approval")
                pump(0.25)
                child.send("\r")
                deadline = time.monotonic() + 10
                switched = None
                while time.monotonic() < deadline:
                    pump(0.25)
                    for switch_y, switch_line in enumerate(screen.display):
                        if "Workspace" in switch_line and " · " in switch_line:
                            switch_x = switch_line.index("Workspace")
                            switched = screen.buffer[switch_y][switch_x].fg
                    if switched is not None:
                        break
                if switched is None:
                    raise RuntimeError("Permission footer did not update after selection\n" + "\n".join(screen.display))
                result["switched_to_workspace_foreground"] = switched
            child.send("/quit")
            pump(0.15)
            child.send("\r")
            child.expect(pexpect.EOF, timeout=10)
            result["native_menu"] = "passed"
            return result
        finally:
            child.close(force=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    parser.add_argument("--expected", choices=["baseline", "app-colors"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = []
    for sandbox, colors, light in [
        ("danger-full-access", True, False),
        ("workspace-write", True, False),
        ("read-only", True, False),
        ("danger-full-access", False, False),
        ("danger-full-access", True, True),
    ]:
        result = scenario(args.binary, sandbox, colors, light)
        results.append(result)
        print(json.dumps(result), flush=True)
    if args.expected == "app-colors":
        assert results[0]["foreground"] == "ff8549", results
        assert results[1]["foreground"] == results[2]["foreground"] == results[3]["foreground"], results
        assert results[0]["foreground"] != results[1]["foreground"], results
        assert results[0]["switched_to_workspace_foreground"] == results[1]["foreground"], results
        assert results[0]["model_foreground"] == results[1]["model_foreground"], results
        assert results[4]["foreground"] != results[0]["foreground"], results
    args.output.write_text(json.dumps({"binary": str(args.binary), "expected": args.expected, "results": results}, indent=2) + "\n")


if __name__ == "__main__":
    main()
