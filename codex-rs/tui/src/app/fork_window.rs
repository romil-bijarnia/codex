//! Hand a completed local fork to a separate Terminal window.

use super::*;
use codex_app_server_client::RemoteAppServerEndpoint;
use codex_app_server_protocol::ThreadLoadedListParams;
use color_eyre::eyre::WrapErr;
use std::process::Stdio;

impl App {
    pub(super) async fn open_fork_window(
        &self,
        app_server: &mut AppServerSession,
        forked: &AppServerStartedThread,
    ) -> Option<Result<()>> {
        let launcher = self.config.codex_home.join("bin/codex-fork-window");
        if !launcher.is_file() {
            return None;
        }
        Some(
            async {
                let id = forked.session.thread_id;
                // Release only the child's writer before its new frontend resumes it.
                // The source chat retains its subscription, input and active turn.
                app_server.thread_unsubscribe(id).await?;
                if matches!(self.app_server_target, AppServerTarget::Embedded) {
                    // The unsubscribe response precedes asynchronous teardown. Wait for the
                    // scoped zero-retention fork to release its writer before another server
                    // opens it. Shared-daemon frontends simply attach to the same runtime.
                    tokio::time::timeout(Duration::from_secs(10), async {
                        loop {
                            let mut cursor = None;
                            loop {
                                let loaded = app_server
                                    .thread_loaded_list(ThreadLoadedListParams {
                                        cursor,
                                        limit: None,
                                    })
                                    .await?;
                                if loaded.data.contains(&id.to_string()) {
                                    break;
                                }
                                cursor = loaded.next_cursor;
                                if cursor.is_none() {
                                    return Ok::<(), color_eyre::eyre::Report>(());
                                }
                            }
                            tokio::time::sleep(Duration::from_millis(25)).await;
                        }
                    })
                    .await
                    .wrap_err("timed out releasing the fork for its new window")??;
                }
                let mut command = tokio::process::Command::new(launcher.as_path());
                command
                    .args(["--resume", &id.to_string(), "--workdir"])
                    .arg(if forked.session.cwd.is_dir() {
                        forked.session.cwd.to_path_buf()
                    } else {
                        std::env::current_dir()?
                    })
                    .env("CODEX_HOME", self.config.codex_home.as_path())
                    .kill_on_drop(true)
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::piped());
                match &self.app_server_target {
                    AppServerTarget::LocalDaemon { endpoint, .. }
                    | AppServerTarget::Remote { endpoint } => match endpoint {
                        RemoteAppServerEndpoint::UnixSocket { socket_path } => {
                            command
                                .arg("--remote")
                                .arg(format!("unix://{}", socket_path.as_path().display()));
                        }
                        RemoteAppServerEndpoint::WebSocket {
                            websocket_url,
                            auth_token,
                        } => {
                            command.arg("--remote").arg(websocket_url);
                            if let Some(token) = auth_token {
                                command.env("CODEX_FORK_WINDOW_AUTH_TOKEN", token).args([
                                    "--remote-auth-token-env",
                                    "CODEX_FORK_WINDOW_AUTH_TOKEN",
                                ]);
                            }
                        }
                    },
                    AppServerTarget::Embedded => {
                        command.arg("--no-daemon");
                    }
                }
                let output =
                    tokio::time::timeout(Duration::from_secs(10), command.output()).await??;
                if !output.status.success() {
                    color_eyre::eyre::bail!(
                        "Terminal launcher exited {}: {}",
                        output.status,
                        String::from_utf8_lossy(&output.stderr).trim()
                    );
                }
                Ok(())
            }
            .await,
        )
    }
}
