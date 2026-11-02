#!/usr/bin/env python3
"""Build the version-matched footer-only patch without changing global Rust tools."""

import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parents[1]
BASELINE_COMMIT = "6ff1e1ca3b30fc8b914720681fcf5fe62e95f856"
BUILD = Path.home() / ".cache/codex-permission-colors-build"
UPSTREAM = Path("/opt/homebrew/lib/node_modules/@openai/codex")
PACKAGE = UPSTREAM / "node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin"


def environment():
    manifest = json.loads((PACKAGE / "codex-resources/voice/manifest.json").read_text())
    subprocess.run(["git", "merge-base", "--is-ancestor", BASELINE_COMMIT, "HEAD"], cwd=SOURCE, check=True)
    commit = BASELINE_COMMIT
    if manifest["buildCommit"] != commit or manifest["appVersion"] != "0.166.0":
        raise RuntimeError("Source and installed voice resources must match exactly")
    env = dict(os.environ)
    env.update({
        "CARGO_HOME": str(BUILD / "cargo"),
        "RUSTUP_HOME": str(BUILD / "rustup"),
        "RUSTUP_TOOLCHAIN": "1.99.1",
        "CARGO_TARGET_DIR": str(BUILD / "target"),
        "STABLE_GIT_COMMIT": commit,
        "CARGO_NET_GIT_FETCH_WITH_CLI": "true",
        "CARGO_HTTP_TIMEOUT": "60",
        "GIT_HTTP_LOW_SPEED_LIMIT": "1024",
        "GIT_HTTP_LOW_SPEED_TIME": "60",
        "CARGO_PROFILE_RELEASE_LTO": "thin",
        "CARGO_PROFILE_RELEASE_CODEGEN_UNITS": "4",
        "CARGO_PROFILE_RELEASE_DEBUG": "0",
        "CARGO_PROFILE_RELEASE_STRIP": "symbols",
    })
    env["PATH"] = str(BUILD / "cargo/bin") + os.pathsep + env.get("PATH", "")
    return env


def main():
    env = environment()
    cargo = str(BUILD / "cargo/bin/cargo")
    commands = [
        [cargo, "fmt", "--", "--check"],
        [cargo, "test", "--locked", "--release", "-p", "codex-tui", "--lib",
         "--jobs", "8", "bottom_pane::status_line_style::tests", "--", "--test-threads=1"],
        [cargo, "build", "--locked", "--release", "-p", "codex-cli", "--bin", "codex", "--jobs", "8"],
    ]
    for command in commands:
        print("Running:", " ".join(command), flush=True)
        subprocess.run(command, cwd=SOURCE / "codex-rs", env=env, check=True)
    if env["STABLE_GIT_COMMIT"].encode() not in (BUILD / "target/release/codex").read_bytes():
        raise RuntimeError("Built CLI is missing the matching voice build identity")


if __name__ == "__main__":
    main()
