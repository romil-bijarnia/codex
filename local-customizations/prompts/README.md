The complete personal base prompt is generated locally as `sara-system.md`. It
preserves the installed model's base prompt and appends the standing
repository-preservation rule. `origin.json` identifies the original GPT-6.1-Sol
text used on this installation by hash. `repository-preservation.md` is the exact
addition. The complete personal prompt is backed up in private GRID.

Run `python3 local-customizations/prompts/install.py` to copy the prompt into
`~/.codex/prompts/sara-system.md` and select it through `model_instructions_file`.
When the complete prompt is absent, the installer reads the selected model's
base instructions from the installed model catalog and appends the rule.
The installer preserves other settings and backs up the previous files. This
changes the local model base prompt, rather than relying on an AGENTS.md entry.
Service instructions, permissions, tools and other instruction layers retain
their existing roles. New conversations load the selected prompt file.

This is a deliberate full base-prompt override. When rebasing onto a different
model prompt, retain the preservation rule and update the origin record.
