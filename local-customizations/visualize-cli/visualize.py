#!/usr/bin/env python3
"""Explicit local browser-viewer fallback for Codex terminal sessions.

This is not a replacement for the native TUI renderer. It keeps Full Access and
all existing launcher integrations intact and needs no preview server.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
MAX_BYTES = 1_000_000


def find_renderer(codex_home: Path) -> Path:
    base = codex_home / "plugins/cache/openai-bundled/visualize"
    candidates = []
    if base.is_dir():
        for version in base.iterdir():
            if not re.fullmatch(r"\d+(?:\.\d+){1,3}", version.name):
                continue
            script = version / "skills/visualize/scripts/render.py"
            if script.is_file():
                candidates.append((tuple(map(int, version.name.split("."))), script))
    if not candidates:
        raise ValueError("The bundled Visualize renderer is not installed in " + str(base))
    return max(candidates)[1]


def load_renderer(path: Path):
    name = "_codex_visualize_cli_renderer"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load the installed Visualize renderer")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def make_viewer(source: Path, *, codex_home: Path, viewer_root: Path) -> dict:
    source = source.expanduser().resolve(strict=True)
    if source.suffix.lower() != ".html" or not source.is_file():
        raise ValueError("Choose a local .html visualization fragment")
    if source.stat().st_size > MAX_BYTES:
        raise ValueError("Visualization fragments must be smaller than 1 MB")
    renderer_path = find_renderer(codex_home)
    renderer = load_renderer(renderer_path)
    # The installed renderer rejects full HTML documents and wraps fragments in
    # its CSP-restricted, opaque-origin iframe. Do not relax those protections.
    document = renderer.render(source)
    encoded = document.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    folder = viewer_root / digest[:24]
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.resolve() != folder.absolute():
        raise ValueError("Viewer storage must not contain symbolic links")
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", source.stem).strip(".-")[:80]
    destination = folder / ((stem or "visualization") + ".html")
    if destination.is_symlink():
        raise ValueError("Viewer destination must not be a symbolic link")
    if destination.exists():
        if destination.read_bytes() != encoded:
            raise ValueError("The existing viewer was modified; refusing to overwrite it")
    else:
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=folder, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
            try:
                # Publish without overwriting any concurrently created file.
                os.link(temporary_path, destination, follow_symlinks=False)
            except FileExistsError:
                if destination.is_symlink() or destination.read_bytes() != encoded:
                    raise ValueError("Viewer destination changed during rendering")
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
    return {
        "source": str(source),
        "viewer": str(destination),
        "url": destination.as_uri(),
        "renderer": str(renderer_path),
        "sha256": digest,
        "server_required": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="codex visualize",
        description="Open a saved visualization in your browser. No preview server is needed.",
    )
    parser.add_argument("fragment", type=Path, help="path to the visualization HTML fragment")
    parser.add_argument("--no-open", action="store_true", help="create the viewer without opening a browser")
    parser.add_argument("--json", action="store_true", help="print machine-readable viewer details")
    args = parser.parse_args(argv)
    try:
        home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser().resolve()
        result = make_viewer(args.fragment, codex_home=home, viewer_root=ROOT / "viewers")
        result["opened"] = False
        if not args.no_open:
            if sys.platform == "darwin":
                subprocess.run(["/usr/bin/open", "-b", "com.apple.Safari", result["viewer"]], check=True)
            elif sys.platform.startswith("linux"):
                subprocess.run(["xdg-open", result["url"]], check=True)
            else:
                raise ValueError("Use --no-open on this platform")
            result["opened"] = True
        if args.json:
            print(json.dumps(result))
        else:
            print("Opened visualization in your browser." if result["opened"] else "Saved browser viewer.")
            print(result["url"])
            print("Saved locally; no preview server is needed.")
        return 0
    except (OSError, UnicodeError, ValueError, subprocess.CalledProcessError) as error:
        print("codex visualize: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
