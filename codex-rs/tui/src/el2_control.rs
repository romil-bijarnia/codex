//! Owner EL2 activation runs on the app-server host, where agent commands execute.

use codex_app_server_client::AppServerRequestHandle;
use codex_app_server_protocol::ClientRequest;
use codex_app_server_protocol::CommandExecParams;
use codex_app_server_protocol::CommandExecResponse;
use codex_app_server_protocol::RequestId;
use codex_app_server_protocol::SandboxPolicy;
use codex_protocol::ThreadId;
use serde::Deserialize;
use serde::Serialize;
use std::path::Path;
use std::path::PathBuf;
use std::time::Duration;

pub(crate) const LABEL: &str = "EL2 Control";
pub(crate) const PROFILE_ALIAS: &str = ":el2-control";

#[derive(Clone, Debug, Deserialize, Serialize)]
pub(crate) struct Activation {
    pub(crate) exception_level: u32,
    pub(crate) native_execution_verified: bool,
    pub(crate) kernel_release: String,
    pub(crate) boot_id: String,
}

pub(crate) fn parse_activation(response: CommandExecResponse) -> Result<Activation, String> {
    if response.exit_code != 0 {
        let detail = response.stderr.trim();
        return Err(if detail.is_empty() {
            format!("EL2 activation exited with status {}", response.exit_code)
        } else {
            detail.chars().take(600).collect()
        });
    }
    let activation: Activation = serde_json::from_str(&response.stdout)
        .map_err(|error| format!("Invalid EL2 activation response: {error}"))?;
    if activation.exception_level != 2 || !activation.native_execution_verified {
        return Err("The interface did not confirm native execution at EL2.".to_string());
    }
    Ok(activation)
}

pub(crate) async fn activate(
    handle: AppServerRequestHandle,
    cwd: PathBuf,
) -> Result<Activation, String> {
    let request = ClientRequest::OneOffCommandExec {
        request_id: RequestId::String(format!("owner-el2-{}", uuid::Uuid::new_v4())),
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
    };
    let response: CommandExecResponse =
        tokio::time::timeout(Duration::from_secs(30), handle.request_typed(request))
            .await
            .map_err(|_| "EL2 activation timed out.".to_string())?
            .map_err(|error| error.to_string())?;
    parse_activation(response)
}

fn selection_path(codex_home: &Path, thread_id: ThreadId) -> PathBuf {
    codex_home
        .join("state/owner-el2-selection")
        .join(format!("{thread_id}.json"))
}

pub(crate) fn default_selected(codex_home: &Path) -> bool {
    #[derive(Deserialize)]
    struct DefaultSelection {
        enabled: bool,
    }
    std::fs::read(codex_home.join("state/owner-el2-selection/default.json"))
        .ok()
        .and_then(|bytes| serde_json::from_slice::<DefaultSelection>(&bytes).ok())
        .is_some_and(|selection| selection.enabled)
}

pub(crate) fn selected(codex_home: &Path, thread_id: ThreadId) -> bool {
    match std::fs::read(selection_path(codex_home, thread_id)) {
        Ok(bytes) => serde_json::from_slice::<Activation>(&bytes)
            .ok()
            .is_some_and(|activation| {
                activation.exception_level == 2 && activation.native_execution_verified
            }),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => default_selected(codex_home),
        Err(_) => false,
    }
}

pub(crate) fn save(
    codex_home: &Path,
    thread_id: ThreadId,
    activation: &Activation,
) -> std::io::Result<()> {
    write_selection(codex_home, thread_id, &serde_json::to_vec(activation)?)
}

fn write_selection(codex_home: &Path, thread_id: ThreadId, bytes: &[u8]) -> std::io::Result<()> {
    use std::io::Write;
    let destination = selection_path(codex_home, thread_id);
    let parent = destination.parent().expect("selection path has a parent");
    std::fs::create_dir_all(parent)?;
    let mut temporary = tempfile::NamedTempFile::new_in(parent)?;
    temporary.write_all(bytes)?;
    temporary
        .persist(destination)
        .map_err(|error| error.error)?;
    Ok(())
}

pub(crate) fn clear(codex_home: &Path, thread_id: ThreadId) {
    // Keep an explicit opt-out so resuming this thread does not reapply the default.
    let _ = write_selection(codex_home, thread_id, br#"{"enabled":false}"#);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn response(level: u32, verified: bool) -> CommandExecResponse {
        CommandExecResponse {
            exit_code: 0,
            stdout: serde_json::json!({
                "exception_level": level, "native_execution_verified": verified,
                "kernel_release": "test-kernel", "boot_id": "test-boot",
            })
            .to_string(),
            stderr: String::new(),
        }
    }

    #[test]
    fn activation_requires_both_el2_and_native_execution() {
        assert!(parse_activation(response(2, true)).is_ok());
        assert!(parse_activation(response(1, true)).is_err());
        assert!(parse_activation(response(2, false)).is_err());
        let mut failed = response(2, true);
        failed.exit_code = 1;
        assert!(parse_activation(failed).is_err());
    }

    #[test]
    fn remembered_selection_is_per_thread_and_can_be_cleared() {
        let directory = tempfile::tempdir().unwrap();
        let first = ThreadId::new();
        let second = ThreadId::new();
        let activation = parse_activation(response(2, true)).unwrap();
        save(directory.path(), first, &activation).unwrap();
        assert!(selected(directory.path(), first));
        assert!(!selected(directory.path(), second));
        clear(directory.path(), first);
        assert!(!selected(directory.path(), first));
    }

    #[test]
    fn global_default_respects_thread_opt_out_and_explicit_reactivation() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory
            .path()
            .join("state/owner-el2-selection/default.json");
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, br#"{"enabled":true}"#).unwrap();
        let first = ThreadId::new();
        let second = ThreadId::new();
        assert!(selected(directory.path(), first));
        clear(directory.path(), first);
        assert!(!selected(directory.path(), first));
        assert!(selected(directory.path(), second));
        let activation = parse_activation(response(2, true)).unwrap();
        save(directory.path(), first, &activation).unwrap();
        assert!(selected(directory.path(), first));
        std::fs::write(&path, br#"{"enabled":false}"#).unwrap();
        assert!(selected(directory.path(), first));
        assert!(!selected(directory.path(), second));
    }

    #[test]
    fn invalid_default_or_thread_marker_does_not_select_el2() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory
            .path()
            .join("state/owner-el2-selection/default.json");
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        let thread_id = ThreadId::new();
        std::fs::write(&path, b"invalid").unwrap();
        assert!(!selected(directory.path(), thread_id));
        std::fs::write(&path, br#"{"enabled":true}"#).unwrap();
        std::fs::write(selection_path(directory.path(), thread_id), b"invalid").unwrap();
        assert!(!selected(directory.path(), thread_id));
    }
}
