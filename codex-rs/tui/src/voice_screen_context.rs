//! On-demand screen images for voice, captured on the TUI host and never written to disk.
//!
//! The caller owns sharing consent and cadence. In particular, constructing this module
//! or a voice session must not start a capture on its own.

use std::fmt;
use std::io::Cursor;
use std::process::Stdio;
use std::time::Duration;
use std::time::SystemTime;
use std::time::UNIX_EPOCH;

use anyhow::Context;
use anyhow::Result;
use anyhow::bail;
use base64::Engine;
use image::DynamicImage;
use image::ImageReader;
use image::codecs::jpeg::JpegEncoder;
use sha2::Digest;
use sha2::Sha256;
use tokio::io::AsyncReadExt;
use tokio::process::Command;

const MAX_CAPTURE_BYTES: usize = 32 * 1024 * 1024;
const MAX_METADATA_BYTES: usize = 64 * 1024;
const MAX_JPEG_BYTES: usize = 750 * 1024;
const MAX_IMAGE_EDGE: u32 = 1920;
const MAX_DECODE_BYTES: u64 = 192 * 1024 * 1024;
const COMMAND_TIMEOUT: Duration = Duration::from_secs(5);
const METADATA_TIMEOUT: Duration = Duration::from_secs(1);
const JPEG_DATA_URL_PREFIX: &str = "data:image/jpeg;base64,";

#[derive(Clone)]
pub(crate) struct ScreenContextFrame {
    pub(crate) image_url: String,
    pub(crate) label: String,
    pub(crate) fingerprint: [u8; 32],
    /// Acquisition time, before image encoding or queuing can make the frame stale.
    pub(crate) captured_at_ms: i64,
}

// Images and window titles may both contain private information. Do not let normal
// Debug logging disclose them, including when a containing app event derives Debug.
impl fmt::Debug for ScreenContextFrame {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ScreenContextFrame")
            .field("image_url", &"[REDACTED]")
            .field("label", &"[REDACTED]")
            .field("fingerprint", &"[REDACTED]")
            .field("captured_at_ms", &self.captured_at_ms)
            .finish()
    }
}

/// Capture the screen the user can actually see, not the contents of a saved file.
/// Helpers execute directly with fixed arguments; no shell or clipboard is involved.
pub(crate) async fn capture_screen_context() -> Result<ScreenContextFrame> {
    let (bytes, label) = capture_platform_screen().await?;
    let captured_at_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(i64::MAX);
    tokio::task::spawn_blocking(move || encode_frame(&bytes, label, captured_at_ms))
        .await
        .context("screen image processing did not finish")?
}

#[cfg(target_os = "linux")]
async fn capture_platform_screen() -> Result<(Vec<u8>, String)> {
    if is_wayland_session(
        std::env::var_os("WAYLAND_DISPLAY").as_deref(),
        std::env::var_os("XDG_SESSION_TYPE").as_deref(),
    ) {
        let monitors = metadata("hyprctl", &["-j", "monitors"]).await;
        let monitor = monitors.as_deref().and_then(focused_monitor);
        let active_window = metadata("hyprctl", &["-j", "activewindow"]).await;
        let title = active_window.as_deref().and_then(window_title);
        let mut arguments = vec!["-c", "-t", "jpeg", "-q", "85"];
        if let Some(monitor) = monitor.as_deref() {
            arguments.extend(["-o", monitor]);
        }
        arguments.push("-");
        let bytes = command_output("grim", &arguments, MAX_CAPTURE_BYTES, COMMAND_TIMEOUT)
            .await
            .context("could not capture the Wayland screen; screen context requires grim and compositor screenshot support")?;
        let mut label = match monitor {
            Some(monitor) => format!("Current screen on {monitor}"),
            None => "Current Wayland desktop".to_owned(),
        };
        if let Some(title) = title {
            label.push_str("; foreground window: ");
            label.push_str(&title);
        }
        return Ok((bytes, clean_label(&label)));
    }

    if std::env::var_os("DISPLAY").is_some_and(|display| !display.is_empty()) {
        // Do not use this fallback from Wayland: DISPLAY may point to a nested or
        // XWayland desktop that does not show the user's actual visible windows.
        let bytes = command_output(
            "import",
            &["-window", "root", "png:-"],
            MAX_CAPTURE_BYTES,
            COMMAND_TIMEOUT,
        )
        .await
        .context("could not capture the X11 desktop; screen context requires ImageMagick import")?;
        return Ok((bytes, "Current X11 desktop".to_owned()));
    }
    bail!("screen context is unavailable: no local Wayland or X11 display is connected")
}

#[cfg(target_os = "macos")]
async fn capture_platform_screen() -> Result<(Vec<u8>, String)> {
    // -m produces one main-display image; /dev/stdout keeps the image out of files.
    // macOS still enforces Screen Recording permission for the terminal host.
    let bytes = command_output(
        "/usr/sbin/screencapture",
        &["-x", "-C", "-m", "-t", "png", "/dev/stdout"],
        MAX_CAPTURE_BYTES,
        COMMAND_TIMEOUT,
    )
    .await
    .context(
        "could not capture the macOS display; check Screen Recording permission for this terminal",
    )?;
    Ok((bytes, "Current main macOS display".to_owned()))
}

#[cfg(windows)]
async fn capture_platform_screen() -> Result<(Vec<u8>, String)> {
    // A fixed script, with no interpolated user/window text and no temporary files.
    // Draw a small ring at the pointer: CopyFromScreen does not include its image.
    const SCRIPT: &str = r#"
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$position = [System.Windows.Forms.Cursor]::Position
$bounds = [System.Windows.Forms.Screen]::FromPoint($position).Bounds
$bitmap = [System.Drawing.Bitmap]::new($bounds.Width, $bounds.Height)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$stream = [System.IO.MemoryStream]::new()
$pen = [System.Drawing.Pen]::new([System.Drawing.Color]::Red, 2)
try {
  $graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
  $graphics.DrawEllipse($pen, $position.X - $bounds.X - 6, $position.Y - $bounds.Y - 6, 12, 12)
  $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
  $bytes = $stream.ToArray()
  [Console]::OpenStandardOutput().Write($bytes, 0, $bytes.Length)
} finally {
  $pen.Dispose()
  $stream.Dispose()
  $graphics.Dispose()
  $bitmap.Dispose()
}
"#;
    let bytes = command_output(
        "powershell.exe",
        &[
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            SCRIPT,
        ],
        MAX_CAPTURE_BYTES,
        COMMAND_TIMEOUT,
    )
    .await
    .context("could not capture the Windows screen")?;
    Ok((bytes, "Current Windows screen at the pointer".to_owned()))
}

#[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
async fn capture_platform_screen() -> Result<(Vec<u8>, String)> {
    bail!("screen context is not implemented for this operating system")
}

fn is_wayland_session(
    display: Option<&std::ffi::OsStr>,
    session_type: Option<&std::ffi::OsStr>,
) -> bool {
    display.is_some_and(|display| !display.is_empty())
        || session_type.is_some_and(|session_type| session_type == "wayland")
}

#[cfg(target_os = "linux")]
async fn metadata(program: &str, arguments: &[&str]) -> Option<Vec<u8>> {
    command_output(program, arguments, MAX_METADATA_BYTES, METADATA_TIMEOUT)
        .await
        .ok()
}

fn focused_monitor(bytes: &[u8]) -> Option<String> {
    let monitors: serde_json::Value = serde_json::from_slice(bytes).ok()?;
    let monitors = monitors.as_array()?;
    let focused = monitors.iter().find(|monitor| monitor["focused"] == true)?;
    let name = focused["name"].as_str()?;
    if name.is_empty() || name.len() > 128 || name.chars().any(char::is_control) {
        return None;
    }
    Some(name.to_owned())
}

fn window_title(bytes: &[u8]) -> Option<String> {
    let window: serde_json::Value = serde_json::from_slice(bytes).ok()?;
    let title = window["title"].as_str()?;
    let title = clean_label(title);
    (!title.is_empty()).then_some(title)
}

fn clean_label(label: &str) -> String {
    label
        .chars()
        .filter(|character| !character.is_control())
        .take(240)
        .collect()
}

async fn command_output(
    program: &str,
    arguments: &[&str],
    max_bytes: usize,
    timeout: Duration,
) -> Result<Vec<u8>> {
    let mut child = Command::new(program)
        .args(arguments)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true)
        .spawn()
        .with_context(|| format!("could not start screen capture helper {program}"))?;
    let stdout = child
        .stdout
        .take()
        .context("screen capture helper has no output pipe")?;
    let result = tokio::time::timeout(timeout, async {
        let mut bytes = Vec::new();
        stdout
            .take(max_bytes as u64 + 1)
            .read_to_end(&mut bytes)
            .await
            .context("could not read screen capture helper output")?;
        if bytes.len() > max_bytes {
            bail!("screen capture helper output exceeds its size limit");
        }
        let status = child
            .wait()
            .await
            .context("could not wait for screen capture helper")?;
        if !status.success() {
            bail!("screen capture helper {program} failed");
        }
        Ok(bytes)
    })
    .await;
    match result {
        Ok(result) => result,
        Err(_) => bail!("screen capture helper {program} timed out"),
    }
}

fn encode_frame(bytes: &[u8], label: String, captured_at_ms: i64) -> Result<ScreenContextFrame> {
    if bytes.is_empty() || bytes.len() > MAX_CAPTURE_BYTES {
        bail!("screen image is empty or exceeds its size limit");
    }
    let mut reader = ImageReader::new(Cursor::new(bytes))
        .with_guessed_format()
        .context("could not identify the captured image")?;
    let mut limits = image::Limits::default();
    limits.max_image_width = Some(16_384);
    limits.max_image_height = Some(16_384);
    limits.max_alloc = Some(MAX_DECODE_BYTES);
    reader.limits(limits);
    let image = reader
        .decode()
        .context("could not decode the captured image")?;
    let mut image = image
        .thumbnail(
            MAX_IMAGE_EDGE.min(image.width()),
            MAX_IMAGE_EDGE.min(image.height()),
        )
        .to_rgb8();
    let mut jpeg = Vec::new();
    loop {
        jpeg.clear();
        JpegEncoder::new_with_quality(&mut jpeg, 80)
            .encode_image(&image)
            .context("could not encode screen image")?;
        if jpeg.len() <= MAX_JPEG_BYTES {
            break;
        }
        let width = (image.width() * 3 / 4).max(1);
        let height = (image.height() * 3 / 4).max(1);
        image = DynamicImage::ImageRgb8(image)
            .thumbnail(width, height)
            .to_rgb8();
    }
    let label = clean_label(&label);
    let mut hasher = Sha256::new();
    hasher.update(image.width().to_be_bytes());
    hasher.update(image.height().to_be_bytes());
    hasher.update(image.as_raw());
    hasher.update(label.as_bytes());
    let fingerprint = hasher.finalize().into();
    let image_url = format!(
        "{JPEG_DATA_URL_PREFIX}{}",
        base64::engine::general_purpose::STANDARD.encode(jpeg)
    );
    Ok(ScreenContextFrame {
        image_url,
        label,
        fingerprint,
        captured_at_ms,
    })
}

#[cfg(test)]
#[path = "voice_screen_context_tests.rs"]
mod tests;
