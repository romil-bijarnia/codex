Personal guidance lives in global `~/.codex/AGENTS.md`. The stock model base
prompt is deliberately disabled with the root-level `instructions = ""` setting.
There is no separate custom system-prompt file to maintain. Skills, tools,
permissions and service-provided instruction layers retain their existing roles.

Run `python3 local-customizations/prompts/install.py` to install this setup. It
sets the empty base instructions, removes the old `model_instructions_file`
selector, and deletes the retired live `~/.codex/prompts/sara-system.md` file.
Any retired Sara prompt or nonempty inline instructions are first preserved in
AGENTS.md alongside existing instructions when migrating an older setup. The
current personal template is a self-contained set of owner instructions. It
records broad standing delegation to administer Romil's systems using the access
actually available, including root, desktop/application control and supported
EL2 operations. Romil states the outcome; Sara owns implementation, verification
and delivery, with concrete reporting of what changed, what was checked, the
observed result and any remaining uncertainty. It retains the communication
preferences, task steering, messaging authorisation, account/password handling,
repository preservation and machine-specific capabilities and limitations.
Privileged work does not require repeated consent merely because it is powerful
or low-level; a requested destructive result authorises its necessary steps.
The instructions distinguish authorisation from actual access and external
controls. No new OS privilege or service capability is created by editing them.
Verification is bounded: run the checks needed for the actual change, then
finish delivery; do not widen or repeat testing without a specific unresolved
concern. The installer itself does not rewrite existing instructions.

The installer preserves unrelated settings and instructions, is idempotent,
and creates no backups. It refuses to remove a different file override or exceed
the default 32 KiB instruction limit. Verify saved changes with a freshly
constructed request; do not infer an existing conversation's loaded state from
its saved configuration alone. The client can deliver instruction updates to an
existing conversation. No CLI rebuild or restart of active work is needed.

The complete personal AGENTS.md is preserved only in private GRID under
`extras/linux-el2-owner-control/instructions/AGENTS.md`. Its installer can also
restore a clean profile from that template, preserving existing instructions.
The public Codex kit ships the migration tool, not the personal template; it
requires existing instructions or the legacy live prompt. The old customization
fragments and origin record are historical reference, not active generators.

Verification uses `codex debug prompt-input` and checks that the stock base is
absent and AGENTS.md is loaded once in the user instruction layer. This checks
request construction without making a real model request. To restore the stock
base later, remove the root-level `instructions = ""` setting and start a new
conversation; do not remove AGENTS.md.
