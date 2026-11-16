# EL2-interface-only build support

This folder builds and stages the single EL2 permissions-interface patch on
official experimental Codex 0.171.0-alpha.7, commit
`73c4af9125a8d5e9aabe5aa900eecf70214ba856`. It contains no installer, updater, launcher,
watcher or automatic patch application. It never changes the current selection
or Codex user data. Commit the actual interface source before stamping binaries.

Always retain the EL2 permissions menu when updating Codex. Never replace this
build with an unmodified stock release. Future stable or experimental updates
must port the interface patch, rebuild matching helpers, and pass the package,
voice-handshake and permissions-menu checks before selecting the new package.
The ordinary `codex update` route installs stock latest stable and is not the
update route for this build.

Use Rust 1.99.1 and a separately controlled build directory/cache. Preserve the
working official installation until package and UI checks pass. Set `SRC` to
this source checkout and `STOCK` to the checksum-verified official
0.171.0-alpha.7 Linux ARM64 standalone package. The compile target is the host
GNU target, not musl. The official package and native voice receipt must both
identify the pinned alpha source commit above.

```sh
export PATH="$HOME/.rustup/toolchains/1.99.1-aarch64-unknown-linux-gnu/bin:$PATH"
export RUSTUP_TOOLCHAIN=1.99.1 CODEX_REPO_ROOT="$SRC"
export STABLE_GIT_COMMIT="$(git -C "$SRC" rev-parse HEAD)"
export CARGO_TARGET_DIR="$HOME/.cache/codex-owner-build" CARGO_BUILD_JOBS=2
export CARGO_PROFILE_RELEASE_DEBUG=0 CARGO_PROFILE_RELEASE_STRIP=symbols
export CARGO_PROFILE_RELEASE_LTO=false CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16
export CODEX_BUILD_TARGET=aarch64-unknown-linux-gnu
export CODEX_BWRAP_SHA256=f5065eb8b9ddd9c89cac0ce79ed1aafab5841c7d9c3e73674c94724d6bf8f7c0
printf '%s  %s\n' "$CODEX_BWRAP_SHA256" "$STOCK/codex-resources/bwrap" | sha256sum -c -

eval "$(PYTHONPATH="$SRC/scripts" python3 - <<'PY'
from pathlib import Path
import shlex
from codex_package.targets import TARGET_SPECS
from codex_package.v8 import resolve_codex_v8_cargo_env
for key, value in resolve_codex_v8_cargo_env(
    TARGET_SPECS['aarch64-unknown-linux-gnu'],
    cache_root=Path.home()/'.cache/codex-owner-v8',
).items():
    print('export '+key+'='+shlex.quote(value))
PY
)"
(cd "$SRC/codex-rs" && cargo build --locked --release --jobs 2 -p codex-cli --bin codex)
python3 "$SRC/tools/el2-interface/voice_build.py" \
  --source "$SRC" --stock-package "$STOCK" --work "$BUILD_WORK"
python3 "$SRC/tools/el2-interface/stage_package.py" \
  --stock-package "$STOCK" --cli "$CARGO_TARGET_DIR/release/codex" \
  --voice-helper "$CARGO_TARGET_DIR/release/codex-voice-host" \
  --source-commit "$STABLE_GIT_COMMIT" --output "$CANDIDATE"
```

`BUILD_WORK` must be fresh for its private voice-link aliases; `CANDIDATE` must
not exist. Check the alpha tag's workspace-only lockfile version refresh before
building with `--locked`; do not update unrelated external dependency pins.

The unchanged helper source must be rebuilt because its handshake requires the
CLI's exact source commit. Its link recipe uses only receipt-checked bundled
GLib/GStreamer libraries, an explicit bundled PCRE2 edge and relocatable RPATH.
The donor SDK is not required because the Rust sys crates have generated
bindings. No native library, runtime receipt or library-source provenance is
rewritten. The staging helper updates app/helper hashes and records the patched
GNU main and helper separately from untouched official code-mode, exec-server,
bwrap and native voice resources. The displayed package version is
`0.171.0-alpha.7+el2.<source-commit-prefix>`, retaining the experimental release
identity rather than presenting it as stable or an unmodified official build.

## Checks before selecting the candidate

```sh
python3 -m unittest discover -s "$SRC/tools/el2-interface" -p 'test_*.py' -v
(cd "$SRC/codex-rs" && cargo fmt --all -- --check)
# Use the same release environment above for focused EL2 and permission tests.
(cd "$SRC/codex-rs" && RUST_MIN_STACK=8388608 cargo test --locked --release -p codex-tui --lib el2)
```

Additionally inspect the real permissions menu and normal permission modes.
The staged helper's `--build-commit` must equal the actual interface commit.
For a no-audio native-runtime check, exchange 4-byte-big-endian-length-prefixed
JSON over its stdio: `hello` with `protocol: 1` and `buildCommit`, then
`initializeRuntime`, then `close`; expect `ready`, `runtimeReady`, `closed`.
Send each request only after its previous acknowledgement and bound every read.
Do not open audio devices or start a transport merely to test this UI patch.
## Actual terminal acceptance

First verify the exact staged voice helper without opening any audio device or
network transport:

```sh
python3 tools/el2-interface/qa_voice.py \
  --package "$CANDIDATE" --source-commit "$STABLE_GIT_COMMIT"
```

`qa_menu.py` requires `pyte` and an existing signed-in CLI profile. It starts a
private terminal frontend with native `--no-daemon`, opens the permissions menu,
accepts the normal Full Access confirmation, verifies the existing EL2 bridge,
and checks that ordinary Full Access clears the interface indicator. No prompt
or model request is sent. Memory/Chronicle processing is disabled only for that
test invocation; saved configuration is unchanged. This is an explicit bridge
activation test and must run only on its authorized owner host.

```sh
python3 tools/el2-interface/qa_menu.py \
  --codex-bin "$CANDIDATE/bin/codex" --artifacts /path/to/private/qa-output
```

Use `--baseline` to inspect the untouched stock picker without selecting a mode
or activating the bridge. Generated captures and verification receipts are not
source files and must not be committed.
