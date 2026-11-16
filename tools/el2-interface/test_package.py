import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import stage_package as stage
import voice_build


COMMIT = "a" * 40


def fixture(root: Path):
    stock = root / "stock"
    files = {
        "bin/codex": b"official CLI",
        "bin/codex-code-mode-host": b"official code mode",
        "bin/codex-exec-server": b"official executor",
        "codex-resources/bwrap": b"official sandbox",
        stage.VOICE_HELPER: b"official helper",
        stage.VOICE_RUNTIME: json.dumps({"target": stage.APP_TARGET,
                                        "sourceCommit": stage.BASE_COMMIT}).encode(),
    }
    for name in [*voice_build.NATIVE_LIBRARIES.values(), "pcre2-8"]:
        files[f"codex-resources/voice/lib/lib{name}.so.0"] = name.encode()
    for name, contents in files.items():
        target = stock / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents)
        target.chmod(0o755)
    metadata = {"layoutVersion": 1, "version": stage.BASE_VERSION,
                "target": stage.STOCK_TARGET, "variant": "codex", "entrypoint": "bin/codex"}
    (stock / "codex-package.json").write_text(json.dumps(metadata))
    (stock / "codex").symlink_to("bin/codex")
    manifest = {"schemaVersion": 1, "buildCommit": stage.BASE_COMMIT,
                "appVersion": stage.BASE_VERSION, "appTarget": stage.STOCK_TARGET,
                "voiceTarget": stage.APP_TARGET,
                "sha256": {name: stage.digest(stock / name) for name in files}}
    (stock / stage.VOICE_MANIFEST).write_text(json.dumps(manifest))
    cli = root / "built-codex"
    cli.write_bytes(b"CLI with EL2 interface only")
    cli.chmod(0o755)
    helper = root / "built-helper"
    helper.write_text(f"#!{sys.executable}\nprint({COMMIT!r})\n")
    helper.chmod(0o755)
    return stock, cli, helper


def fingerprint(root: Path):
    return {str(p.relative_to(root)): ("link", str(p.readlink())) if p.is_symlink()
            else ("file", p.read_bytes()) for p in root.rglob("*") if p.is_file() or p.is_symlink()}


class StagingTests(unittest.TestCase):
    def test_overlay_truthful_provenance_and_unchanged_donor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stock, cli, helper = fixture(root)
            before = fingerprint(stock)
            output = stage.stage(stock, cli, helper, root / "candidate", COMMIT)
            self.assertEqual(fingerprint(stock), before)
            metadata = json.loads((output / "codex-package.json").read_text())
            self.assertEqual(metadata["target"], stage.APP_TARGET)
            self.assertEqual(metadata["version"], "0.171.0-alpha.7+el2." + COMMIT[:12])
            self.assertEqual((output / "codex").readlink(), Path("bin/codex"))
            self.assertEqual((output / "bin/codex").read_bytes(), cli.read_bytes())
            self.assertEqual((output / stage.VOICE_RUNTIME).read_bytes(),
                             (stock / stage.VOICE_RUNTIME).read_bytes())
            manifest = json.loads((output / stage.VOICE_MANIFEST).read_text())
            self.assertEqual(manifest["buildCommit"], COMMIT)
            self.assertEqual(manifest["appTarget"], stage.APP_TARGET)
            for name, expected in manifest["sha256"].items():
                self.assertEqual(stage.digest(output / name), expected)
            receipt = json.loads((output / "custom-build.json").read_text())
            self.assertEqual(receipt["upstream_channel"], "alpha")
            self.assertEqual(receipt["upstream_release"], "rust-v0.171.0-alpha.7")
            self.assertEqual(receipt["upstream_commit"], stage.BASE_COMMIT)
            self.assertEqual(receipt["components"]["bin/codex"]["source_commit"], COMMIT)
            self.assertEqual(receipt["components"]["bin/codex-exec-server"]["source_commit"],
                             stage.BASE_COMMIT)
            self.assertEqual(receipt["native_voice_runtime"]["source_commit"], stage.BASE_COMMIT)
            self.assertEqual((output / "bin/codex-code-mode-host").read_bytes(),
                             (stock / "bin/codex-code-mode-host").read_bytes())

    def test_existing_output_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stock, cli, helper = fixture(root)
            output = root / "existing"
            output.mkdir()
            (output / "keep").write_text("unrelated")
            with self.assertRaises(FileExistsError):
                stage.stage(stock, cli, helper, output, COMMIT)
            self.assertEqual((output / "keep").read_text(), "unrelated")

    def test_stable_or_other_alpha_donor_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            stock, _, _ = fixture(Path(temporary))
            path = stock / "codex-package.json"
            metadata = json.loads(path.read_text())
            for version in ("0.169.0", "0.171.0-alpha.6"):
                with self.subTest(version=version):
                    metadata["version"] = version
                    path.write_text(json.dumps(metadata))
                    with self.assertRaisesRegex(ValueError, "Expected the official"):
                        stage.validate_stock(stock)

    def test_mixed_alpha_voice_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            stock, _, _ = fixture(Path(temporary))
            path = stock / stage.VOICE_MANIFEST
            manifest = json.loads(path.read_text())
            manifest["buildCommit"] = "b" * 40
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "voice manifest does not match"):
                stage.validate_stock(stock)

    def test_mixed_native_runtime_provenance_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            stock, _, _ = fixture(Path(temporary))
            path = stock / stage.VOICE_RUNTIME
            runtime = json.loads(path.read_text())
            runtime["sourceCommit"] = "b" * 40
            path.write_text(json.dumps(runtime))
            with self.assertRaisesRegex(ValueError, "runtime provenance does not match"):
                stage.validate_stock(stock)

    def test_bad_donor_checksum_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stock, cli, helper = fixture(root)
            (stock / "bin/codex").write_text("changed")
            output = root / "candidate"
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                stage.stage(stock, cli, helper, output, COMMIT)
            self.assertFalse(output.exists())

    def test_mixed_helper_stamp_removes_only_own_failed_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stock, cli, helper = fixture(root)
            before = fingerprint(stock)
            helper.write_text(f"#!{sys.executable}\nprint({'b' * 40!r})\n")
            output = root / "candidate"
            with self.assertRaisesRegex(ValueError, "does not match"):
                stage.stage(stock, cli, helper, output, COMMIT)
            self.assertFalse(output.exists())
            self.assertEqual(fingerprint(stock), before)

    def test_voice_link_recipe_uses_verified_libs_pcre2_and_rpath(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stock, _, _ = fixture(root)
            source = root / "source"
            (source / "codex-rs").mkdir(parents=True)
            env = {"STABLE_GIT_COMMIT": COMMIT, "CARGO_TARGET_DIR": str(root / "target")}
            with patch.object(voice_build.subprocess, "run") as run:
                binary = voice_build.build_voice_host(source, stock, env, root / "build")
            args = run.call_args.args[0]
            used = run.call_args.kwargs["env"]
            self.assertEqual(binary, root / "target/release/codex-voice-host")
            self.assertIn("link-arg=-Wl,-rpath,$ORIGIN/../lib", args)
            self.assertEqual(used["STABLE_GIT_COMMIT"], COMMIT)
            for key in voice_build.NATIVE_LIBRARIES:
                self.assertEqual(used[f"SYSTEM_DEPS_{key}_NO_PKG_CONFIG"], "1")
            linker = Path(used["CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER"])
            self.assertIn("'-lpcre2-8'", linker.read_text())
            self.assertEqual((linker.parent / "libpcre2-8.so").resolve(),
                             stock / "codex-resources/voice/lib/libpcre2-8.so.0")


if __name__ == "__main__":
    unittest.main()
