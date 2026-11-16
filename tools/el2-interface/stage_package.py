#!/usr/bin/env python3
"""Stage the EL2-interface build without installing or modifying its stock donor."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


BASE_VERSION = "0.171.0-alpha.7"
BASE_COMMIT = "73c4af9125a8d5e9aabe5aa900eecf70214ba856"
APP_TARGET = "aarch64-unknown-linux-gnu"
STOCK_TARGET = "aarch64-unknown-linux-musl"
VOICE_MANIFEST = "codex-resources/voice/manifest.json"
VOICE_RUNTIME = "codex-resources/voice/runtime.json"
VOICE_HELPER = "codex-resources/voice/bin/codex-voice-host"


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def physical_file(root: Path, relative: str) -> Path:
    path = root / relative
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Expected a physical package file: {relative}")
    if not path.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
        raise ValueError(f"Package path escapes its root: {relative}")
    return path


def validate_stock(package: Path) -> tuple[dict, dict, dict]:
    package = package.resolve(strict=True)
    metadata = json.loads(physical_file(package, "codex-package.json").read_text())
    manifest = json.loads(physical_file(package, VOICE_MANIFEST).read_text())
    runtime = json.loads(physical_file(package, VOICE_RUNTIME).read_text())
    if (metadata.get("version"), metadata.get("target"), metadata.get("entrypoint")) != (
        BASE_VERSION, STOCK_TARGET, "bin/codex"
    ):
        raise ValueError(f"Expected the official {BASE_VERSION} Linux ARM64 package")
    if (manifest.get("buildCommit"), manifest.get("appVersion"),
        manifest.get("appTarget"), manifest.get("voiceTarget")) != (
        BASE_COMMIT, BASE_VERSION, STOCK_TARGET, APP_TARGET
    ):
        raise ValueError("Stock voice manifest does not match the official release")
    if (runtime.get("sourceCommit"), runtime.get("target")) != (BASE_COMMIT, APP_TARGET):
        raise ValueError("Stock native runtime provenance does not match the release")
    hashes = manifest.get("sha256", {})
    if not {"bin/codex", VOICE_HELPER, VOICE_RUNTIME}.issubset(hashes):
        raise ValueError("Stock voice manifest omits required files")
    for name, expected in hashes.items():
        if not re.fullmatch(r"[0-9a-f]{64}", expected) or digest(physical_file(package, name)) != expected:
            raise ValueError(f"Stock package checksum mismatch: {name}")
    return metadata, manifest, runtime


def stage(stock: Path, cli: Path, voice_helper: Path, output: Path, source_commit: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit) or source_commit == BASE_COMMIT:
        raise ValueError("Provide the actual, distinct EL2-interface source commit")
    stock, cli, voice_helper = (p.resolve(strict=True) for p in (stock, cli, voice_helper))
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Refusing to overwrite an existing output: {output}")
    if output.resolve().is_relative_to(stock):
        raise ValueError("Output must be outside the stock donor")
    for path in (cli, voice_helper):
        if not path.is_file() or not path.stat().st_mode & 0o111:
            raise ValueError("Built CLI and helper must be executable regular files")
    metadata, manifest, runtime = validate_stock(stock)
    runtime_bytes = (stock / VOICE_RUNTIME).read_bytes()
    version = f"{BASE_VERSION}+el2.{source_commit[:12]}"
    created = False
    try:
        output.mkdir()
        created = True
        shutil.copytree(stock, output, symlinks=True, dirs_exist_ok=True)
        shutil.copy2(cli, physical_file(output, "bin/codex"))
        shutil.copy2(voice_helper, physical_file(output, VOICE_HELPER))
        actual = subprocess.run(
            [str(output / VOICE_HELPER), "--build-commit"],
            check=True, capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        if actual != source_commit:
            raise ValueError("Built voice helper does not match the application source commit")

        metadata.update(version=version, target=APP_TARGET)
        (output / "codex-package.json").write_text(json.dumps(metadata, indent=2) + "\n")
        manifest.update(buildCommit=source_commit, appVersion=version, appTarget=APP_TARGET)
        manifest["sha256"] = {
            name: digest(physical_file(output, name)) for name in manifest["sha256"]
        }
        (output / VOICE_MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
        assert (output / VOICE_RUNTIME).read_bytes() == runtime_bytes

        components = {}
        for name in ("bin/codex", VOICE_HELPER, "bin/codex-code-mode-host",
                     "bin/codex-exec-server", "codex-resources/bwrap"):
            path = physical_file(output, name)
            modified = name in {"bin/codex", VOICE_HELPER}
            components[name] = {
                "source_commit": source_commit if modified else BASE_COMMIT,
                "target": APP_TARGET if modified else STOCK_TARGET,
                "sha256": digest(path),
            }
        receipt = {
            "schema_version": 1,
            "customization": "EL2 interface, permission UI and Daybreak model presets",
            "version": version,
            "source_commit": source_commit,
            "upstream_channel": "alpha",
            "upstream_release": f"rust-v{BASE_VERSION}",
            "upstream_commit": BASE_COMMIT,
            "target": APP_TARGET,
            "components": components,
            "native_voice_runtime": {
                "source_commit": runtime["sourceCommit"],
                "target": runtime["target"],
                "receipt_sha256": digest(output / VOICE_RUNTIME),
                "unchanged_from_official_release": True,
            },
        }
        (output / "custom-build.json").write_text(json.dumps(receipt, indent=2) + "\n")
        return output
    except BaseException:
        if created:
            shutil.rmtree(output)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock-package", type=Path, required=True)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--voice-helper", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(stage(args.stock_package, args.cli, args.voice_helper, args.output, args.source_commit))


if __name__ == "__main__":
    main()
