//! Owns review reporting and the per-thread denial window. The host publishes
//! events and enforces interruption only on the still-active owning turn.
//! Internal accounting runs even when no client event is available. Review
//! completion captures one monotonic duration for latency reporting while retaining
//! the actual wall-clock completion timestamp for event ordering.

use std::time::Duration;
use std::time::Instant;

use codex_analytics::AnalyticsEventsClient;
use codex_analytics::GuardianApprovalRequestSource;
use codex_analytics::GuardianReviewAnalyticsResult;
use codex_analytics::GuardianReviewTrackContext;
use codex_analytics::GuardianReviewedAction;
use codex_otel::SessionTelemetry;
use codex_protocol::approvals::GuardianAssessmentAction;
use codex_protocol::approvals::GuardianReviewReason;
use codex_protocol::items::ModelInvocationContext;
use codex_protocol::openai_models::ModelInfo;
use codex_protocol::protocol::GuardianAssessmentDecisionSource;
use codex_protocol::protocol::GuardianAssessmentEvent;
use codex_protocol::protocol::GuardianAssessmentStatus;
use crate::GuardianReviewOutcome;
use crate::ReviewCompletion;

/// Captured action attribution used for both the started and terminal report.
pub struct ReviewMetadata {
    pub thread_id: String,
    pub turn_id: String,
    pub review_id: String,
    pub target_item_id: Option<String>,
    pub plugin_id: Option<String>,
    pub script_path: Option<String>,
    pub approval_request_source: GuardianApprovalRequestSource,
    pub reviewed_action: GuardianReviewedAction,
    /// Absent when the action has no client-facing representation.
    pub action: Option<GuardianAssessmentAction>,
    pub review_reason: GuardianReviewReason,
    pub model_context: ModelInvocationContext,
    /// Configured review budget; None is reported as zero (unlimited).
    pub timeout: Option<Duration>,
}

pub struct ReviewReport {
    started: Option<GuardianAssessmentEvent>,
    started_instant: Instant,
    tracking: GuardianReviewTrackContext,
    approval_request_source: GuardianApprovalRequestSource,
    reviewed_action: GuardianReviewedAction,
}

impl ReviewReport {
    pub fn new(metadata: ReviewMetadata) -> Self {
        let started_instant = Instant::now();
        let tracking = GuardianReviewTrackContext::new(
            metadata.thread_id,
            metadata.turn_id.clone(),
            metadata.review_id.clone(),
            metadata.target_item_id.clone(),
            metadata.approval_request_source,
            metadata.reviewed_action.clone(),
            metadata
                .timeout
                .map_or(0, |timeout| timeout.as_millis() as u64),
        );
        let started = metadata.action.map(|action| GuardianAssessmentEvent {
            review_reason: Some(metadata.review_reason),
            model_context: Some(metadata.model_context),
            id: metadata.review_id,
            target_item_id: metadata.target_item_id,
            plugin_id: metadata.plugin_id,
            script_path: metadata.script_path,
            turn_id: metadata.turn_id,
            started_at_ms: tracking.started_at_ms.try_into().unwrap_or_default(),
            completed_at_ms: None,
            status: GuardianAssessmentStatus::InProgress,
            risk_level: None,
            user_authorization: None,
            rationale: None,
            decision_source: None,
            action,
        });
        Self {
            started,
            started_instant,
            tracking,
            approval_request_source: metadata.approval_request_source,
            reviewed_action: metadata.reviewed_action,
        }
    }

    pub fn started_event(&self) -> Option<GuardianAssessmentEvent> {
        self.started.clone()
    }

    /// Completes the report and captures the elapsed duration to use when tracking it.
    pub fn complete(
        &self,
        outcome: GuardianReviewOutcome,
        model: &ModelInfo,
        require_guardian: bool,
        analytics: GuardianReviewAnalyticsResult,
        completed_at_ms: i64,
    ) -> (ReviewCompletion, Duration) {
        let elapsed = self.started_instant.elapsed();
        let mut event = self.started.clone();
        if let Some(event) = &mut event {
            event.completed_at_ms = Some(completed_at_ms);
            event.decision_source = Some(GuardianAssessmentDecisionSource::Agent);
        }
        (
            crate::complete_review(outcome, model, require_guardian, event, analytics),
            elapsed,
        )
    }

    pub fn track(
        &self,
        telemetry: &SessionTelemetry,
        analytics: &AnalyticsEventsClient,
        result: GuardianReviewAnalyticsResult,
        completed_at_ms: u64,
        elapsed: Duration,
    ) {
        crate::metrics::emit_guardian_review_metrics(
            telemetry,
            &result,
            self.approval_request_source,
            &self.reviewed_action,
            elapsed,
        );
        analytics.track_guardian_review(&self.tracking, result, completed_at_ms, elapsed);
    }
}
