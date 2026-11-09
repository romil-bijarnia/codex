use super::*;

#[tokio::test]
async fn long_literal_paste_submits_full_text_without_running_a_shell() {
    let (mut chat, _rx, mut op_rx) = make_chatwidget_manual(Some("gpt-5.5")).await;
    chat.thread_id = Some(ThreadId::new());
    chat.set_feature_enabled(Feature::SteerOnly, true);
    let payload = format!("!AUDIT_LITERAL_PASTE_{}", "x".repeat(4096));
    chat.handle_paste(payload.clone());
    assert!(chat.bottom_pane.composer_text().contains("[Pasted Content"));
    chat.handle_key_event(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));

    let Op::UserTurn { items, .. } = next_submit_op(&mut op_rx) else {
        panic!("literal paste must submit a model turn, not a shell command");
    };
    assert!(items.iter().any(|item| {
        matches!(item, codex_app_server_protocol::UserInput::Text { text, .. } if text == &payload)
    }));
    assert!(chat.input_queue.queued_user_messages.is_empty());
}

#[tokio::test]
async fn blocked_literal_paste_remains_editable_and_retries_with_full_text() {
    let (mut chat, _rx, mut op_rx) = make_chatwidget_manual(Some("gpt-5.5")).await;
    chat.thread_id = Some(ThreadId::new());
    chat.set_feature_enabled(Feature::SteerOnly, true);
    chat.input_queue.rate_limit_recovery_pending = true;
    let payload = format!("!AUDIT_LITERAL_PASTE_{}", "x".repeat(4096));
    chat.handle_paste(payload.clone());
    chat.handle_key_event(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));

    assert!(chat.bottom_pane.composer_text().contains("[Pasted Content"));
    assert!(
        chat.bottom_pane
            .composer_pending_pastes()
            .iter()
            .any(|(_, text)| text == &payload)
    );
    assert!(chat.input_queue.queued_user_messages.is_empty());
    chat.input_queue.rate_limit_recovery_pending = false;
    chat.handle_key_event(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
    let Op::UserTurn { items, .. } = next_submit_op(&mut op_rx) else {
        panic!("retry must preserve literal submission");
    };
    assert!(items.iter().any(|item| {
        matches!(item, codex_app_server_protocol::UserInput::Text { text, .. } if text == &payload)
    }));
}

#[tokio::test]
async fn rejected_start_by_client_id_restores_visible_unsent_text() {
    let (mut chat, _rx, _op_rx) = make_chatwidget_manual(Some("gpt-5.5")).await;
    chat.set_feature_enabled(Feature::SteerOnly, true);
    chat.input_queue.pending_user_message_client_id = Some("rejected-start".to_owned());
    chat.input_queue.user_turn_pending_start = true;
    chat.safety_buffering_prompt = Some(UserMessage::from("keep this visibly unsent"));

    assert!(chat.enqueue_rejected_steer_by_id(Some("rejected-start")));
    assert_eq!(chat.bottom_pane.composer_text(), "keep this visibly unsent");
    assert!(chat.input_queue.queued_user_messages.is_empty());
    assert!(chat.input_queue.rejected_steers_queue.is_empty());
    assert!(!chat.input_queue.user_turn_pending_start);
}

#[tokio::test]
async fn rejected_start_preserves_the_original_history_override() {
    let (mut chat, _rx, _op_rx) = make_chatwidget_manual(Some("gpt-5.5")).await;
    chat.set_feature_enabled(Feature::SteerOnly, true);
    chat.input_queue.pending_user_message_client_id = Some("goal-start".to_owned());
    chat.input_queue.pending_user_message_history_record = Some(
        UserMessageHistoryRecord::Override(UserMessageHistoryOverride {
            text: "/goal finish the task".to_owned(),
            text_elements: Vec::new(),
        }),
    );
    chat.input_queue.user_turn_pending_start = true;
    chat.safety_buffering_prompt = Some(UserMessage::from("expanded goal instructions"));

    assert!(chat.enqueue_rejected_steer_by_id(Some("goal-start")));
    assert_eq!(chat.bottom_pane.composer_text(), "/goal finish the task");
    assert!(
        chat.input_queue
            .pending_user_message_history_record
            .is_none()
    );
    assert!(chat.input_queue.queued_user_messages.is_empty());
}

#[tokio::test]
async fn rejected_steer_client_id_does_not_take_an_unrelated_pending_message() {
    let (mut chat, _rx, _op_rx) = make_chatwidget_manual(Some("gpt-5.5")).await;
    chat.set_feature_enabled(Feature::SteerOnly, false);
    for (id, text) in [
        ("first", "leave this pending"),
        ("second", "recover this steer"),
    ] {
        chat.input_queue.pending_steers.push_back(PendingSteer {
            client_id: id.to_owned(),
            user_message: UserMessage::from(text),
            history_record: UserMessageHistoryRecord::UserMessageText,
            source: UserMessageSource::Prompt,
            compare_key: crate::chatwidget::user_messages::PendingSteerCompareKey {
                message: text.to_owned(),
                image_count: 0,
            },
        });
    }

    assert!(chat.enqueue_rejected_steer_by_id(Some("second")));
    assert_eq!(chat.input_queue.pending_steers.len(), 1);
    assert_eq!(chat.input_queue.pending_steers[0].client_id, "first");
    assert_eq!(chat.input_queue.rejected_steers_queue.len(), 1);
    assert_eq!(
        chat.input_queue.rejected_steers_queue[0].text,
        "recover this steer"
    );
}
