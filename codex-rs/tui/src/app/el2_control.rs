//! Selecting EL2 performs activation on the active app server before adopting the mode.

use super::*;

impl App {
    pub(super) fn activate_el2_control(
        &mut self,
        app_server: &AppServerSession,
        thread_id: Option<ThreadId>,
        quiet: bool,
    ) {
        if thread_id.is_none() || self.chat_widget.thread_id() != thread_id {
            self.chat_widget.add_error_message(
                "Wait for this task to connect before enabling EL2 Control.".into(),
            );
            return;
        }
        if self.reject_pending_permission_change() {
            return;
        }
        let Some(request_id) = self.chat_widget.begin_el2_control_activation() else {
            return;
        };
        let handle = app_server.request_handle();
        let cwd = self.chat_widget.config_ref().cwd.to_path_buf();
        let sender = self.app_event_tx.clone();
        tokio::spawn(async move {
            let result = crate::el2_control::activate(handle, cwd).await;
            sender.send(AppEvent::El2ControlActivated {
                thread_id,
                request_id,
                quiet,
                result,
            });
        });
    }

    pub(super) async fn on_el2_control_activated(
        &mut self,
        app_server: &mut AppServerSession,
        thread_id: Option<ThreadId>,
        request_id: uuid::Uuid,
        quiet: bool,
        result: Result<crate::el2_control::Activation, String>,
    ) {
        if self.chat_widget.thread_id() != thread_id
            || !self.chat_widget.finish_el2_control_activation(request_id)
        {
            return;
        }
        if self
            .chat_widget
            .complete_inline_permission_profile_selection()
        {
            self.app_event_tx.send(AppEvent::SettingsSelectionClosed);
        }
        let activation = match result {
            Ok(activation) => activation,
            Err(error) => {
                self.chat_widget
                    .add_error_message(format!("Could not enable EL2 Control: {error}"));
                return;
            }
        };
        if quiet {
            if self
                .chat_widget
                .config_ref()
                .permissions
                .effective_permission_profile()
                == PermissionProfile::Disabled
            {
                self.chat_widget.enable_el2_control_selection(&activation);
            }
            return;
        }
        let selection = PermissionProfileSelection {
            profile_id: codex_protocol::models::BUILT_IN_PERMISSION_PROFILE_DANGER_FULL_ACCESS
                .to_string(),
            approval_policy: Some(AskForApproval::Never),
            approvals_reviewer: Some(ApprovalsReviewer::AutoReview),
            display_label: crate::el2_control::LABEL.to_string(),
        };
        if self.select_permission_profile(app_server, selection).await {
            self.chat_widget.enable_el2_control_selection(&activation);
            let message = if self.chat_widget.el2_control_is_active() {
                format!("EL2 Control enabled on {}", activation.kernel_release)
            } else {
                "EL2 interface verified; waiting for permissions to update.".to_string()
            };
            self.chat_widget.add_info_message(message, None);
        }
    }
}
