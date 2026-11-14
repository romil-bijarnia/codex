//! Production Core/app-server/TUI voice routing with synthetic screen pixels.
//! Only the network peers are fixtures; no microphone or screen permission is used.

use super::*;
use app_test_support::MockResponsesConfig;
use app_test_support::create_mock_responses_server_sequence_unchecked;
use base64::Engine;
use codex_app_server_client::AppServerEvent;
use codex_app_server_protocol::ServerNotification;
use codex_app_server_protocol::ThreadRealtimeScreenContextFrame;
use codex_login::AuthCredentialsStoreMode;
use codex_login::AuthKeyringBackendKind;
use codex_model_provider_info::ModelProviderInfo;
use core_test_support::responses;
use core_test_support::responses::WebSocketConnectionConfig;
use pretty_assertions::assert_eq;
use serde_json::Value;
use serde_json::json;
use std::io::Cursor;
use std::time::Duration;
use std::time::SystemTime;
use std::time::UNIX_EPOCH;
use tokio::time::timeout;
use wiremock::Mock;
use wiremock::ResponseTemplate;
use wiremock::matchers::method;
use wiremock::matchers::path;

#[test]
fn voice_screen_context_pixels_reach_vision_backend_and_answer_returns_to_voice() -> Result<()> {
    run_voice_screen_context_fixture(/*clear_before_delegation*/ false)
}

#[test]
fn voice_screen_context_clear_prevents_image_attachment_to_later_delegation() -> Result<()> {
    run_voice_screen_context_fixture(/*clear_before_delegation*/ true)
}

fn run_voice_screen_context_fixture(clear_before_delegation: bool) -> Result<()> {
    // This debug-only, in-process App fixture polls large TUI/server futures.
    // Construct and poll it on a bounded fixture thread instead of requiring a
    // process-wide RUST_MIN_STACK setting or changing production runtime stacks.
    let fixture = std::thread::Builder::new()
        .name("voice-screen-context-fixture".to_string())
        .stack_size(16 * 1024 * 1024)
        .spawn(move || -> Result<()> {
            let runtime = tokio::runtime::Builder::new_multi_thread()
                .worker_threads(2)
                .thread_stack_size(16 * 1024 * 1024)
                .enable_all()
                .build()?;
            runtime.block_on(Box::pin(check_voice_screen_context(
                clear_before_delegation,
            )))
        })?;
    match fixture.join() {
        Ok(result) => result,
        Err(panic) => std::panic::resume_unwind(panic),
    }
}

fn synthetic_screen_frame() -> Result<ThreadRealtimeScreenContextFrame> {
    let mut bytes = Cursor::new(Vec::new());
    image::DynamicImage::new_rgb8(12, 8).write_to(&mut bytes, image::ImageFormat::Png)?;
    Ok(ThreadRealtimeScreenContextFrame {
        image_url: format!(
            "data:image/png;base64,{}",
            base64::engine::general_purpose::STANDARD.encode(bytes.into_inner())
        ),
        label: "Unsaved Program.cs in Cursor".to_string(),
        captured_at_ms: i64::try_from(SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis())?,
    })
}

fn input_content(body: &Value) -> Vec<&Value> {
    body["input"]
        .as_array()
        .into_iter()
        .flatten()
        .flat_map(|item| item["content"].as_array().into_iter().flatten())
        .collect()
}

async fn check_voice_screen_context(clear_before_delegation: bool) -> Result<()> {
    core_test_support::skip_if_no_network!(Ok(()));
    const ANSWER: &str = "The current unsaved code declares the screen marker.";
    let mut answer = responses::ev_assistant_message("screen-answer", ANSWER);
    answer["item"]["phase"] = json!("final_answer");
    let model_server = create_mock_responses_server_sequence_unchecked(vec![responses::sse(vec![
        responses::ev_response_created("screen-response"),
        answer,
        responses::ev_completed("screen-response"),
    ])])
    .await;
    Mock::given(method("POST"))
        .and(path("/v1/live"))
        .respond_with(
            ResponseTemplate::new(200)
                .insert_header("Location", "/v1/live/rtc_voice_screen_context")
                .set_body_string("v=answer\r\n"),
        )
        .mount(&model_server)
        .await;

    // A screen enable appends developer context. Do not request delegation until
    // a later explicit fixture prompt, after the frame RPC has acknowledged.
    let mut requests = vec![vec![json!({
        "type": "session.started",
        "session": {"id": "sess_voice_screen", "instructions": "backend prompt"}
    })]];
    if clear_before_delegation {
        // Clearing also appends a developer notice. It must not trigger work.
        requests.push(vec![]);
    }
    let delegation_request_index = requests.len();
    requests.push(vec![json!({
        "type": "delegation.created",
        "offset_ms": 100,
        "item": {
            "id": "delegation_voice_screen",
            "type": "delegation",
            "target": "client",
            "content": [{"type": "input_text", "text": "Explain the code visible on my current screen."}]
        }
    })]);
    requests.extend([vec![], vec![]]);
    let realtime_server =
        responses::start_websocket_server_with_headers(vec![WebSocketConnectionConfig {
            requests,
            response_headers: Vec::new(),
            accept_delay: None,
            close_after_requests: false,
        }])
        .await;

    let (mut app, mut app_events, mut ops) = Box::pin(make_test_app_with_channels()).await;
    let codex_home = tempfile::tempdir()?;
    MockResponsesConfig::new(&model_server.uri())
        .with_root_config(&format!(
            "experimental_realtime_ws_base_url = {:?}\nexperimental_realtime_webrtc_call_base_url = {:?}",
            realtime_server.uri(),
            format!("{}/v1", model_server.uri()),
        ))
        .with_extra_config("[realtime]\nversion = \"v3\"\ntype = \"conversational\"")
        .write(codex_home.path())?;
    codex_login::login_with_api_key(
        codex_home.path(),
        "sk-test-key",
        AuthCredentialsStoreMode::File,
        AuthKeyringBackendKind::default(),
    )?;
    app.config.codex_home = codex_home.path().to_path_buf().abs();
    app.config.sqlite = codex_state::SqliteConfig::new_for_testing(codex_home.path().abs());
    app.config.cli_auth_credentials_store_mode = AuthCredentialsStoreMode::File;
    app.config.model = Some("mock-model".to_string());
    app.config.model_provider_id = "mock_provider".to_string();
    app.config.model_provider = ModelProviderInfo {
        name: "Mock vision provider for voice screen test".to_string(),
        base_url: Some(format!("{}/v1", model_server.uri())),
        request_max_retries: Some(0),
        stream_max_retries: Some(0),
        ..ModelProviderInfo::default()
    };
    app.config.experimental_realtime_ws_base_url = Some(realtime_server.uri().to_string());
    app.config.experimental_realtime_webrtc_call_base_url =
        Some(format!("{}/v1", model_server.uri()));
    let mut app_server = Box::pin(crate::start_embedded_app_server_for_picker(&app.config)).await?;
    let started = app_server.start_thread(&app.config).await?;
    let thread_id = started.session.thread_id;
    app.enqueue_primary_thread_session(started.session, started.turns)
        .await?;
    crate::chatwidget::activate_voice_for_thread(&mut app.chat_widget, thread_id);
    while ops.try_recv().is_ok() {}
    let mut tui = crate::tui::test_support::make_test_tui()?;
    app_server
        .thread_realtime_start(
            thread_id,
            "v=offer\r\n".to_string(),
            /*model*/ None,
            /*voice*/ None,
            /*realtime_session_id*/ Some("voice-screen-test".into()),
        )
        .await?;
    // The start RPC acknowledges submission, not completed call creation. A frame
    // captured before the real started event correctly fails the session-age guard.
    timeout(Duration::from_secs(5), async {
        loop {
            let event = app_server
                .next_event()
                .await
                .expect("embedded app-server should stay connected during voice start");
            let started = matches!(&event.event,
                AppServerEvent::ServerNotification(notification)
                    if matches!(notification.as_ref(),
                        ServerNotification::ThreadRealtimeStarted(started)
                            if started.thread_id == thread_id.to_string()));
            Box::pin(app.handle_backend_event(&app_server, event)).await;
            app.drain_active_thread_events(&mut tui).await?;
            if started {
                break Ok::<_, color_eyre::Report>(());
            }
        }
    })
    .await??;
    let frame = synthetic_screen_frame()?;
    app_server
        .thread_realtime_set_screen_context(
            thread_id,
            "voice-screen-test".into(),
            0,
            Some(frame.clone()),
        )
        .await?;
    if clear_before_delegation {
        app_server
            .thread_realtime_set_screen_context(thread_id, "voice-screen-test".into(), 1, None)
            .await?;
    }
    // Neither enable nor clear should create an inference request by itself.
    assert!(
        responses::received_responses_requests(&model_server)
            .await
            .is_empty()
    );
    app_server
        .thread_realtime_append_speech(thread_id, "fixture prompt".to_string())
        .await?;

    let speech = timeout(Duration::from_secs(15), async {
        loop {
            tokio::select! {
                event = app_server.next_event() => {
                    Box::pin(app.handle_backend_event(
                        &app_server,
                        event.expect("embedded app-server should stay connected"),
                    )).await;
                    app.drain_active_thread_events(&mut tui).await?;
                }
                event = app_events.recv() => {
                    Box::pin(app.handle_event(&mut tui, &mut app_server,
                        event.expect("TUI event stream should stay open"))).await?;
                }
                op = ops.recv(), if !ops.is_closed() => {
                    let op = op.expect("TUI command stream should stay open");
                    if matches!(op, Op::RealtimeConversationSpeech { .. }) {
                        break Ok::<_, color_eyre::Report>(op);
                    }
                    Box::pin(app.handle_event(&mut tui, &mut app_server, AppEvent::CodexOp(op))).await?;
                }
            }
        }
    })
    .await??;
    assert!(
        matches!(&speech, Op::RealtimeConversationSpeech { text, .. } if text.as_str() == ANSWER)
    );
    let model_requests = responses::received_responses_requests(&model_server).await;
    assert_eq!(model_requests.len(), 1);
    let body = model_requests[0].body_json();
    let content = input_content(&body);
    let images: Vec<_> = content
        .iter()
        .filter(|item| item["type"] == "input_image")
        .collect();
    if clear_before_delegation {
        assert!(
            images.is_empty(),
            "cleared screen image must not reach a later turn"
        );
    } else {
        assert_eq!(images.len(), 1);
        assert_eq!(images[0]["image_url"], frame.image_url);
        assert!(content.iter().any(|item| {
            item["type"] == "input_text"
                && item["text"]
                    .as_str()
                    .is_some_and(|text| text.contains("[voice_screen_context]"))
        }));
        assert!(
            !content.iter().any(|item| item["type"] == "input_text"
                && item["text"]
                    .as_str()
                    .is_some_and(|text| text.contains(&frame.image_url))),
            "screen pixels must be vision input, not a URL embedded in text"
        );
    }
    Box::pin(app.handle_event(&mut tui, &mut app_server, AppEvent::CodexOp(speech))).await?;
    let spoken = timeout(
        Duration::from_secs(5),
        realtime_server.wait_for_request(0, delegation_request_index + 1),
    )
    .await?
    .body_json();
    assert_eq!(spoken["type"], "session.context.append");
    assert_eq!(spoken["channel"], "speakable");
    assert_eq!(spoken["content"][0]["text"], ANSWER);
    assert!(
        ops.try_recv().is_err(),
        "screen answer must only be sent once"
    );
    realtime_server.shutdown().await;
    app_server.shutdown().await?;
    Ok(())
}
