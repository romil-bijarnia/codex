//! Select Daybreak Blue at Extra high for this thread without changing launch defaults.

use super::*;
use codex_protocol::openai_models::ReasoningEffort;
use codex_protocol::turn_input::CyberAccessProgram;

#[derive(serde::Serialize, serde::Deserialize)]
struct DaybreakReturnSelection {
    model: String,
    effort: Option<ReasoningEffort>,
}

impl App {
    fn daybreak_return_path(&self, thread_id: ThreadId) -> std::path::PathBuf {
        self.config
            .codex_home
            .join("state/daybreak-return")
            .join(format!("{thread_id}.json"))
            .to_path_buf()
    }

    fn read_daybreak_return(
        &self,
        thread_id: ThreadId,
    ) -> std::io::Result<Option<DaybreakReturnSelection>> {
        match std::fs::read(self.daybreak_return_path(thread_id)) {
            Ok(bytes) => serde_json::from_slice(&bytes)
                .map(Some)
                .map_err(std::io::Error::other),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(error) => Err(error),
        }
    }

    fn write_daybreak_return(
        &self,
        thread_id: ThreadId,
        selection: &DaybreakReturnSelection,
    ) -> std::io::Result<()> {
        let path = self.daybreak_return_path(thread_id);
        let parent = path.parent().expect("Daybreak return directory");
        std::fs::create_dir_all(parent)?;
        let mut temporary = tempfile::NamedTempFile::new_in(parent)?;
        serde_json::to_writer(&mut temporary, selection).map_err(std::io::Error::other)?;
        temporary.persist(path).map_err(|error| error.error)?;
        Ok(())
    }

    fn clear_daybreak_return(&mut self, thread_id: ThreadId) {
        if let Err(error) = std::fs::remove_file(self.daybreak_return_path(thread_id))
            && error.kind() != std::io::ErrorKind::NotFound
        {
            self.chat_widget.add_error_message(format!(
                "Failed to clear the Daybreak return model: {error}"
            ));
        }
    }

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
    ) {
        if !self
            .chat_widget
            .config_ref()
            .features
            .enabled(Feature::CliDaybreak)
            || self.active_thread_id != Some(thread_id)
        {
            return;
        }
        if enabled
            && (!self.chat_widget.daybreak_account_eligible()
                || !crate::daybreak::available(&self.chat_widget.model_catalog().models))
        {
            self.chat_widget.add_error_message(
                "Daybreak availability could not be confirmed for this account.".into(),
            );
            return;
        }
        let return_selection = if enabled {
            Some(DaybreakReturnSelection {
                model: self.chat_widget.current_model().to_string(),
                effort: self.chat_widget.effective_reasoning_effort().or_else(|| {
                    self.chat_widget
                        .model_catalog()
                        .models
                        .iter()
                        .find(|model| model.model == self.chat_widget.current_model())
                        .map(|model| model.default_reasoning_effort.clone())
                }),
            })
        } else {
            match self.read_daybreak_return(thread_id) {
                Ok(selection) => selection,
                Err(error) => {
                    self.chat_widget.add_error_message(format!(
                        "Failed to read the model to restore after Daybreak: {error}"
                    ));
                    return;
                }
            }
        };
        if enabled {
            let supported = self.chat_widget.model_catalog().models.iter().any(|model| {
                model.model == "gpt-6-sol"
                    && model
                        .available_access_programs
                        .as_ref()
                        .is_some_and(|programs| {
                            programs.cyber.contains(&CyberAccessProgram::DaybreakBlue)
                        })
                    && model
                        .supported_reasoning_efforts
                        .iter()
                        .any(|option| option.effort == ReasoningEffort::XHigh)
            });
            if !supported || self.chat_widget.restrict_model_picker_to_luna_reserve() {
                self.chat_widget.add_error_message(
                    "Daybreak defaults require GPT-6-Sol with Daybreak Blue and Extra high reasoning, but that combination is unavailable. Your model and Daybreak setting were not changed.".into(),
                );
                return;
            }
        }
        if enabled && !self.chat_widget.daybreak_enabled {
            if let Err(error) = app_server.thread_read(thread_id, false).await {
                self.chat_widget.add_error_message(format!(
                    "Failed to read the thread to save Daybreak: {error}"
                ));
                return;
            }
            if let Err(error) = self.write_daybreak_return(
                thread_id,
                return_selection.as_ref().expect("original model selection"),
            ) {
                self.chat_widget.add_error_message(format!(
                    "Failed to remember the model to restore after Daybreak: {error}"
                ));
                return;
            }
        }
        if enabled
            && !self
                .apply_daybreak_model(app_server, "gpt-6-sol".into(), Some(ReasoningEffort::XHigh))
                .await
        {
            self.chat_widget.add_error_message(
                "Could not switch to GPT-6-Sol at Extra high reasoning; Daybreak was not enabled."
                    .into(),
            );
            return;
        }
        if !self
            .save_daybreak_preference(app_server, thread_id, enabled)
            .await
        {
            if enabled
                && let Some(selection) = return_selection
                && self
                    .apply_daybreak_model(app_server, selection.model, selection.effort)
                    .await
            {
                self.clear_daybreak_return(thread_id);
            }
            return;
        }
        if !enabled {
            if let Some(selection) = return_selection
                && !self
                    .apply_daybreak_model(app_server, selection.model, selection.effort)
                    .await
            {
                self.chat_widget.add_error_message(
                    "Daybreak is off, but the original model and reasoning level could not be restored.".into(),
                );
                return;
            }
            self.clear_daybreak_return(thread_id);
        }
    }

    pub(super) async fn disable_daybreak_for_model_change(
        &mut self,
        app_server: &mut AppServerSession,
        thread_id: ThreadId,
    ) {
        if self
            .save_daybreak_preference(app_server, thread_id, false)
            .await
        {
            self.clear_daybreak_return(thread_id);
        }
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
