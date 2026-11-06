# Local CLI customizations

This branch preserves the installed Codex CLI 0.166.0 customizations on top of
official release commit `6ff1e1ca3b30fc8b914720681fcf5fe62e95f856`.
The upstream `main` branch is left separate; this is not an update to a newer CLI.

Included:
- The permission-status footer source patch, its snapshot, and the release lockfile correction.
- The launcher and immediate idle-chat unloading.
- Native Linux EL2 selection, saved startup choice, input scrolling and Daybreak behavior.
- The native Linux allocator repair and current doctor source fixes.
- The global AGENTS.md migration and repository-preservation rule.
- The terminal `codex visualize` command and its tests.
- The optional setup-reminder helper, including its existing package/resource safeguards.
- The exact-version native build, staging and terminal-colour test helpers.

The old custom `/terminate` integration is retired. Standard `/exit` and `/quit`
are used instead. Immediate release uses the upstream `thread_unload_delay_secs = 0`
setting; it is not a replacement lock implementation. A chat still open in
another client, or still doing work, is not forcibly unlocked.

Personal guidance is loaded from global AGENTS.md rather than a custom base
prompt. The migration removes `model_instructions_file` and preserves the full
previous guidance alongside personal instructions; see [the instructions kit](prompts/README.md).
Codex's built-in model base prompt, skills, tool instructions and service
instructions retain their existing roles. Neither retired Sara prompt override
is installed.

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
