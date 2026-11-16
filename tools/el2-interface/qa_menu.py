#!/usr/bin/env python3
"""Exercise permission colours and model presets without a prompt or model request."""
from __future__ import annotations

import argparse
import codecs
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import struct
import subprocess
import termios
import time

import pyte

from stage_package import BASE_VERSION


class Terminal:
    def __init__(self, binary: Path, artifacts: Path):
        self.artifacts = artifacts
        artifacts.mkdir(parents=True, exist_ok=True)
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 48, 160, 0, 0))
        self.screen = pyte.Screen(160, 48)
        self.stream = pyte.Stream(self.screen)
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.raw = bytearray()
        self.cursor_queries = 0
        env = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor")
        env.pop("NO_COLOR", None)  # Only this colour-verification process.
        for key in ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_INTERNAL_ORIGINATOR_OVERRIDE"):
            env.pop(key, None)
        self.process = subprocess.Popen(
            [str(binary.resolve()), "--no-daemon", "--no-alt-screen",
             "-c", "check_for_update_on_startup=false",
             "-c", "features.memories=false", "-c", "features.chronicle=false"],
            stdin=slave, stdout=slave, stderr=slave, env=env,
            cwd=Path.home(), start_new_session=True,
        )
        os.close(slave)

    def frame(self) -> str:
        return "\n".join(self.screen.display)

    def pump(self, timeout: float = 0.2) -> None:
        ready, _, _ = select.select([self.master], [], [], timeout)
        if not ready:
            return
        try:
            data = os.read(self.master, 65536)
        except OSError:
            return
        self.raw.extend(data)
        self.stream.feed(self.decoder.decode(data))
        queries = self.raw.count(b"\x1b[6n")
        if queries > self.cursor_queries:
            os.write(self.master, b"\x1b[1;1R" * (queries - self.cursor_queries))
            self.cursor_queries = queries
        if b"\x1b]11;?" in data:
            os.write(self.master, b"\x1b]11;rgb:0000/0000/0000\x1b\\")
        if b"\x1b[?u" in data:
            os.write(self.master, b"\x1b[?0u")

    def wait(self, predicate, label: str, timeout: float = 45) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump()
            frame = self.frame()
            if "Enable full access?" in frame:
                self.save("unexpected-warning")
                raise RuntimeError("Unexpected extra Full Access warning")
            if predicate(frame):
                return frame
            if self.process.poll() is not None:
                raise RuntimeError(f"CLI exited before {label}")
        self.save("failed")
        raise TimeoutError(f"Timed out waiting for {label}; inspect the private terminal capture")

    def send(self, keys: bytes) -> None:
        os.write(self.master, keys)

    def save(self, name: str) -> None:
        output = self.artifacts / f"{name}.txt"
        output.write_text(self.frame())
        output.chmod(0o600)

    def permissions(self) -> str:
        self.slash("permissions", lambda s: "Update Model Permissions" in s)
        return self.wait(lambda s: "Update Model Permissions" in s and "Full Access" in s,
                         "permissions picker")

    def slash(self, command: str, ready) -> str:
        self.send(("/" + command + "\r").encode())
        # Enter can first accept the slash-command completion rather than run it.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.pump()
            if ready(self.frame()):
                break
        if not ready(self.frame()) and re.search(
            r"^\s*› /" + re.escape(command) + r"\s*$", self.frame(), re.MULTILINE
        ):
            self.send(b"\r")
        return self.wait(ready, command + " command")

    def model_status(self, model: str) -> str:
        # Status writes history, not a popup. Escape before the next slash can
        # become an Alt-slash key sequence and leave plain text in the composer.
        def current_status(frame: str) -> bool:
            selections = re.findall(r"Model:\s+(.+?)\s+\(reasoning (\w+)", frame, re.IGNORECASE)
            return bool(selections) and tuple(s.lower() for s in selections[-1]) == (model, "xhigh")

        return self.slash("status", current_status)

    def select_row(self, label: str) -> None:
        match = re.search(r"^.*?([1-9])\.\s+" + re.escape(label), self.frame(), re.MULTILINE)
        if not match:
            raise RuntimeError(f"No numbered {label} row in the current picker")
        self.send(match.group(1).encode())

    def label_colour(self, label: str, expected: str) -> bool:
        for row in range(self.screen.lines - 1, -1, -1):
            text = self.screen.display[row]
            col = text.find(label)
            if col >= 0:
                colours = {self.screen.buffer[row][col + i].fg.lower().lstrip("#")
                           for i, char in enumerate(label) if not char.isspace()}
                return colours == {expected}
        return False

    def close(self) -> None:
        self.send(b"\x1b\x04")
        deadline = time.monotonic() + 5
        while self.process.poll() is None and time.monotonic() < deadline:
            self.pump()
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        output = self.artifacts / "terminal.log"
        output.write_bytes(self.raw)
        output.chmod(0o600)
        os.close(self.master)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-bin", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--baseline", action="store_true", help="Inspect the untouched stock picker only")
    args = parser.parse_args()
    terminal = Terminal(args.codex_bin, args.artifacts)
    checks = {}
    try:
        terminal.wait(lambda s: BASE_VERSION in s, "CLI startup")
        if not args.baseline:
            terminal.model_status("gpt-6.1-sol")
            checks["normal_launch_sol61_xhigh"] = True
        terminal.permissions()
        terminal.save("permissions-before")
        checks["stock_permissions_visible"] = True
        if not args.baseline:
            terminal.wait(lambda s: "EL2 Control" in s, "EL2 interface row")
            terminal.select_row("EL2 Control")
            checks["no_extra_full_access_warning"] = True
            terminal.wait(lambda s: "Existing EL2 interface verified" in s,
                          "real host EL2 verification", timeout=40)
            checks["real_host_verified"] = True
            terminal.wait(lambda s: terminal.label_colour("EL2 Control", "ff5f6d"),
                          "crimson verified EL2 footer", timeout=10)
            checks["el2_crimson"] = True
            terminal.permissions()
            terminal.wait(lambda s: re.search(r"EL2 Control\s+\(current\)", s) is not None,
                          "verified EL2 selection")
            terminal.save("permissions-verified")
            checks["verified_row_current"] = True
            terminal.select_row("Full Access")
            terminal.wait(lambda s: "Update Model Permissions" not in s,
                          "ordinary permission selection closing")
            terminal.permissions()
            terminal.wait(lambda s: re.search(r"Full Access\s+\(current\)", s) is not None,
                          "ordinary permissions restored")
            assert re.search(r"EL2 Control\s+\(current\)", terminal.frame()) is None
            terminal.save("permissions-cleared")
            checks["ordinary_selection_clears_el2_indicator"] = True
            terminal.send(b"\x1b")
            terminal.wait(lambda s: "Update Model Permissions" not in s,
                          "permissions picker closing")
            terminal.wait(lambda s: terminal.label_colour("Full Access", "ff8549"),
                          "app-orange Full Access footer", timeout=10)
            checks["full_access_app_orange"] = True
            terminal.slash("daybreak", lambda s: "Daybreak on. Applies to new turns." in s)
            terminal.model_status("gpt-6-sol")
            terminal.save("daybreak-on")
            checks["daybreak_on_sol6_blue_xhigh"] = True
            terminal.slash("daybreak", lambda s: "Daybreak off. Applies to new turns." in s)
            terminal.model_status("gpt-6.1-sol")
            terminal.save("daybreak-off")
            checks["daybreak_off_sol61_xhigh"] = True
        checks["model_prompt_submitted"] = False
    finally:
        terminal.close()
    output = args.artifacts / "verification.json"
    output.write_text(json.dumps(checks, indent=2) + "\n")
    print(json.dumps(checks, indent=2))


if __name__ == "__main__":
    main()
