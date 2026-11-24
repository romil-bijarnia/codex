# Update the custom harness

```sh
codex update
```

The default target is the newest published upstream release, including alpha
releases. This command does not replace the harness with the official binary.
It performs ordinary Git, build and verification operations without model calls.
Compilation can still take time, but it does not need a CLI-model conversation.

The current personal fork is the single authoritative customization source.
The updater computes its complete difference from its recorded upstream baseline
and merges that difference into the newest upstream release with Git's
three-way merge. The recorded release is the merge base even when upstream was
published from a different branch. Using only the history's common ancestor
would wrongly carry changes from the old upstream release into the new one.
The updater does not freeze whole files, use a blanket `ours`/`theirs` policy,
or discard upstream changes just because they touch custom code. It does not mix in
the other launcher's independent custom branch or replace the fork with a curated
subset of features. The intended result is one `codex` command; `codex+1` is
retired only after the unified custom package has passed verification.

Generated Cargo workspace version stamps are normalized separately. Actual custom
dependency pins and dependency-edge changes are still three-way merged; they are
not discarded with the generated metadata. Cargo refreshes only workspace entries
against the new source manifests before the candidate is committed.

Integration happens in a private checkout under
`$CODEX_HOME/state/codex-customizations/updates/`. Overlapping source changes
leave a real staged merge in a retained checkout with `result.json` and
`integration.log`. The command does not guess the intended resolution or
install a partial merge. Resolving an incompatibility may require adapting
custom code; preserving behaviour does not mean preserving every line.

Use the checkout path printed by the updater to inspect the conflicts. Resolve
the files there, stage the resolved paths, and leave the candidate uncommitted.
Run `codex update --continue` to finish integration and run the normal
verification, build and installation steps. If there is more than one candidate
for this source revision, specify its release tag after `--continue`. A resumed
candidate uses the original upstream tag and digest rather than jumping to a
release published while the conflicts were being resolved. To finish preparing
without building or installing, use `codex update --prepare-only --continue`.

Clean candidates must pass updater tests, the native build and the existing
synthetic package checks, including permission/MCP, steering, fork-window and
native voice checks. Their exact tag, source revision and resource hashes remain
in the receipt. The verified source is pushed normally to the personal repository
before installation; a failed push, build or check leaves the selected package
unchanged. A newer installed upstream version is never silently downgraded.

The final selection is atomic. Settings, account data, sessions and active chats
are not rewritten or restarted. Running clients retain their loaded executable;
the existing backend handover waits for observed idle time.

Useful checks:

```sh
codex update --check
codex update --prepare-only
codex custom-status
```

`--check` queries release/provenance metadata but does not attempt integration.
`--prepare-only` exercises the real integration without compilation or installation.
`--channel stable` selects stable releases only, but still refuses a downgrade.
The updater uses committed fork source. It stops at a dirty source checkout
rather than overwriting it or publishing unreviewed files.

Install or restore the command dispatcher without changing the selected binary:

```sh
python3 local-customizations/distribution/manage.py install-launcher
```

The manifest records the current source baseline, not a permanent update ceiling.
The lower-level `manage.py update --revision ...` command remains available for
installing an explicitly pinned, already-maintained source revision; it is not
the default terminal update path.
