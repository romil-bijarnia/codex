The complete personal base prompt is generated locally as `sara-system.md` from
the installed model's base prompt. It replaces the writing-style section with
`writing-style.md`, applies the investigate-before-asking rule in
`investigation.md`, and appends `repository-preservation.md`. The writing rules
require honest, direct answers and routine handling of password-related tasks
without unsolicited warnings or repeated reminders. Other prompt sections are
preserved. `origin.json` identifies the original GPT-6.1-Sol text by hash. The
complete personal prompt is preserved in private GRID.

Run `python3 local-customizations/prompts/install.py` to copy the prompt into
`~/.codex/prompts/sara-system.md` and select it through `model_instructions_file`.
When the complete prompt is absent, the installer reads the selected model's
base instructions from the installed model catalog. `customize.py` applies the
same communication rules to generated prompts and supplied complete prompts.
It stops if expected section markers have changed so an upstream change cannot
silently remove other instructions.
The installer preserves other settings and backs up the previous files. This
changes the local model base prompt, rather than relying on an AGENTS.md entry.
Service instructions, permissions, tools and other instruction layers retain
their existing roles. New conversations load the selected prompt file.

This is a deliberate full base-prompt override. It does not automatically track
upstream prompt changes. When rebasing onto a different model prompt, review
the communication rules, retain the preservation rule and update the origin
record. Start a new conversation to load prompt changes; no CLI rebuild is
required.
