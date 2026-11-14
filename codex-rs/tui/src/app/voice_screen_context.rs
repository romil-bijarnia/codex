//! Delivers host-local screenshots to the active voice session, never to a different task.

use super::*;
use codex_app_server_protocol::ThreadRealtimeScreenContextFrame;

impl App {
    pub(super) fn voice_screen_context_backend_matches(
        &self,
        app_server: &AppServerSession,
        thread_id: ThreadId,
    ) -> bool {
        self.thread_event_channels
            .get(&thread_id)
            .and_then(|channel| channel.backend_id)
            == Some(app_server.backend_id())
    }

    pub(super) async fn set_voice_screen_context_enabled(
        &mut self,
        app_server: &mut AppServerSession,
        enabled: bool,
    ) {
        // Apply the call control before disk I/O. In particular, a read-only
        // config or a command-line override must never prevent stopping capture.
        let clear = if let Some(thread_id) = self.voice_owner_thread_id()
            && let Some(owner) = self.voice_widget_for_thread(thread_id)
        {
            let had_frame = owner.realtime_screen_context_has_frame();
            owner.set_realtime_screen_context_enabled(enabled);
            had_frame
                .then(|| owner.realtime_screen_context_clear_binding())
                .flatten()
        } else {
            self.chat_widget
                .set_realtime_screen_context_enabled(enabled);
            None
        };
        // Complete the bound backend clear before awaiting config persistence.
        if let Some((thread_id, context_id, generation)) = clear {
            let cleared = if self.voice_screen_context_backend_matches(app_server, thread_id) {
                matches!(
                    tokio::time::timeout(
                        Duration::from_secs(2),
                        app_server.thread_realtime_set_screen_context(
                            thread_id, context_id, generation, None
                        )
                    )
                    .await,
                    Ok(Ok(()))
                )
            } else {
                false
            };
            if !cleared {
                self.chat_widget.add_error_message(
                    "Local screen capture stopped, but the backend clear was not confirmed. Its last image expires within ten seconds.".into(),
                );
            }
        }
        self.persist_realtime_audio("screen_context", Some(toml::Value::Boolean(enabled)))
            .await;
        // Keep this client's explicit choice even if saving failed. Persistence
        // errors are still shown; other clients retain their own preferences.
        if let Ok(audio) = &mut self.local_settings.audio {
            audio.screen_context = enabled;
        }
        self.chat_widget.local_settings.audio = self.local_settings.audio.clone();
        if let Some(owner) = self.background_voice.as_mut() {
            owner.local_settings.audio = self.local_settings.audio.clone();
        }
    }

    pub(super) fn show_voice_screen_context_status(&mut self) {
        let enabled = self
            .voice_owner_thread_id()
            .and_then(|thread_id| self.voice_widget_for_thread(thread_id))
            .map(|owner| owner.realtime_screen_context_enabled())
            .unwrap_or_else(|| {
                self.local_settings
                    .audio
                    .as_ref()
                    .is_ok_and(|audio| audio.screen_context)
            });
        let message = if enabled {
            "Voice screen context is on. Current screen images accompany spoken requests sent to the coding model."
        } else {
            "Voice screen context is off. Use /voice screen on to enable it."
        };
        self.chat_widget
            .add_info_message(message.into(), /*hint*/ None);
    }

    pub(super) async fn deliver_voice_screen_context(
        &mut self,
        app_server: &mut AppServerSession,
        thread_id: ThreadId,
        attempt_id: u64,
        generation: u64,
        result: Result<crate::voice_screen_context::ScreenContextFrame, String>,
    ) -> bool {
        if !self.voice_screen_context_backend_matches(app_server, thread_id) {
            return false;
        }
        if !self
            .voice_widget_for_thread(thread_id)
            .is_some_and(|owner| owner.accepts_realtime_screen_context(attempt_id, generation))
        {
            return false;
        }
        let Some((context_id, bound_generation)) = self
            .voice_widget_for_thread(thread_id)
            .map(|owner| owner.realtime_screen_context_binding())
        else {
            return false;
        };
        if bound_generation != generation {
            return false;
        }
        let result = match result {
            Ok(frame) => {
                let frame = ThreadRealtimeScreenContextFrame {
                    image_url: frame.image_url,
                    label: frame.label,
                    captured_at_ms: frame.captured_at_ms,
                };
                match tokio::time::timeout(
                    Duration::from_secs(5),
                    app_server.thread_realtime_set_screen_context(
                        thread_id,
                        context_id.clone(),
                        generation,
                        Some(frame),
                    ),
                )
                .await
                {
                    Ok(result) => result.map_err(|error| error.to_string()),
                    Err(_) => Err("screen context delivery timed out".into()),
                }
            }
            Err(error) => Err(error),
        };
        if let Err(error) = result {
            // Clearing is best effort; the backend also expires old frames and clears on stop.
            let _ = tokio::time::timeout(
                Duration::from_secs(2),
                app_server.thread_realtime_set_screen_context(
                    thread_id,
                    context_id,
                    generation.saturating_add(1),
                    None,
                ),
            )
            .await;
            if let Some(owner) = self.voice_widget_for_thread(thread_id) {
                owner.realtime_screen_context_failed(error);
            }
            return false;
        }
        if let Some(owner) = self.voice_widget_for_thread(thread_id) {
            owner.realtime_screen_context_delivered();
        }
        true
    }
}
