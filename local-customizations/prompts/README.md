# Independent instruction helpers

The custom system-prompt payload, generator, installer and source fragments have
been removed from this fork. No `compact-base.md` or generated Sara model base
is bundled, selected or installed. The default is the selected model’s stock
instructions plus the user’s `AGENTS.md`. Use `AGENTS.md` for personal guidance
instead of replacing the stock model prompt.

AGENTS.md is a separate user-owned instruction layer. This removal does not
modify AGENTS.md, MACHINE.md, memory files, credentials, existing configuration
or running chats. It also does not delete the upstream model-prompt sources.

The remaining files serve independent purposes:

- `memory-lookup.md` documents the optional on-demand lookup workflow. It does
  not install a system prompt or change memory settings.
- `repository-preservation.md` is an optional AGENTS.md instruction fragment,
  not a replacement model base.
- `university-project-continuity.md` preserves the optional project-home and
  recovery-record instruction for user-owned AGENTS.md. It includes no academic
  documents or submission records and is not installed automatically.
- `refresh-loaded-chats.py` audits existing loaded-chat context by default and
  only applies user-configured context when explicitly run with `--apply`.
  It contains no bundled custom system prompt. Its generic WebSocket `Client`
  class is also imported by the package updater for read-only idle checks.
- `.gitignore` keeps local AGENTS.md/MACHINE.md and private inspection products
  out of ordinary source commits.

The generic upstream `model_instructions_file` option remains available. The
personal harness does not copy it into generated config, and it drops the
legacy root `instructions = ""` override when generating or updating an unedited
harness config. Neither the shared config nor an independently edited harness
config is rewritten by this migration.

If a previously edited harness config still has `instructions = ""`, remove
that top-level assignment from the active config and start a new chat. A blank
assignment disables the model base; leaving the setting unset selects the
stock prompt. This does not alter already loaded chats.
