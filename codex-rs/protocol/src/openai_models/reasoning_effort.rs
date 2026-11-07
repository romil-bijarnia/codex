//! Model-owned effort normalization shared by native requests and configuration update items.
//!
//! UI aliases resolve through the same model defaults and fallbacks in both paths.

use super::ModelInfo;
use super::ReasoningEffort;

impl ModelInfo {
    /// Resolves a selected effort to the value sent in an ordinary inference request.
    pub fn resolve_reasoning_effort(&self, effort: ReasoningEffort) -> ReasoningEffort {
        match effort {
            ReasoningEffort::Ultra => self
                .multi_agent_reasoning_effort
                .as_ref()
                .filter(|effort| {
                    !matches!(effort, ReasoningEffort::Ultra | ReasoningEffort::Persistent)
                        && self
                            .supported_reasoning_levels
                            .iter()
                            .any(|preset| &preset.effort == *effort)
                })
                .cloned()
                .unwrap_or_else(|| self.strongest_inference_reasoning_effort()),
            ReasoningEffort::Persistent => {
                if self
                    .supported_reasoning_levels
                    .iter()
                    .any(|preset| preset.effort == ReasoningEffort::Persistent)
                {
                    // Preserve the native wire alias for models that advertise it.
                    ReasoningEffort::Custom("disabled".to_string())
                } else {
                    // Keep persistence in session settings and instructions, but
                    // do not send an unsupported inference effort to the API.
                    self.strongest_inference_reasoning_effort()
                }
            }
            effort => effort,
        }
    }

    fn strongest_inference_reasoning_effort(&self) -> ReasoningEffort {
        self.supported_reasoning_levels
            .iter()
            .find(|preset| preset.effort == ReasoningEffort::Max)
            .or_else(|| {
                self.supported_reasoning_levels.iter().rev().find(|preset| {
                    !matches!(
                        preset.effort,
                        ReasoningEffort::Ultra | ReasoningEffort::Persistent
                    )
                })
            })
            .map(|preset| preset.effort.clone())
            .unwrap_or(ReasoningEffort::Medium)
    }
}
#[cfg(test)]
#[path = "reasoning_effort_tests.rs"]
mod tests;
