//! Central approval policy-stage execution and reviewer routing.
//! Managed human checkpoints cannot be satisfied by hooks, automated review, or reusable approvals.

use crate::command_canonicalization::canonicalize_command_for_approval;
use crate::exec_policy::prompt_is_rejected_by_policy;
use crate::guardian::GuardianNetworkAccessTrigger;
use crate::guardian::GuardianReviewContext;
use crate::hook_runtime::run_permission_request_hooks;
use crate::sandboxing::SandboxPermissions;
use crate::session::NetworkApprovalTurn;
use crate::session::session::Session;
use crate::tools::hook_names::HookToolName;
use crate::tools::runtimes::apply_patch::ApplyPatchApprovalKey;
use crate::tools::runtimes::unified_exec::UnifiedExecApprovalKey;
use crate::tools::sandboxing::ApprovalRequestReasons;
use crate::tools::sandboxing::ExecApprovalRequirement;
use crate::tools::sandboxing::PermissionRequestPayload;
use crate::tools::sandboxing::ToolError;
use crate::tools::sandboxing::with_cached_approval;
use codex_analytics::GuardianApprovalRequestSource;
use codex_config::types::AppToolApproval;
use codex_hooks::PermissionRequestDecision;
use codex_otel::ToolDecisionSource;
use codex_prompts::ResolvedModelMessages;
use codex_protocol::approvals::ExecApprovalKind;
use codex_protocol::approvals::ExecPolicyAmendment;
use codex_protocol::approvals::NetworkApprovalContext;
use codex_protocol::approvals::NetworkApprovalProtocol;
use codex_protocol::config_types::ApprovalsReviewer;
use codex_protocol::error::CodexErr;
use codex_protocol::mcp_approval_meta::McpApprovalCancellationReason;
use codex_protocol::models::AdditionalPermissionProfile;
use codex_protocol::openai_models::ModelInfo;
use codex_protocol::protocol::AskForApproval;
use codex_protocol::protocol::FileChange;
use codex_protocol::protocol::NetworkPolicyRuleAction;
use codex_protocol::protocol::ReviewDecision;
use codex_protocol::request_permissions::RequestPermissionProfile;
use codex_tools::ToolName;
use codex_utils_absolute_path::AbsolutePathBuf;
use codex_utils_path_uri::PathConvention;
use codex_utils_path_uri::PathUri;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use tokio_util::sync::CancellationToken;
use tracing::error;
use tracing::warn;

#[derive(Clone)]
pub(crate) struct ApprovalContext {
    /// Preserve the command's policy result through review and retries. Other actions
    /// use their existing reviewer routing without an execution policy requirement.
    pub(crate) exec_requirement: Option<ExecApprovalRequirement>,
    pub(crate) review_context: GuardianReviewContext,
    pub(crate) cancellation_token: Option<CancellationToken>,
    pub(crate) call_id: String,
    pub(crate) tool_name: ToolName,
    pub(crate) strict_auto_review: bool,
    pub(crate) approval_reason: Option<String>,
    pub(crate) retry_reason: Option<String>,
    pub(crate) network_approval_context: Option<NetworkApprovalContext>,
}

impl ApprovalContext {
    fn requires_human_approval(&self) -> bool {
        matches!(
            self.exec_requirement,
            Some(ExecApprovalRequirement::NeedsHumanApproval { .. })
        )
    }
}

#[derive(Clone, Debug, PartialEq, serde::Serialize)]
#[serde(tag = "tool", rename_all = "snake_case")]
pub(crate) enum ApprovalAction {
    ExecCommand {
        id: String,
        environment_id: String,
        command: Vec<String>,
        #[serde(skip_serializing)]
        hook_command: String,
        cwd: PathUri,
        sandbox_permissions: SandboxPermissions,
        additional_permissions: Option<AdditionalPermissionProfile>,
        justification: Option<String>,
        tty: bool,
        proposed_execpolicy_amendment: Option<ExecPolicyAmendment>,
    },
    WriteStdin {
        id: String,
        approval_id: String,
        environment_id: String,
        process_id: i32,
        original_command: String,
        input: String,
        cwd: PathUri,
        tty: bool,
        sandbox_permissions: SandboxPermissions,
        additional_permissions: Option<AdditionalPermissionProfile>,
        #[serde(skip)]
        additional_permissions_preapproved: bool,
    },
    ApplyPatch {
        id: String,
        environment_id: String,
        cwd: PathUri,
        files: Vec<PathUri>,
        patch: String,
        #[serde(skip_serializing)]
        changes: Arc<HashMap<PathBuf, FileChange>>,
        permissions_preapproved: bool,
    },
    McpToolCall {
        id: String,
        environment_id: String,
        server: String,
        tool_name: String,
        arguments: Option<serde_json::Value>,
        connector_id: Option<String>,
        connector_name: Option<String>,
        connector_description: Option<String>,
        connected_account_email: Option<String>,
        tool_title: Option<String>,
        tool_description: Option<String>,
        annotations: Option<crate::guardian::GuardianMcpAnnotations>,
        #[serde(skip_serializing)]
        hook_tool_name: HookToolName,
        approval_policy: AskForApproval,
        reviewer: ApprovalsReviewer,
        approval_mode: AppToolApproval,
        allow_session_remember: bool,
        allow_persistent_approval: bool,
    },
    NetworkAccess {
        id: String,
        turn_id: String,
        environment_id: String,
        target: String,
        host: String,
        protocol: NetworkApprovalProtocol,
        port: u16,
        trigger: Option<GuardianNetworkAccessTrigger>,
        #[serde(skip_serializing)]
        hook_command: String,
        #[serde(skip_serializing)]
        hook_run_id: String,
        command: Vec<String>,
        cwd: AbsolutePathBuf,
    },
    RequestPermissions {
        id: String,
        environment_id: String,
        turn_id: String,
        reason: Option<String>,
        permissions: RequestPermissionProfile,
    },
}

#[derive(Clone, Debug, Eq, Hash, PartialEq, serde::Serialize)]
#[serde(untagged)]
pub(crate) enum ApprovalCacheKey {
    ExecCommand(UnifiedExecApprovalKey),
    ApplyPatch(ApplyPatchApprovalKey),
}

impl ApprovalAction {
    fn hook_environment_id(&self) -> &str {
        match self {
            Self::ExecCommand { environment_id, .. }
            | Self::WriteStdin { environment_id, .. }
            | Self::ApplyPatch { environment_id, .. }
            | Self::McpToolCall { environment_id, .. }
            | Self::NetworkAccess { environment_id, .. }
            | Self::RequestPermissions { environment_id, .. } => environment_id,
        }
    }

    pub(crate) fn permission_request_payload(&self) -> PermissionRequestPayload {
        let mut payload = match self {
            Self::ExecCommand {
                hook_command,
                justification,
                cwd,
                ..
            } => PermissionRequestPayload::bash(
                hook_command.clone(),
                justification.clone(),
                cwd.clone(),
            ),
            Self::WriteStdin {
                id,
                approval_id,
                environment_id,
                process_id,
                original_command: _,
                input,
                cwd,
                tty,
                sandbox_permissions,
                additional_permissions,
                additional_permissions_preapproved: _,
            } => PermissionRequestPayload {
                tool_name: HookToolName::new("write_stdin"),
                tool_cwd: Some(cwd.clone()),
                tool_input: serde_json::json!({
                    "session_id": process_id,
                    "chars": input,
                    "parent_call_id": id,
                    "approval_id": approval_id,
                    "environment_id": environment_id,
                    "cwd": cwd,
                    "tty": tty,
                    "sandbox_permissions": sandbox_permissions,
                    "additional_permissions": additional_permissions,
                }),
            },
            Self::ApplyPatch { patch, .. } => PermissionRequestPayload {
                tool_name: HookToolName::apply_patch(),
                tool_cwd: None,
                tool_input: serde_json::json!({ "command": patch }),
            },
            Self::McpToolCall {
                hook_tool_name,
                arguments,
                ..
            } => PermissionRequestPayload {
                tool_name: hook_tool_name.clone(),
                tool_cwd: None,
                tool_input: arguments
                    .clone()
                    .unwrap_or_else(|| serde_json::Value::Object(serde_json::Map::new())),
            },
            Self::NetworkAccess {
                hook_command,
                target,
                cwd,
                trigger,
                ..
            } => PermissionRequestPayload::bash(
                hook_command.clone(),
                Some(format!("network-access {target}")),
                trigger.as_ref().map_or_else(
                    || PathUri::from_abs_path(cwd),
                    |trigger| trigger.cwd.clone(),
                ),
            ),
            Self::RequestPermissions {
                reason,
                permissions,
                ..
            } => PermissionRequestPayload {
                tool_name: HookToolName::new("request_permissions"),
                tool_cwd: None,
                tool_input: serde_json::json!({
                    "reason": reason,
                    "permissions": permissions,
                }),
            },
        };
        // Command hooks run on the host. Keep remote paths in the action's
        // original tool input, but never use them as the hook's top-level cwd.
        if matches!(
            self,
            Self::ExecCommand { environment_id, .. }
                | Self::WriteStdin { environment_id, .. }
                | Self::NetworkAccess { environment_id, .. }
                if environment_id != codex_exec_server::LOCAL_ENVIRONMENT_ID
        ) {
            payload.tool_cwd = None;
        }
        payload
    }

    pub(crate) fn cache_keys(&self) -> Vec<ApprovalCacheKey> {
        match self {
            Self::ExecCommand {
                environment_id,
                command,
                cwd,
                tty,
                sandbox_permissions,
                additional_permissions,
                ..
            } => vec![ApprovalCacheKey::ExecCommand(UnifiedExecApprovalKey {
                environment_id: environment_id.clone(),
                executable: command.first().cloned(),
                command: canonicalize_command_for_approval(command),
                cwd: cwd.clone(),
                tty: *tty,
                sandbox_permissions: *sandbox_permissions,
                additional_permissions: additional_permissions.clone(),
            })],
            Self::McpToolCall { .. }
            | Self::NetworkAccess { .. }
            | Self::RequestPermissions { .. }
            | Self::WriteStdin { .. } => Vec::new(),
            Self::ApplyPatch {
                environment_id,
                files,
                ..
            } => files
                .iter()
                .cloned()
                .map(|path| {
                    ApprovalCacheKey::ApplyPatch(ApplyPatchApprovalKey {
                        environment_id: environment_id.clone(),
                        path,
                    })
                })
                .collect(),
        }
    }

    pub(crate) fn into_guardian_request(
        self,
        exec_command_cwd_convention: Option<PathConvention>,
    ) -> std::io::Result<crate::guardian::GuardianApprovalRequest> {
        Ok(match self {
            Self::ExecCommand {
                id,
                environment_id,
                command,
                cwd,
                sandbox_permissions,
                additional_permissions,
                justification,
                tty,
                ..
            } => crate::guardian::GuardianApprovalRequest::ExecCommand {
                id,
                environment_id,
                command,
                guardian_cwd: codex_utils_path_uri::LegacyAppPathString::from_path_uri(
                    &cwd,
                    exec_command_cwd_convention.ok_or_else(|| {
                        std::io::Error::other("missing exec command cwd convention")
                    })?,
                )
                .map_err(std::io::Error::other)?,
                cwd,
                sandbox_permissions,
                additional_permissions,
                justification,
                tty,
            },
            Self::WriteStdin {
                id,
                approval_id,
                environment_id,
                process_id,
                original_command,
                input,
                cwd,
                tty,
                sandbox_permissions,
                additional_permissions,
                additional_permissions_preapproved: _,
            } => crate::guardian::GuardianApprovalRequest::WriteStdin {
                id,
                approval_id,
                environment_id,
                process_id,
                original_command,
                input,
                cwd,
                tty,
                sandbox_permissions,
                additional_permissions,
            },
            Self::ApplyPatch {
                id,
                environment_id,
                cwd,
                files,
                patch,
                ..
            } => crate::guardian::GuardianApprovalRequest::ApplyPatch {
                id,
                environment_id,
                cwd,
                files,
                patch,
            },
            Self::McpToolCall {
                id,
                server,
                tool_name,
                arguments,
                connector_id,
                connector_name,
                connector_description,
                connected_account_email,
                tool_title,
                tool_description,
                annotations,
                ..
            } => crate::guardian::GuardianApprovalRequest::McpToolCall {
                id,
                server,
                tool_name,
                arguments,
                connector_id,
                connector_name,
                connector_description,
                connected_account_email,
                tool_title,
                tool_description,
                annotations,
            },
            Self::NetworkAccess {
                id,
                turn_id,
                environment_id,
                target,
                host,
                protocol,
                port,
                trigger,
                ..
            } => crate::guardian::GuardianApprovalRequest::NetworkAccess {
                id,
                turn_id,
                environment_id,
                target,
                host,
                protocol,
                port,
                trigger,
            },
            Self::RequestPermissions {
                id,
                environment_id,
                turn_id,
                reason,
                permissions,
            } => crate::guardian::GuardianApprovalRequest::RequestPermissions {
                id,
                environment_id,
                turn_id,
                reason,
                permissions,
            },
        })
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum ApprovalResolutionSource {
    Hook,
    Guardian,
    User,
}

/// Preserves the approval path even when the decision becomes a tool error.
/// The source identifies the reviewer, not the actor that caused cancellation.
#[must_use]
pub(crate) struct ApprovalResult {
    pub(crate) result: Result<ReviewDecision, ToolError>,
    pub(crate) source: Option<ApprovalResolutionSource>,
    pub(crate) cancellation_reason: Option<McpApprovalCancellationReason>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct ApprovalResolution {
    pub(crate) decision: ReviewDecision,
    pub(crate) source: ApprovalResolutionSource,
    pub(crate) cancellation_reason: Option<McpApprovalCancellationReason>,
}

impl ApprovalResolution {
    pub(super) fn user(decision: ReviewDecision) -> Self {
        Self {
            decision,
            source: ApprovalResolutionSource::User,
            cancellation_reason: None,
        }
    }

    fn into_tool_result(self, model_info: &ModelInfo) -> Result<ReviewDecision, ToolError> {
        let source = self.source;
        match self.decision {
            ReviewDecision::ApprovedMcpPolicyAmendment => {
                error!("Tool approval received ApprovedMcpPolicyAmendment");
                Err(ToolError::Rejected(
                    "Error while requesting approval".to_string(),
                ))
            }
            ReviewDecision::NetworkPolicyAmendment {
                network_policy_amendment,
            } if network_policy_amendment.action == NetworkPolicyRuleAction::Deny => {
                let rejection = match source {
                    ApprovalResolutionSource::Hook => "rejected by configuration",
                    ApprovalResolutionSource::Guardian => {
                        "automatic approval review denied the action"
                    }
                    ApprovalResolutionSource::User => "rejected by user",
                };
                Err(ToolError::Rejected(rejection.to_string()))
            }
            ReviewDecision::Denied { rejection } => Err(ToolError::Rejected(rejection)),
            ReviewDecision::TimedOut => Err(ToolError::Rejected(
                ResolvedModelMessages::from_model(model_info)
                    .auto_review()
                    .timeout_instructions
                    .to_string(),
            )),
            ReviewDecision::Abort => Err(ToolError::Codex(CodexErr::TurnAborted)),
            decision => Ok(decision),
        }
    }
}
