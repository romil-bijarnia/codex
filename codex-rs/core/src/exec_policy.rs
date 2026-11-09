use std::io::ErrorKind;
use std::path::Path;
use std::path::PathBuf;
use std::sync::Arc;

use arc_swap::ArcSwap;

use codex_config::ConfigLayerSource;
use codex_config::ConfigLayerStack;
use codex_execpolicy::AmendError;
use codex_execpolicy::Decision;
use codex_execpolicy::Error as ExecPolicyRuleError;
use codex_execpolicy::Evaluation;
use codex_execpolicy::MatchOptions;
use codex_execpolicy::NetworkRuleProtocol;
use codex_execpolicy::Policy;
use codex_execpolicy::PolicyParser;
use codex_execpolicy::RequirementsExecPolicy;
use codex_execpolicy::RuleMatch;
use codex_execpolicy::blocking_append_allow_prefix_rule;
use codex_execpolicy::blocking_append_network_rule;
use codex_protocol::approvals::ExecPolicyAmendment;
use codex_protocol::config_types::WindowsSandboxLevel;
use codex_protocol::models::PermissionProfile;
use codex_protocol::permissions::FileSystemSandboxKind;
use codex_protocol::protocol::AskForApproval;
use codex_shell_command::is_dangerous_command::DangerousCommandPlatform;
use thiserror::Error;
use tokio::fs;
use tokio::sync::Semaphore;
use tokio::task::spawn_blocking;
use tracing::instrument;

use crate::config::Config;
use crate::sandboxing::SandboxPermissions;
use crate::tools::sandboxing::ExecApprovalRequirement;
use codex_shell_command::bash::parse_shell_lc_plain_commands;
use codex_utils_absolute_path::AbsolutePathBuf;
use shlex::try_join as shlex_try_join;

mod inheritance;

pub(crate) use inheritance::InheritedExecPolicy;

const PROMPT_CONFLICT_REASON: &str =
    "approval required by policy, but AskForApproval is set to Never";
const REJECT_SANDBOX_APPROVAL_REASON: &str =
    "approval required by policy, but AskForApproval::Granular.sandbox_approval is false";
const REJECT_RULES_APPROVAL_REASON: &str =
    "approval required by policy rule, but AskForApproval::Granular.rules is false";
const RULES_DIR_NAME: &str = "rules";
const RULE_EXTENSION: &str = "rules";
const DEFAULT_POLICY_FILE: &str = "default.rules";
pub(crate) static BANNED_PREFIX_SUGGESTIONS: &[&[&str]] = &[
    &["/bin/bash"],
    &["/bin/bash", "-c"],
    &["/bin/bash", "-lc"],
    &["/bin/sh"],
    &["/bin/sh", "-c"],
    &["/bin/sh", "-lc"],
    &["/bin/zsh"],
    &["/bin/zsh", "-c"],
    &["/bin/zsh", "-lc"],
    &["Rscript"],
    &["bash"],
    &["bash", "-c"],
    &["bash", "-lc"],
    &["bun"],
    &["bun", "-e"],
    &["bun", "run"],
    &["cmd"],
    &["cmd", "/c"],
    &["cmd", "/k"],
    &["cmd.exe"],
    &["cmd.exe", "/c"],
    &["cmd.exe", "/k"],
    &["dash"],
    &["dash", "-c"],
    &["deno"],
    &["deno", "eval"],
    &["env"],
    &["fish"],
    &["fish", "-c"],
    &["git"],
    &["julia"],
    &["julia", "-e"],
    &["ksh"],
    &["ksh", "-c"],
    &["lua"],
    &["lua", "-e"],
    &["node"],
    &["node", "-e"],
    &["nodejs"],
    &["nodejs", "-e"],
    &["npm", "run"],
    &["osascript"],
    &["perl"],
    &["perl", "-e"],
    &["php"],
    &["php", "-r"],
    &["pnpm", "run"],
    &["powershell"],
    &["powershell", "-Command"],
    &["powershell", "-EncodedCommand"],
    &["powershell", "-File"],
    &["powershell", "-c"],
    &["powershell.exe"],
    &["powershell.exe", "-Command"],
    &["powershell.exe", "-EncodedCommand"],
    &["powershell.exe", "-File"],
    &["powershell.exe", "-c"],
    &["pwsh"],
    &["pwsh", "-Command"],
    &["pwsh", "-EncodedCommand"],
    &["pwsh", "-File"],
    &["pwsh", "-c"],
    &["pwsh", "-e"],
    &["pwsh", "-ec"],
    &["pwsh", "-f"],
    &["py"],
    &["py", "-3"],
    &["pypy"],
    &["pypy3"],
    &["python"],
    &["python", "-"],
    &["python", "-c"],
    &["python3"],
    &["python3", "-"],
    &["python3", "-c"],
    &["pythonw"],
    &["pyw"],
    &["rm"],
    &["ruby"],
    &["ruby", "-e"],
    &["sh"],
    &["sh", "-c"],
    &["sh", "-lc"],
    &["sudo"],
    &["yarn", "run"],
    &["zsh"],
    &["zsh", "-c"],
    &["zsh", "-lc"],
];

/// Describes which unmatched-command heuristics should classify the command
/// words being evaluated by exec-policy.
///
/// The command tokens may be the original argv or a shell-specific lowering of
/// a wrapper such as `bash -lc ...` or `powershell.exe -Command ...`. We only
/// need to distinguish the PowerShell case because its dangerous-command
/// heuristics operate on PowerShell-flavored inner command words rather than
/// the generic command classifier.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ExecPolicyCommandOrigin {
    /// Use the generic unmatched-command heuristics.
    Generic,
    /// The command words came from the `-Command` body of a top-level
    /// PowerShell wrapper, so use PowerShell-specific unmatched-command
    /// heuristics for the lowered words.
    PowerShell,
    /// Strictly lowered commands with no pipeline or executable-string dependencies.
    PowerShellLiteral,
}

#[derive(Clone, Copy)]
pub(crate) struct UnmatchedCommandContext<'a> {
    pub(crate) approval_policy: AskForApproval,
    pub(crate) permission_profile: &'a PermissionProfile,
    // TODO(anp): Reconcile this decision input with TurnEnvironment::sandbox_context
    // so approval heuristics match the selected environment's Windows backend.
    pub(crate) windows_sandbox_level: WindowsSandboxLevel,
    pub(crate) sandbox_permissions: SandboxPermissions,
    pub(crate) command_origin: ExecPolicyCommandOrigin,
}

#[derive(Debug, Eq, PartialEq)]
struct ExecPolicyCommands {
    commands: Vec<Vec<String>>,
    command_origin: ExecPolicyCommandOrigin,
}

pub(crate) fn child_uses_parent_exec_policy(parent_config: &Config, child_config: &Config) -> bool {
    fn exec_policy_config_folders(config: &Config) -> Vec<AbsolutePathBuf> {
        config
            .config_layer_stack
            .layers_low_to_high()
            .filter_map(codex_config::ConfigLayerEntry::config_folder)
            .collect()
    }

    exec_policy_config_folders(parent_config) == exec_policy_config_folders(child_config)
        && parent_config
            .config_layer_stack
            .ignore_user_and_project_exec_policy_rules()
            == child_config
                .config_layer_stack
                .ignore_user_and_project_exec_policy_rules()
        && parent_config.config_layer_stack.requirements().exec_policy
            == child_config.config_layer_stack.requirements().exec_policy
}

fn is_policy_match(rule_match: &RuleMatch) -> bool {
    match rule_match {
        RuleMatch::PrefixRuleMatch { .. } => true,
        RuleMatch::HeuristicsRuleMatch { .. } => false,
    }
}

/// Returns a rejection reason when `approval_policy` disallows surfacing the
/// current prompt to the user.
///
/// `prompt_is_rule` distinguishes policy-rule prompts from sandbox/escalation
/// prompts so granular `rules` and `sandbox_approval` settings are honored
/// independently. When both are present, policy-rule prompts take precedence.
pub(crate) fn prompt_is_rejected_by_policy(
    approval_policy: AskForApproval,
    prompt_is_rule: bool,
) -> Option<&'static str> {
    match approval_policy {
        AskForApproval::Never => Some(PROMPT_CONFLICT_REASON),
        AskForApproval::OnRequest => None,
        AskForApproval::UnlessTrusted => None,
        AskForApproval::Granular(granular_config) => {
            if prompt_is_rule {
                if !granular_config.allows_rules_approval() {
                    Some(REJECT_RULES_APPROVAL_REASON)
                } else {
                    None
                }
            } else if !granular_config.allows_sandbox_approval() {
                Some(REJECT_SANDBOX_APPROVAL_REASON)
            } else {
                None
            }
        }
    }
}

#[derive(Debug, Error)]
pub enum ExecPolicyError {
    #[error("failed to read rules files from {dir}: {source}")]
    ReadDir {
        dir: PathBuf,
        source: std::io::Error,
    },

    #[error("failed to read rules file {path}: {source}")]
    ReadFile {
        path: PathBuf,
        source: std::io::Error,
    },

    #[error("failed to parse rules file {path}: {source}")]
    ParsePolicy {
        path: String,
        source: codex_execpolicy::Error,
    },
}

#[derive(Debug, Error)]
pub enum ExecPolicyUpdateError {
    #[error("failed to update rules file {path}: {source}")]
    AppendRule { path: PathBuf, source: AmendError },

    #[error("failed to join blocking rules update task: {source}")]
    JoinBlockingTask { source: tokio::task::JoinError },

    #[error("failed to update in-memory rules: {source}")]
    AddRule {
        #[from]
        source: ExecPolicyRuleError,
    },
}

pub(crate) struct ExecPolicyManager {
    policy: ArcSwap<Policy>,
    source_requirements: Option<Arc<RequirementsExecPolicy>>,
    update_lock: Semaphore,
}

impl ExecPolicyManager {
    pub(crate) fn new(policy: Arc<Policy>) -> Self {
        Self::from_snapshot(policy, /*source_requirements*/ None)
    }

    pub(crate) fn from_snapshot(
        policy: Arc<Policy>,
        source_requirements: Option<Arc<RequirementsExecPolicy>>,
    ) -> Self {
        Self {
            policy: ArcSwap::from(policy),
            source_requirements,
            update_lock: Semaphore::new(/*permits*/ 1),
        }
    }

    #[instrument(level = "info", skip_all)]
    pub(crate) async fn load(config_stack: &ConfigLayerStack) -> Result<Self, ExecPolicyError> {
        let (policy, warning) = load_exec_policy_with_warning(config_stack).await?;
        if let Some(err) = warning.as_ref() {
            tracing::warn!("failed to parse rules: {err}");
        }
        let source_requirements = config_stack.requirements().exec_policy.as_deref().cloned();
        Ok(Self::from_snapshot(
            Arc::new(policy),
            source_requirements.map(Arc::new),
        ))
    }

    pub(crate) fn current(&self) -> Arc<Policy> {
        self.policy.load_full()
    }

    pub(crate) fn snapshot(&self) -> (Arc<Policy>, Option<Arc<RequirementsExecPolicy>>) {
        (self.current(), self.source_requirements.clone())
    }

    pub(crate) async fn append_amendment_and_update(
        &self,
        codex_home: &Path,
        amendment: &ExecPolicyAmendment,
    ) -> Result<(), ExecPolicyUpdateError> {
        let _update_guard =
            self.update_lock
                .acquire()
                .await
                .map_err(|_| ExecPolicyUpdateError::AddRule {
                    source: ExecPolicyRuleError::InvalidRule(
                        "exec policy update semaphore closed".to_string(),
                    ),
                })?;
        let policy_path = default_policy_path(codex_home);
        spawn_blocking({
            let policy_path = policy_path.clone();
            let prefix = amendment.command.clone();
            move || blocking_append_allow_prefix_rule(&policy_path, &prefix)
        })
        .await
        .map_err(|source| ExecPolicyUpdateError::JoinBlockingTask { source })?
        .map_err(|source| ExecPolicyUpdateError::AppendRule {
            path: policy_path,
            source,
        })?;

        let current_policy = self.current();
        let match_options = MatchOptions {
            resolve_host_executables: true,
        };
        let existing_evaluation = current_policy.check_multiple_with_options(
            [&amendment.command],
            &|_| Decision::Forbidden,
            &match_options,
        );
        let already_allowed = existing_evaluation.decision == Decision::Allow
            && existing_evaluation.matched_rules.iter().any(|rule_match| {
                is_policy_match(rule_match) && rule_match.decision() == Decision::Allow
            });
        if already_allowed {
            return Ok(());
        }

        let mut updated_policy = current_policy.as_ref().clone();
        updated_policy.add_prefix_rule(&amendment.command, Decision::Allow)?;
        self.policy.store(Arc::new(updated_policy));
        Ok(())
    }

    pub(crate) async fn append_network_rule_and_update(
        &self,
        codex_home: &Path,
        host: &str,
        protocol: NetworkRuleProtocol,
        decision: Decision,
        justification: Option<String>,
    ) -> Result<(), ExecPolicyUpdateError> {
        let _update_guard =
            self.update_lock
                .acquire()
                .await
                .map_err(|_| ExecPolicyUpdateError::AddRule {
                    source: ExecPolicyRuleError::InvalidRule(
                        "exec policy update semaphore closed".to_string(),
                    ),
                })?;
        let policy_path = default_policy_path(codex_home);
        let host = host.to_string();
        spawn_blocking({
            let policy_path = policy_path.clone();
            let host = host.clone();
            let justification = justification.clone();
            move || {
                blocking_append_network_rule(
                    &policy_path,
                    &host,
                    protocol,
                    decision,
                    justification.as_deref(),
                )
            }
        })
        .await
        .map_err(|source| ExecPolicyUpdateError::JoinBlockingTask { source })?
        .map_err(|source| ExecPolicyUpdateError::AppendRule {
            path: policy_path,
            source,
        })?;

        let mut updated_policy = self.current().as_ref().clone();
        updated_policy.add_network_rule(&host, protocol, decision, justification)?;
        self.policy.store(Arc::new(updated_policy));
        Ok(())
    }
}

impl Default for ExecPolicyManager {
    fn default() -> Self {
        Self::new(Arc::new(Policy::empty()))
    }
}

pub async fn check_execpolicy_for_warnings(
    config_stack: &ConfigLayerStack,
) -> Result<Option<ExecPolicyError>, ExecPolicyError> {
    let (_, warning) = load_exec_policy_with_warning(config_stack).await?;
    Ok(warning)
}

fn exec_policy_message_for_display(source: &codex_execpolicy::Error) -> String {
    let message = source.to_string();
    if let Some(line) = message
        .lines()
        .find(|line| line.trim_start().starts_with("error: "))
    {
        return line.to_owned();
    }
    if let Some(first_line) = message.lines().next()
        && let Some((_, detail)) = first_line.rsplit_once(": starlark error: ")
    {
        return detail.trim().to_string();
    }

    message
        .lines()
        .next()
        .unwrap_or_default()
        .trim()
        .to_string()
}

fn parse_starlark_line_from_message(message: &str) -> Option<(PathBuf, usize)> {
    let first_line = message.lines().next()?.trim();
    let (path_and_position, _) = first_line.rsplit_once(": starlark error:")?;

    let mut parts = path_and_position.rsplitn(3, ':');
    let _column = parts.next()?.parse::<usize>().ok()?;
    let line = parts.next()?.parse::<usize>().ok()?;
    let path = PathBuf::from(parts.next()?);

    if line == 0 {
        return None;
    }

    Some((path, line))
}

pub fn format_exec_policy_error_with_source(error: &ExecPolicyError) -> String {
    match error {
        ExecPolicyError::ParsePolicy { path, source } => {
            let rendered_source = source.to_string();
            let structured_location = source
                .location()
                .map(|location| (PathBuf::from(location.path), location.range.start.line));
            let parsed_location = parse_starlark_line_from_message(&rendered_source);
            let location = match (structured_location, parsed_location) {
                (Some((_, 1)), Some((parsed_path, parsed_line))) if parsed_line > 1 => {
                    Some((parsed_path, parsed_line))
                }
                (Some(structured), _) => Some(structured),
                (None, parsed) => parsed,
            };
            let message = exec_policy_message_for_display(source);
            match location {
                Some((path, line)) => {
                    format!(
                        "{}:{}: {} (problem is on or around line {})",
                        path.display(),
                        line,
                        message,
                        line
                    )
                }
                None => format!("{path}: {message}"),
            }
        }
        _ => error.to_string(),
    }
}

pub(crate) async fn load_exec_policy_with_warning(
    config_stack: &ConfigLayerStack,
) -> Result<(Policy, Option<ExecPolicyError>), ExecPolicyError> {
    match load_exec_policy(config_stack).await {
        Ok(policy) => Ok((policy, None)),
        Err(err @ ExecPolicyError::ParsePolicy { .. }) => {
            let policy = config_stack
                .requirements()
                .exec_policy
                .as_deref()
                .map_or_else(Policy::empty, |policy| policy.as_ref().clone());
            Ok((policy, Some(err)))
        }
        Err(err) => Err(err),
    }
}

async fn policy_files(stack: &ConfigLayerStack) -> Result<Vec<PathBuf>, ExecPolicyError> {
    let folders = stack.layers_low_to_high().filter_map(|layer| {
        if stack.ignore_user_and_project_exec_policy_rules()
            && matches!(
                layer.name,
                ConfigLayerSource::User { .. } | ConfigLayerSource::Project { .. }
            )
        {
            None
        } else {
            layer.config_folder()
        }
    });
    // Disabled project layers already represent the trust decision, so hooks
    // and exec-policy loading can reuse the normal trusted-layer view.
    // Iterate the layers in increasing order of precedence, adding the *.rules
    // from each layer, so that higher-precedence layers can override
    // rules defined in lower-precedence ones.
    let mut policy_paths = Vec::new();
    for config_folder in folders {
        let policy_dir = config_folder.join(RULES_DIR_NAME);
        policy_paths.extend(collect_policy_files(&policy_dir).await?);
    }
    Ok(policy_paths)
}

pub async fn load_exec_policy(config_stack: &ConfigLayerStack) -> Result<Policy, ExecPolicyError> {
    let policy_paths = policy_files(config_stack).await?;
    tracing::trace!(
        policy_paths = ?policy_paths,
        "loaded exec policies"
    );

    let mut parser = PolicyParser::new();
    for policy_path in &policy_paths {
        let contents =
            fs::read_to_string(policy_path)
                .await
                .map_err(|source| ExecPolicyError::ReadFile {
                    path: policy_path.clone(),
                    source,
                })?;
        let identifier = policy_path.to_string_lossy().to_string();
        parser
            .parse(&identifier, &contents)
            .map_err(|source| ExecPolicyError::ParsePolicy {
                path: identifier,
                source,
            })?;
    }

    let policy = parser.build();
    tracing::debug!("loaded rules from {} files", policy_paths.len());
    tracing::trace!(rules = ?policy, "exec policy rules loaded");

    let Some(requirements_policy) = config_stack.requirements().exec_policy.as_deref() else {
        return Ok(policy);
    };

    Ok(policy.merge_overlay(requirements_policy.as_ref()))
}

pub(crate) fn default_policy_path(codex_home: &Path) -> PathBuf {
    codex_home.join(RULES_DIR_NAME).join(DEFAULT_POLICY_FILE)
}

#[cfg(test)]
fn commands_for_exec_policy(command: &[String]) -> ExecPolicyCommands {
    commands_for_exec_policy_for_platform(command, DangerousCommandPlatform::host())
}

fn commands_for_exec_policy_for_platform(
    command: &[String],
    command_platform: DangerousCommandPlatform,
) -> ExecPolicyCommands {
    if let Some(commands) = parse_shell_lc_plain_commands(command)
        && !commands.is_empty()
    {
        return ExecPolicyCommands {
            commands,
            command_origin: ExecPolicyCommandOrigin::Generic,
        };
    }

    if command_platform == DangerousCommandPlatform::Windows
        && let Some((_, script)) =
            codex_shell_command::powershell::extract_powershell_command(command)
        && let Some(commands) =
            codex_shell_command::powershell::parse_powershell_script_into_plain_commands(script)
        && !commands.is_empty()
    {
        return ExecPolicyCommands {
            command_origin: powershell_command_origin(script, &commands),
            commands,
        };
    }

    ExecPolicyCommands {
        commands: vec![command.to_vec()],
        command_origin: ExecPolicyCommandOrigin::Generic,
    }
}

fn powershell_command_origin(script: &str, commands: &[Vec<String>]) -> ExecPolicyCommandOrigin {
    if codex_shell_command::is_dangerous_command::powershell_commands_are_independent(
        script, commands,
    ) {
        ExecPolicyCommandOrigin::PowerShellLiteral
    } else {
        ExecPolicyCommandOrigin::PowerShell
    }
}

/// Derive a proposed execpolicy amendment when a command requires user approval
/// - If any execpolicy rule prompts, return None, because an amendment would not skip that policy requirement.
/// - Otherwise return the first heuristics Prompt.
/// - Examples:
/// - execpolicy: empty. Command: `["python"]`. Heuristics prompt -> `Some(vec!["python"])`.
/// - execpolicy: empty. Command: `["bash", "-c", "cd /some/folder && prog1 --option1 arg1 && prog2 --option2 arg2"]`.
///   Parsed commands include `cd /some/folder`, `prog1 --option1 arg1`, and `prog2 --option2 arg2`. If heuristics allow `cd` but prompt
///   on `prog1`, we return `Some(vec!["prog1", "--option1", "arg1"])`.
/// - execpolicy: contains a `prompt for prefix ["prog2"]` rule. For the same command as above,
///   we return `None` because an execpolicy prompt still applies even if we amend execpolicy to allow ["prog1", "--option1", "arg1"].
fn try_derive_execpolicy_amendment_for_prompt_rules(
    matched_rules: &[RuleMatch],
) -> Option<ExecPolicyAmendment> {
    if matched_rules
        .iter()
        .any(|rule_match| is_policy_match(rule_match) && rule_match.decision() == Decision::Prompt)
    {
        return None;
    }

    matched_rules
        .iter()
        .find_map(|rule_match| match rule_match {
            RuleMatch::HeuristicsRuleMatch {
                command,
                decision: Decision::Prompt,
            } => Some(ExecPolicyAmendment::from(command.clone())),
            _ => None,
        })
}

/// - Note: we only use this amendment when the command fails to run in sandbox and codex prompts the user to run outside the sandbox
/// - The purpose of this amendment is to bypass sandbox for similar commands in the future
/// - If any execpolicy rule matches, return None, because we would already be running command outside the sandbox
fn try_derive_execpolicy_amendment_for_allow_rules(
    matched_rules: &[RuleMatch],
) -> Option<ExecPolicyAmendment> {
    if matched_rules.iter().any(is_policy_match) {
        return None;
    }

    matched_rules
        .iter()
        .find_map(|rule_match| match rule_match {
            RuleMatch::HeuristicsRuleMatch {
                command,
                decision: Decision::Allow,
            } => Some(ExecPolicyAmendment::from(command.clone())),
            _ => None,
        })
}

fn derive_requested_execpolicy_amendment_from_prefix_rule(
    prefix_rule: Option<&Vec<String>>,
    matched_rules: &[RuleMatch],
    exec_policy: &Policy,
    commands: &[Vec<String>],
    exec_policy_fallback: &impl Fn(&[String]) -> Decision,
    match_options: &MatchOptions,
) -> Option<ExecPolicyAmendment> {
    let prefix_rule = prefix_rule?;
    if prefix_rule.is_empty() {
        return None;
    }
    if BANNED_PREFIX_SUGGESTIONS.iter().any(|banned| {
        prefix_rule.len() == banned.len()
            && prefix_rule
                .iter()
                .map(String::as_str)
                .eq(banned.iter().copied())
    }) {
        return None;
    }

    // if any policy rule already matches, don't suggest an additional rule that might conflict or not apply
    if matched_rules.iter().any(is_policy_match) {
        return None;
    }

    let amendment = ExecPolicyAmendment::new(prefix_rule.clone());
    if prefix_rule_would_approve_all_commands(
        exec_policy,
        &amendment.command,
        commands,
        exec_policy_fallback,
        match_options,
    ) {
        Some(amendment)
    } else {
        None
    }
}

fn prefix_rule_would_approve_all_commands(
    exec_policy: &Policy,
    prefix_rule: &[String],
    commands: &[Vec<String>],
    exec_policy_fallback: &impl Fn(&[String]) -> Decision,
    match_options: &MatchOptions,
) -> bool {
    let mut policy_with_prefix_rule = exec_policy.clone();
    if policy_with_prefix_rule
        .add_prefix_rule(prefix_rule, Decision::Allow)
        .is_err()
    {
        return false;
    }

    commands.iter().all(|command| {
        policy_with_prefix_rule
            .check_with_options(command, exec_policy_fallback, match_options)
            .decision
            == Decision::Allow
    })
}

/// Only return a reason when a policy rule drove the prompt decision.
fn derive_prompt_reason(command_args: &[String], evaluation: &Evaluation) -> Option<String> {
    let command = render_shlex_command(command_args);

    let most_specific_prompt = evaluation
        .matched_rules
        .iter()
        .filter_map(|rule_match| match rule_match {
            RuleMatch::PrefixRuleMatch {
                matched_prefix,
                decision,
                justification,
                ..
            } if *decision == evaluation.decision => {
                Some((matched_prefix.len(), justification.as_deref()))
            }
            _ => None,
        })
        .max_by_key(|(matched_prefix_len, _)| *matched_prefix_len);

    let approval = if evaluation.decision == Decision::PromptUser {
        "human approval"
    } else {
        "approval"
    };
    match most_specific_prompt {
        Some((_matched_prefix_len, Some(justification))) => {
            Some(format!("`{command}` requires {approval}: {justification}"))
        }
        Some((_matched_prefix_len, None)) => {
            Some(format!("`{command}` requires {approval} by policy"))
        }
        None => None,
    }
}

fn render_shlex_command(args: &[String]) -> String {
    shlex_try_join(args.iter().map(String::as_str)).unwrap_or_else(|_| args.join(" "))
}

async fn collect_policy_files(dir: impl AsRef<Path>) -> Result<Vec<PathBuf>, ExecPolicyError> {
    let dir = dir.as_ref();
    let mut read_dir = match fs::read_dir(dir).await {
        Ok(read_dir) => read_dir,
        Err(err) if err.kind() == ErrorKind::NotFound => return Ok(Vec::new()),
        Err(source) => {
            return Err(ExecPolicyError::ReadDir {
                dir: dir.to_path_buf(),
                source,
            });
        }
    };

    let mut policy_paths = Vec::new();
    while let Some(entry) =
        read_dir
            .next_entry()
            .await
            .map_err(|source| ExecPolicyError::ReadDir {
                dir: dir.to_path_buf(),
                source,
            })?
    {
        let path = entry.path();
        let file_type = entry
            .file_type()
            .await
            .map_err(|source| ExecPolicyError::ReadDir {
                dir: dir.to_path_buf(),
                source,
            })?;

        if path
            .extension()
            .and_then(|ext| ext.to_str())
            .is_some_and(|ext| ext == RULE_EXTENSION)
            && file_type.is_file()
        {
            policy_paths.push(path);
        }
    }

    policy_paths.sort();

    tracing::debug!(
        "loaded {} .rules files in {}",
        policy_paths.len(),
        dir.display()
    );
    Ok(policy_paths)
}

#[cfg(test)]
#[path = "exec_policy_tests.rs"]
mod tests;
