The operating base is `compact-base.md`, a 902-word stock-derived replacement
starting with the approved operator role: Romil sets the outcome and boundaries;
Sara owns approach, implementation, verification and delivery. The previous
3,320-word Sol base and separate 328-word execution block are no longer stacked.
AGENTS.md retains personal preferences and delivery destinations, without
repeating the operating base or blanket permission grants. In the private kit,
Linux interface procedures live in the sibling MACHINE.md and are read only
for relevant tasks. The installer restores missing machine notes alongside a
fresh personal profile without overwriting existing notes.

Run `python3 local-customizations/prompts/install.py` to copy the compact source to
`~/.codex/prompts/sara-system.md` and select it using `model_instructions_file`.
The installer removes the retired managed execution block from developer_instructions
while preserving unrelated developer text, the model, permissions, existing
AGENTS.md and other configuration. It is idempotent and creates no backups.
The private GRID kit can restore AGENTS.md and MACHINE.md for a clean profile;
existing files are never overwritten. The source catalog hash and instruction coverage are recorded
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

The intent-retention paragraph keeps corrections and rejected interpretations
explicit, including access, billing, delivery and method requirements.
Parent-managed multi-agent v2 subagents are skipped by direct chat refreshes;
their lifecycle and input remain under their parent agent.

Criticism, frustration and failure reports do not cancel an authorised project.
The operating base retains the goal, requires corrective work after mistakes,
and distinguishes a deployment/reboot restriction from a stop on diagnosis or
source work. These general rules are no longer repeated in AGENTS.md. Genuine
stop/wait instructions remain binding.

The 9 November AGENTS.md cleanup removes duplicated workflow paragraphs and
blanket owner/account/contact/root/EL2 permission grants. Personal writing and
learning preferences, the no-backup rule and repository delivery destinations
remain. MACHINE.md contains technical procedures and limits, not blanket
authorisation. The configured Sara base and model settings are unchanged.
Existing chats retain earlier text in history; new instruction updates replace
the active AGENTS.md guidance, while a fresh chat starts with less prior text.

The personal AGENTS.md template is 239 words (previously 2,618). The private
GRID kit and installed profile match. The public fork shares the installer
changes; personal instruction text remains private, as its .gitignore requires.
