//! Linux permission choices apply directly; managed limits and reviewer choices remain intact.

use super::*;
use crate::permission_discovery::PermissionDiscovery;
use pretty_assertions::assert_eq;

#[tokio::test]
async fn full_access_catalog_selection_has_no_extra_warning_dialog() {
    let (mut chat, mut events, _ops) = make_chatwidget_manual(None).await;
    chat.thread_id = Some(ThreadId::new());
    let discovery = PermissionDiscovery::local(&chat.config);
    let preset = builtin_approval_presets()
        .into_iter()
        .find(|preset| preset.id == "full-access")
        .unwrap();
    let item = chat.builtin_permission_mode_selection_item(
        &discovery,
        &preset,
        ":danger-full-access",
        String::new(),
        AskForApproval::Never,
        ApprovalsReviewer::User,
    );
    assert!(item.disabled_reason.is_none());
    item.actions[0](&chat.app_event_tx);
    match events.try_recv().unwrap() {
        AppEvent::SelectPermissionProfile(selection) => {
            assert_eq!(selection.profile_id, ":danger-full-access");
            assert_eq!(selection.approval_policy, Some(AskForApproval::Never));
            assert_eq!(selection.approvals_reviewer, Some(ApprovalsReviewer::User));
        }
        event => panic!("expected immediate selection, got {event:?}"),
    }
    assert!(events.try_recv().is_err());
    assert!(chat.no_modal_or_popup_active());
}

#[tokio::test]
async fn full_access_legacy_selection_emits_one_update_without_warning() {
    let (mut chat, mut events, _ops) = make_chatwidget_manual(None).await;
    chat.set_feature_enabled(Feature::GuardianApproval, false);
    chat.open_legacy_permissions_popup();
    chat.handle_key_event(KeyCode::Down.into());
    chat.handle_key_event(KeyCode::Enter.into());
    let mut history_updates = 0;
    while let Ok(event) = events.try_recv() {
        match event {
            AppEvent::OpenFullAccessConfirmation { .. } => panic!("unexpected extra warning"),
            AppEvent::UpdateAskForApprovalPolicy(policy) => {
                assert_eq!(policy, AskForApproval::Never)
            }
            AppEvent::UpdateApprovalsReviewer(reviewer) => {
                assert_eq!(reviewer, ApprovalsReviewer::User)
            }
            AppEvent::InsertHistoryCell(cell) => {
                history_updates += 1;
                assert!(
                    lines_to_single_string(&cell.display_lines(80))
                        .contains("Permissions updated to Full Access")
                );
            }
            _ => {}
        }
    }
    assert_eq!(history_updates, 1);
    assert!(chat.no_modal_or_popup_active());
}

#[tokio::test]
async fn full_access_direct_warning_route_applies_selection_without_dialog() {
    let (mut chat, mut events, _ops) = make_chatwidget_manual(None).await;
    chat.thread_id = Some(ThreadId::new());
    let preset = builtin_approval_presets()
        .into_iter()
        .find(|preset| preset.id == "full-access")
        .unwrap();
    chat.open_full_access_confirmation(
        preset,
        true,
        Some(PermissionProfileSelection {
            profile_id: ":danger-full-access".into(),
            approval_policy: Some(AskForApproval::Never),
            approvals_reviewer: Some(ApprovalsReviewer::User),
            display_label: "Full Access".into(),
        }),
    );
    assert!(matches!(
        events.try_recv(),
        Ok(AppEvent::SelectPermissionProfile(_))
    ));
    assert!(events.try_recv().is_err());
    assert!(chat.no_modal_or_popup_active());
}

#[tokio::test]
async fn full_access_inline_selection_preserves_explicit_reviewer_without_warning() {
    let (mut chat, mut events, _ops) = make_chatwidget_manual(None).await;
    chat.thread_id = Some(ThreadId::new());
    chat.set_feature_enabled(Feature::GuardianApproval, true);
    chat.permission_discovery = Some(PermissionDiscovery::local(&chat.config));
    chat.select_permission_profile(":danger-full-access --reviewer=auto_review");
    match events.try_recv().unwrap() {
        AppEvent::SelectPermissionProfile(selection) => {
            assert_eq!(selection.profile_id, ":danger-full-access");
            assert_eq!(selection.approval_policy, Some(AskForApproval::Never));
            assert_eq!(
                selection.approvals_reviewer,
                Some(ApprovalsReviewer::AutoReview)
            );
        }
        event => panic!("expected the requested reviewer unchanged, got {event:?}"),
    }
    assert!(matches!(
        events.try_recv(),
        Ok(AppEvent::SettingsSelectionClosed)
    ));
    assert!(events.try_recv().is_err());
    assert!(chat.no_modal_or_popup_active());
}

#[tokio::test]
async fn full_access_managed_deny_remains_blocked_without_warning_dialog() {
    let (mut chat, mut events, _ops) = make_chatwidget_manual(None).await;
    chat.thread_id = Some(ThreadId::new());
    let mut discovery = PermissionDiscovery::local(&chat.config);
    discovery
        .profiles
        .iter_mut()
        .find(|profile| profile.id == ":danger-full-access")
        .unwrap()
        .allowed = false;
    assert!(!chat.try_select_permission_profile(&discovery, ":danger-full-access", None, None));
    while let Ok(event) = events.try_recv() {
        assert!(!matches!(
            event,
            AppEvent::SelectPermissionProfile(_) | AppEvent::OpenFullAccessConfirmation { .. }
        ));
    }
    assert!(chat.no_modal_or_popup_active());
}
