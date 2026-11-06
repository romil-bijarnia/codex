//! Dispatch the user's local helpers while retaining the native CLI entrypoint.

use anyhow::Context;
use clap::Args;
use std::ffi::OsString;
use std::path::PathBuf;
use std::process::Command;

#[derive(Debug, Args)]
#[command(disable_help_flag = true, disable_help_subcommand = true)]
pub(super) struct HelperArgs {
    #[arg(num_args = 0.., trailing_var_arg = true, allow_hyphen_values = true)]
    pub(super) args: Vec<OsString>,
}

pub(super) enum Helper {
    Visualize,
    Api,
}

pub(super) fn direct_invocation(
    args: impl IntoIterator<Item = OsString>,
) -> Option<(Helper, Vec<OsString>)> {
    let mut args = args.into_iter();
    args.next()?;
    let helper = match args.next()?.to_str()? {
        "visualize" => Helper::Visualize,
        "api" => Helper::Api,
        _ => return None,
    };
    Some((helper, args.collect()))
}

pub(super) fn run(helper: Helper, args: Vec<OsString>) -> anyhow::Result<()> {
    let script = match helper {
        Helper::Visualize => codex_core::config::find_codex_home()?
            .join("local-overrides/visualize-cli/visualize.py")
            .to_path_buf(),
        Helper::Api => std::env::var_os("HOME")
            .filter(|home| !home.is_empty())
            .map(PathBuf::from)
            .context("HOME is not set; cannot locate the API profile helper")?
            .join(".local/bin/codex-api-chat"),
    };
    anyhow::ensure!(
        script.is_file(),
        "Local Codex helper is missing: {}",
        script.display()
    );
    let python = if cfg!(target_os = "macos")
        && std::path::Path::new("/opt/homebrew/bin/python3").is_file()
    {
        "/opt/homebrew/bin/python3"
    } else {
        "/usr/bin/python3"
    };
    let mut command = Command::new(python);
    command.arg(&script).args(args);
    if matches!(helper, Helper::Api) && std::env::var_os("CODEX_API_BINARY").is_none() {
        command.env("CODEX_API_BINARY", std::env::current_exe()?);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        Err(command.exec()).with_context(|| format!("Could not start {}", script.display()))
    }
    #[cfg(not(unix))]
    {
        let status = command
            .status()
            .with_context(|| format!("Could not start {}", script.display()))?;
        std::process::exit(status.code().unwrap_or(1));
    }
}
