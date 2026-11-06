Personal guidance now lives in global `~/.codex/AGENTS.md`. The custom
`model_instructions_file` override has been retired, so fresh conversations use
Codex's built-in model base prompt plus the normal user-instruction layers.

Run `python3 local-customizations/prompts/install.py` to migrate the previous
`~/.codex/prompts/sara-system.md` into AGENTS.md, remove its root-level config
selector, and delete the retired live file. The migration retains the full old
prompt and existing personal/machine instructions. It does not simplify them.
The original personal instructions follow the migrated block, so the user's
more specific preferences remain explicit.

The installer preserves unrelated settings and instructions, is idempotent,
and creates no backups. It refuses to remove a different override or exceed
the default 32 KiB instruction limit. Start a new conversation after migration;
already-running conversations keep their loaded instructions. No rebuild or
restart of active work is needed.

The complete merged AGENTS.md is preserved only in private GRID under
`extras/linux-el2-owner-control/instructions/AGENTS.md`. Its installer can also
restore a clean profile from that template, preserving existing instructions.
The public Codex kit ships the migration tool, not the personal template; it
requires existing instructions or the legacy live prompt. The old customization
fragments and origin record are historical reference, not active generators.
