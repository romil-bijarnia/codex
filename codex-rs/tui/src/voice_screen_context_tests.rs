use super::*;
use image::ImageBuffer;
use image::Rgb;

fn png(image: DynamicImage) -> Vec<u8> {
    let mut bytes = Cursor::new(Vec::new());
    image.write_to(&mut bytes, image::ImageFormat::Png).unwrap();
    bytes.into_inner()
}

fn decode_frame_image(frame: &ScreenContextFrame) -> DynamicImage {
    let encoded = frame.image_url.strip_prefix(JPEG_DATA_URL_PREFIX).unwrap();
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(encoded)
        .unwrap();
    assert!(bytes.len() <= MAX_JPEG_BYTES);
    image::load_from_memory(&bytes).unwrap()
}

#[test]
fn converts_real_pixels_to_a_bounded_jpeg_data_url() {
    let image = ImageBuffer::from_pixel(2400, 1600, Rgb([4_u8, 80, 220]));
    let frame = encode_frame(&png(image.into()), "Editor".to_owned(), 123_456).unwrap();
    let decoded = decode_frame_image(&frame);
    assert_eq!(decoded.width(), 1920);
    assert_eq!(decoded.height(), 1280);
    assert!(frame.image_url.len() < 1024 * 1024);
    assert_eq!(frame.label, "Editor");
    assert_eq!(frame.captured_at_ms, 123_456);
}

#[test]
fn keeps_small_images_at_their_original_size() {
    let image = ImageBuffer::from_pixel(200, 100, Rgb([20_u8, 30, 40]));
    let frame = encode_frame(&png(image.into()), "Editor".to_owned(), 123_456).unwrap();
    let decoded = decode_frame_image(&frame);
    assert_eq!((decoded.width(), decoded.height()), (200, 100));
}

#[test]
fn fingerprints_are_stable_and_change_when_pixels_or_label_change() {
    let image = ImageBuffer::from_pixel(40, 40, Rgb([0_u8, 0, 0]));
    let bytes = png(image.into());
    let a = encode_frame(&bytes, "First".to_owned(), 123_456).unwrap();
    let b = encode_frame(&bytes, "First".to_owned(), 123_457).unwrap();
    let c = encode_frame(&bytes, "Second".to_owned(), 123_456).unwrap();
    assert_eq!(a.fingerprint, b.fingerprint);
    assert_eq!(b.captured_at_ms, 123_457);
    assert_ne!(a.fingerprint, c.fingerprint);
    let image = ImageBuffer::from_pixel(40, 40, Rgb([255_u8, 0, 0]));
    let d = encode_frame(&png(image.into()), "First".to_owned(), 123_456).unwrap();
    assert_ne!(a.fingerprint, d.fingerprint);
}

#[test]
fn does_not_disclose_images_or_window_titles_in_debug() {
    let frame = ScreenContextFrame {
        image_url: "data:image/jpeg;base64,PRIVATE_IMAGE".to_owned(),
        label: "PRIVATE_WINDOW_TITLE".to_owned(),
        fingerprint: [7; 32],
        captured_at_ms: 123_456,
    };
    let debug = format!("{frame:?}");
    assert!(debug.contains("REDACTED"));
    assert!(!debug.contains("PRIVATE"));
    assert!(!debug.contains("data:image"));
    assert!(!debug.contains("7, 7"));
}

#[test]
fn rejects_empty_malformed_oversized_and_over_dimensioned_images() {
    assert!(encode_frame(&[], String::new(), 123_456).is_err());
    assert!(encode_frame(b"not an image", String::new(), 123_456).is_err());
    assert!(encode_frame(&vec![0; MAX_CAPTURE_BYTES + 1], String::new(), 123_456).is_err());
    let image = ImageBuffer::from_pixel(16_385, 1, Rgb([0_u8, 0, 0]));
    assert!(encode_frame(&png(image.into()), String::new(), 123_456).is_err());
}

#[test]
fn keeps_high_detail_images_under_the_transport_budget() {
    let mut state = 1234_u32;
    let image = ImageBuffer::from_fn(2048, 2048, |_, _| {
        let mut pixel = [0_u8; 3];
        for channel in &mut pixel {
            state = state.wrapping_mul(1_664_525).wrapping_add(1_013_904_223);
            *channel = (state >> 24) as u8;
        }
        Rgb(pixel)
    });
    let frame = encode_frame(&png(image.into()), "Detailed screen".to_owned(), 123_456).unwrap();
    let decoded = decode_frame_image(&frame);
    assert!(decoded.width() <= MAX_IMAGE_EDGE);
    assert!(decoded.height() <= MAX_IMAGE_EDGE);
    assert!(frame.image_url.len() < 1024 * 1024);
}

#[test]
fn detects_wayland_without_mistaking_an_x11_session_for_it() {
    use std::ffi::OsStr;
    assert!(is_wayland_session(Some(OsStr::new("wayland-1")), None));
    assert!(is_wayland_session(None, Some(OsStr::new("wayland"))));
    assert!(!is_wayland_session(None, Some(OsStr::new("x11"))));
    assert!(!is_wayland_session(Some(OsStr::new("")), None));
}

#[test]
fn chooses_only_the_focused_monitor_from_valid_metadata() {
    assert_eq!(
        focused_monitor(br#"[{"name":"one","focused":false},{"name":"two","focused":true}]"#),
        Some("two".to_owned())
    );
    assert_eq!(
        focused_monitor(br#"[{"name":"one","focused":false}]"#),
        None
    );
    assert_eq!(
        focused_monitor(br#"[{"name":"bad\nname","focused":true}]"#),
        None
    );
    assert_eq!(focused_monitor(b"invalid"), None);
}

#[test]
fn strips_terminal_controls_and_bounds_window_labels() {
    assert_eq!(
        window_title(br#"{"title":"Cursor\n\u001bCode"}"#),
        Some("CursorCode".to_owned())
    );
    assert_eq!(window_title(br#"{"title":""}"#), None);
    assert_eq!(clean_label(&"a".repeat(1000)).len(), 240);
}

#[cfg(unix)]
#[tokio::test]
async fn helper_execution_bounds_output_and_reports_failures() {
    let bytes = command_output(
        "/bin/sh",
        &["-c", "printf pixels"],
        10,
        Duration::from_secs(1),
    )
    .await
    .unwrap();
    assert_eq!(bytes, b"pixels");
    assert!(
        command_output(
            "/bin/sh",
            &["-c", "printf too-many-bytes"],
            3,
            Duration::from_secs(1)
        )
        .await
        .is_err()
    );
    assert!(
        command_output("/bin/sh", &["-c", "exit 3"], 10, Duration::from_secs(1))
            .await
            .is_err()
    );
    assert!(
        command_output(
            "/definitely/not/a/screen/helper",
            &[],
            10,
            Duration::from_secs(1)
        )
        .await
        .is_err()
    );
}

#[cfg(unix)]
#[tokio::test]
async fn helper_execution_has_a_deadline() {
    let result = command_output(
        "/bin/sh",
        &["-c", "exec sleep 10"],
        10,
        Duration::from_millis(20),
    )
    .await;
    assert!(result.unwrap_err().to_string().contains("timed out"));
}
