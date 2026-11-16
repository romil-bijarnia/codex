#!/usr/bin/env python3
"""Build a matching GNU voice helper using the verified stock native libraries."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess

from stage_package import validate_stock


NATIVE_LIBRARIES = {
    "GLIB_2_0": "glib-2.0",
    "GOBJECT_2_0": "gobject-2.0",
    "GIO_2_0": "gio-2.0",
    "GSTREAMER_1_0": "gstreamer-1.0",
    "GSTREAMER_BASE_1_0": "gstbase-1.0",
    "GSTREAMER_APP_1_0": "gstapp-1.0",
    "GSTREAMER_AUDIO_1_0": "gstaudio-1.0",
}


def build_voice_host(source: Path, stock: Path, env: dict[str, str], work: Path) -> Path:
    source, stock = source.resolve(strict=True), stock.resolve(strict=True)
    validate_stock(stock)
    if not re.fullmatch(r"[0-9a-f]{40}", env.get("STABLE_GIT_COMMIT", "")):
        raise ValueError("A complete actual source commit is required")
    if not env.get("CARGO_TARGET_DIR"):
        raise ValueError("An explicit Cargo target directory is required")
    library_dir = stock / "codex-resources/voice/lib"
    libraries = {}
    for key, name in NATIVE_LIBRARIES.items():
        path = library_dir / f"lib{name}.so.0"
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"A verified native library is missing or indirect: {path.name}")
        libraries[key] = path
    pcre2 = library_dir / "libpcre2-8.so.0"
    if not pcre2.is_file() or pcre2.is_symlink():
        raise ValueError("The verified native PCRE2 library is missing or indirect")

    aliases = work.resolve() / "voice-link-inputs"
    aliases.mkdir(parents=True, exist_ok=False)
    build_env = dict(env)
    for key, path in libraries.items():
        name = NATIVE_LIBRARIES[key]
        (aliases / f"lib{name}.so").symlink_to(path)
        prefix = "SYSTEM_DEPS_" + key
        build_env.update({prefix + "_NO_PKG_CONFIG": "1", prefix + "_LIB": name,
                          prefix + "_SEARCH_NATIVE": str(aliases), prefix + "_LINK": "dynamic"})
    (aliases / "libpcre2-8.so").symlink_to(pcre2)
    linker = aliases / "voice-cc.py"
    linker.write_text(
        "#!/usr/bin/env python3\nimport os, sys\n"
        f"aliases = {str(aliases)!r}\n"
        "args = sys.argv[1:]\n"
        "if any('codex_voice_host' in arg for arg in args):\n"
        "    args = ['-L' + aliases, *args, '-Wl,--no-as-needed', '-lpcre2-8']\n"
        "os.execv('/usr/bin/cc', ['/usr/bin/cc', *args])\n"
    )
    linker.chmod(0o700)
    build_env["CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER"] = str(linker)
    subprocess.run(
        ["cargo", "rustc", "--locked", "--release", "-p", "codex-voice-host",
         "--bin", "codex-voice-host", "--", "-C", "link-arg=-Wl,-rpath,$ORIGIN/../lib",
         "-C", "link-arg=-Wl,-rpath-link," + str(library_dir)],
        cwd=source / "codex-rs", env=build_env, check=True,
    )
    return Path(env["CARGO_TARGET_DIR"]) / "release/codex-voice-host"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--stock-package", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    print(build_voice_host(args.source, args.stock_package, dict(os.environ), args.work))


if __name__ == "__main__":
    main()
