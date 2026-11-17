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

No custom system prompt is bundled or installed by this fork. The CLI uses
its normal upstream model instructions unless the user explicitly configures
an override. Global AGENTS.md remains separate personal context. Existing
instructions, account data and memory settings are not rewritten by this
source cleanup. The remaining [instruction helpers](prompts/README.md) do not
provide or select a replacement model base.

The optional `distribution/bin/codex+1` launcher runs a separately pinned fork
package without replacing the primary `codex` command. It shares login,
sessions, AGENTS.md, plugins and databases, but filters the primary launcher's
`model_instructions_file` and selects the chosen model's stock instructions.
This keeps `codex+1` independent from a personal prompt configured for the
primary CLI.

On Linux that launcher marks the native process as the owner harness. Its
permission picker exposes only **EL2 Control**, which applies Full Access with
`approval_policy = "never"` and `approvals_reviewer = "user"` before enabling
the additional kernel and CPU interfaces. The harness does not install the
local Guardian approval reviewer. Its MCP elicitation handler accepts empty
approval forms directly without reviewer inference, and it does not locally
lock the composer after a hosted precaution stop. Hosted-service decisions are
still reported. Conversation writer locks remain because they protect saved
history from concurrent writes; they are not permission or review controls.

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
