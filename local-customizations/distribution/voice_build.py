"""Build the GNU voice helper against the verified official native runtime.

The Rust GLib/GStreamer sys crates contain generated bindings, so they need link
inputs, not a fabricated SDK or the host's older GStreamer headers. Their pinned
system-deps build helper supports explicit NO_PKG_CONFIG, LIB and SEARCH_NATIVE
overrides. The caller must first verify the pinned runtime receipt and hashes.
"""
from pathlib import Path
import re

from common import run


# system-deps dependency keys from the pinned sys crates' Cargo metadata, paired
# with the prepared runtime's actual ELF library names (not pkg-config names).
NATIVE_LIBRARIES = {
    'GLIB_2_0': 'glib-2.0',
    'GOBJECT_2_0': 'gobject-2.0',
    'GIO_2_0': 'gio-2.0',
    'GSTREAMER_1_0': 'gstreamer-1.0',
    'GSTREAMER_BASE_1_0': 'gstbase-1.0',
    'GSTREAMER_APP_1_0': 'gstapp-1.0',
    'GSTREAMER_AUDIO_1_0': 'gstaudio-1.0',
}

# GLib's regex dependency is bundled with the verified runtime but is not
# represented by a Rust system-deps key. Link it explicitly so a newer host
# GLib/PCRE2 pair cannot be selected ahead of the package's matching pair.
RUNTIME_LINK_LIBRARIES = {
    'pcre2-8': 'libpcre2-8.so.0',
}


def build_voice_host(source, package, env, work):
    """Build from the repository root with the app's unchanged source stamp.

    Only this final executable receives loader flags; the main CLI's Cargo flags
    and cache remain unchanged. Aliases are build-only, outside the package.
    Native library bytes and their original provenance receipts are not modified.
    """
    if not re.fullmatch(r'[0-9a-f]{40}', env.get('STABLE_GIT_COMMIT', '')):
        raise RuntimeError('The voice helper requires the complete application source commit.')
    if not env.get('CARGO_TARGET_DIR'):
        raise RuntimeError('The voice helper requires an explicit Cargo target directory.')
    library_dir = Path(package).resolve() / 'codex-resources/voice/lib'
    libraries = {}
    for key, name in NATIVE_LIBRARIES.items():
        path = library_dir / ('lib' + name + '.so.0')
        if not path.is_file() or path.resolve() != path:
            raise RuntimeError(f'The verified native voice library is missing or indirect: {path}')
        libraries[key] = path
    runtime_links = {}
    for name, filename in RUNTIME_LINK_LIBRARIES.items():
        path = library_dir / filename
        if not path.is_file() or path.resolve() != path:
            raise RuntimeError(f'The verified native voice library is missing or indirect: {path}')
        runtime_links[name] = path

    aliases = Path(work).resolve() / 'voice-link-inputs'
    aliases.mkdir(parents=True, exist_ok=False)
    build_env = dict(env)
    for key, path in libraries.items():
        name = NATIVE_LIBRARIES[key]
        (aliases / ('lib' + name + '.so')).symlink_to(path)
        prefix = 'SYSTEM_DEPS_' + key
        build_env.update({prefix + '_NO_PKG_CONFIG': '1',
                          prefix + '_LIB': name,
                          prefix + '_SEARCH_NATIVE': str(aliases),
                          prefix + '_LINK': 'dynamic'})
    for name, path in runtime_links.items():
        (aliases / ('lib' + name + '.so')).symlink_to(path)

    # Cargo can otherwise place host /usr/lib ahead of the verified aliases
    # when another native dependency contributes a search path. Restrict the
    # reordering and the explicit PCRE2 edge to the final voice executable.
    linker = aliases / 'voice-cc.py'
    linker.write_text(
        '#!/usr/bin/env python3\n'
        'import os, sys\n'
        f'aliases = {str(aliases)!r}\n'
        'args = sys.argv[1:]\n'
        "if any('codex_voice_host' in arg for arg in args):\n"
        "    args = ['-L' + aliases, *args, '-Wl,--no-as-needed', '-lpcre2-8']\n"
        "os.execv('/usr/bin/cc', ['/usr/bin/cc', *args])\n"
    )
    linker.chmod(0o700)
    build_env['CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER'] = str(linker)

    run(['cargo', 'rustc', '--locked', '--release', '-p', 'codex-voice-host',
         '--bin', 'codex-voice-host', '--',
         '-C', 'link-arg=-Wl,-rpath,$ORIGIN/../lib',
         '-C', 'link-arg=-Wl,-rpath-link,' + str(library_dir)],
        cwd=Path(source) / 'codex-rs', env=build_env)
    return Path(env['CARGO_TARGET_DIR']) / 'release/codex-voice-host'
