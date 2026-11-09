# Stable Codex source workflow

## Source of truth

- Repository: https://github.com/romil-bijarnia/codex
- Only working/default branch: `main`.
- Current official baseline: `rust-v0.167.0`.
- Baseline commit: `086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a`.
- Local checkout: `$HOME/Work/codex`.
- Native customizations, launchers, helpers and prompt sources are ordinary Git files.

The repository keeps prior development history, but its current source tree is
ported to the stable release. The explicit pin in
`local-customizations/distribution/manifest.json` identifies the baseline; a
merge-base against upstream main does not identify the version being built.

## Source changes and stable upgrades

Save custom changes normally on `main` and push them to `origin/main`.
Do not use GitHub's **Sync fork / Update branch** against upstream main for a
stable-version upgrade: main includes unreleased development changes.

For an upstream upgrade, select the next non-prerelease official release, port
the retained custom changes onto that release's actual source, and update the
pinned tag, commit and resource checksum together. Preserve the single main
branch and existing history; do not discard custom behaviour or force-push
merely to change the displayed version. Review and test the resulting source
before building. Changing only a version string is not a stable-source upgrade.

The builder checks the official release is neither draft nor prerelease, checks
the fetched tag's exact commit, requires the matching workspace version and
verifies the official package checksum. It does not fetch development-main
code or choose a newer alpha resource package.

## Build, test and install

The optional `personal-build.yml` workflow is manual. The exact source commit
includes its recipe and checks. The build compiles the CLI and its companion
executables from the same stable-based tree and retains the matching complete
resource package. It does not copy a single executable over a working install.

Focused updater tests:

```sh
python3 -m unittest discover -s local-customizations/distribution/tests -v
```

Package checks exercise defaults, Daybreak, picker display, helpers, literal
input/API regressions and steering with disposable profiles and local responses.
Those checks do not establish live kernel/EL2 activation or every Rust test target.

Voice has a mandatory native startup gate at build time and before installation.
The CLI and voice helper are built from the same committed source with the same
`STABLE_GIT_COMMIT`; the strict same-build handshake is not weakened or bypassed.
The helper links the hash-verified pinned official audio libraries through
build-only aliases and supported system-deps link inputs, not the runner's
GStreamer version. Native library bytes, `runtime.json` and `sources.json` remain
unchanged; the assembled manifest records the custom helper and its actual hash
separately from native library provenance. The complete voice inventory is checked
against its hashes and stable source before compilation. The real
`codex debug voice` client must then complete the helper handshake, initialize
the native audio runtime and shut down cleanly. It opens no audio streams and
starts no network session. A picker-only check cannot satisfy this gate.
Use `codex debug voice --list-devices` to additionally enumerate local devices
without recording or playing audio.

After the exact stable-based build passes, the source updater installs it with
checksum verification and the required local renderer check:

```sh
codex update
codex custom-status
```

Normal launches execute the selected complete package. Running terminal clients
retain their loaded executable; the shared backend only rolls over after observed
idle time. Do not terminate active chats merely to change the footer version.
The CLI reports `0.167.0`; package metadata uses `0.167.0+romil.<source SHA>` so
custom provenance is retained without a development-version label.

## Data and recovery

Credentials, private AGENTS.md, sessions, personal databases, caches and generated
products stay out of source commits. Recovery documentation and reusable
installation/configuration material are in GRID. Live package receipts determine
what is installed; never rewrite old receipts to make them look like a new build.
