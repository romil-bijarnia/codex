#!/usr/bin/env python3
"""Stage the patched CLI with all original resources, without activating it."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from build import BASELINE_COMMIT, BUILD, PACKAGE, ROOT, SOURCE, UPSTREAM


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_resources(staged):
    checked = 0
    for original in PACKAGE.rglob("*"):
        if not original.is_file() or original == PACKAGE / "bin/codex":
            continue
        relative = original.relative_to(PACKAGE)
        copy = staged / relative
        if not copy.is_file() or digest(original) != digest(copy):
            raise RuntimeError(f"Packaged resource changed: {relative}")
        checked += 1
    return checked


def main():
    version = json.loads((UPSTREAM / "package.json").read_text())["version"]
    if version != "0.166.0":
        raise RuntimeError("Installed CLI changed; rebuild against the new release first")
    voice = json.loads((PACKAGE / "codex-resources/voice/manifest.json").read_text())
    built = BUILD / "target/release/codex"
    if voice["buildCommit"].encode() not in built.read_bytes():
        raise RuntimeError("Built CLI lacks the matching voice release identity")
    changes = subprocess.check_output(
        ["git", "diff", BASELINE_COMMIT, "--name-only", "--", "codex-rs"], cwd=SOURCE, text=True
    ).splitlines()
    if set(changes) != {
        "codex-rs/Cargo.lock",
        "codex-rs/tui/src/bottom_pane/status_line_style.rs",
        "codex-rs/tui/src/bottom_pane/snapshots/codex_tui__bottom_pane__status_line_style__tests__light_status_line_corrects_pale_custom_theme_colors.snap",
    }:
        raise RuntimeError(f"Unexpected source changes: {changes}")
    destination = ROOT / "package"
    if destination.exists():
        raise RuntimeError("Preserve the existing staged package before replacing it")
    with tempfile.TemporaryDirectory(prefix="stage-", dir=ROOT) as temporary:
        temporary = Path(temporary)
        staged = temporary / "package"
        subprocess.run(["/bin/cp", "-cR", str(PACKAGE), str(staged)], check=True)
        shutil.copy2(built, staged / "bin/codex")
        entitlements = subprocess.check_output(
            ["codesign", "-d", "--entitlements", ":-", str(PACKAGE / "bin/codex")],
            stderr=subprocess.DEVNULL,
        )
        entitlement_file = temporary / "entitlements.plist"
        entitlement_file.write_bytes(entitlements)
        subprocess.run(
            ["codesign", "--force", "--sign", "-", "--entitlements", str(entitlement_file),
             str(staged / "bin/codex")], check=True, capture_output=True,
        )
        subprocess.run(
            ["codesign", "--verify", "--strict", str(staged / "bin/codex")],
            check=True, capture_output=True,
        )
        reported = subprocess.check_output([str(staged / "bin/codex"), "--version"], text=True).strip()
        if reported != f"codex-cli {version}":
            raise RuntimeError(f"Unexpected staged version: {reported}")
        resources = verify_resources(staged)
        os.replace(staged, destination)
    original = PACKAGE / "bin/codex"
    stat = original.stat()
    manifest = {
        "version": version,
        "active": False,
        "source_commit": voice["buildCommit"],
        "source": {
            "path": str(original.resolve()),
            "size": stat.st_size,
            "mtime_ms": stat.st_mtime_ns // 1_000_000,
            "sha256": digest(original),
        },
        "binary_sha256": digest(destination / "bin/codex"),
        "features": ["app-matched-footer-permission-colors"],
        "scope": "CLI footer rendering only; no permission, app-server, voice, or account changes",
        "verification": {"original_resources_unchanged": resources, "signature": "passed"},
    }
    (ROOT / "installation.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest["verification"]))
    print(destination / "bin/codex")


if __name__ == "__main__":
    main()
