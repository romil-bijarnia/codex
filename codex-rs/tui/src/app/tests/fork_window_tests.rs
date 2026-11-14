//! The window handoff must not turn the source frontend into its fork.

use super::session_lifecycle_requests::recorded_params;
use super::session_lifecycle_requests::start_recording_app_server;
use super::*;
use app_test_support::create_fake_rollout;
use codex_app_server_client::RemoteAppServerEndpoint;
use pretty_assertions::assert_eq;
use std::os::unix::fs::PermissionsExt;

#[derive(Clone, Copy)]
enum ForkRoute {
    Slash,
    Locked,
    Agents,
}

#[tokio::test]
async fn fork_window_preserves_source_draft_and_subscription() -> Result<()> {
    check_window_fork(ForkRoute::Slash, Some(0), AppServerTarget::Embedded).await
}

#[tokio::test]
async fn fork_window_failure_leaves_saved_child_and_source_intact() -> Result<()> {
    check_window_fork(ForkRoute::Slash, Some(7), AppServerTarget::Embedded).await
}

#[tokio::test]
async fn fork_window_missing_helper_preserves_portable_in_place_behavior() -> Result<()> {
    check_window_fork(ForkRoute::Slash, None, AppServerTarget::Embedded).await
}

#[tokio::test]
async fn fork_window_locked_shortcut_still_attaches_in_place() -> Result<()> {
    check_window_fork(ForkRoute::Locked, Some(0), AppServerTarget::Embedded).await
}

#[tokio::test]
async fn fork_window_agents_shortcut_still_attaches_in_place() -> Result<()> {
    check_window_fork(ForkRoute::Agents, Some(0), AppServerTarget::Embedded).await
}

#[tokio::test]
async fn fork_window_retains_local_daemon_endpoint() -> Result<()> {
    check_window_fork(
        ForkRoute::Slash,
        Some(0),
        AppServerTarget::LocalDaemon {
            endpoint: RemoteAppServerEndpoint::UnixSocket {
                socket_path: test_path_buf("/tmp/codex-fork-test.sock").abs(),
            },
            allow_embedded_fallback: false,
        },
    )
    .await
}

#[tokio::test]
async fn fork_window_keeps_remote_credentials_out_of_arguments() -> Result<()> {
    check_window_fork(
        ForkRoute::Slash,
        Some(0),
        AppServerTarget::Remote {
            endpoint: RemoteAppServerEndpoint::WebSocket {
                websocket_url: "ws://127.0.0.1:45123".into(),
                auth_token: Some("fork-test-token".into()),
            },
        },
    )
    .await
}

async fn check_window_fork(
    route: ForkRoute,
    launcher_status: Option<i32>,
    target: AppServerTarget,
) -> Result<()> {
    let (mut app, mut events, _operations) = Box::pin(make_test_app_with_channels()).await;
    let home = tempdir()?;
    app.config.codex_home = home.path().to_path_buf().abs();
    app.config.sqlite = codex_state::SqliteConfig::new_for_testing(home.path().abs());
    app.app_server_target = target.clone();
    if let Some(status) = launcher_status {
        let bin = home.path().join("bin");
        std::fs::create_dir(&bin)?;
        let launcher = bin.join("codex-fork-window");
        std::fs::write(
            &launcher,
            format!(
                "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CODEX_HOME/launcher-args\"\nprintf '%s' \"$CODEX_FORK_WINDOW_AUTH_TOKEN\" > \"$CODEX_HOME/launcher-token\"\nexit {status}\n"
            ),
        )?;
        std::fs::set_permissions(&launcher, std::fs::Permissions::from_mode(0o755))?;
    }
    let source_id = ThreadId::from_string(
        &create_fake_rollout(
            home.path(),
            "2026-01-01T00-00-00",
            "2026-01-01T00:00:00Z",
            "Saved source message",
            Some(app.config.model_provider_id.as_str()),
            /*git_info*/ None,
        )
        .expect("create source rollout"),
    )?;
    let (mut server, requests, proxy) = Box::pin(start_recording_app_server(
        &app.config,
        /*blocked_thread_list*/ None,
        /*failed_thread_name*/ None,
    ))
    .await?;
    let source = server
        .resume_thread(
            &app.local_settings,
            app.config.clone(),
            source_id,
            app.resume_model_settings(),
        )
        .await?;
    app.enqueue_primary_thread_session(source.session, source.turns)
        .await?;
    app.thread_event_listener_tasks
        .insert(source_id, tokio::spawn(std::future::pending::<()>()));
    app.chat_widget.insert_str("Unsent source draft");
    if matches!(route, ForkRoute::Locked) {
        app.chat_widget.show_external_writer_thread();
    }
    let input = app.chat_widget.capture_thread_input_state();
    requests.lock().expect("request recorder lock").clear();
    while events.try_recv().is_ok() {}
    let mut tui = crate::tui::test_support::make_test_tui()?;
    let event = if matches!(route, ForkRoute::Agents) {
        AppEvent::ForkAgentsOverviewThreadReady {
            thread_id: source_id,
        }
    } else {
        AppEvent::ForkCurrentSession { name: None }
    };
    let control = Box::pin(app.handle_event(&mut tui, &mut server, event)).await?;
    assert!(matches!(control, AppRunControl::Continue));
    assert!(!app.chat_widget.fork_in_progress);
    let fork_requests = recorded_params(&requests, "thread/fork");
    assert_eq!(fork_requests.len(), 1);
    if matches!(route, ForkRoute::Slash)
        && launcher_status.is_some()
        && matches!(target, AppServerTarget::Embedded)
    {
        assert_eq!(fork_requests[0]["config"]["thread_unload_delay_secs"], 0);
        assert_eq!(app.config.thread_unload_delay, Duration::from_secs(60));
    } else {
        assert!(
            fork_requests[0]["config"]
                .get("thread_unload_delay_secs")
                .is_none()
        );
    }
    assert!(recorded_params(&requests, "turn/interrupt").is_empty());
    assert!(!app.thread_event_listener_tasks[&source_id].is_finished());

    let args_path = home.path().join("launcher-args");
    if matches!(route, ForkRoute::Slash) && launcher_status.is_some() {
        assert_eq!(app.chat_widget.thread_id(), Some(source_id));
        assert_eq!(app.chat_widget.capture_thread_input_state(), input);
        assert!(app.thread_event_channels.contains_key(&source_id));
        let args_text = std::fs::read_to_string(args_path)?;
        let args: Vec<_> = args_text.lines().collect();
        assert_eq!(args[0], "--resume");
        let child_id = ThreadId::from_string(args[1])?;
        assert_ne!(child_id, source_id);
        assert_eq!(args[2], "--workdir");
        assert!(std::path::Path::new(args[3]).is_dir());
        let embedded = matches!(target, AppServerTarget::Embedded);
        let expected_backend: Vec<String> = match target {
            AppServerTarget::Embedded => vec!["--no-daemon".into()],
            AppServerTarget::LocalDaemon { endpoint, .. }
            | AppServerTarget::Remote { endpoint } => match endpoint {
                RemoteAppServerEndpoint::UnixSocket { socket_path } => vec![
                    "--remote".into(),
                    format!("unix://{}", socket_path.display()),
                ],
                RemoteAppServerEndpoint::WebSocket { websocket_url, .. } => vec![
                    "--remote".into(),
                    websocket_url,
                    "--remote-auth-token-env".into(),
                    "CODEX_FORK_WINDOW_AUTH_TOKEN".into(),
                ],
            },
        };
        assert_eq!(&args[4..], expected_backend);
        assert!(!args_text.contains("fork-test-token"));
        if matches!(app.app_server_target, AppServerTarget::Remote { .. }) {
            assert_eq!(
                std::fs::read_to_string(home.path().join("launcher-token"))?,
                "fork-test-token"
            );
        }
        assert_eq!(
            recorded_params(&requests, "thread/unsubscribe"),
            vec![serde_json::json!({"threadId": child_id.to_string()})]
        );
        if embedded {
            // A separate server must take the child immediately while this server
            // stays alive and keeps the original chat's writer.
            let mut child_server = crate::start_embedded_app_server_for_picker(&app.config).await?;
            let resumed = child_server
                .resume_thread(
                    &app.local_settings,
                    app.config.clone(),
                    child_id,
                    app.resume_model_settings(),
                )
                .await?;
            assert_eq!(resumed.session.thread_id, child_id);
            child_server.shutdown().await?;
        }
        let child = server
            .thread_read(child_id, /*include_turns*/ false)
            .await?;
        assert_eq!(child.forked_from_id, Some(source_id.to_string()));
        let messages = std::iter::from_fn(|| events.try_recv().ok())
            .filter_map(|event| match event {
                AppEvent::InsertHistoryCell(cell) => {
                    Some(lines_to_single_string(&cell.display_lines(/*width*/ 300)))
                }
                _ => None,
            })
            .collect::<Vec<_>>()
            .join("\n");
        assert!(messages.contains(&child_id.to_string()), "{messages}");
        assert!(messages.contains("original chat"), "{messages}");
        assert!(
            messages.contains(if launcher_status == Some(0) {
                "opened in a new Terminal window"
            } else {
                "window could not open"
            }),
            "{messages}"
        );
    } else {
        assert_ne!(app.chat_widget.thread_id(), Some(source_id));
        assert!(!args_path.exists());
        assert!(recorded_params(&requests, "thread/unsubscribe").is_empty());
    }
    app.discard_thread_local_state(source_id).await;
    server.shutdown().await?;
    proxy.await??;
    Ok(())
}
