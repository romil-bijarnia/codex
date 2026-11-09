//! Restricted updates to a running turn's immutable settings snapshots.

use super::environment::ensure_configs_stay_owner_provided;
use super::environment::validate_environment_configs;
use super::session::Session;
use super::session::SessionConfiguration;
use super::step_settings::ResolvedStepSettings;
use super::step_settings::StepSettingsConstraints;
use super::step_settings::StepSettingsUpdate;
use super::turn_context::TurnContext;
use crate::config::ConstraintResult;
use crate::environment_selection::validate_environment_ids_and_cwds;
use codex_features::Feature;
use codex_protocol::protocol::EnvironmentConfigState;
use codex_protocol::protocol::TurnEnvironmentSelection;
use codex_protocol::protocol::TurnSettingsUpdate;
use codex_protocol::protocol::TurnSettingsUpdateOutcome;
use std::sync::Arc;

impl Session {
    /// Publishes settings to the named, originally captured live task, regardless
    /// of task kind. Publication does not propagate to child sessions or require
    /// the task to sample; consumers using initial settings remain unchanged.
    ///
    /// Callers must serialize updates through completion, including model
    /// resolution, so each sparse patch sees the preceding publication.
    #[expect(
        clippy::await_holding_invalid_type,
        reason = "the final managed-policy check and active settings publication must remain atomic"
    )]
    pub(super) async fn apply_turn_settings(
        &self,
        turn_id: &str,
        update: TurnSettingsUpdate,
    ) -> TurnSettingsUpdateOutcome {
        let updates_model_settings = update.model.is_some()
            || update.effort.is_some()
            || update.summary.is_some()
            || update.service_tier.is_some();
        let requires_model_switching = updates_model_settings
            || (update.approvals_reviewer.is_none() && update.environments.is_none());
        if requires_model_switching && !self.features.enabled(Feature::StepModelSwitching) {
            return TurnSettingsUpdateOutcome::Rejected {
                reason: "turn settings updates require the step_model_switching feature"
                    .to_string(),
            };
        }

        // Capture the running task named by this update and its settings, then release the lock.
        // A task that starts during preparation is never a new target.
        let target = {
            let active = self.active_turn.lock().await;
            active.as_ref().and_then(|active| {
                active.task.as_ref().and_then(|task| {
                    (task.turn_context.sub_id == turn_id && !task.cancellation_token.is_cancelled())
                        .then(|| {
                            (
                                Arc::clone(&task.turn_context),
                                Arc::clone(&task.done),
                                task.turn_context.next_step_settings.load_full(),
                            )
                        })
                })
            })
        };
        let Some((turn_context, task_done, current)) = target else {
            return TurnSettingsUpdateOutcome::TargetUnavailable;
        };
        let TurnSettingsUpdate {
            approvals_reviewer,
            environments: environment_requests,
            model,
            effort,
            summary,
            service_tier,
        } = update;
        let environments = environment_requests.map(|requests| {
            requests
                .into_iter()
                .map(|request| {
                    TurnEnvironmentSelection::new(request, &self.services.selected_capability_roots)
                })
                .collect::<Vec<_>>()
        });
        let updates_step_settings = updates_model_settings || approvals_reviewer.is_some();
        let update = StepSettingsUpdate {
            approvals_reviewer,
            model,
            effort,
            reasoning_summary: summary,
            service_tier,
            ..Default::default()
        };
        // Build the proposed model and reviewer settings.
        // Apply only the fields the caller supplied to the settings captured above. The task can
        // progress, finish, or be cancelled while this awaits; we don't hold the update locks here.
        let prepared = if updates_step_settings {
            let current_environments = self.services.turn_environments.selections();
            let proposed = environments.as_deref().unwrap_or(&current_environments);
            self.prepare_step_settings_activation(&turn_context, &current, &update, proposed)
                .await
                .map(Some)
        } else {
            Ok(None)
        };

        // Validate the environment configuration supplied in the update.
        let environment_config_validation = environments
            .as_deref()
            .map(|environments| validate_environment_configs(environments, &self.features));

        // Confirm the task we originally targeted is still running.
        let active = self.active_turn.lock().await;
        let Some(task) = active.as_ref().and_then(|active| active.task.as_ref()) else {
            return TurnSettingsUpdateOutcome::TargetUnavailable;
        };
        // A later task may reuse the same context and turn ID. `done` is a completion signal
        // created for each task, so matching only the ID/context is insufficient. A mismatch
        // abandons the update without retrying or applying it to the replacement.
        if !Arc::ptr_eq(&task.done, &task_done)
            || !Arc::ptr_eq(&task.turn_context, &turn_context)
            || task.cancellation_token.is_cancelled()
        {
            return TurnSettingsUpdateOutcome::TargetUnavailable;
        }
        let updated_settings = match prepared {
            Ok(settings) => settings,
            Err(reason) => return TurnSettingsUpdateOutcome::Rejected { reason },
        };
        let candidate_settings = updated_settings.as_ref().unwrap_or(current.as_ref());

        // Recheck the latest rules before applying the update.
        // Managed requirements can change during model lookup. Keep the live authorization and
        // safety checks together with applying the update under state and active_turn; no model
        // lookup or other preparation runs under these locks.
        let state = self.state.lock().await;
        // Environment configuration can arrive before its executor connects. If this update
        // omits environments, use what the running turn's manager knows now.
        let current_environments = self.services.turn_environments.selections();
        let proposed = environments.as_deref().unwrap_or(&current_environments);
        let validation = (|| {
            if let Some(configs) = environment_config_validation {
                validate_environment_ids_and_cwds(
                    &self.services.turn_environments.environment_manager(),
                    proposed
                        .iter()
                        .map(|selection| (selection.environment_id.as_str(), &selection.cwd)),
                )
                .map_err(|error| error.to_string())?;
                ensure_configs_stay_owner_provided(&current_environments, proposed)
                    .map_err(|error| error.to_string())?;
                configs.map_err(|error| error.to_string())?;
            }
            self.validate_active_step_settings(
                &turn_context,
                candidate_settings,
                &state.session_configuration,
                proposed,
            )
            .map_err(|error| error.to_string())?;
            Ok::<_, String>(())
        })();
        if let Err(reason) = validation {
            return TurnSettingsUpdateOutcome::Rejected { reason };
        }

        // Save changed settings first. The manager then installs any new environment list before
        // waking waiting work. The next step cannot read either until we release this lock.
        if let Some(settings) = updated_settings {
            task.turn_context
                .next_step_settings
                .store(Arc::new(settings));
        }
        if environments.is_some() {
            self.services.turn_environments.update_selections(proposed);
        }
        TurnSettingsUpdateOutcome::Applied
    }

    async fn prepare_step_settings_activation(
        &self,
        turn_context: &TurnContext,
        current: &ResolvedStepSettings,
        update: &StepSettingsUpdate,
        environments: &[TurnEnvironmentSelection],
    ) -> Result<ResolvedStepSettings, String> {
        let (requirements, overrides, trusted_guardian_reviewer) = {
            let state = self.state.lock().await;
            let configuration = &state.session_configuration;
            let stack = &configuration.original_config_do_not_use.config_layer_stack;
            (
                stack.requirements().clone(),
                configuration.model_info_overrides.clone(),
                configuration.trusted_guardian_reviewer,
            )
        };
        let constraints = StepSettingsConstraints {
            requirements: &requirements,
            guardian_approval_enabled: self.features.enabled(Feature::GuardianApproval),
            trusted_guardian_reviewer,
            has_full_disk_write_access: any_environment_has_full_disk_write(
                turn_context,
                environments,
            ),
        };
        current
            .apply_update(
                update,
                &constraints,
                self.services.models_manager.as_ref(),
                &overrides,
                &self.features,
            )
            .await
            .map_err(|error| error.to_string())
    }

    /// Rechecks ordinary managed authorization after asynchronous resolution.
    /// Unlike the temporary legacy-turn check, these requirements also apply
    /// once all execution consumers read their captured `StepContext`.
    fn validate_active_step_settings(
        &self,
        turn_context: &TurnContext,
        settings: &ResolvedStepSettings,
        configuration: &SessionConfiguration,
        environments: &[TurnEnvironmentSelection],
    ) -> ConstraintResult<()> {
        let requirements = configuration
            .original_config_do_not_use
            .config_layer_stack
            .requirements();
        settings.revalidate(&StepSettingsConstraints {
            requirements,
            guardian_approval_enabled: self.features.enabled(Feature::GuardianApproval),
            trusted_guardian_reviewer: configuration.trusted_guardian_reviewer,
            has_full_disk_write_access: any_environment_has_full_disk_write(
                turn_context,
                environments,
            ),
        })
    }
}

fn any_environment_has_full_disk_write(
    turn: &TurnContext,
    environments: &[TurnEnvironmentSelection],
) -> bool {
    let fallback = turn.config.permissions.permission_profile();
    let mut known = environments.iter().filter_map(|environment| {
        let (profile, roots) = match &environment.config {
            EnvironmentConfigState::FromThread => (fallback, &environment.workspace_roots),
            EnvironmentConfigState::Ready(config) => (
                config.permission_profile.permission_profile(),
                &config.workspace_roots,
            ),
            EnvironmentConfigState::Pending | EnvironmentConfigState::Failed(_) => return None,
        };
        Some(
            profile
                .clone()
                .materialize_project_roots_with_path_uris(roots)
                .file_system_sandbox_policy()
                .has_full_disk_write_access_for_convention(environment.cwd.infer_path_convention()),
        )
    });
    match known.next() {
        Some(first) => first || known.any(|full_write| full_write),
        None => fallback
            .file_system_sandbox_policy()
            .has_full_disk_write_access(),
    }
}

#[cfg(test)]
#[path = "step_activation_tests.rs"]
mod tests;
