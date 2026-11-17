# Update the custom harness

```sh
codex update
```

The default target is the newest published upstream release, including alpha
releases. This command does not replace the harness with the official binary.
It performs ordinary Git, build and verification operations without model calls.
Compilation can still take time, but it does not need a CLI-model conversation.

The updater computes the custom difference from its recorded upstream baseline
and applies that difference to the new release with a three-way merge. It does
not freeze whole files, use a blanket `ours`/`theirs` policy, or discard upstream
changes just because they touch a file containing custom code. If the selected
installation contains custom code from an independent source branch, that delta
is also included rather than silently lost.

Integration happens in a private checkout under
`$CODEX_HOME/state/codex-customizations/updates/`. Overlapping source changes
produce a retained checkout, `result.json` and `integration.log`. The command
does not guess the intended resolution or install a partial merge. Resolving a
real incompatibility may require adapting the custom code; preserving behaviour
is not a promise that every original line remains unchanged.

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

Install or restore the command dispatcher without changing the selected binary:

```sh
python3 local-customizations/distribution/manage.py install-launcher
```

The manifest records the current source baseline, not a permanent update ceiling.
The lower-level `manage.py update --revision ...` command remains available for
installing an explicitly pinned, already-maintained source revision; it is not
the default terminal update path.
