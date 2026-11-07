The operating base is `compact-base.md`, a 733-word stock-derived replacement
starting with the approved operator role: Romil sets the outcome and boundaries;
Sara owns approach, implementation, verification and delivery. The previous
3,320-word Sol base and separate 328-word execution block are no longer stacked.
AGENTS.md retains personal preferences, standing authorisations and machine facts.

Run `python3 local-customizations/prompts/install.py` to copy the compact source to
`~/.codex/prompts/sara-system.md` and select it using `model_instructions_file`.
The installer removes the retired managed execution block from developer_instructions
while preserving unrelated developer text, the model, permissions, existing
AGENTS.md and other configuration. It is idempotent and creates no backups.
The private kit can restore AGENTS.md for a clean profile; existing files are
never overwritten. The source catalog hash and instruction coverage are recorded
in `origin.json`. This is a maintained replacement, so future stock changes need
review rather than being inherited automatically. It applies as the configured
base when switching models; tool schemas and model capabilities remain runtime-owned.

To restore the model's original stock base, run the same installer with `--stock`.
AGENTS.md remains active, unrelated developer instructions are preserved, and
the installed compact file is left unselected. No files are deleted for rollback.

Run `python3 local-customizations/prompts/refresh-loaded-chats.py --apply` to refresh currently loaded
chats without starting work turns. Without --apply it audits. The helper reads
the configured base (compact file, inline override or the model catalog), updates
it independently of AGENTS.md, and deactivates the retired separate execution block.
Task identities, goals, project guidance and history are preserved. If saved-log
readback lags an accepted update, audit again without --apply rather than resending.
Archived and unloaded histories are not opened by this helper.

Verification checks the exact compact base once, AGENTS.md once, absence of the
old execution block, installer preservation and rollback, and loaded-chat receipts.
These are construction and delivery checks, not proof of future model behaviour
or a measured latency improvement. Word counts describe prompt text, not API tokens.
