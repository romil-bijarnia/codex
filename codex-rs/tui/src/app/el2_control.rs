//! Explicit, thread-bound activation of the existing EL2 interface.

use super::*;
use crate::permission_discovery::PermissionDiscovery;

impl App {
    pub(super) fn activate_el2_control(
        &mut self,
        app_server: &AppServerSession,
        thread_id: ThreadId,
        request_id: uuid::Uuid,
        discovery: Option<PermissionDiscovery>,
    ) {
        if self.chat_widget.thread_id() != Some(thread_id)
            || self.reject_pending_permission_change()
        {
            self.chat_widget.el2_control.cancel(thread_id, request_id);
            return;
        }
        if let Some(reason) = self
            .chat_widget
            .el2_control_disabled_reason(discovery.as_ref())
        {
            self.chat_widget.el2_control.cancel(thread_id, request_id);
            self.chat_widget
                .add_error_message(format!("EL2 Control is unavailable: {reason}"));
            return;
        }
        if !self.chat_widget.el2_control.start(thread_id, request_id) {
            return;
        }
        let handle = app_server.request_handle();
        let cwd = self.chat_widget.config_ref().cwd.to_path_buf();
        let sender = self.app_event_tx.clone();
        tokio::spawn(async move {
            let result = crate::el2_control::activate(handle, cwd).await;
            sender.send(AppEvent::El2ControlActivated {
                thread_id,
                request_id,
                result,
            });
        });
    }

    pub(super) async fn on_el2_control_activated(
        &mut self,
        app_server: &mut AppServerSession,
        thread_id: ThreadId,
        request_id: uuid::Uuid,
        result: Result<crate::el2_control::Activation, String>,
    ) {
        if self.chat_widget.thread_id() != Some(thread_id)
            || !self.chat_widget.el2_control.finish(thread_id, request_id)
        {
            return;
        }
        if let Err(error) = result {
            self.chat_widget
                .add_error_message(format!("Could not enable EL2 Control: {error}"));
            return;
        }
        // Use the same built-in selection as stock Full Access, not a new profile.
        let selection = PermissionProfileSelection {
            profile_id: codex_protocol::models::BUILT_IN_PERMISSION_PROFILE_DANGER_FULL_ACCESS
                .to_string(),
            approval_policy: Some(AskForApproval::Never),
            approvals_reviewer: Some(ApprovalsReviewer::User),
            display_label: crate::el2_control::LABEL.to_string(),
        };
        if self.select_permission_profile(app_server, selection).await
            && self.chat_widget.thread_id() == Some(thread_id)
        {
            self.chat_widget.el2_control.mark_verified(thread_id);
            self.chat_widget.add_info_message(
                "Existing EL2 interface verified; Full Access selected for this session."
                    .to_string(),
                /*hint*/ None,
            );
        }
    }
}
