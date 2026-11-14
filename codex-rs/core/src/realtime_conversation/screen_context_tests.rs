use super::super::ConversationState;
use super::super::RealtimeHandoffAdmission;
use super::super::RealtimeHandoffState;
use super::super::RealtimeSessionKind;
use super::*;
use codex_api::RealtimeEventParser;
use codex_protocol::models::ResponseInputItem;
use codex_protocol::protocol::CodexResponseHandoffMode;
use std::collections::BTreeMap;
use std::sync::Arc;
use std::sync::atomic::AtomicBool;
use tokio::sync::Mutex;
use tokio_util::sync::CancellationToken;

fn frame_at(captured_at_ms: i64) -> RealtimeScreenContextFrame {
    let mut bytes = Cursor::new(Vec::new());
    image::DynamicImage::new_rgb8(2, 2)
        .write_to(&mut bytes, image::ImageFormat::Png)
        .unwrap();
    RealtimeScreenContextFrame {
        image_url: format!(
            "data:image/png;base64,{}",
            STANDARD.encode(bytes.into_inner())
        ),
        label: "Unsaved test.rs — Cursor".to_string(),
        captured_at_ms,
    }
}

async fn active_manager() -> (
    RealtimeConversationManager,
    async_channel::Receiver<ConversationTextParams>,
) {
    let manager = RealtimeConversationManager::new();
    let (text_tx, text_rx) = async_channel::bounded(8);
    let handoff = RealtimeHandoffState {
        output_tx: async_channel::bounded(1).0,
        last_output: Arc::new(Mutex::new(None)),
        stream: Arc::new(Mutex::new(Default::default())),
        client_managed_handoffs: true,
        codex_responses_as_items: false,
        codex_response_item_prefix: None,
        backend_reasoning_status: false,
        codex_response_handoff_mode: CodexResponseHandoffMode::Thinking,
        codex_response_handoff_channel_prefixes: Arc::new(BTreeMap::new()),
        session_kind: RealtimeSessionKind::V1,
        event_parser: RealtimeEventParser::FramelessBidi,
    };
    manager.state.lock().await.conversation = Some(ConversationState {
        screen_context: None,
        screen_context_binding: None,
        started_at_ms: now_ms() - 100,
        session_id: Some("call-a".to_string()),
        sub_id: "voice_screen_test".to_string(),
        audio_tx: async_channel::bounded(1).0,
        text_tx,
        session_kind: RealtimeSessionKind::V1,
        handoff,
        input_task: tokio::spawn(async {}),
        fanout_task: None,
        realtime_active: Arc::new(AtomicBool::new(true)),
        route_handoffs: Arc::new(RealtimeHandoffAdmission::new()),
        stop_token: CancellationToken::new(),
    });
    (manager, text_rx)
}

#[test]
fn validates_bounded_inline_images_without_accepting_remote_urls() {
    let now = now_ms();
    let mut frame = frame_at(now);
    validate(&frame, now).unwrap();
    frame.image_url = "https://example.test/image.png".to_string();
    assert!(validate(&frame, now).is_err());
    frame.image_url = "data:image/png;base64,not-base64!".to_string();
    assert!(validate(&frame, now).is_err());
    frame.image_url = format!("data:image/png;base64,{}", STANDARD.encode(b"not an image"));
    assert!(validate(&frame, now).is_err());
    frame.image_url = "x".repeat(MAX_DATA_URL_BYTES + 1);
    assert!(validate(&frame, now).is_err());
}

#[test]
fn stale_and_future_frames_are_rejected() {
    let now = now_ms();
    assert!(validate(&frame_at(now - MAX_FRAME_AGE_MS - 1), now).is_err());
    assert!(validate(&frame_at(now + MAX_FUTURE_SKEW_MS + 1), now).is_err());
    assert!(validate(&frame_at(0), now).is_err());
}

#[test]
fn debug_output_never_contains_screen_pixels_or_window_titles() {
    let frame = frame_at(now_ms());
    let debug = format!("{frame:?}");
    assert!(!debug.contains(&frame.image_url));
    assert!(!debug.contains(&frame.label));
    assert!(debug.contains("REDACTED"));
}

#[tokio::test]
async fn screen_pixels_become_actual_vision_input_and_refresh_does_not_start_work() {
    let (manager, text_rx) = active_manager().await;
    let mut frame = frame_at(now_ms());
    manager
        .set_screen_context("call-a".into(), 0, Some(frame.clone()))
        .await
        .unwrap();
    let notice = text_rx.recv().await.unwrap();
    assert_eq!(notice.role, ConversationTextRole::Developer);
    assert!(notice.text.contains("delegated vision-capable agent"));
    frame.label = "Current unsaved file".to_string();
    manager
        .set_screen_context("call-a".into(), 0, Some(frame.clone()))
        .await
        .unwrap();
    assert!(
        text_rx.try_recv().is_err(),
        "passive refresh must not generate a voice turn"
    );
    let input = manager.screen_context_input().await;
    let body = serde_json::to_value(ResponseInputItem::from(input)).unwrap();
    assert_eq!(body["content"][0]["type"], "input_text");
    assert_eq!(body["content"][1]["type"], "input_image");
    assert_eq!(body["content"][1]["image_url"], frame.image_url);
    assert!(
        body["content"][0]["text"]
            .as_str()
            .unwrap()
            .contains("Current unsaved file")
    );
}

#[tokio::test]
async fn disabled_expired_closed_and_previous_session_frames_are_not_attached() {
    let (manager, _text_rx) = active_manager().await;
    let frame = frame_at(now_ms());
    manager
        .set_screen_context("call-a".into(), 0, Some(frame.clone()))
        .await
        .unwrap();
    manager
        .set_screen_context("call-a".into(), 1, None)
        .await
        .unwrap();
    assert!(manager.screen_context_input().await.is_empty());
    manager
        .state
        .lock()
        .await
        .conversation
        .as_mut()
        .unwrap()
        .screen_context = Some(frame_at(now_ms() - MAX_FRAME_AGE_MS - 1));
    let expired = manager.screen_context_input().await;
    assert!(
        matches!(expired.as_slice(), [UserInput::Text { text, .. }] if text.contains("No fresh screen image"))
    );
    manager
        .state
        .lock()
        .await
        .conversation
        .as_mut()
        .unwrap()
        .started_at_ms = frame.captured_at_ms + 1;
    assert!(
        manager
            .set_screen_context("call-a".into(), 0, Some(frame))
            .await
            .is_err()
    );
    manager.shutdown().await.unwrap();
    assert!(manager.screen_context_input().await.is_empty());
    assert!(
        manager
            .set_screen_context("call-a".into(), 1, None)
            .await
            .is_err()
    );
}

#[tokio::test]
async fn clearing_screen_context_notifies_voice_without_sending_images_to_audio_frontend() {
    let (manager, text_rx) = active_manager().await;
    manager
        .set_screen_context("call-a".into(), 0, Some(frame_at(now_ms())))
        .await
        .unwrap();
    let _ = text_rx.recv().await.unwrap();
    manager
        .set_screen_context("call-a".into(), 1, None)
        .await
        .unwrap();
    assert!(
        text_rx
            .recv()
            .await
            .unwrap()
            .text
            .contains("Screen context has stopped")
    );
    assert!(text_rx.try_recv().is_err());
    assert!(manager.screen_context_input().await.is_empty());
}

#[tokio::test]
async fn owner_and_generation_tombstone_reject_delayed_clears_and_updates() {
    let (manager, _text_rx) = active_manager().await;
    assert!(
        manager
            .set_screen_context("old-call".into(), 1, None)
            .await
            .is_err()
    );
    manager
        .set_screen_context("call-a".into(), 0, Some(frame_at(now_ms())))
        .await
        .unwrap();
    assert!(
        manager
            .set_screen_context("call-b".into(), 99, None)
            .await
            .is_err()
    );
    assert!(
        manager
            .screen_context_input()
            .await
            .iter()
            .any(|item| matches!(item, UserInput::Image { .. }))
    );
    manager
        .set_screen_context("call-a".into(), 1, None)
        .await
        .unwrap();
    assert!(
        manager
            .set_screen_context("call-a".into(), 0, None)
            .await
            .is_err()
    );
    assert!(
        manager
            .set_screen_context("call-a".into(), 0, Some(frame_at(now_ms())))
            .await
            .is_err()
    );
    assert!(
        manager
            .set_screen_context("call-a".into(), 1, Some(frame_at(now_ms())))
            .await
            .is_err()
    );
    assert!(manager.screen_context_input().await.is_empty());
    manager
        .set_screen_context("call-a".into(), 2, Some(frame_at(now_ms())))
        .await
        .unwrap();
    assert!(
        manager
            .set_screen_context("call-a".into(), 1, None)
            .await
            .is_err()
    );
    assert!(
        manager
            .screen_context_input()
            .await
            .iter()
            .any(|item| matches!(item, UserInput::Image { .. }))
    );
}

#[tokio::test]
async fn capture_time_cannot_go_backwards_within_a_sharing_generation() {
    let (manager, _text_rx) = active_manager().await;
    let current = now_ms();
    manager
        .set_screen_context("call-a".into(), 0, Some(frame_at(current)))
        .await
        .unwrap();
    assert!(
        manager
            .set_screen_context("call-a".into(), 0, Some(frame_at(current - 50)))
            .await
            .is_err()
    );
    let state = manager.state.lock().await;
    assert_eq!(
        state
            .conversation
            .as_ref()
            .unwrap()
            .screen_context
            .as_ref()
            .unwrap()
            .captured_at_ms,
        current
    );
}

#[tokio::test]
async fn new_call_requires_a_fresh_first_frame_before_any_clear_can_apply() {
    let (manager, _text_rx) = active_manager().await;
    let started = now_ms();
    {
        let mut state = manager.state.lock().await;
        let conversation = state.conversation.as_mut().unwrap();
        conversation.started_at_ms = started;
        conversation.session_id = Some("new-call".to_string());
    }
    assert!(
        manager
            .set_screen_context("old-call".into(), 200, None)
            .await
            .is_err()
    );
    assert!(
        manager
            .set_screen_context("old-call".into(), 200, Some(frame_at(started - 1)))
            .await
            .is_err()
    );
    assert!(
        manager
            .state
            .lock()
            .await
            .conversation
            .as_ref()
            .unwrap()
            .screen_context_binding
            .is_none()
    );
    manager
        .set_screen_context("new-call".into(), 0, Some(frame_at(now_ms())))
        .await
        .unwrap();
    assert!(
        manager
            .set_screen_context("old-call".into(), 201, None)
            .await
            .is_err()
    );
    assert!(
        manager
            .set_screen_context(String::new(), 1, None)
            .await
            .is_err()
    );
    assert!(
        manager
            .set_screen_context("x".repeat(129), 1, None)
            .await
            .is_err()
    );
    assert!(
        manager
            .screen_context_input()
            .await
            .iter()
            .any(|item| matches!(item, UserInput::Image { .. }))
    );
}

#[tokio::test]
async fn first_fresh_frame_cannot_bind_a_different_voice_session_nonce() {
    let (manager, _text_rx) = active_manager().await;
    assert!(
        manager
            .set_screen_context("other-call".into(), 0, Some(frame_at(now_ms())))
            .await
            .is_err()
    );
    assert!(
        manager
            .state
            .lock()
            .await
            .conversation
            .as_ref()
            .unwrap()
            .screen_context_binding
            .is_none()
    );
    assert!(manager.screen_context_input().await.is_empty());
    manager
        .set_screen_context("call-a".into(), 0, Some(frame_at(now_ms())))
        .await
        .unwrap();
    assert!(
        manager
            .screen_context_input()
            .await
            .iter()
            .any(|item| matches!(item, UserInput::Image { .. }))
    );
}
