# Linux CLI restoration and regressions

The fork preserves `features.steer_only` and the installed steering paths:
follow-ups steer ordinary work and streaming plans; reviews and explicit
compaction are interrupted before submitting the pending instruction. Blocked
input and recovered queued drafts remain editable instead of being sent later.

Collapsed literal pastes retain their complete text and do not execute as
shell commands. Blocked submissions retain the paste map for a literal retry.
The focused tests are `chatwidget::tests::steer_only` in `codex-tui`.

The API helper is versioned in `local-customizations/api-cli`. Root configuration
overrides before `api` are forwarded to it. It accepts leading overrides before
its own `login`, `status` and `logout` commands, preserving root-before-child
precedence. The visualization helper is versioned in
`local-customizations/visualize-cli` and uses `xdg-open` on Linux while preserving
the Safari path on macOS.

Install both helpers and build from this checkout using the matching GRID kit's
`extras/linux-el2-owner-control/CLI_REPAIR.md` instructions. That kit preserves
the builder, installer and localhost runtime checks. Installation does not
change the stock prompt, personal instructions, model, reasoning, context
thresholds or running conversations. New terminal launches load the selected
package; the compatible shared backend can remain running.

The core crate's recursion limit is 256 to permit Rust 1.95 to compile the nested
async tool-dispatch layout encountered by the focused terminal tests.
