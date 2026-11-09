//! Coordinates tool approval, sandbox selection, and retries after sandbox denial.
//! Guardian approval applies to the reviewed authority; escalating a sandboxed
//! attempt requires a fresh review of the retry.
use crate::config::NetworkProxySpec;
use crate::guardian::GuardianReviewContext;
use crate::network_policy_decision::network_approval_context_from_payload;
use crate::sandboxing::SandboxPermissions;
use crate::tools::flat_tool_name;
use crate::tools::network_approval::ActiveNetworkApproval;
use crate::tools::network_approval::DeferredNetworkApproval;
use crate::tools::network_approval::NetworkApprovalSpec;
use crate::tools::network_approval::begin_network_approval;
use crate::tools::network_approval::finish_deferred_network_approval;
use crate::tools::sandboxing::ExecApprovalRequirement;
use crate::tools::sandboxing::SandboxAttempt;
use crate::tools::sandboxing::SandboxOverride;
use crate::tools::sandboxing::ToolCtx;
use crate::tools::sandboxing::ToolError;
use crate::tools::sandboxing::ToolRuntime;
use crate::tools::sandboxing::default_exec_approval_requirement;
use crate::tools::sandboxing::ensure_native_sandbox;
use crate::tools::sandboxing::sandbox_override_for_first_attempt;
use crate::tools::sandboxing::unsandboxed_execution_allowed;
use codex_otel::ToolDecisionSource;
use codex_protocol::error::CodexErrorDetails;
use codex_protocol::error::SandboxErr;
use codex_protocol::exec_output::ExecToolCallOutput;
use codex_protocol::models::PermissionProfile;
use codex_protocol::permissions::PROTECTED_METADATA_PATH_NAMES;
use codex_protocol::permissions::file_system_root;
use codex_protocol::protocol::AskForApproval;
use codex_protocol::protocol::ReviewDecision;
use codex_sandboxing::SandboxManager;
use codex_sandboxing::SandboxType;
use codex_sandboxing::policy_transforms::effective_network_sandbox_policy;
use std::sync::Arc;
use std::time::Instant;

pub(crate) struct ToolOrchestrator;

pub(crate) struct OrchestratorRunResult<Out> {
    pub output: Out,
    pub deferred_network_approval: Option<DeferredNetworkApproval>,
}

impl ToolOrchestrator {
    pub fn new() -> Self {
        Self
    }

    async fn run_attempt<Rq, Out, T>(
        tool: &mut T,
        req: &Rq,
        tool_ctx: &ToolCtx,
        attempt: &SandboxAttempt<'_>,
        network_approval_spec: Option<NetworkApprovalSpec>,
    ) -> (Result<Out, ToolError>, Option<DeferredNetworkApproval>)
    where
        T: ToolRuntime<Rq, Out>,
    {
        let network_approval = match begin_network_approval(
            &tool_ctx.session,
            &tool_ctx.step_context.turn,
            &tool_ctx.step_context.environments,
            attempt.enforce_managed_network,
            network_approval_spec,
        )
        .await
        {
            Ok(network_approval) => network_approval,
            Err(err) => return (Err(err), None),
        };

        let attempt_tool_ctx = ToolCtx {
            session: tool_ctx.session.clone(),
            step_context: Arc::clone(&tool_ctx.step_context),
            cancellation_token: tool_ctx.cancellation_token.clone(),
            call_id: tool_ctx.call_id.clone(),
            tool_name: tool_ctx.tool_name.clone(),
        };
        let attempt_with_network_approval = SandboxAttempt {
            sandbox_override: attempt.sandbox_override,
            sandbox: attempt.sandbox,
            sandbox_requested: attempt.sandbox_requested,
            permissions: attempt.permissions,
            exec_server_permissions: attempt.exec_server_permissions,
            enforce_managed_network: attempt.enforce_managed_network,
            manager: attempt.manager,
            sandbox_cwd: attempt.sandbox_cwd,
            workspace_roots: attempt.workspace_roots,
            sandbox_exe: attempt.sandbox_exe,
            use_legacy_landlock: attempt.use_legacy_landlock,
            windows_sandbox_type: attempt.windows_sandbox_type,
            windows_sandbox_level: attempt.windows_sandbox_level,
            network_denial_cancellation_token: network_approval
                .as_ref()
                .map(ActiveNetworkApproval::cancellation_token),
            network_proxy: network_approval
                .as_ref()
                .map(ActiveNetworkApproval::execution_proxy),
        };
        let run_result = tool
            .run(req, &attempt_with_network_approval, &attempt_tool_ctx)
            .await;

        let Some(network_approval) = network_approval else {
            return (run_result, None);
        };

        let deferred = network_approval.into_deferred();
        if run_result.is_err() {
            let finalize_result =
                finish_deferred_network_approval(&tool_ctx.session, deferred).await;
            if let Err(err) = finalize_result {
                return (Err(err), None);
            }
            return (run_result, None);
        }
        (run_result, deferred)
    }

    pub async fn run<Rq, Out, T>(
        &mut self,
        tool: &mut T,
        req: &Rq,
        tool_ctx: &ToolCtx,
    ) -> Result<OrchestratorRunResult<Out>, ToolError>
    where
        T: ToolRuntime<Rq, Out>,
    {
        let environment = tool.turn_environment(req);
        let sandbox_manager = SandboxManager::new();
        let permission_profile = environment.permission_profile();
        let workspace_roots = environment.workspace_roots();
        let sandbox_config = environment.config();
        let sandbox_cwd = tool
            .sandbox_cwd(req)
            .cloned()
            .unwrap_or_else(|| environment.cwd().clone());
        let windows_sandbox_type = codex_protocol::sandbox::effective_windows_sandbox_type(
            sandbox_config.windows_sandbox_type,
            sandbox_config.windows_sandbox_level,
        );
        let attempt = SandboxAttempt {
            sandbox_override: SandboxOverride::BypassSandboxFirstAttempt,
            sandbox: SandboxType::None,
            sandbox_requested: false,
            permissions: permission_profile,
            exec_server_permissions: permission_profile,
            enforce_managed_network: false,
            manager: &sandbox_manager,
            sandbox_cwd: &sandbox_cwd,
            workspace_roots,
            sandbox_exe: None,
            use_legacy_landlock: sandbox_config.use_legacy_landlock,
            windows_sandbox_type,
            windows_sandbox_level: sandbox_config.windows_sandbox_level,
            network_denial_cancellation_token: None,
            network_proxy: None,
        };
        let (output, deferred_network_approval) =
            Self::run_attempt(tool, req, tool_ctx, &attempt, None).await;
        Ok(OrchestratorRunResult {
            output: output?,
            deferred_network_approval,
        })
    }
}

fn sandbox_outcome_from_tool_error(err: &ToolError) -> Option<&'static str> {
    match err {
        ToolError::Codex(err) => match err.details() {
            CodexErrorDetails::Sandbox(SandboxErr::Denied { .. }) => Some("denied"),
            CodexErrorDetails::Sandbox(SandboxErr::Timeout { .. }) => Some("timed_out"),
            CodexErrorDetails::Sandbox(SandboxErr::Signal(_)) => Some("signal"),
            _ => None,
        },
        ToolError::Rejected(_) => None,
    }
}

fn build_denial_reason_from_output(_output: &ExecToolCallOutput) -> String {
    // Keep approval reason terse and stable for UX/tests, but accept the
    // output so we can evolve heuristics later without touching call sites.
    "command failed; retry without sandbox?".to_string()
}
