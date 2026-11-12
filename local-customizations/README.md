# Local CLI customizations

The primary source is `romil-bijarnia/codex`, branch `main`, based on the official
stable `rust-v0.167.2` release. The native customizations and helpers are ordinary
tracked source files. The local checkout is `$HOME/Work/codex`.

Use [WORKFLOW.md](WORKFLOW.md). Stable upgrades must use release tags rather than
unreleased upstream main. The build in `distribution/` verifies the exact tag,
commit, source workspace version and official resource checksum. The native
version is `0.167.2`; complete package provenance includes the custom source SHA.
The original 0.166.0 `SNAPSHOT.json` and macOS recipes below are historical,
not current installation instructions or evidence about the selected Linux build.

Included:
- The permission-status footer source patch, its snapshot, and the release lockfile correction.
- The launcher and immediate idle-chat unloading.
- Native Linux EL2 selection without automatic review, saved startup choice, input scrolling and Daybreak behavior.
- Cyber model selection preserves the current permission profile and reviewer.
- Structured blue question tools are disabled; necessary questions use ordinary chat text.
- The native Linux allocator repair and current doctor source fixes.
- The global AGENTS.md migration and repository-preservation rule.
- The terminal `codex visualize` command and its tests.
- The optional setup-reminder helper, including its existing package/resource safeguards.
- The exact-version native build, staging and terminal-colour test helpers.

The old custom `/terminate` integration is retired. Standard `/exit` and `/quit`
are used instead. Immediate release uses the upstream `thread_unload_delay_secs = 0`
setting; it is not a replacement lock implementation. A chat still open in
another client, or still doing work, is not forcibly unlocked.

The prompt kit installs a compact stock-derived model base and retains global
AGENTS.md for personal context. See [the instructions kit](prompts/README.md) for
source coverage, installation and stock-mode rollback.
A compact stock-derived base contains the approved operator role and the general
working rules. Global AGENTS.md supplies personal guidance and standing
authorisation. The installer preserves model and permission settings and supports
--stock rollback; see the prompt kit for counts and source coverage. Skills,
tool instructions and service instructions retain their normal roles.

The original macOS launcher recipe below remains available. Current Linux
behavior and recovery are documented in [the Linux notes](linux/README.md).

## Restore the launcher

This installation layout targets the existing Apple-silicon macOS/Homebrew CLI.
It does not upload or restore account credentials, personal instructions, chats,
databases, browser profiles, runtime manifests or generated binaries.

```sh
python3 local-customizations/install.py
```

The installer backs up replaced runtime files, retains unrelated configuration,
disables the retired lifecycle extension, and links `~/.local/bin/codex` to the
launcher. It leaves running sessions alone. Relaunch existing CLI sessions after
installing. The shared backend must also load the zero-delay setting; restart it
only when it has no active turns, goals or background terminals.

## Native footer build

The native footer changes are already applied to this fork's `codex-rs` tree.
The helpers require the matching official npm package and the isolated Rust
toolchain configured in `permission-colors/build.py`. The voice release identity
stays pinned to the matching upstream release rather than the fork commit.

```sh
python3 local-customizations/permission-colors/build.py
python3 local-customizations/permission-colors/stage.py
python3 local-customizations/install.py --with-staged-footer
```

The installer never substitutes an old native package for a newer upstream
version. Footer package selection remains guarded by the installed original
executable's fingerprint. The upstream executable is not overwritten.

## Checks

```sh
node --check local-customizations/security-setup-reminder/codex-launcher.cjs
python3 -m unittest discover -s local-customizations/visualize-cli -p 'test_*.py' -v
python3 -m unittest discover -s local-customizations/tests -p 'test_*.py' -v
```

Lifecycle checks were run offline with disposable homes against the stock
0.166.0 server. They exercise real `/exit` and `/quit`, immediate ownership
release, history preservation, and reopening from another server. Normal
resume bookkeeping may append a `thread_settings_applied` event; existing
conversation records must remain byte-for-byte intact.
