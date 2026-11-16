//! EL2 completion uses the stock settings API only after successful verification.

use super::*;
use pretty_assertions::assert_eq;

#[tokio::test]
async fn el2_failure_keeps_permissions_and_configuration_unchanged() -> Result<()> {
    let (mut app, _events, _ops) = make_test_app_with_channels().await;
    let mut server = start_config_write_test_app_server(&app).await?;
    let started = server.start_thread(&app.config).await?;
    let thread_id = started.session.thread_id;
    app.enqueue_primary_thread_session(started.session, started.turns)
        .await?;
    let config_path = app.config.codex_home.join("config.toml");
    let before_file = std::fs::read(&config_path).ok();
    let before = (
        app.chat_widget
            .config_ref()
            .permissions
            .permission_profile()
            .clone(),
        AskForApproval::from(
            app.chat_widget
                .config_ref()
                .permissions
                .approval_policy
                .value(),
        ),
        app.chat_widget.config_ref().approvals_reviewer,
    );
    let request_id = app.chat_widget.el2_control.confirm(thread_id).unwrap();
    assert!(app.chat_widget.el2_control.start(thread_id, request_id));
    app.on_el2_control_activated(
        &mut server,
        thread_id,
        request_id,
        Err("fixture unavailable".into()),
    )
    .await;
    assert_eq!(
        (
            app.chat_widget
                .config_ref()
                .permissions
                .permission_profile()
                .clone(),
            AskForApproval::from(
                app.chat_widget
                    .config_ref()
                    .permissions
                    .approval_policy
                    .value()
            ),
            app.chat_widget.config_ref().approvals_reviewer,
        ),
        before,
    );
    assert_eq!(std::fs::read(config_path).ok(), before_file);
    assert!(!app.chat_widget.el2_control.is_verified(thread_id));
    assert!(
        !app.config
            .codex_home
            .join("state/owner-el2-selection")
            .exists()
    );
    server.shutdown().await?;
    Ok(())
}

#[tokio::test]
async fn el2_success_uses_stock_full_access_and_duplicate_completion_is_inert() -> Result<()> {
    let (mut app, _events, _ops) = make_test_app_with_channels().await;
    let mut server = start_config_write_test_app_server(&app).await?;
    let started = server.start_thread(&app.config).await?;
    let thread_id = started.session.thread_id;
    app.enqueue_primary_thread_session(started.session, started.turns)
        .await?;
    let config_path = app.config.codex_home.join("config.toml");
    let before_file = std::fs::read(&config_path).ok();
    let request_id = app.chat_widget.el2_control.confirm(thread_id).unwrap();
    assert!(app.chat_widget.el2_control.start(thread_id, request_id));
    let activation = crate::el2_control::Activation {
        exception_level: 2,
        native_execution_verified: true,
    };
    app.on_el2_control_activated(&mut server, thread_id, request_id, Ok(activation.clone()))
        .await;
    assert!(app.chat_widget.el2_control.is_verified(thread_id));
    if let Some(selection) = app
        .agents_overview
        .requested_permission_profiles
        .get(&thread_id)
    {
        assert_eq!(selection.profile_id, ":danger-full-access");
        assert_eq!(selection.approval_policy, Some(AskForApproval::Never));
        assert_eq!(selection.approvals_reviewer, Some(ApprovalsReviewer::User));
    } else {
        assert_eq!(
            app.chat_widget
                .config_ref()
                .permissions
                .active_permission_profile()
                .unwrap()
                .id,
            ":danger-full-access"
        );
        assert_eq!(
            app.chat_widget
                .config_ref()
                .permissions
                .approval_policy
                .value(),
            AskForApproval::Never.to_core()
        );
        assert_eq!(
            app.chat_widget.config_ref().approvals_reviewer,
            ApprovalsReviewer::User
        );
    }
    app.on_el2_control_activated(&mut server, thread_id, request_id, Ok(activation))
        .await;
    assert_eq!(std::fs::read(config_path).ok(), before_file);
    assert!(
        !app.config
            .codex_home
            .join("state/owner-el2-selection")
            .exists()
    );
    server.shutdown().await?;
    Ok(())
}

#[tokio::test]
async fn ordinary_permission_choice_invalidates_pending_el2_completion() -> Result<()> {
    let (mut app, _events, _ops) = make_test_app_with_channels().await;
    let mut server = start_config_write_test_app_server(&app).await?;
    let started = server.start_thread(&app.config).await?;
    let thread_id = started.session.thread_id;
    app.enqueue_primary_thread_session(started.session, started.turns)
        .await?;
    let request_id = app.chat_widget.el2_control.confirm(thread_id).unwrap();
    assert!(app.chat_widget.el2_control.start(thread_id, request_id));
    let config = app.chat_widget.config_ref();
    let selection = PermissionProfileSelection {
        profile_id: config.permissions.active_permission_profile().unwrap().id,
        approval_policy: Some(config.permissions.approval_policy.value().into()),
        approvals_reviewer: Some(config.approvals_reviewer),
        display_label: "Current permissions".to_string(),
    };
    let before = selection.clone();
    let mut tui = crate::tui::test_support::make_test_tui()?;
    app.handle_event(
        &mut tui,
        &mut server,
        AppEvent::SelectPermissionProfile(selection),
    )
    .await?;
    app.on_el2_control_activated(
        &mut server,
        thread_id,
        request_id,
        Ok(crate::el2_control::Activation {
            exception_level: 2,
            native_execution_verified: true,
        }),
    )
    .await;
    assert!(!app.chat_widget.el2_control.is_verified(thread_id));
    assert_eq!(
        app.chat_widget
            .config_ref()
            .permissions
            .active_permission_profile()
            .unwrap()
            .id,
        before.profile_id
    );
    assert_eq!(
        app.chat_widget.config_ref().approvals_reviewer,
        before.approvals_reviewer.unwrap()
    );
    server.shutdown().await?;
    Ok(())
}
