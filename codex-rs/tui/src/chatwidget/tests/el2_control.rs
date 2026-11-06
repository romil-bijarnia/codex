use super::*;
use crate::permission_discovery::PermissionDiscovery;
use codex_protocol::models::ActivePermissionProfile;

#[tokio::test]
async fn picker_requests_real_activation_on_its_original_thread() {
    let (mut chat, mut events, _ops) = make_chatwidget_manual(None).await;
    let thread_id = ThreadId::new();
    chat.thread_id = Some(thread_id);
    let item = chat.el2_control_selection_item(None).unwrap();
    assert_eq!(item.name, "EL2 Control");
    assert!(!item.is_current);
    item.actions[0](&chat.app_event_tx);
    assert!(
        matches!(events.try_recv(), Ok(AppEvent::ActivateEl2Control {
        thread_id: Some(id), quiet: false,
    }) if id == thread_id)
    );
}

#[tokio::test]
async fn inline_alias_requests_activation_instead_of_an_unknown_server_profile() {
    let (mut chat, mut events, _ops) = make_chatwidget_manual(None).await;
    chat.thread_id = Some(ThreadId::new());
    let discovery = PermissionDiscovery::local(&chat.config);
    assert!(chat.try_select_permission_profile(&discovery, "el2-control", None, None));
    assert!(matches!(
        events.try_recv(),
        Ok(AppEvent::ActivateEl2Control { quiet: false, .. })
    ));
}

#[tokio::test]
async fn stale_activation_cannot_override_a_later_permission_choice() {
    let (mut chat, _events, _ops) = make_chatwidget_manual(None).await;
    let request_id = chat.begin_el2_control_activation().unwrap();
    chat.clear_el2_control_selection();
    assert!(!chat.finish_el2_control_activation(request_id));
    assert!(!chat.el2_control_is_active());
}

#[tokio::test]
async fn active_label_requires_both_activation_and_full_access() {
    let (mut chat, _events, _ops) = make_chatwidget_manual(None).await;
    chat.set_permission_profile_from_session_snapshot(PermissionProfileSnapshot::active(
        PermissionProfile::workspace_write(),
        ActivePermissionProfile::new(":workspace"),
    ))
    .unwrap();
    chat.el2_control_enabled = true;
    assert!(!chat.el2_control_is_active());
    chat.set_permission_profile_from_session_snapshot(PermissionProfileSnapshot::active(
        PermissionProfile::Disabled,
        ActivePermissionProfile::new(":danger-full-access"),
    ))
    .unwrap();
    assert!(chat.el2_control_is_active());
}
