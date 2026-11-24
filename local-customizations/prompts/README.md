# Independent instruction helpers

The custom system-prompt payload, generator, installer and source fragments have
been removed from this fork. No `compact-base.md` or generated Sara model base
is bundled, selected or installed. The default is the selected model’s stock
instructions plus the user’s `AGENTS.md`. Use `AGENTS.md` for personal guidance
instead of replacing the stock model prompt.

AGENTS.md is a separate user-owned instruction layer. This removal does not
modify AGENTS.md, MACHINE.md, memory files, credentials, existing configuration
or running chats. It also does not delete the upstream model-prompt sources.

## Delegation with the stock prompt

Keep the model base unset and add delegation to the user-owned `AGENTS.md`.
Use `task-authorization.md` when assigned work should accept the user's stated
access authorization and require a concrete blocker before declining. This
does not change tool permissions or hosted service policy.

The remaining files serve independent purposes:

- `memory-lookup.md` documents the optional on-demand lookup workflow. It does
  not install a system prompt or change memory settings.
- `repository-preservation.md` is an optional AGENTS.md instruction fragment,
  not a replacement model base.
- `task-authorization.md` is an optional AGENTS.md instruction fragment that
  requires concrete blocker evidence and accepts the user's stated access
  authorization for assigned work.
- `university-project-continuity.md` preserves the optional project-home and
  recovery-record instruction for user-owned AGENTS.md. It includes no academic
  documents or submission records and is not installed automatically.
- `refresh-loaded-chats.py` audits existing loaded-chat context by default and
  only applies user-configured context when explicitly run with `--apply`. It
  uses `model_catalog_json` when set; otherwise it reads the provider cache.
  It contains no bundled custom system prompt. Its generic WebSocket `Client`
  class is also imported by the package updater for read-only idle checks.
- `.gitignore` keeps local AGENTS.md/MACHINE.md and private inspection products
  out of ordinary source commits.

The generic upstream `model_instructions_file` option remains available. The
personal harness does not copy it into generated config, and it drops the
legacy root `instructions = ""` override when generating or updating an unedited
harness config. Neither the shared config nor an independently edited harness
config is rewritten by this migration.

If a previously edited harness still sets a top-level `instructions` override,
remove that assignment from the active config and start a new chat. An empty
value omits the model base; a nonempty value replaces it. Leaving the setting
unset selects the stock prompt for new chats. Existing chats can retain the
base they started with.

## Optional edits to the stock completion prompts

`build-completion-catalog.py` derives a local catalog from the unmodified provider
cache. Sol and Astra skip optional questions, verify the requested workflow, and
retry only when another attempt can add evidence. The edits remove writing and
reporting rules that add noise. Required approvals and unrelated model fields
are preserved.

```sh
python3 local-customizations/prompts/build-completion-catalog.py \
  --source "$HOME/.codex/models_cache.json" \
  --output "$HOME/.codex/prompts/task-completion.models.json"
```

Set top-level `model_catalog_json` in the active config to the generated file
using its absolute path. Verify each model with `codex debug prompt-input` and
start a new chat. A configured catalog is static. To pick up provider changes,
unset that setting, refresh the unmodified provider cache, regenerate, and verify
again. Removing the setting restores stock discovery and prompts for fresh chats.
Do not commit generated catalogs, the provider cache, or personal config.
