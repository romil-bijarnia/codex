# Source fork workflow

## Source of truth

- Repository: https://github.com/romil-bijarnia/codex-customizations
- Active source branch: `personal`.
- Upstream remote: https://github.com/openai/codex.git, branch `main`.
- Local checkout on the current Linux machine: `$HOME/Work/codex`.
- Personal code and helpers are ordinary tracked files, not an applied patch or overlay.
- The old package-only `main` branch and its releases remain historical. Do not
  edit that branch expecting changes to enter the source fork automatically.

The fork retains upstream history and the earlier personal commits. The one-time
migration imported the exact recorded changes from the package repository,
then merged upstream using Git. Future changes should be made directly in source.

## Incorporate an upstream update

Start with a clean working tree; commit intended work rather than discarding it.

```sh
cd "$HOME/Work/codex"
git switch personal
git pull --ff-only origin personal
git fetch upstream
git merge upstream/main
```

If Git reports conflicts, resolve them deliberately and run the relevant tests
before completing and publishing the merge. Do not resolve every conflict by
choosing all of either side: that would discard custom features or upstream fixes.
There is no scheduled upstream merge and no agent required for a clean Git merge.
A clean merge alone does not establish behavioural compatibility.

Run the focused updater tests before publishing build/installer changes:

```sh
python3 -m unittest discover -s local-customizations/distribution/tests -v
```

Run the affected native project tests when changing Rust behaviour. The package
build also runs terminal fixtures for defaults, Daybreak, picker display,
integrations, regression cases and steering. The fixtures use local responses,
not paid model inference, and do not establish kernel/EL2 activation.

```sh
git push origin personal
```

GitHub Actions builds that exact custom source commit. It does not fetch a newer
upstream revision behind the branch or apply a stored patch. Build output stays
in release assets, not Git source history. Imported upstream workflows are
kept disabled in this repository; only the personal build workflow is enabled.
When bringing in new upstream workflow files, review their triggers before
allowing them to run against this personal repository.

## Select a tested build

After the source build succeeds:

```sh
codex update
codex custom-status
```

The updater follows `origin/personal`, not bare `openai/main`. It verifies package
identity, checksums and required local checks before changing the selected package.
Existing terminal processes keep their executable. The shared backend rolls over
only after observed idle time; running conversations are not forcibly restarted.
Source revision, incorporated upstream revision and the selected package can be
checked independently with `codex custom-status`.

The build recipe uses an isolated source checkout for any version stamping. Do
not copy a single executable over the working package: voice and other bundled
resources require the complete package layout. Do not run the old historical
macOS installer for the current Linux workflow.

## Files that stay local

Credentials, profiles, chat history, private AGENTS.md, personal databases,
build caches and generated packages do not belong in this source repository.
The workflow does not replace those files or create backup copies of them.

## Migration evidence

The source merge through `27fcd7cbedd7efd63fd704bd01a0cfe1d7b85a26` was compared
against the package repository at `97cdd01a3ef38ae2381ba2387be1d021a7300c13`
applied to that same upstream revision. Their complete tracked source trees
matched before distribution tooling and documentation were migrated. This is
source-preservation evidence, not a claim of a completed native build or live
hardware verification. Package manifests record build-specific checks.
