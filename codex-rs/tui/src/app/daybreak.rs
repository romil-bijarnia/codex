//! Select the existing Daybreak Blue model at Extra high for this thread only.
//!
//! Normal launch defaults remain unchanged. Turning Daybreak off explicitly
//! returns this thread to GPT-6.1-Sol at Extra high, without a side-file stash.

use super::*;
use codex_protocol::openai_models::ReasoningEffort;
use codex_protocol::turn_input::CyberAccessProgram;

const DAYBREAK_MODEL: &str = "gpt-6-sol";
const NORMAL_MODEL: &str = "gpt-6.1-sol";

impl App {
    // Reuse the fork's session-only model selection and the stock server/permission checks.
    async fn apply_daybreak_model(
        &mut self,
        app_server: &mut AppServerSession,
        model: String,
        effort: Option<ReasoningEffort>,
    ) -> bool {
        if self.chat_widget.current_model() == model
            && self.chat_widget.effective_reasoning_effort() == effort
        {
            return true;
        }
        self.select_session_model(app_server, model.clone(), effort.clone(), None)
            .await;
        self.chat_widget.current_model() == model
            && self.chat_widget.effective_reasoning_effort() == effort
    }

    pub(super) async fn persist_daybreak_selection(
        &mut self,
        app_server: &mut AppServerSession,
        thread_id: ThreadId,
        enabled: bool,
    ) -> bool {
        if !self
            .chat_widget
            .config_ref()
            .features
            .enabled(Feature::CliDaybreak)
            || self.active_thread_id != Some(thread_id)
        {
            return false;
        }
        if enabled && !self.chat_widget.daybreak_account_eligible() {
            self.chat_widget.add_error_message(
                "Daybreak availability could not be confirmed for this account.".into(),
            );
            return false;
        }
        let target = if enabled {
            DAYBREAK_MODEL
        } else {
            NORMAL_MODEL
        };
        let supported = self.chat_widget.model_catalog().models.iter().any(|model| {
            model.model == target
                && model
                    .supported_reasoning_efforts
                    .iter()
                    .any(|option| option.effort == ReasoningEffort::XHigh)
                && (!enabled
                    || model
                        .available_access_programs
                        .as_ref()
                        .is_some_and(|programs| {
                            programs.cyber.contains(&CyberAccessProgram::DaybreakBlue)
                        }))
        });
        if !supported || self.chat_widget.restrict_model_picker_to_luna_reserve() {
            self.chat_widget.add_error_message(format!(
                "Daybreak {} requires {target} at Extra high reasoning{}, but that combination is unavailable. Your model and Daybreak setting were not changed.",
                if enabled { "on" } else { "off" },
                if enabled { " with Daybreak Blue" } else { "" },
            ));
            return false;
        }
        // Fail before changing the model when the thread cannot be persisted.
        if let Err(error) = app_server
            .thread_read(thread_id, /*include_turns*/ false)
            .await
        {
            self.chat_widget.add_error_message(format!(
                "Failed to read the thread to save Daybreak: {error}"
            ));
            return false;
        }
        let original_model = self.chat_widget.current_model().to_string();
        let original_effort = self.chat_widget.effective_reasoning_effort();
        if !self
            .apply_daybreak_model(app_server, target.into(), Some(ReasoningEffort::XHigh))
            .await
        {
            if (self.chat_widget.current_model() != original_model
                || self.chat_widget.effective_reasoning_effort() != original_effort)
                && !self
                    .apply_daybreak_model(app_server, original_model, original_effort)
                    .await
            {
                self.chat_widget.add_error_message(
                    "The Daybreak model change failed and the previous session selection could not be restored.".into(),
                );
            }
            self.chat_widget.add_error_message(
                "Could not select the Daybreak model and Extra high reasoning; Daybreak was not changed.".into(),
            );
            return false;
        }
        if !self
            .save_daybreak_preference(app_server, thread_id, enabled)
            .await
        {
            if !self
                .apply_daybreak_model(app_server, original_model, original_effort)
                .await
            {
                self.chat_widget.add_error_message(
                    "Daybreak was not changed, but the previous model and reasoning selection could not be restored.".into(),
                );
            }
            return false;
        }
        true
    }

    // Automatic model-change handling must not replace an explicit model choice.
    pub(super) async fn disable_daybreak_for_model_change(
        &mut self,
        app_server: &mut AppServerSession,
        thread_id: ThreadId,
    ) -> bool {
        self.save_daybreak_preference(app_server, thread_id, /*enabled*/ false)
            .await
    }

    async fn save_daybreak_preference(
        &mut self,
        app_server: &mut AppServerSession,
        thread_id: ThreadId,
        enabled: bool,
    ) -> bool {
        if self.active_thread_id != Some(thread_id) {
            return false;
        }
        match app_server
            .thread_read(thread_id, /*include_turns*/ false)
            .await
        {
            Ok(thread) if !thread.ephemeral => {
                let request_id = app_server.next_request_id();
                let response: Result<codex_app_server_protocol::ThreadMetadataUpdateResponse, _> =
                    app_server
                        .request_handle()
                        .request_typed(ClientRequest::ThreadMetadataUpdate {
                            request_id,
                            params: codex_app_server_protocol::ThreadMetadataUpdateParams {
                                thread_id: thread_id.to_string(),
                                daybreak_enabled: Some(enabled),
                                project_id: None,
                                git_info: None,
                            },
                        })
                        .await;
                if let Err(error) = response {
                    self.chat_widget.add_error_message(format!(
                        "Failed to save Daybreak for this thread: {error}"
                    ));
                    return false;
                }
            }
            Ok(_) => {}
            Err(error) => {
                self.chat_widget.add_error_message(format!(
                    "Failed to read the thread to save Daybreak: {error}"
                ));
                return false;
            }
        }
        self.chat_widget.set_daybreak_enabled(enabled);
        if self.primary_thread_id == Some(thread_id)
            && let Some(session) = self.primary_session_configured.as_mut()
        {
            session.daybreak_enabled = enabled;
        }
        if let Some(channel) = self.thread_event_channels.get(&thread_id) {
            let mut store = channel.store.lock().await;
            if let Some(session) = store.session.as_mut() {
                session.daybreak_enabled = enabled;
            }
        }
        self.refresh_status_line();
        self.chat_widget.add_info_message(
            format!(
                "Daybreak {}. Applies to new turns.",
                if enabled { "on" } else { "off" }
            ),
            /*hint*/ None,
        );
        true
    }
}
