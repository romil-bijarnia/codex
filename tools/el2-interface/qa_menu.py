#!/usr/bin/env python3
"""Exercise the real permissions UI without sending a prompt or model request."""
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
        self.send(b"/permissions\r")
        # Enter can first accept the slash-command completion rather than run it.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.pump()
            if "Update Model Permissions" in self.frame():
                break
        if "Update Model Permissions" not in self.frame() and re.search(
            r"^\s*› /permissions\s*$", self.frame(), re.MULTILINE
        ):
            self.send(b"\r")
        return self.wait(lambda s: "Update Model Permissions" in s and "Full Access" in s,
                         "permissions picker")

    def select_row(self, label: str) -> None:
        match = re.search(r"^.*?([1-9])\.\s+" + re.escape(label), self.frame(), re.MULTILINE)
        if not match:
            raise RuntimeError(f"No numbered {label} row in the current picker")
        self.send(match.group(1).encode())

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
        terminal.wait(lambda s: "0.169.0" in s, "stable CLI startup")
        terminal.permissions()
        terminal.save("permissions-before")
        checks["stock_permissions_visible"] = True
        if not args.baseline:
            terminal.wait(lambda s: "EL2 Control" in s, "EL2 interface row")
            terminal.select_row("EL2 Control")
            terminal.wait(lambda s: "Enable full access?" in s and "without your approval" in s,
                          "the unchanged Full Access confirmation")
            terminal.save("confirmation")
            checks["stock_confirmation_visible"] = True
            terminal.send(b"\r")
            terminal.wait(lambda s: "Existing EL2 interface verified" in s,
                          "real host EL2 verification", timeout=40)
            checks["real_host_verified"] = True
            terminal.permissions()
            terminal.wait(lambda s: re.search(r"EL2 Control\s+\(current\)", s) is not None,
                          "verified EL2 selection")
            terminal.save("permissions-verified")
            checks["verified_row_current"] = True
            terminal.select_row("Full Access")
            terminal.wait(lambda s: "Enable full access?" in s, "ordinary Full Access confirmation")
            terminal.send(b"\r")
            terminal.wait(lambda s: "Enable full access?" not in s,
                          "ordinary permission confirmation closing")
            terminal.permissions()
            terminal.wait(lambda s: re.search(r"Full Access\s+\(current\)", s) is not None,
                          "ordinary permissions restored")
            assert re.search(r"EL2 Control\s+\(current\)", terminal.frame()) is None
            terminal.save("permissions-cleared")
            checks["ordinary_selection_clears_el2_indicator"] = True
        checks["model_prompt_submitted"] = False
    finally:
        terminal.close()
    output = args.artifacts / "verification.json"
    output.write_text(json.dumps(checks, indent=2) + "\n")
    print(json.dumps(checks, indent=2))


if __name__ == "__main__":
    main()
