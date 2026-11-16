# EL2 Control interface

This optional Linux terminal interface is a small patch on official experimental
Codex 0.171.0-alpha.7. It is not an additional operating-system privilege level
or an unmodified OpenAI build. The old custom launcher, updater and unrelated
native changes are not included.

The permissions picker offers **EL2 Control** for the existing owner-authorized
`/usr/local/bin/el2-control` interface on the execution host. Choosing Full Access
or EL2 Control is the explicit selection on Linux; the extra Full Access warning
dialog is not shown. Managed restrictions and per-action approval policies remain
unchanged. EL2 selection runs the fixed helper's `activate` command through the
active app server and requires verified native execution at exception level 2
before selecting ordinary Full Access for the current chat.

Full Access uses the app's orange colour (`#ff8549` on dark backgrounds and
`#e25507` on light). The verified EL2 footer uses distinct crimson (`#ff5f6d`).
Other status colours, monochrome mode and terminal palette fallbacks are retained.

Codex itself remains an ordinary application. The separately installed driver
performs requested operations inside the existing Linux kernel at EL2. This is
not a resident supervisor underneath Linux or authority over protected Apple
firmware. The menu does not install or rebuild a kernel module.

Activation is explicit and bounded. A failed command, invalid response or
unverified exception level leaves the current permissions unchanged. Completion
from an old request or different chat cannot change the active chat. The
verified indicator is session-local and is cleared when changing chats or
choosing another permission option. Clearing the indicator does not unload the
shared kernel interface.

There are no startup probes, automatic activation, saved-default changes,
background watchers or custom updater. Personal configuration, instructions,
memories, account data and existing desktop integration are retained. The
desktop application's independent runtime is not replaced by this CLI patch.

The existing interface must already be installed for the running Linux kernel.
Read its machine-specific documentation before operating it. The host's normal
device ownership and managed Codex permission requirements still apply.

## Build and verification

Manual Daybreak selection reuses the fork's preset: turning it on selects
`gpt-6-sol` with Daybreak Blue and Extra high reasoning; turning it off selects
`gpt-6.1-sol` with Extra high reasoning. These selections apply to the current
chat, not global model defaults. Account eligibility, model capabilities and
managed restrictions are checked before changing the selection. An automatic
Daybreak disable after an explicit model change preserves that chosen model.
The normal saved launch default remains GPT-6.1-Sol at Extra high with Daybreak
off. This is a preset, not a restriction on subsequent explicit model choices.

Use Rust 1.99.1 and the pinned third-party dependencies. The release tag's lock
file has workspace entries at 0.0.0 while its workspace version is
0.171.0-alpha.7; the version-only lock refresh does not change any external
dependency pin.

The CLI and voice helper must embed the actual patch source commit. Reusing an
official helper with a different commit breaks its identity handshake. Native
voice libraries and unchanged executors may retain their verified official
0.171.0-alpha.7 provenance; package metadata must identify these components accurately.
The small staging helpers in `tools/el2-interface/` do not install, update or
modify a running selection.

Verification includes focused Rust UI/state tests, ordinary permissions-menu
regressions, an actual terminal selection, and the matching voice helper's
initialize/close handshake without opening audio or network transports.
The installed CLI changes only after those checks pass. Every future update
must retain the EL2 menu and the user's permission colours and Daybreak presets.
Do not select stock or use the ordinary stock-stable updater. No automatic
reapplication has been installed; port and verify these changes before selecting
another upstream release. The authoritative build recipe is in
[`tools/el2-interface/README.md`](../tools/el2-interface/README.md).
