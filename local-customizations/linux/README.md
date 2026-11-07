The Linux client preserves the existing footer colors, local helpers, voice
settings and setup reminders. It adds EL2 Control as the first permission choice,
followed by Full Access, Approve for me, Ask for approval, and Read Only. The
selected EL2 default is restored directly on startup; opening or resuming a
conversation does not run the bridge verification. Explicit picker activation
still uses `/usr/local/bin/el2-control activate` on the command host.

EL2 Control uses the existing Full Access server profile. The local client remains
an ordinary process and accesses the Linux kernel through the owner-authorized
bridge. The bridge is versioned separately in the private GRID repository under
`extras/linux-el2-owner-control`. It does not establish an independent supervisor
underneath Linux or arbitrary protected-firmware control.

Build from `codex-rs` using Rust 1.95.0 and the existing compiler cache:

```bash
RUSTUP_TOOLCHAIN=1.95.0 \
CARGO_TARGET_DIR=/tmp/owner-el2-default-target \
CARGO_PROFILE_RELEASE_DEBUG=0 \
CARGO_PROFILE_RELEASE_STRIP=symbols \
CARGO_PROFILE_RELEASE_LTO=false \
CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16 \
STABLE_GIT_COMMIT=6ff1e1ca3b30fc8b914720681fcf5fe62e95f856 \
cargo build --locked --offline --release -p codex-cli --bin codex --jobs 4
```

Offline builds require previously downloaded dependencies. Keep the compiler
cache for later changes. Stage a new versioned package from the existing complete
CLI package and replace its executable with the new build; never overwrite an
executable used by running sessions. Preserve companion resources, select the
new package atomically and keep the previous generation for recovery.

The EL2 default is `~/.codex/state/owner-el2-selection/default.json` containing
`{"enabled":true}`. Per-conversation opt-outs take precedence. The personal
guidance is migrated to global AGENTS.md with
`python3 local-customizations/prompts/install.py`, which removes the custom
base-prompt file override and restores the selected model's stock base prompt.
Do not replace a user's complete configuration with a saved machine snapshot.

If a stock update removes the custom permissions menu, restore an already
verified personal package instead of rebuilding it:

```bash
python3 local-customizations/linux/restore-client.py \
  ~/.codex/packages/standalone/releases/local-romil-cli-b06119f483487060-aarch64-unknown-linux-gnu
```

The helper checks the saved executable hash and completed verification suites,
takes the daemon and installer locks, disables daemon automatic updates and the
separate terminal startup update popup, then atomically selects the package.
It preserves unrelated settings, resources, other packages and running chats.
It does not rebuild, launch a watcher, run a model request or probe EL2. Relaunch
the terminal frontend to load the restored executable; the shared backend need
not restart. An explicit stock installer or update command can replace this
selection again. Incorporate upstream updates into the custom build deliberately.

The 6 November installed startup build was verified with one fresh terminal
launch: EL2 appeared without a Full Access flash, without an activation receipt
or a model request. The kernel bridge was verified separately earlier. This
source snapshot also preserves current doctor repairs; saving source does not
claim that every later edit is already in the running executable.
