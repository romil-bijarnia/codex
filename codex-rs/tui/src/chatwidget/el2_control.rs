//! EL2 is a selected local capability, layered over the server's Full Access profile.

use super::*;

impl ChatWidget {
    pub(crate) fn el2_control_is_active(&self) -> bool {
        self.el2_control_enabled
            && self.config.permissions.effective_permission_profile() == PermissionProfile::Disabled
    }

    pub(super) fn el2_control_selection_item(
        &self,
        disabled_reason: Option<String>,
    ) -> Option<SelectionItem> {
        if !cfg!(target_os = "linux") {
            return None;
        }
        let thread_id = self.thread_id;
        Some(SelectionItem {
            name: crate::el2_control::LABEL.to_string(),
            description: Some("Enable kernel and CPU control on this Linux computer".into()),
            is_current: self.el2_control_is_active(),
            disabled_reason: disabled_reason.or_else(|| {
                thread_id
                    .is_none()
                    .then(|| "Wait for this task to connect.".to_string())
            }),
            actions: vec![Box::new(move |tx| {
                tx.send(AppEvent::ActivateEl2Control {
                    thread_id,
                    quiet: false,
                });
            })],
            dismiss_on_select: true,
            ..Default::default()
        })
    }

    pub(crate) fn clear_el2_control_selection(&mut self) {
        self.el2_control_enabled = false;
        self.el2_control_request_id = None;
        if let Some(thread_id) = self.thread_id {
            crate::el2_control::clear(&self.config.codex_home, thread_id);
        }
        self.refresh_status_surfaces();
    }

    pub(crate) fn begin_el2_control_activation(&mut self) -> Option<uuid::Uuid> {
        if self.el2_control_request_id.is_some() {
            return None;
        }
        let request_id = uuid::Uuid::new_v4();
        self.el2_control_request_id = Some(request_id);
        Some(request_id)
    }

    pub(crate) fn finish_el2_control_activation(&mut self, request_id: uuid::Uuid) -> bool {
        if self.el2_control_request_id != Some(request_id) {
            return false;
        }
        self.el2_control_request_id = None;
        true
    }

    pub(crate) fn enable_el2_control_selection(
        &mut self,
        activation: &crate::el2_control::Activation,
    ) {
        self.el2_control_enabled = true;
        if let Some(thread_id) = self.thread_id
            && let Err(error) =
                crate::el2_control::save(&self.config.codex_home, thread_id, activation)
        {
            self.add_error_message(format!(
                "EL2 Control is available, but its selection could not be saved: {error}"
            ));
        }
        self.refresh_status_surfaces();
    }

    pub(super) fn restore_el2_control_selection(&mut self) {
        self.el2_control_enabled = self.config.permissions.effective_permission_profile()
            == PermissionProfile::Disabled
            && self
                .thread_id
                .is_some_and(|id| crate::el2_control::selected(&self.config.codex_home, id));
        self.refresh_status_surfaces();
    }
}
