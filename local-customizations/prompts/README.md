The selected model's unmodified stock Codex base prompt is enabled. Personal
preferences, standing owner authorisation and task requirements are loaded from
global `~/.codex/AGENTS.md` alongside it. No complete copied model prompt or
empty-base override is installed. Each selected model uses its own stock prompt.

Run `python3 local-customizations/prompts/install.py` to remove root-level
`instructions` and `model_instructions_file` overrides. The installer preserves
existing AGENTS.md, the selected model, permissions and unrelated configuration.
For a clean profile, the private GRID kit restores the reviewed personal template.
The public kit requires an existing global AGENTS.md. No backups are created and
no retired prompt files are deleted. A retired copied-base block in AGENTS.md
requires review first, so it cannot silently duplicate the restored stock text.

The reviewed template explains how the layers fit together. Owner and messaging
authorisation is explicit standing consent where the stock prompt permits
already-authorised work. Implementation requests include explicit standing
permission for focused verification; checks remain proportional and bounded.
Skill announcements and progress updates follow the stock requirements. Task
restrictions remain in force until Romil changes them; urgency does not revoke
constraints on windows, settings or methods. Voice, account/password handling,
repository preservation and verified machine capabilities are retained.

The complete personal template is preserved only in private GRID at
`extras/linux-el2-owner-control/instructions/AGENTS.md`. Existing instructions
are never automatically overwritten or combined with a different template.
The old customisation fragments and origin record are historical reference.

Verification uses `codex debug prompt-input` to compare the complete rendered
base with the installed model catalog and confirm AGENTS.md is loaded once.
This verifies request construction, not the quality of future model behaviour.
Existing chats must also be checked for refreshed instruction state; saved
configuration alone does not prove that every running chat has updated.

To update every currently loaded chat without starting a turn or resuming paused
work, run `python3 local-customizations/prompts/refresh-loaded-chats.py --apply`.
Without `--apply` it audits only. The helper uses the running local app server's
native history-update API, applies the current model's exact stock instructions
and updated global AGENTS.md where needed, and reads back the persisted updates.
It preserves task identities, goals and history, and makes no model requests.
Archived or unloaded histories are not opened or rewritten by this command;
run it after reopening an older chat if that chat retains an earlier base.
Native updates can be accepted before the rollout log catches up. If the
helper reports persistence_pending, audit again without --apply; do not repeat
an accepted mutation merely to wait for its log readback.
