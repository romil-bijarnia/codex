#!/usr/bin/env python3
"""Install the local CLI sources without copying account or conversation data."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parent
FILES = {
    "security-setup-reminder": ["codex-launcher.cjs", "mute.py", "restore.py"],
    "visualize-cli": ["visualize.py"],
}

def shared_config(text):
    data = tomllib.loads(text)
    if "thread_unload_delay_secs" not in data:
        return "thread_unload_delay_secs = 0\n" + text
    import re
    return re.sub(r"(?m)^thread_unload_delay_secs\s*=.*$", "thread_unload_delay_secs = 0", text, count=1)

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex"))))
    parser.add_argument("--bin-dir", type=Path, default=Path.home()/".local/bin")
    parser.add_argument("--upstream", type=Path, default=Path("/opt/homebrew/lib/node_modules/@openai/codex/bin/codex.js"))
    parser.add_argument("--with-staged-footer", action="store_true")
    args = parser.parse_args(argv)
    if not args.upstream.is_file():
        parser.error("The official npm CLI launcher was not found; pass --upstream explicitly.")
    staged = ROOT/"permission-colors"
    manifest = None
    if args.with_staged_footer:
        manifest = json.loads((staged/"installation.json").read_text())
        native = staged/"package/bin/codex"
        original = Path(manifest["source"]["path"])
        if hashlib.sha256(native.read_bytes()).hexdigest() != manifest["binary_sha256"]:
            parser.error("The staged footer executable does not match its manifest.")
        if hashlib.sha256(original.read_bytes()).hexdigest() != manifest["source"]["sha256"]:
            parser.error("The installed official executable changed; rebuild the footer first.")
        manifest["active"] = True
    home = args.home.expanduser().resolve()
    runtime = home/"local-overrides"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = home/"backups"/("fork-install-"+stamp)
    backup.mkdir(parents=True)
    for folder, names in FILES.items():
        target = runtime/folder
        target.mkdir(parents=True, exist_ok=True)
        for name in names:
            dest = target/name
            if dest.exists():
                saved = backup/folder/name
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, saved)
            content = (ROOT/folder/name).read_bytes()
            if name == "codex-launcher.cjs":
                content = content.replace(
                    b"const upstream = '/opt/homebrew/lib/node_modules/@openai/codex/bin/codex.js';",
                    ("const upstream = "+json.dumps(str(args.upstream.resolve()))+";").encode(),
                )
            dest.write_bytes(content)
            dest.chmod((ROOT/folder/name).stat().st_mode & 0o777)
    config = home/"config.toml"
    if config.exists():
        shutil.copy2(config, backup/"config.toml")
    text = config.read_text() if config.exists() else ""
    config.write_text(shared_config(text))
    retired = runtime/"thread-release"
    if retired.exists():
        (retired/"disabled").write_text("Retired; standard exit uses upstream idle-unload configuration.\n")
    if manifest is not None:
        target = runtime/"permission-colors"
        target.mkdir(parents=True, exist_ok=True)
        if (target/"package").exists():
            shutil.move(str(target/"package"), str(backup/"footer-package"))
        if (target/"installation.json").exists():
            shutil.copy2(target/"installation.json", backup/"footer-installation.json")
        copied = subprocess.run(["/bin/cp", "-cR", str(staged/"package"), str(target/"package")], capture_output=True)
        if copied.returncode:
            shutil.rmtree(target/"package", ignore_errors=True)
            shutil.copytree(staged/"package", target/"package")
        (target/"installation.json").write_text(json.dumps(manifest, indent=2)+"\n")
        (target/"disabled").unlink(missing_ok=True)
    args.bin_dir.mkdir(parents=True, exist_ok=True)
    link = args.bin_dir/"codex"
    target = runtime/"security-setup-reminder/codex-launcher.cjs"
    if link.is_symlink() or link.exists():
        link.rename(backup/"previous-codex-entry")
    link.symlink_to(target)
    print("Installed CLI sources; no server was restarted.")
    print("Backup:", backup)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
