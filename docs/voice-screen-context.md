# Voice screen context

The CLI can attach images of the current desktop to delegated voice requests.
This includes visible unsaved editor changes and the pointer, rather than relying
on the contents of saved files. The audio frontend delegates visual reasoning to
the selected vision-capable Codex model and speaks its findings. It does not
receive a continuous video stream or accept image input directly.

## Controls

- `/voice screen on` enables screen context and saves the machine-local preference.
- `/voice screen off` immediately stops capture and clears the active image. A
  configuration-save failure does not prevent stopping the current call.
- `/voice screen status` reports whether sharing is enabled.
- `/voice settings` includes a screen-context toggle.

Screen context is off by default. The saved user setting is:

```toml
[audio]
screen_context = true
```

Like local microphone and speaker choices, this setting does not come from a
project's configuration. Capture runs on the TUI host, even if its app server is
remote. The voice strip shows `voice (screen)` only after the server accepts an
image. Mute and screen sharing are independent; use the screen control to stop
images. Ending the voice call also stops capture.

## Refresh and data handling

While an enabled voice call is active, the client checks the visible screen every
two seconds. Identical images are refreshed at least every six seconds. There is
at most one image in flight and one latest image in the server's active voice
state. The latest image expires after ten seconds and is cleared when sharing or
the call stops. Delayed images and clear operations are bound to their owning
voice attempt so they cannot affect a replacement call.

Passive refreshes do not start model turns, interrupt speech, or create screenshot
files. A delegated spoken request receives the current image as actual vision
input, together with its capture time and screen/window label. That request and
its attachment follow the ordinary conversation handling of the Codex backend;
this is not a promise that image-bearing model requests leave no history.
Screen contents and labels are source data, not executable instructions. Normal
diagnostics redact screen pixels and window titles.

Images are reduced to at most 1920 pixels on the longest edge and encoded as
bounded JPEG data. Capture helpers and image decoding have size and time limits.
Capture or delivery failures stop sharing for that call and report the error;
they do not silently switch to a different desktop.

## Platform support

- Linux Wayland uses `grim`; the current Hyprland desktop is verified. On
  Hyprland, the focused monitor and foreground-window title come from `hyprctl`.
  The pointer is included. Compositors without the required screenshot protocol
  need a different capture implementation.
- Linux X11 uses ImageMagick `import` only when there is no Wayland session. It
  does not substitute a nested X11 desktop for a Wayland user's real screen.
- macOS uses `screencapture` for the main display. Screen Recording permission is
  still required. This code path is not runtime-verified on the Linux host.
- Windows uses a fixed PowerShell/.NET capture of the pointer's display. This
  code path is not runtime-verified on the Linux host.

## App-server integration

The experimental `thread/realtime/setScreenContext` request replaces or clears the
latest image for an active voice session. It takes `threadId`, a unique per-call
`contextId`, a monotonically increasing sharing `generation`, and either a `frame`
with `imageUrl`, `label`, and `capturedAtMs`, or `frame: null`. Inline images are
validated and bounded; remote image URLs are not accepted. The server binds the
context ID to the `realtimeSessionId` supplied at voice start. Clients must use
the same unique ID in both requests. The server keeps a generation tombstone after
clearing, rejecting delayed updates or clears from an earlier call or sharing
generation. Unsupported older
backends report a delivery error instead of claiming the image was shared.

Run a newly built client with `--no-daemon` when verifying a new protocol against
an installation whose shared backend is still serving active older clients. Do
not terminate those clients or weaken the same-build native voice handshake.

## Verification

Tests cover bounded capture and redaction, generation/lifecycle guards, controls,
actual image serialization, clearing and expiry, and app-server-to-model visual
delegation. The live verification helper is maintained in GRID under
`tools/voice-screen-context/`. It sends locally generated speech through an
isolated WebRTC peer and checks a marker visible only in the captured screen,
without using the hardware microphone, playing speaker audio, or letting the
backend obtain the answer through tools.
