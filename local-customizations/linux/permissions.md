EL2 Control selects the existing `:danger-full-access` profile with
`approval_policy = "never"` and `approvals_reviewer = "auto_review"`. Selecting it
replaces the previous approval settings instead of inheriting them. With
normal approvals disabled, automatic review handles separate strict tool checks
without adding user approval prompts.

The footer renders EL2 Control in crimson red (`#FF5F6D`) when status-line
colours are enabled, using bright red on 16-colour terminals. The existing
monochrome toggle and contrast adjustment still apply.

The owner installation uses the same saved defaults in `config.toml`:

```toml
default_permissions = ":danger-full-access"
sandbox_mode = "danger-full-access"
approval_policy = "never"
approvals_reviewer = "auto_review"

[apps._default]
approvals_reviewer = "auto_review"
default_tools_approval_mode = "approve"
```

The portable configuration installer and recovery notes are preserved in
`romil-bijarnia/GRID`, under `extras/linux-el2-owner-control/permissions/`.
It updates only these settings and the EL2 startup default; existing model,
account, plugin and project configuration stays intact.

The owner account already has passwordless administrator access. The kernel
interface supplies kernel memory operations, kernel calls, register operations
and native execution at EL2. The permission profile controls local Codex
execution; it does not provide authority over remote services or protected
firmware. Existing conversations may retain their saved settings until the
owner explicitly selects EL2 Control again. New terminal launches use the
installed client and saved defaults.
