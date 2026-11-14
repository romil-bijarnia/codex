//! Bounded screen refresh for the one local voice owner, including parked widgets.
//! Passive refresh never runs a model turn; only a spoken delegation uses the image.

use super::*;
use tokio::task::JoinHandle;

const CAPTURE_INTERVAL: Duration = Duration::from_secs(2);
// Keep an unchanged screen fresh within Core's ten-second expiry.
const UNCHANGED_REFRESH: Duration = Duration::from_secs(6);

pub(super) struct ScreenContextSharing {
    context_id: String,
    enabled_override: Option<bool>,
    generation: u64,
    task: Option<JoinHandle<()>>,
    delivered: bool,
}

impl Default for ScreenContextSharing {
    fn default() -> Self {
        Self {
            context_id: uuid::Uuid::new_v4().to_string(),
            enabled_override: None,
            generation: 0,
            task: None,
            delivered: false,
        }
    }
}

impl Drop for ScreenContextSharing {
    fn drop(&mut self) {
        if let Some(task) = self.task.take() {
            task.abort();
        }
    }
}

impl ChatWidget {
    pub(crate) fn realtime_screen_context_enabled(&self) -> bool {
        self.realtime_conversation
            .screen_context
            .enabled_override
            .unwrap_or_else(|| {
                self.local_settings
                    .audio
                    .as_ref()
                    .is_ok_and(|audio| audio.screen_context)
            })
    }

    pub(crate) fn set_realtime_screen_context_enabled(&mut self, enabled: bool) {
        self.realtime_conversation.screen_context.enabled_override = Some(enabled);
        self.stop_screen_context_capture();
        if enabled {
            self.start_screen_context_capture();
        }
        self.update_realtime_footer();
        self.request_redraw();
    }

    pub(in crate::chatwidget) fn start_screen_context_capture(&mut self) {
        if !self.realtime_screen_context_enabled()
            || self.realtime_conversation.phase != RealtimeConversationPhase::Active
            || self.realtime_conversation.screen_context.task.is_some()
        {
            return;
        }
        let Some(thread_id) = self.realtime_conversation.thread_id else {
            return;
        };
        let attempt_id = self.realtime_conversation.attempt_id;
        let state = &mut self.realtime_conversation.screen_context;
        state.generation = state.generation.wrapping_add(1);
        state.delivered = false;
        let generation = state.generation;
        let tx = self.app_event_tx.clone();
        state.task = Some(tokio::spawn(async move {
            let mut interval = tokio::time::interval(CAPTURE_INTERVAL);
            interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
            let mut last_frame = None;
            loop {
                interval.tick().await;
                let result = crate::voice_screen_context::capture_screen_context()
                    .await
                    .map_err(|error| format!("{error:#}"));
                if let Ok(frame) = &result {
                    if last_frame.as_ref().is_some_and(
                        |(fingerprint, sent): &([u8; 32], Instant)| {
                            *fingerprint == frame.fingerprint && sent.elapsed() < UNCHANGED_REFRESH
                        },
                    ) {
                        continue;
                    }
                    last_frame = Some((frame.fingerprint, Instant::now()));
                }
                let (completion, done) = tokio::sync::oneshot::channel();
                tx.send(AppEvent::RealtimeScreenContextCaptured {
                    thread_id,
                    attempt_id,
                    generation,
                    result,
                    completion,
                });
                // One in-flight image at most, even if an RPC or the UI is slow.
                if !matches!(done.await, Ok(true)) {
                    break;
                }
            }
        }));
    }

    pub(in crate::chatwidget) fn stop_screen_context_capture(&mut self) {
        let state = &mut self.realtime_conversation.screen_context;
        if let Some(task) = state.task.take() {
            task.abort();
        }
        state.generation = state.generation.wrapping_add(1);
        state.delivered = false;
    }

    pub(crate) fn accepts_realtime_screen_context(&self, attempt_id: u64, generation: u64) -> bool {
        self.realtime_conversation.phase == RealtimeConversationPhase::Active
            && self.realtime_conversation.attempt_id == attempt_id
            && self.realtime_conversation.screen_context.generation == generation
            && self.realtime_screen_context_enabled()
    }

    pub(crate) fn realtime_screen_context_binding(&self) -> (String, u64) {
        (
            self.realtime_conversation.screen_context.context_id.clone(),
            self.realtime_conversation.screen_context.generation,
        )
    }

    pub(crate) fn realtime_screen_context_clear_binding(&self) -> Option<(ThreadId, String, u64)> {
        if self.realtime_conversation.phase != RealtimeConversationPhase::Active
            || self.realtime_screen_context_enabled()
        {
            return None;
        }
        let thread_id = self.realtime_conversation.thread_id?;
        let (context_id, generation) = self.realtime_screen_context_binding();
        Some((thread_id, context_id, generation))
    }

    pub(crate) fn realtime_screen_context_delivered(&mut self) {
        self.realtime_conversation.screen_context.delivered = true;
        self.update_realtime_footer();
        self.request_redraw();
    }

    pub(crate) fn realtime_screen_context_has_frame(&self) -> bool {
        self.realtime_conversation.screen_context.delivered
    }

    pub(crate) fn realtime_screen_context_failed(&mut self, message: String) {
        self.stop_screen_context_capture();
        // Fail closed for this call, without overwriting the saved preference.
        self.realtime_conversation.screen_context.enabled_override = Some(false);
        self.add_error_message(format!("Screen sharing stopped: {message}"));
        self.update_realtime_footer();
        self.request_redraw();
    }

    pub(in crate::chatwidget) fn realtime_screen_context_activity(
        &self,
        activity: &'static str,
    ) -> &'static str {
        if !self.realtime_conversation.screen_context.delivered {
            return activity;
        }
        match activity {
            "muted" => "muted, screen shared",
            "heard" => "heard, screen shared",
            "speaking" => "speaking, screen shared",
            _ => "listening, screen shared",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::chatwidget::tests::make_chatwidget_manual_with_sender;

    #[tokio::test]
    async fn screen_context_is_off_until_enabled_and_attempt_bound() {
        let (mut chat, _, _, _) = make_chatwidget_manual_with_sender().await;
        assert!(!chat.realtime_screen_context_enabled());
        crate::chatwidget::activate_voice_for_thread(&mut chat, ThreadId::new());
        chat.realtime_conversation.attempt_id = 19;
        chat.realtime_conversation.screen_context.generation = 7;
        chat.realtime_conversation.screen_context.enabled_override = Some(true);
        assert!(chat.accepts_realtime_screen_context(19, 7));
        assert!(!chat.accepts_realtime_screen_context(18, 7));
        assert!(!chat.accepts_realtime_screen_context(19, 6));
        chat.realtime_conversation.phase = RealtimeConversationPhase::Stopping;
        assert!(!chat.accepts_realtime_screen_context(19, 7));
    }

    #[tokio::test]
    async fn disabling_screen_context_clears_images_and_rejects_queued_frames() {
        let (mut chat, _, _, _) = make_chatwidget_manual_with_sender().await;
        let thread_id = ThreadId::new();
        crate::chatwidget::activate_voice_for_thread(&mut chat, thread_id);
        chat.realtime_conversation.attempt_id = 19;
        chat.realtime_conversation.screen_context.generation = 7;
        chat.realtime_conversation.screen_context.enabled_override = Some(true);
        chat.set_realtime_screen_context_enabled(false);
        assert!(!chat.realtime_screen_context_enabled());
        assert!(!chat.accepts_realtime_screen_context(19, 7));
        let generation = chat.realtime_conversation.screen_context.generation;
        let clear = chat.realtime_screen_context_clear_binding().unwrap();
        assert_eq!(clear.0, thread_id);
        assert_eq!(clear.2, generation);
        chat.realtime_conversation.screen_context.generation += 1;
        assert_ne!(
            chat.realtime_screen_context_clear_binding().unwrap().2,
            generation
        );
    }

    #[tokio::test]
    async fn stopping_and_dropping_screen_context_abort_capture_workers() {
        let (mut chat, _, _, _) = make_chatwidget_manual_with_sender().await;
        let task = tokio::spawn(std::future::pending::<()>());
        let abort = task.abort_handle();
        chat.realtime_conversation.screen_context.task = Some(task);
        chat.stop_screen_context_capture();
        tokio::task::yield_now().await;
        assert!(abort.is_finished());
        let task = tokio::spawn(std::future::pending::<()>());
        let abort = task.abort_handle();
        let mut state = ScreenContextSharing::default();
        state.task = Some(task);
        drop(state);
        tokio::task::yield_now().await;
        assert!(abort.is_finished());
    }

    #[tokio::test]
    async fn screen_indicator_only_claims_delivery_after_acknowledgement() {
        let (mut chat, _, _, _) = make_chatwidget_manual_with_sender().await;
        assert_eq!(
            chat.realtime_screen_context_activity("listening"),
            "listening"
        );
        chat.realtime_conversation.screen_context.delivered = true;
        assert_eq!(
            chat.realtime_screen_context_activity("listening"),
            "listening, screen shared"
        );
        chat.stop_screen_context_capture();
        assert_eq!(
            chat.realtime_screen_context_activity("listening"),
            "listening"
        );
    }

    #[tokio::test]
    async fn voice_screen_context_commands_route_to_explicit_controls() {
        let (mut chat, _, mut events, _) = make_chatwidget_manual_with_sender().await;
        for (args, expected) in [
            ("screen on", Some(true)),
            ("screen off", Some(false)),
            ("screen status", None),
        ] {
            chat.dispatch_command_with_args(
                crate::slash_command::SlashCommand::Voice,
                args.into(),
                Vec::new(),
            );
            let mut found = false;
            while let Ok(event) = events.try_recv() {
                found |= match event {
                    AppEvent::SetRealtimeScreenContextEnabled { enabled } => {
                        expected == Some(enabled)
                    }
                    AppEvent::RealtimeScreenContextStatus => expected.is_none(),
                    _ => false,
                };
            }
            assert!(found, "missing screen-context control for {args}");
        }
    }

    #[test]
    fn screen_context_image_attachments_keep_voice_delegation_recognition() {
        let input = vec![
            UserInput::Text {
                text: "<realtime_delegation><input>What is this?</input></realtime_delegation>"
                    .into(),
                text_elements: Vec::new(),
            },
            UserInput::Text {
                text: "[voice_screen_context] captured now".into(),
                text_elements: Vec::new(),
            },
            UserInput::Image {
                image: codex_app_server_protocol::ImageReference::Inline {
                    url: "data:image/png;base64,fixture".into(),
                },
                detail: None,
            },
        ];
        assert_eq!(
            super::super::realtime_delegation_input(&input),
            Some("What is this?")
        );
    }
}
