//! EL2 is a confirmed, session-local action, not an additional permission profile.

use super::*;
use crate::permission_discovery::PermissionDiscovery;
use pretty_assertions::assert_eq;

#[tokio::test]
async fn el2_row_shows_stock_confirmation_before_requesting_activation() {
    let (mut chat, mut events, _ops) = make_chatwidget_manual(None).await;
    let thread_id = ThreadId::new();
    chat.thread_id = Some(thread_id);
    let before = (
        chat.config.permissions.permission_profile().clone(),
        AskForApproval::from(chat.config.permissions.approval_policy.value()),
        chat.config.approvals_reviewer,
    );
    let discovery = PermissionDiscovery::local(&chat.config);
    let item = chat.el2_control_selection_item(Some(&discovery)).unwrap();
    assert_eq!(item.name, "EL2 Control");
    assert!(item.disabled_reason.is_none());
    item.actions[0](&chat.app_event_tx);
    let (origin, discovery) = match events.try_recv().unwrap() {
        AppEvent::ConfirmEl2Control {
            thread_id,
            discovery,
        } => (thread_id, discovery),
        event => panic!("expected confirmation, got {event:?}"),
    };
    chat.open_el2_control_confirmation(origin, discovery);
    let popup = render_bottom_popup(&chat, 80);
    assert!(popup.contains("Enable full access?"));
    assert!(popup.contains("without your approval"));
    assert!(events.try_recv().is_err());
    chat.handle_key_event(KeyCode::Enter.into());
    let request_id = match events.try_recv().unwrap() {
        AppEvent::ActivateEl2Control {
            thread_id: origin,
            request_id,
            ..
        } if origin == thread_id => request_id,
        event => panic!("expected activation only after confirmation, got {event:?}"),
    };
    assert!(chat.el2_control.start(thread_id, request_id));
    assert!(!chat.el2_control.start(thread_id, request_id));
    assert!(!chat.el2_control_is_active());
    assert_eq!(
        (
            chat.config.permissions.permission_profile().clone(),
            AskForApproval::from(chat.config.permissions.approval_policy.value()),
            chat.config.approvals_reviewer,
        ),
        before,
    );
}

#[tokio::test]
async fn el2_confirmation_cancel_and_escape_never_request_activation() {
    for escape in [false, true] {
        let (mut chat, mut events, _ops) = make_chatwidget_manual(None).await;
        let thread_id = ThreadId::new();
        chat.thread_id = Some(thread_id);
        chat.open_el2_control_confirmation(
            Some(thread_id),
            Some(PermissionDiscovery::local(&chat.config)),
        );
        if escape {
            chat.handle_key_event(KeyCode::Esc.into());
        } else {
            chat.handle_key_event(KeyCode::Down.into());
            chat.handle_key_event(KeyCode::Enter.into());
        }
        let request_id = match events.try_recv().unwrap() {
            AppEvent::CancelEl2Control {
                thread_id: origin,
                request_id,
            } if origin == thread_id => request_id,
            event => panic!("expected cancellation, got {event:?}"),
        };
        chat.el2_control.cancel(thread_id, request_id);
        while let Ok(event) = events.try_recv() {
            assert!(matches!(event, AppEvent::OpenPermissionsPopup));
        }
        assert!(chat.el2_control.confirm(thread_id).is_some());
        assert!(!chat.el2_control_is_active());
    }
}

#[tokio::test]
async fn el2_row_respects_catalog_profile_and_reviewer_restrictions() {
    for reviewer_restricted in [false, true] {
        let (mut chat, mut events, _ops) = make_chatwidget_manual(None).await;
        let thread_id = ThreadId::new();
        chat.thread_id = Some(thread_id);
        let mut discovery = PermissionDiscovery::local(&chat.config);
        if reviewer_restricted {
            discovery.requirements = Some(
                serde_json::from_value(serde_json::json!({
                    "allowedApprovalsReviewers": ["auto_review"],
                }))
                .unwrap(),
            );
        } else {
            discovery
                .profiles
                .iter_mut()
                .find(|profile| profile.id == ":danger-full-access")
                .unwrap()
                .allowed = false;
        }
        assert!(
            chat.el2_control_selection_item(Some(&discovery))
                .unwrap()
                .disabled_reason
                .is_some()
        );
        chat.open_el2_control_confirmation(Some(thread_id), Some(discovery));
        while let Ok(event) = events.try_recv() {
            assert!(!matches!(
                event,
                AppEvent::ActivateEl2Control { .. } | AppEvent::SelectPermissionProfile(_)
            ));
        }
        assert!(chat.el2_control.confirm(thread_id).is_some());
        assert!(!chat.el2_control_is_active());
    }
}

#[tokio::test]
async fn el2_row_is_disabled_before_connection_and_stale_confirmation_is_ignored() {
    let (mut chat, mut events, _ops) = make_chatwidget_manual(None).await;
    assert!(
        chat.el2_control_selection_item(None)
            .unwrap()
            .disabled_reason
            .is_some()
    );
    chat.thread_id = Some(ThreadId::new());
    chat.open_el2_control_confirmation(Some(ThreadId::new()), None);
    assert!(events.try_recv().is_err());
    assert!(!chat.el2_control_is_active());
}

#[tokio::test]
async fn el2_selected_label_requires_verification_and_stock_full_access() {
    let (mut chat, _events, _ops) = make_chatwidget_manual(None).await;
    let thread_id = ThreadId::new();
    chat.thread_id = Some(thread_id);
    chat.set_permission_profile_from_session_snapshot(PermissionProfileSnapshot::active(
        PermissionProfile::workspace_write(),
        codex_protocol::models::ActivePermissionProfile::new(":workspace"),
    ))
    .unwrap();
    chat.el2_control.mark_verified(thread_id);
    assert!(!chat.el2_control_is_active());
    chat.set_permission_profile_from_session_snapshot(PermissionProfileSnapshot::active(
        PermissionProfile::Disabled,
        codex_protocol::models::ActivePermissionProfile::new(":danger-full-access"),
    ))
    .unwrap();
    chat.set_approval_policy(AskForApproval::Never);
    chat.set_approvals_reviewer(ApprovalsReviewer::User);
    assert!(chat.el2_control_is_active());
    let discovery = PermissionDiscovery::local(&chat.config);
    let full_access = builtin_approval_presets()
        .into_iter()
        .find(|preset| preset.id == "full-access")
        .unwrap();
    assert!(
        !chat
            .builtin_permission_mode_selection_item(
                &discovery,
                &full_access,
                ":danger-full-access",
                String::new(),
                AskForApproval::Never,
                ApprovalsReviewer::User,
            )
            .is_current
    );
    assert!(
        chat.el2_control_selection_item(Some(&discovery))
            .unwrap()
            .is_current
    );
    let request_id = chat.el2_control.confirm(thread_id).unwrap();
    assert!(chat.el2_control.start(thread_id, request_id));
    chat.clear_el2_control_selection();
    assert!(!chat.el2_control.finish(thread_id, request_id));
    assert!(!chat.el2_control_is_active());
}

#[tokio::test]
async fn el2_action_is_present_in_both_permissions_menus_without_reordering_stock_rows() {
    let (mut chat, _events, _ops) = make_chatwidget_manual(None).await;
    chat.thread_id = Some(ThreadId::new());
    chat.open_legacy_permissions_popup();
    assert!(render_bottom_popup(&chat, 100).contains("EL2 Control"));
    chat.open_permission_profiles_popup(PermissionDiscovery::local(&chat.config));
    let popup = render_bottom_popup(&chat, 100);
    assert!(popup.contains("EL2 Control"));
    let full_access = popup
        .find("Full Access")
        .or_else(|| popup.find("Full access"))
        .unwrap();
    assert!(full_access < popup.find("EL2 Control").unwrap());
}
