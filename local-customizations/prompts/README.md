The effective operating prompt is the selected model's unmodified stock base
plus the small `execution-rules.md` block at the same developer-instruction
priority. Global `~/.codex/AGENTS.md` retains Romil's voice, standing authorisations
and machine context. This strengthens the working rules without pinning a full
copied stock prompt or disabling the base. Model changes retain their own stock
instructions, and stock updates continue to apply.

Run `python3 local-customizations/prompts/install.py` to install this setup. The
installer removes root-level base replacements and selects the personal block
through `developer_instructions`. It replaces only the managed execution block,
preserving unrelated developer instructions, the selected model, permission
settings, existing AGENTS.md and other configuration. The private GRID template
can populate a clean profile; existing personal files are never overwritten.
No backups are created. Root keys written by this installer are single-line TOML
strings; if it cannot safely update another format, it stops before any writes.

The execution block keeps the actual requested outcome and all task constraints
in view, requires continued useful work instead of stopping at preparation,
bounds verification, distinguishes compatibility from measured behaviour, and
requires concrete truthful reporting. Explicit standing permissions remain in
AGENTS.md. The stock base and user preferences retain their existing roles;
this is not a promise that wording alone guarantees correct model behaviour.

The complete personal template is preserved only in private GRID at
`extras/linux-el2-owner-control/instructions/AGENTS.md`. The old complete-prompt
fragments and origin record are historical reference, not the active generator.

Use `python3 local-customizations/prompts/refresh-loaded-chats.py --apply` to update currently loaded
chats through the running local app server. Without `--apply` it audits only.
It preserves task identities, goals, applicable project instructions and history,
starts no turns, and makes no model requests. It applies the stock base, personal
execution block and global AGENTS.md independently, so there is no duplicated
complete model prompt. Archived or unloaded histories are not opened or rewritten.
If persistence_pending is reported, audit again without --apply; do not repeat
an accepted update merely to wait for its saved-log readback.

Fresh-request verification checks the stock base, execution block and AGENTS.md
once each. It verifies construction and loading, not future model behaviour.
To remove the personal block, remove its marked section from developer_instructions
and refresh loaded chats; leave the stock base and AGENTS.md in place.
