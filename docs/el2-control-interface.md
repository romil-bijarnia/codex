# EL2 Control interface

This optional Linux terminal interface is a small patch on official Codex
0.169.0. It is not an additional operating-system privilege level or an
unmodified OpenAI build. The old custom launcher, updater and unrelated native
changes are not included.

The permissions picker offers **EL2 Control** for the existing owner-authorized
`/usr/local/bin/el2-control` interface on the execution host. Selection respects
the same availability restrictions and confirmation as ordinary Full Access.
After confirmation, the active app server runs that fixed helper's `activate`
command. The response must verify native execution and exception level 2 before
the interface selects ordinary Full Access for the current chat.

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

Use Rust 1.95.0 and the pinned third-party dependencies. The release tag's lock
file has workspace entries at 0.0.0 while its workspace version is 0.169.0; the
version-only lock refresh does not change any external dependency pin.

The CLI and voice helper must embed the actual patch source commit. Reusing an
official helper with a different commit breaks its identity handshake. Native
voice libraries and unchanged executors may retain their verified official
0.169.0 provenance; package metadata must identify these components accurately.
The small staging helpers in `tools/el2-interface/` do not install, update or
modify a running selection.

Verification includes focused Rust UI/state tests, ordinary permissions-menu
regressions, an actual terminal selection, and the matching voice helper's
initialize/close handshake without opening audio or network transports.
The installed CLI changes only after those checks pass. A normal future
official update can replace this interface patch; no automatic reapplication
has been installed.
