The operating base is `compact-base.md`, a maintained replacement.
Romil sets outcomes and boundaries; Sara owns routine decisions, execution,
verification and delivery. AGENTS.md retains personal preferences, a standing
proactive-delegation instruction and repository destinations. The complete
personal template and machine procedures live in private GRID;
personal files are not tracked in the public customization repository.

The positive working standard is outcome ownership: choose the approach,
resolve problems within scope, and carry authorised work across follow-up
questions until completion or an explicit pause, cancellation or superseding
request. Match verification to the practical result; component tests guide
progress rather than replace that result. Bring genuine user decisions with
evidence and a recommendation.

Run `python3 local-customizations/prompts/install.py` from an assembled Codex
source tree, or `python3 instructions/install.py` from the GRID recovery package,
to select and install `~/.codex/prompts/sara-system.md`. The installer preserves
unrelated settings, model selection, permissions and existing AGENTS.md. It
removes only the retired managed execution block, is idempotent and creates no
backups. Missing AGENTS.md and MACHINE.md can be restored from private templates;
existing files are not overwritten.

When upgrading an existing profile, reconcile the peer-communication and
proactive-delegation paragraphs from the private template without replacing
unrelated instructions, then refresh loaded chats. The standing AGENTS.md rule
explicitly requests useful independent subagent work, satisfying the runtime's
user-or-AGENTS delegation condition without a blanket runtime override.
Small and tightly coupled tasks stay in the parent. Agents receive bounded
ownership and acceptance criteria; the parent verifies the integrated result.

Ordinary clarification updates understanding without apology routines. Actual
errors get a brief factual correction followed by repair. Discussion remains
discussion until action is requested; existing task restrictions, stop requests,
authorisation boundaries and data preservation still apply. The design reduces
repeated narration and redundant work rather than suppressing useful progress
or the reporting of actual failures.

Run `python3 instructions/refresh-loaded-chats.py --apply` from the recovery
package (or the equivalent `local-customizations/prompts/` path) to refresh
loaded chats without starting turns. Without `--apply`, it audits. The helper
updates the configured base and AGENTS.md independently and removes retired
execution guidance. It preserves task identities, goals and project constraints.
If saved-history readback lags an accepted update, audit again rather than
repeating the mutation. Archived/unloaded histories are not rewritten, and
parent-managed subagents are skipped rather than receiving direct updates.

Run the installer with `--stock` to restore the selected model's stock base.
AGENTS.md and unrelated settings remain; no files are deleted. The maintained
replacement does not automatically inherit future stock-prompt changes:
review those deliberately. Tool schemas, permissions and capabilities remain
runtime-owned. Current content hashes and coverage are recorded in `origin.json`.

Verification covers installer preservation and idempotency, a fresh rendered
prompt, loaded-chat receipts and source equality. These establish installation,
not a behavioural guarantee or measured reduction in model usage. Word counts
are text measurements, not token accounting. Historical verification files
refer to their recorded prompt hashes; `autonomy-verification.json` records
the earlier autonomy revision's checks.

The private AGENTS.md template also routes disk discovery through the separately
installed GRID disk-index extra when its coverage and freshness are adequate.
The index stores local source data, not authoritative instructions; execution
and verification still use original files. No index database or indexed content
is part of this source repository.
