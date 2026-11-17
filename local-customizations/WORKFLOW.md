# Codex source and verified package workflow

## Source of truth

- Repository: https://github.com/romil-bijarnia/codex
- Working/default branch: `main`.
- Exact upstream release, commit and resource checksum: `distribution/manifest.json`.
- Local checkout: `$HOME/Work/codex`.
- Native customizations, launch helpers and prompt sources are ordinary Git files.

The current personal fork is the sole customization source. Do not combine an
independently installed alpha's custom branch with it. Do not replace the fork
with stock binaries or choose a subset of its custom features without instruction.
GitHub's fork banner compares against development main, not the recorded release.

## Routine update

```sh
codex update
codex custom-status
```

See [UPDATE.md](UPDATE.md). The updater starts from the newest published release,
including alpha releases, and applies the fork's difference from its recorded
base. It does not freeze entire files or use blanket ours/theirs resolution.
Conflicts remain in a private checkout; the working installation is unchanged.
A compatibility port preserves custom behaviour while retaining unrelated new
upstream APIs and features, then advances the exact source/resource pin together.

Do not use GitHub Sync fork against development main as a substitute for this
release-based process, force-push over custom work, or relabel an old source tree.
The builder checks the release channel, exact tag commit, workspace version and
resource checksum. Alpha and stable pins are distinct and are never relabelled.

## Verification and selection

```sh
python3 -m unittest discover -s local-customizations/distribution/tests -v
python3 -m unittest discover -s local-customizations/tests -v
```

The committed recipe builds a complete native package. It exercises defaults,
Daybreak, picker display, integrations, MCP handling, steering and fork windows
with disposable homes and local synthetic responses. No real model requests are
required. These checks are not a claim that every Rust test target was run.

Native voice startup is mandatory during build and before installation. The CLI
and voice helper use the same committed source and STABLE_GIT_COMMIT. Their
strict identity handshake is not weakened. Matching official native libraries,
runtime.json and sources.json retain their hashes; custom helper provenance is
recorded separately. `codex debug voice` initializes and shuts down the native
runtime without opening audio streams or making a network session.

The verified source is published with a normal fast-forward push before atomic
package selection. The primary launcher retains the fork's existing launch
behaviour and saved settings. Only then is the recognised old codex+1 command
retired. Active clients are not restarted; backend handover waits for observed
idle time. Kernel/EL2 activation is not established merely by these package tests.

The optional personal-build.yml GitHub workflow can build the same exact source;
local updates do not need an agent to supervise compilation or runner queues.

## Data and recovery

Credentials, personal instructions, sessions, databases, caches and generated
products stay out of commits. Reusable recovery instructions are in GRID under
tools/codex-update. Live receipts identify the installed source and upstream base;
never rewrite an old receipt to make it appear current.
