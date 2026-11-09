use crate::config::NetworkProxySpec;
use crate::environment_selection::TurnEnvironmentSnapshot;
use crate::guardian::GuardianNetworkAccessTrigger;
use crate::guardian::GuardianReviewContext;
use crate::network_policy_decision::denied_network_policy_message;
use crate::session::session::Session;
use crate::session::turn_context::TurnContext;
use crate::session::turn_context::TurnEnvironment;
use crate::tools::events::truncate_rejection_message;
use crate::tools::sandboxing::ToolError;
use codex_features::Feature;
use codex_network_proxy::BlockedRequest;
use codex_network_proxy::BlockedRequestObserver;
use codex_network_proxy::EnvironmentNetworkPolicy;
use codex_network_proxy::NetworkDecision;
use codex_network_proxy::NetworkPolicyDecider;
use codex_network_proxy::NetworkPolicyRequest;
use codex_network_proxy::NetworkProtocol;
use codex_network_proxy::NetworkProxy;
use codex_network_proxy::NetworkRequestCancellation;
use codex_network_proxy::NetworkRequestCancellationReason;
use codex_network_proxy::NetworkRequestDisconnect;
use codex_network_proxy::RemoteCredentialMasking;
use codex_network_proxy::RemoteNetworkProxyLaunchConfig;
use codex_protocol::approvals::NetworkApprovalContext;
use codex_protocol::approvals::NetworkApprovalProtocol;
use codex_protocol::approvals::NetworkPolicyRuleAction;
use codex_protocol::error::CodexErr;
use codex_protocol::models::PermissionProfile;
use codex_protocol::protocol::AskForApproval;
use codex_protocol::protocol::Event;
use codex_protocol::protocol::EventMsg;
use codex_protocol::protocol::ReviewDecision;
use codex_protocol::protocol::WarningEvent;
use codex_sandboxing::record_network_sandbox_violation;
use codex_tools::ToolName;
use codex_utils_path_uri::Platform;
use indexmap::IndexMap;
use std::collections::HashMap;
use std::collections::HashSet;
use std::io;
use std::sync::Arc;
use std::sync::Mutex as SyncMutex;
use std::sync::OnceLock as SyncOnceLock;
use tokio::sync::Mutex;
use tokio::sync::Notify;
use tokio::sync::OnceCell;
use tokio::sync::OwnedSemaphorePermit;
use tokio::sync::RwLock;
use tokio::sync::Semaphore;
use tokio_util::sync::CancellationToken;
use tracing::error;
use tracing::warn;
use uuid::Uuid;

const REASON_NOT_ALLOWED: &str = "not_allowed";
const ABANDONED_NETWORK_APPROVAL_MESSAGE: &str =
    "network approval was cancelled before a decision was returned";

#[derive(Debug)]
pub(crate) struct NetworkApprovalSpec {
    pub network: Option<NetworkProxy>,
    pub trigger: GuardianNetworkAccessTrigger,
    /// Preserve the typed identity independently of Guardian's display-name payload.
    pub tool_name: ToolName,
    pub command: String,
    pub environment_id: String,
    pub permission_profile: PermissionProfile,
    pub network_policy: Option<EnvironmentNetworkPolicy>,
}

#[derive(Clone, Debug)]
pub(crate) struct DeferredNetworkApproval {
    registration_id: String,
    cancellation_token: CancellationToken,
    finish_outcome: Arc<OnceCell<Option<String>>>,
    _execution_proxy: Option<NetworkProxy>,
}

impl DeferredNetworkApproval {
    pub(crate) fn registration_id(&self) -> &str {
        &self.registration_id
    }

    pub(crate) fn cancellation_token(&self) -> CancellationToken {
        self.cancellation_token.clone()
    }

    pub(crate) fn is_cancelled(&self) -> bool {
        self.cancellation_token.is_cancelled()
    }

    async fn finish(&self, service: &NetworkApprovalService) -> Result<(), ToolError> {
        let outcome = self
            .finish_outcome
            .get_or_init(|| async { service.finish_call_outcome(&self.registration_id).await })
            .await
            .clone();
        let outcome =
            outcome.or_else(|| abandoned_network_approval_outcome(&self.cancellation_token));
        network_approval_outcome_to_result(outcome)
    }
}

#[derive(Debug)]
pub(crate) struct ActiveNetworkApproval {
    registration_id: Option<String>,
    cancellation_token: CancellationToken,
    execution_proxy: NetworkProxy,
}

impl ActiveNetworkApproval {
    pub(crate) fn cancellation_token(&self) -> CancellationToken {
        self.cancellation_token.clone()
    }

    pub(crate) fn execution_proxy(&self) -> &NetworkProxy {
        &self.execution_proxy
    }

    pub(crate) fn into_deferred(self) -> Option<DeferredNetworkApproval> {
        let ActiveNetworkApproval {
            registration_id,
            cancellation_token,
            execution_proxy,
        } = self;
        registration_id.map(|registration_id| DeferredNetworkApproval {
            registration_id,
            cancellation_token,
            finish_outcome: Arc::new(OnceCell::new()),
            _execution_proxy: Some(execution_proxy),
        })
    }
}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
struct HostApprovalKey {
    environment_id: String,
    host: String,
    protocol: &'static str,
    port: u16,
}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
struct PendingHostApprovalKey {
    host: HostApprovalKey,
    turn_id: String,
    execution_id: Option<String>,
}

impl HostApprovalKey {
    fn from_request(
        request: &NetworkPolicyRequest,
        protocol: NetworkApprovalProtocol,
        environment_id: String,
    ) -> Self {
        Self {
            environment_id,
            host: request.host.to_ascii_lowercase(),
            protocol: protocol_key_label(protocol),
            port: request.port,
        }
    }
}

fn protocol_key_label(protocol: NetworkApprovalProtocol) -> &'static str {
    match protocol {
        NetworkApprovalProtocol::Http => "http",
        NetworkApprovalProtocol::Https => "https",
        NetworkApprovalProtocol::Socks5Tcp => "socks5-tcp",
        NetworkApprovalProtocol::Socks5Udp => "socks5-udp",
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum PendingApprovalDecision {
    AllowOnce,
    AllowForSession,
    Deny,
}

fn network_approval_outcome_to_result(outcome: Option<String>) -> Result<(), ToolError> {
    match outcome {
        Some(rejection) => Err(ToolError::Rejected(truncate_rejection_message(&rejection))),
        None => Ok(()),
    }
}

fn abandoned_network_approval_outcome(cancellation_token: &CancellationToken) -> Option<String> {
    cancellation_token
        .is_cancelled()
        .then(|| ABANDONED_NETWORK_APPROVAL_MESSAGE.to_string())
}

/// Whether an allowlist miss may be reviewed instead of hard-denied.
fn allows_network_approval_flow(policy: AskForApproval) -> bool {
    !matches!(policy, AskForApproval::Never)
}

fn permission_profile_allows_network_approval_flow(permission_profile: &PermissionProfile) -> bool {
    matches!(permission_profile, PermissionProfile::Managed { .. })
}

impl PendingApprovalDecision {
    fn to_network_decision(self) -> NetworkDecision {
        match self {
            Self::AllowOnce | Self::AllowForSession => NetworkDecision::Allow,
            Self::Deny => NetworkDecision::deny(REASON_NOT_ALLOWED),
        }
    }
}

struct PendingHostApproval {
    decision: SyncOnceLock<PendingApprovalDecision>,
    notify: Notify,
}

impl PendingHostApproval {
    fn new() -> Self {
        Self {
            decision: SyncOnceLock::new(),
            notify: Notify::new(),
        }
    }

    async fn wait_for_decision(&self) -> PendingApprovalDecision {
        loop {
            let notified = self.notify.notified();
            if let Some(decision) = self.decision.get() {
                return *decision;
            }
            notified.await;
        }
    }

    fn set_decision(&self, decision: PendingApprovalDecision) {
        if self.decision.set(decision).is_ok() {
            self.notify.notify_waiters();
        }
    }
}

struct ActiveNetworkApprovalCall {
    registration_id: String,
    turn_id: String,
    trigger: GuardianNetworkAccessTrigger,
    tool_name: ToolName,
    command: String,
    environment_id: String,
    permission_profile: PermissionProfile,
    environments: TurnEnvironmentSnapshot,
    cancellation_token: CancellationToken,
}

enum ActiveNetworkApprovalAttribution {
    None,
    Single(Arc<ActiveNetworkApprovalCall>),
    Ambiguous,
}

struct NetworkRequestAttribution {
    owner_call: Option<Arc<ActiveNetworkApprovalCall>>,
    environment_id: Option<String>,
}

#[derive(Default)]
struct NetworkApprovalCallState {
    active_calls: IndexMap<String, Arc<ActiveNetworkApprovalCall>>,
    call_outcomes: HashMap<String, String>,
}

#[derive(Default)]
struct PendingNetworkApprovals {
    executions: HashMap<PendingHostApprovalKey, Arc<PendingHostApproval>>,
    manual_prompts: HashMap<HostApprovalKey, Arc<Semaphore>>,
}

pub(crate) struct NetworkApprovalService {
    // Pending-request cleanup must publish a tool outcome synchronously before cancelling it.
    calls: SyncMutex<NetworkApprovalCallState>,
    // Owner cleanup runs from Drop, so this lock cannot require an async task.
    pending_host_approvals: SyncMutex<PendingNetworkApprovals>,
    // Keep persisted session policy and the in-memory approval caches in the same order.
    session_policy_commit_lock: Mutex<()>,
    session_approved_hosts: Mutex<HashSet<HostApprovalKey>>,
    session_denied_hosts: Mutex<HashSet<HostApprovalKey>>,
}

/// Removes and resolves the exact pending generation created by one request.
///
/// Dropping an unfinished owner fails its existing waiters closed without
/// deleting a newer same-host request.
struct PendingHostApprovalOwner<'a> {
    service: &'a NetworkApprovalService,
    key: PendingHostApprovalKey,
    pending: Arc<PendingHostApproval>,
    execution_cancellation: Option<CancellationToken>,
    disconnect: Option<NetworkRequestDisconnect>,
    cancellation: Option<NetworkRequestCancellation>,
    decision_on_drop: PendingApprovalDecision,
    prompt_permit: Option<OwnedSemaphorePermit>,
    completed: bool,
}

impl<'a> PendingHostApprovalOwner<'a> {
    fn new(
        service: &'a NetworkApprovalService,
        key: PendingHostApprovalKey,
        pending: Arc<PendingHostApproval>,
        execution_cancellation: Option<CancellationToken>,
    ) -> Self {
        Self {
            service,
            key,
            pending,
            execution_cancellation,
            disconnect: None,
            cancellation: None,
            decision_on_drop: PendingApprovalDecision::Deny,
            prompt_permit: None,
            completed: false,
        }
    }

    /// Keep the endpoint's prompt until this execution commits or abandons its decision.

    fn set_decision_on_drop(&mut self, decision: PendingApprovalDecision) {
        self.decision_on_drop = decision;
    }

    fn complete(mut self, decision: PendingApprovalDecision) {
        self.cancel_execution_if_denied(decision);
        self.publish_and_remove(decision);
        self.completed = true;
    }

    fn cancel_execution_if_denied(&self, decision: PendingApprovalDecision) {
        if matches!(decision, PendingApprovalDecision::Deny)
            && let Some(execution_cancellation) = &self.execution_cancellation
        {
            execution_cancellation.cancel();
        }
    }

    fn publish_and_remove(&mut self, decision: PendingApprovalDecision) {
        // Remove this generation before waking its waiters so a concurrent retry
        // cannot attach to an already-completed approval.
        let mut approvals = self
            .service
            .pending_host_approvals
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if let Some(queue) = approvals.manual_prompts.get(&self.key.host) {
            let permit = self.prompt_permit.take();
            if permit.is_some() && decision == PendingApprovalDecision::AllowForSession {
                queue.close();
            }
            // Returning the permit lets one waiter take over the prompt.
            drop(permit);
            // Each queued or active execution holds a reference in addition to this map.
            if queue.is_closed() || Arc::strong_count(queue) == 1 {
                approvals.manual_prompts.remove(&self.key.host);
            }
        }
        if approvals
            .executions
            .get(&self.key)
            .is_some_and(|current| Arc::ptr_eq(current, &self.pending))
        {
            approvals.executions.remove(&self.key);
        }
        drop(approvals);
        self.pending.set_decision(decision);
    }
}

impl Drop for PendingHostApprovalOwner<'_> {
    fn drop(&mut self) {
        if !self.completed {
            if self
                .cancellation
                .as_ref()
                .and_then(NetworkRequestCancellation::reason)
                == Some(NetworkRequestCancellationReason::ProcessFinished)
            {
                // Normal process cleanup is not a new review failure. Any earlier
                // denial is already recorded in the call outcome.
                self.publish_and_remove(self.decision_on_drop);
                return;
            }
            if matches!(self.decision_on_drop, PendingApprovalDecision::Deny)
                && let Some(registration_id) = self.key.execution_id.as_deref()
                && let Some(elapsed) = self
                    .disconnect
                    .as_ref()
                    .and_then(NetworkRequestDisconnect::elapsed)
            {
                let elapsed_ms = elapsed.as_millis();
                self.service.record_call_outcome_if_absent(registration_id, format!(
                    "Network request disconnected after {elapsed_ms} ms, before approval could complete"
                ));
            }
            self.cancel_execution_if_denied(self.decision_on_drop);
            self.publish_and_remove(self.decision_on_drop);
        }
    }
}

impl Default for NetworkApprovalService {
    fn default() -> Self {
        Self {
            calls: SyncMutex::new(NetworkApprovalCallState::default()),
            pending_host_approvals: SyncMutex::default(),
            session_policy_commit_lock: Mutex::new(()),
            session_approved_hosts: Mutex::new(HashSet::new()),
            session_denied_hosts: Mutex::new(HashSet::new()),
        }
    }
}

impl NetworkApprovalService {
    /// Replace the target session's approval cache with the source session's
    /// currently approved hosts.
    #[expect(
        clippy::await_holding_invalid_type,
        reason = "the approval snapshot must not interleave with a session policy commit"
    )]
    pub(crate) async fn sync_session_approved_hosts_to(&self, other: &Self) {
        let _commit_guard = self.session_policy_commit_lock.lock().await;
        let approved_hosts = self.session_approved_hosts.lock().await.clone();
        let mut other_approved_hosts = other.session_approved_hosts.lock().await;
        other_approved_hosts.clear();
        other_approved_hosts.extend(approved_hosts.iter().cloned());
    }

    async fn register_call(&self, call: ActiveNetworkApprovalCall) {
        let mut calls = self
            .calls
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        calls
            .active_calls
            .insert(call.registration_id.clone(), Arc::new(call));
    }

    pub(crate) async fn unregister_call(&self, registration_id: &str) {
        self.remove_call(registration_id).await;
    }

    async fn resolve_single_active_call(&self) -> Option<Arc<ActiveNetworkApprovalCall>> {
        let calls = self
            .calls
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        // Shared proxy requests can still arrive without an execution ID. Only pick an owner when
        // there is exactly one candidate; with concurrent calls, canceling one would be a guess.
        if calls.active_calls.len() == 1 {
            return calls.active_calls.values().next().cloned();
        }

        None
    }

    async fn resolve_active_call_by_execution_id(
        &self,
        execution_id: &str,
    ) -> Option<Arc<ActiveNetworkApprovalCall>> {
        self.calls
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .active_calls
            .get(execution_id)
            .cloned()
    }

    async fn resolve_active_call_attribution(&self) -> ActiveNetworkApprovalAttribution {
        let calls = self
            .calls
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        match calls.active_calls.len() {
            0 => ActiveNetworkApprovalAttribution::None,
            1 => calls.active_calls.values().next().cloned().map_or(
                ActiveNetworkApprovalAttribution::None,
                ActiveNetworkApprovalAttribution::Single,
            ),
            _ => ActiveNetworkApprovalAttribution::Ambiguous,
        }
    }

    async fn resolve_request_attribution(
        &self,
        request: &NetworkPolicyRequest,
    ) -> Option<NetworkRequestAttribution> {
        if let Some(execution_id) = request.execution_id.as_deref() {
            let call = self
                .resolve_active_call_by_execution_id(execution_id)
                .await?;
            let environment_id = request
                .environment_id
                .clone()
                .unwrap_or_else(|| call.environment_id.clone());
            return (call.environment_id == environment_id).then_some(NetworkRequestAttribution {
                owner_call: Some(call),
                environment_id: Some(environment_id),
            });
        }

        if let Some(environment_id) = request.environment_id.clone() {
            let owner_call = match self.resolve_active_call_attribution().await {
                ActiveNetworkApprovalAttribution::Single(call) => {
                    (call.environment_id == environment_id).then_some(call)
                }
                ActiveNetworkApprovalAttribution::None
                | ActiveNetworkApprovalAttribution::Ambiguous => None,
            };
            return Some(NetworkRequestAttribution {
                owner_call,
                environment_id: Some(environment_id),
            });
        }

        match self.resolve_active_call_attribution().await {
            ActiveNetworkApprovalAttribution::None => Some(NetworkRequestAttribution {
                owner_call: None,
                environment_id: None,
            }),
            ActiveNetworkApprovalAttribution::Single(call) => {
                let environment_id = call.environment_id.clone();
                Some(NetworkRequestAttribution {
                    owner_call: Some(call),
                    environment_id: Some(environment_id),
                })
            }
            ActiveNetworkApprovalAttribution::Ambiguous => None,
        }
    }

    fn get_or_create_pending_approval(
        &self,
        key: PendingHostApprovalKey,
    ) -> (Arc<PendingHostApproval>, bool) {
        let mut pending = self
            .pending_host_approvals
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if let Some(existing) = pending.executions.get(&key).cloned() {
            return (existing, false);
        }

        let created = Arc::new(PendingHostApproval::new());
        pending.executions.insert(key, Arc::clone(&created));
        (created, true)
    }

    #[expect(
        clippy::await_holding_invalid_type,
        reason = "read both host caches under the session policy commit lock"
    )]
    async fn session_host_decision(&self, key: &HostApprovalKey) -> Option<NetworkDecision> {
        let _commit_guard = self.session_policy_commit_lock.lock().await;
        if self.session_denied_hosts.lock().await.contains(key) {
            return Some(NetworkDecision::deny(REASON_NOT_ALLOWED));
        }
        self.session_approved_hosts
            .lock()
            .await
            .contains(key)
            .then_some(NetworkDecision::Allow)
    }

    #[cfg(test)]
    async fn take_call_outcome(&self, registration_id: &str) -> Option<String> {
        let mut calls = self
            .calls
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        calls.call_outcomes.remove(registration_id)
    }

    fn record_call_outcome(&self, registration_id: &str, outcome: String) {
        if let Some(cancellation_token) = self.store_call_outcome(registration_id, outcome) {
            cancellation_token.cancel();
        }
    }

    fn store_call_outcome(
        &self,
        registration_id: &str,
        outcome: String,
    ) -> Option<CancellationToken> {
        let mut calls = self
            .calls
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let call = calls.active_calls.get(registration_id)?.clone();
        // Explicit network-review outcomes replace generic blocked-request fallbacks.
        calls
            .call_outcomes
            .insert(registration_id.to_string(), outcome);

        Some(call.cancellation_token.clone())
    }

    fn record_call_outcome_if_absent(&self, registration_id: &str, outcome: String) {
        let mut calls = self
            .calls
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let Some(call) = calls.active_calls.get(registration_id).cloned() else {
            return;
        };
        // Cancelling an execution can disconnect its other pending requests.
        // Those fallbacks must not replace the reason it was cancelled.
        calls
            .call_outcomes
            .entry(registration_id.to_string())
            .or_insert(outcome);

        drop(calls);
        call.cancellation_token.cancel();
    }

    async fn remove_call(&self, registration_id: &str) -> Option<String> {
        let mut calls = self
            .calls
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        calls.active_calls.shift_remove(registration_id);
        calls.call_outcomes.remove(registration_id)
    }

    async fn finish_call_outcome(&self, registration_id: &str) -> Option<String> {
        self.remove_call(registration_id).await
    }

    pub(crate) async fn record_blocked_request(&self, blocked: BlockedRequest) {
        let Some(message) = denied_network_policy_message(&blocked) else {
            return;
        };

        let owner_call = if let Some(execution_id) = blocked.execution_id.as_deref() {
            self.resolve_active_call_by_execution_id(execution_id).await
        } else {
            self.resolve_single_active_call().await
        };
        let Some(owner_call) = owner_call else {
            return;
        };

        self.record_call_outcome_if_absent(&owner_call.registration_id, message);
    }

    fn format_network_target(protocol: &str, host: &str, port: u16) -> String {
        format!("{protocol}://{host}:{port}")
    }

    fn approval_id_for_key(key: &HostApprovalKey) -> String {
        format!(
            "network#{}#{}#{}#{}",
            key.environment_id, key.protocol, key.host, key.port
        )
    }
}

pub(crate) fn build_blocked_request_observer(
    network_approval: Arc<NetworkApprovalService>,
) -> Arc<dyn BlockedRequestObserver> {
    Arc::new(move |blocked: BlockedRequest| {
        let network_approval = Arc::clone(&network_approval);
        async move {
            record_network_sandbox_violation(&blocked);
            network_approval.record_blocked_request(blocked).await;
        }
    })
}

pub(crate) async fn begin_network_approval(
    session: &Arc<Session>,
    turn: &TurnContext,
    environments: &TurnEnvironmentSnapshot,
    managed_network_active: bool,
    spec: Option<NetworkApprovalSpec>,
) -> Result<Option<ActiveNetworkApproval>, ToolError> {
    let NetworkApprovalSpec {
        network,
        trigger,
        tool_name,
        command,
        environment_id,
        permission_profile,
        network_policy,
    } = match spec {
        Some(spec) => spec,
        None => return Ok(None),
    };
    if !managed_network_active {
        return Ok(None);
    }

    let registration_id = Uuid::new_v4().to_string();
    let Some(execution_proxy) = execution_network_proxy(
        session,
        turn,
        environments
            .turn_environments()
            .find(|environment| environment.selection.environment_id == environment_id),
        network,
        &environment_id,
        &permission_profile,
        network_policy.as_ref(),
        &registration_id,
        None,
    )
    .await?
    else {
        return Ok(None);
    };
    let cancellation_token = CancellationToken::new();
    session
        .services
        .network_approval
        .register_call(ActiveNetworkApprovalCall {
            registration_id: registration_id.clone(),
            turn_id: turn.sub_id.clone(),
            trigger,
            tool_name,
            command,
            environment_id,
            permission_profile,
            environments: environments.clone(),
            cancellation_token: cancellation_token.clone(),
        })
        .await;

    Ok(Some(ActiveNetworkApproval {
        registration_id: Some(registration_id),
        cancellation_token,
        execution_proxy,
    }))
}

/// Resolve the issuing environment's network policy under controller requirements and
/// return a proxy with unique execution attribution. Does not register an approval flow.
#[allow(clippy::too_many_arguments)]
pub(crate) async fn execution_network_proxy(
    session: &Session,
    turn: &TurnContext,
    environment: Option<&TurnEnvironment>,
    network: Option<NetworkProxy>,
    environment_id: &str,
    permission_profile: &PermissionProfile,
    network_policy: Option<&EnvironmentNetworkPolicy>,
    registration_id: &str,
    fallback_policy_decider: Option<Arc<dyn NetworkPolicyDecider>>,
) -> Result<Option<NetworkProxy>, ToolError> {
    let controller = turn.config.permissions.network.as_ref();
    let owner_spec = network_policy
        .map(|policy| {
            // Resolve owner policy once, where the command's actual enforcing proxy is built.
            NetworkProxySpec::for_environment(
                controller,
                policy,
                permission_profile,
                session.services.exec_policy.current().as_ref(),
                environment.map_or(
                    codex_network_proxy::LocalBindingPolicy::DefaultFalse,
                    |environment| {
                        crate::windows_sandbox::local_binding_policy_for_sandbox(
                            environment.config().windows_sandbox_type,
                            environment.executor_platform_os.as_deref(),
                        )
                    },
                ),
            )
        })
        .transpose()
        .map_err(|error| {
            ToolError::Rejected(format!(
                "failed to resolve environment network policy: {error}"
            ))
        })?;
    let network = if let Some(owner_spec) = owner_spec.as_ref() {
        if controller.is_some() {
            network
                .or_else(|| {
                    session
                        .services
                        .network_proxy
                        .load_full()
                        .map(|controller| controller.proxy())
                })
                .ok_or_else(|| {
                    ToolError::Rejected(
                        "environment network policy requires its configured controller proxy"
                            .to_string(),
                    )
                })?
        } else {
            // This carrier never listens: the executor starts the real per-command proxy.
            let executor_os = Platform::from_platform_os(
                environment.and_then(|environment| environment.executor_platform_os.as_deref()),
            );
            let state = owner_spec
                .build_state_with_audit_metadata(
                    session.services.network_proxy_audit_metadata.clone(),
                    executor_os,
                    codex_network_proxy::LocalBindingPolicy::DefaultFalse,
                )
                .map_err(|error| {
                    ToolError::Rejected(format!(
                        "failed to build environment network policy: {error}"
                    ))
                })?;
            NetworkProxy::builder()
                .state(Arc::new(state))
                .managed_by_codex(/*managed_by_codex*/ false)
                .build()
                .await
                .map_err(|error| {
                    ToolError::Rejected(format!(
                        "failed to build execution-scoped network proxy: {error}"
                    ))
                })?
        }
    } else if let Some(network) = network {
        network
    } else {
        return Ok(None);
    };
    let environment_policy = owner_spec.map(|spec| spec.environment_policy());
    let attribution_token = Uuid::new_v4().to_string();
    let execution_proxy = network
        .for_execution(
            environment_id,
            registration_id,
            attribution_token,
            environment_policy,
            fallback_policy_decider,
        )
        .map_err(|err| {
            ToolError::Codex(CodexErr::Io(io::Error::other(format!(
                "failed to create execution-scoped network proxy: {err}"
            ))))
        })?;
    Ok(Some(execution_proxy))
}

/// Resolve a proxy launch against the executor's capabilities, failing closed if required.
pub(crate) async fn remote_network_proxy_launch(
    environment: &TurnEnvironment,
    network: &NetworkProxy,
    turn: &TurnContext,
) -> Result<(RemoteNetworkProxyLaunchConfig, RemoteCredentialMasking), ToolError> {
    let network_proxy = environment
        .environment
        .info()
        .await
        .map_err(|err| {
            ToolError::Codex(CodexErr::Io(io::Error::other(format!(
                "failed to query exec-server capabilities: {err}"
            ))))
        })?
        .capabilities
        .network_proxy();
    let credential_masking = if turn.config.features.enabled(Feature::CredentialMasking) {
        network_proxy.credential_masking
    } else {
        RemoteCredentialMasking::Disabled
    };
    let launch = network
        .remote_launch_config(
            crate::windows_sandbox::local_binding_policy_for_sandbox(
                environment.config().windows_sandbox_type,
                environment.executor_platform_os.as_deref(),
            ),
            credential_masking,
        )
        .await
        .map_err(|err| ToolError::Codex(CodexErr::Io(io::Error::other(err))))?;
    if launch.proxy.enabled && !network_proxy.launch {
        return Err(ToolError::Rejected(
            "selected exec-server does not support executor-local network proxy launches"
                .to_string(),
        ));
    }
    Ok((launch, credential_masking))
}

pub(crate) async fn finish_deferred_network_approval(
    session: &Session,
    deferred: Option<DeferredNetworkApproval>,
) -> Result<(), ToolError> {
    let Some(deferred) = deferred else {
        return Ok(());
    };
    deferred.finish(&session.services.network_approval).await
}

#[cfg(test)]
#[path = "network_approval_tests.rs"]
mod tests;
