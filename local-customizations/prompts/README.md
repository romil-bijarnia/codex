# Independent instruction helpers

The custom system-prompt payload, generator, installer and source fragments have
been removed from this fork. No `compact-base.md` or generated Sara model base
is bundled, selected or installed. The CLI retains its normal upstream model
instructions unless the user explicitly configures an override.

AGENTS.md is a separate user-owned instruction layer. This removal does not
modify AGENTS.md, MACHINE.md, memory files, credentials, existing configuration
or running chats. It also does not delete the upstream model-prompt sources.

The remaining files serve independent purposes:

- `memory-lookup.md` documents the optional on-demand lookup workflow. It does
  not install a system prompt or change memory settings.
- `repository-preservation.md` is an optional AGENTS.md instruction fragment,
  not a replacement model base.
- `refresh-loaded-chats.py` audits existing loaded-chat context by default and
  only applies user-configured context when explicitly run with `--apply`.
  It contains no bundled custom system prompt. Its generic WebSocket `Client`
  class is also imported by the package updater for read-only idle checks.
- `.gitignore` keeps local AGENTS.md/MACHINE.md and private inspection products
  out of ordinary source commits.

The generic upstream `model_instructions_file` configuration option remains
available to users. Existing explicit overrides are not stripped or rewritten
by the launcher installer.
