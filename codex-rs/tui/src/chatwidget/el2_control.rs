//! The EL2 row verifies the existing bridge, then uses ordinary Full Access.

use super::*;
use crate::permission_discovery::PermissionDiscovery;

impl ChatWidget {
    pub(crate) fn el2_control_is_active(&self) -> bool {
        self.thread_id
            .is_some_and(|thread| self.el2_control.is_verified(thread))
            && self.config.permissions.effective_permission_profile() == PermissionProfile::Disabled
            && AskForApproval::from(self.config.permissions.approval_policy.value())
                == AskForApproval::Never
            && self.config.approvals_reviewer == ApprovalsReviewer::User
    }

    pub(crate) fn el2_control_disabled_reason(
        &self,
        discovery: Option<&PermissionDiscovery>,
    ) -> Option<String> {
        if !cfg!(target_os = "linux") {
            return Some("The EL2 interface is available only on Linux.".to_string());
        }
        if self.thread_id.is_none() {
            return Some("Wait for this task to connect.".to_string());
        }
        self.config
            .permissions
            .can_set_permission_profile(&PermissionProfile::Disabled)
            .err()
            .map(|error| error.to_string())
            .or_else(|| {
                self.config
                    .permissions
                    .approval_policy
                    .can_set(&AskForApproval::Never.to_core())
                    .err()
                    .map(|error| error.to_string())
            })
            .or_else(|| {
                self.config
                    .config_layer_stack
                    .requirements()
                    .approvals_reviewer
                    .can_set(&ApprovalsReviewer::User)
                    .err()
                    .map(|error| error.to_string())
            })
            .or_else(|| {
                discovery.and_then(|discovery| {
                    discovery.disabled_reason(
                        codex_protocol::models::BUILT_IN_PERMISSION_PROFILE_DANGER_FULL_ACCESS,
                        Some(AskForApproval::Never),
                        Some(ApprovalsReviewer::User.into()),
                    )
                })
            })
    }

    pub(super) fn el2_control_selection_item(
        &self,
        discovery: Option<&PermissionDiscovery>,
    ) -> Option<SelectionItem> {
        if !cfg!(target_os = "linux") {
            return None;
        }
        let thread_id = self.thread_id;
        let discovery = discovery.cloned();
        let disabled_reason = self.el2_control_disabled_reason(discovery.as_ref());
        Some(SelectionItem {
            name: crate::el2_control::LABEL.to_string(),
            description: Some("Verify the existing EL2 interface and use Full Access".into()),
            is_current: self.el2_control_is_active(),
            disabled_reason,
            actions: vec![Box::new(move |tx| {
                tx.send(AppEvent::ConfirmEl2Control {
                    thread_id,
                    discovery: discovery.clone(),
                });
            })],
            dismiss_on_select: true,
            ..Default::default()
        })
    }

    pub(crate) fn open_el2_control_confirmation(
        &mut self,
        thread_id: Option<ThreadId>,
        discovery: Option<PermissionDiscovery>,
    ) {
        if self.thread_id != thread_id {
            return;
        }
        if let Some(reason) = self.el2_control_disabled_reason(discovery.as_ref()) {
            self.add_error_message(format!("EL2 Control is unavailable: {reason}"));
            return;
        }
        let Some(thread_id) = thread_id else {
            return;
        };
        let Some(request_id) = self.el2_control.confirm(thread_id) else {
            return;
        };
        let accept_actions: Vec<SelectionAction> = vec![Box::new(move |tx| {
            tx.send(AppEvent::ActivateEl2Control {
                thread_id,
                request_id,
                discovery: discovery.clone(),
            });
        })];
        let cancel = move |tx: &AppEventSender| {
            tx.send(AppEvent::CancelEl2Control {
                thread_id,
                request_id,
            });
        };
        self.open_full_access_confirmation_with_actions(
            /*return_to_permissions*/ true,
            accept_actions,
            vec![Box::new(cancel)],
            Some(Box::new(cancel)),
        );
    }

    pub(crate) fn clear_el2_control_selection(&mut self) {
        self.el2_control.clear();
    }
}
