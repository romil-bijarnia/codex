# Main Codex source workflow

## Source of truth

- GitHub fork: https://github.com/romil-bijarnia/codex
- Working and default branch: `main`.
- Upstream: https://github.com/openai/codex.git, branch `main`.
- Local checkout: `$HOME/Work/codex`.
- Native code, launchers, helpers and prompt sources are ordinary Git files.
- `codex-customizations` is retained as historical material, not the main source.

## Update from upstream

Use **Sync fork**, then **Update branch**, on GitHub. Retain personal commits;
resolve conflicts rather than discarding local changes. Pull the result locally:

```sh
cd "$HOME/Work/codex"
git switch main
git pull --ff-only origin main
```

Or perform the equivalent source update locally, starting with intended work
committed and a clean working tree:

```sh
git fetch upstream
git merge upstream/main
# Resolve any conflicts and run the tests relevant to the changes.
git push origin main
```

Edit the real source here, not a separate aggregate patch. Commit and push custom
changes to `origin/main`. A successful merge is not proof that behaviour is
unchanged, so use focused tests appropriate to the actual source change.

## Building and installation are separate

Syncing or pushing source does not rebuild or replace the installed CLI.
The optional `personal-build.yml` workflow is manual only. When a build is
requested, it builds an exact source commit and runs the existing package checks.
Do not enable automatic builds merely to save or publish source changes.

The distribution tools live under `local-customizations/distribution/` and now
target this fork's `main` branch. Their focused tests are:

```sh
python3 -m unittest discover -s local-customizations/distribution/tests -v
```

The current installed launcher/package was deliberately not changed when this
fork became primary. Its legacy `codex update` entrypoint may still refer to the
old package workflow; do not use that command as a substitute for Sync fork.
Switching the installed updater or installing a new native build is a separate
operation. Read the live package receipt before claiming that saved source is
already installed. Existing conversations must not be restarted just to publish
source, and complete package resources must be preserved during any later install.

## Data and recovery

Publish source and reusable project tooling, not credentials, private AGENTS.md,
account sessions, personal databases, caches or generated build products.
Recovery documentation and installation/configuration templates are in GRID.
No backup copies or runtime-profile changes are required to update this repository.
