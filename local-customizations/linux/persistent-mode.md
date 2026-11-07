The terminal offers **Persistent** alongside Max and Ultra under `/model`, the
chosen model, and **More reasoning…**. Enter applies and saves the selection as
the default; `s` applies it only to the current conversation. Selecting a normal
reasoning level turns persistence off. This change exposes the option; it does
not enable it or replace the owner's existing default.

Persistence retains the native session setting and developer instructions. It
is an execution mode, not an inference capability that every model catalogue
advertises. Models without a native Persistent level use their strongest
advertised inference effort, preferring Max, while the session remains
persistent. This avoids sending `reasoning.effort="disabled"` to models such as
GPT-6.1-Sol, which reject it. Models explicitly advertising the existing native
Persistent alias keep their original wire behaviour.

The inline session-only command is `/model --reasoning=persistent`. Persistent
remains an explicit menu choice rather than an accidental reasoning-shortcut
step. It does not enable proactive subagent use or change permissions.

Both the terminal frontend and its app-server backend need the updated build.
An already running shared daemon keeps its loaded code; selecting a new CLI
package does not hot-update it. Do not interrupt unrelated running work just
to reload it. `codex --no-daemon` uses the updated embedded backend immediately;
the shared daemon can use the selected package after a coordinated restart.

The disposable PTY acceptance check is preserved in GRID at
`extras/linux-el2-owner-control/workflow/persistent-picker-qa.py`. It checks a
catalogue lacking Persistent, explicit menu selection, session-only versus saved
defaults, the actual inference request and persistent instructions, startup
restoration, and disabling the mode. It uses a localhost-only model fixture and
does not contact the live account or change personal settings.
