//! A session-local UI action for the existing owner-authorized EL2 interface.
//! This does not introduce a permission profile or change the CLI's execution level.

use codex_app_server_client::AppServerRequestHandle;
use codex_app_server_protocol::ClientRequest;
use codex_app_server_protocol::CommandExecParams;
use codex_app_server_protocol::CommandExecResponse;
use codex_app_server_protocol::RequestId;
use codex_app_server_protocol::SandboxPolicy;
use codex_protocol::ThreadId;
use serde::Deserialize;
use std::path::PathBuf;
use std::time::Duration;
use uuid::Uuid;

pub(crate) const LABEL: &str = "EL2 Control";

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
pub(crate) struct Activation {
    pub(crate) exception_level: u32,
    pub(crate) native_execution_verified: bool,
}

pub(crate) fn parse_activation(response: CommandExecResponse) -> Result<Activation, String> {
    if response.exit_code != 0 {
        return Err(format!(
            "The EL2 interface command exited with status {}.",
            response.exit_code
        ));
    }
    let activation: Activation = serde_json::from_str(&response.stdout)
        .map_err(|_| "The EL2 interface returned an invalid activation response.".to_string())?;
    if activation.exception_level != 2 || !activation.native_execution_verified {
        return Err("The interface did not verify native execution at EL2.".to_string());
    }
    Ok(activation)
}

fn activation_request(cwd: PathBuf) -> ClientRequest {
    ClientRequest::OneOffCommandExec {
        request_id: RequestId::String(format!("owner-el2-{}", Uuid::new_v4())),
        params: CommandExecParams {
            command: vec!["/usr/local/bin/el2-control".into(), "activate".into()],
            process_id: None,
            tty: false,
            stream_stdin: false,
            stream_stdout_stderr: false,
            output_bytes_cap: Some(16_384),
            disable_output_cap: false,
            disable_timeout: false,
            timeout_ms: Some(25_000),
            cwd: Some(cwd),
            env: None,
            size: None,
            sandbox_policy: Some(SandboxPolicy::DangerFullAccess),
            permission_profile: None,
        },
    }
}

pub(crate) async fn activate(
    handle: AppServerRequestHandle,
    cwd: PathBuf,
) -> Result<Activation, String> {
    let response: CommandExecResponse = tokio::time::timeout(
        Duration::from_secs(30),
        handle.request_typed(activation_request(cwd)),
    )
    .await
    .map_err(|_| "EL2 interface activation timed out.".to_string())?
    .map_err(|error| error.to_string())?;
    parse_activation(response)
}

#[derive(Default)]
pub(crate) struct SelectionState {
    verified_thread: Option<ThreadId>,
    pending: Option<(ThreadId, Uuid, bool)>,
}

impl SelectionState {
    pub(crate) fn confirm(&mut self, thread_id: ThreadId) -> Option<Uuid> {
        if self.pending.is_some() {
            return None;
        }
        let request_id = Uuid::new_v4();
        self.pending = Some((thread_id, request_id, false));
        Some(request_id)
    }

    pub(crate) fn start(&mut self, thread_id: ThreadId, request_id: Uuid) -> bool {
        if self.pending != Some((thread_id, request_id, false)) {
            return false;
        }
        self.pending = Some((thread_id, request_id, true));
        true
    }

    pub(crate) fn finish(&mut self, thread_id: ThreadId, request_id: Uuid) -> bool {
        if self.pending != Some((thread_id, request_id, true)) {
            return false;
        }
        self.pending = None;
        true
    }

    pub(crate) fn cancel(&mut self, thread_id: ThreadId, request_id: Uuid) {
        if self
            .pending
            .is_some_and(|(thread, request, _)| thread == thread_id && request == request_id)
        {
            self.pending = None;
        }
    }

    pub(crate) fn mark_verified(&mut self, thread_id: ThreadId) {
        self.verified_thread = Some(thread_id);
    }

    pub(crate) fn is_verified(&self, thread_id: ThreadId) -> bool {
        self.verified_thread == Some(thread_id)
    }

    pub(crate) fn clear(&mut self) {
        *self = Self::default();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn response(level: u32, verified: bool) -> CommandExecResponse {
        CommandExecResponse {
            exit_code: 0,
            stdout: serde_json::json!({
                "exception_level": level, "native_execution_verified": verified,
            })
            .to_string(),
            stderr: String::new(),
        }
    }

    #[test]
    fn el2_activation_rejects_failure_malformed_or_unverified_results() {
        assert!(parse_activation(response(2, true)).is_ok());
        assert!(parse_activation(response(1, true)).is_err());
        assert!(parse_activation(response(2, false)).is_err());
        let mut failed = response(2, true);
        failed.exit_code = 1;
        assert!(parse_activation(failed).is_err());
        for stdout in ["not JSON", "{}", r#"{"exception_level":2}"#] {
            assert!(
                parse_activation(CommandExecResponse {
                    exit_code: 0,
                    stdout: stdout.into(),
                    stderr: String::new(),
                })
                .is_err()
            );
        }
    }

    #[test]
    fn el2_activation_request_has_fixed_command_and_bounds() {
        let ClientRequest::OneOffCommandExec { params, .. } =
            activation_request(PathBuf::from("/test/project"))
        else {
            panic!("expected command/exec");
        };
        assert_eq!(params.command, ["/usr/local/bin/el2-control", "activate"]);
        assert_eq!(params.timeout_ms, Some(25_000));
        assert_eq!(params.output_bytes_cap, Some(16_384));
        assert!(!params.disable_timeout);
        assert!(!params.disable_output_cap);
        assert!(!params.tty);
        assert_eq!(params.cwd, Some(PathBuf::from("/test/project")));
        assert!(params.env.is_none());
        assert!(params.permission_profile.is_none());
        assert!(matches!(
            params.sandbox_policy,
            Some(SandboxPolicy::DangerFullAccess)
        ));
    }

    #[test]
    fn el2_requests_require_confirmation_and_reject_duplicate_or_stale_completions() {
        let mut state = SelectionState::default();
        let thread = ThreadId::new();
        let other = ThreadId::new();
        let request = state.confirm(thread).unwrap();
        assert!(state.confirm(thread).is_none());
        assert!(!state.finish(thread, request));
        assert!(!state.start(other, request));
        assert!(state.start(thread, request));
        assert!(!state.start(thread, request));
        assert!(!state.finish(other, request));
        assert!(state.finish(thread, request));
        assert!(!state.finish(thread, request));
        assert!(!state.is_verified(thread));
        state.mark_verified(thread);
        assert!(state.is_verified(thread));
        assert!(!state.is_verified(other));
        let request = state.confirm(thread).unwrap();
        assert!(state.start(thread, request));
        state.clear();
        assert!(!state.finish(thread, request));
        assert!(!state.is_verified(thread));
    }
}
