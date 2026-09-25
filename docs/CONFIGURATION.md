# Configuration

MAW separates **portable repository preferences** from **trusted runtime policy**.

## Precedence

```text
CLI > project .maw.toml > user config > built-in non-model defaults
```

The user config is:

```text
~/.multiagent-workflow/config.toml
```

On Windows that normally resolves under `%USERPROFILE%`.

The project config is `<repo>/.maw.toml`.

## Project config: intentionally narrow

Project `.maw.toml` may contain only:

```toml
[workflow]
architecture = "standalone" # or "c2c"

[worker]
model = "model-id"
reasoning_effort = "high"
```

All three are optional except that an architecture must ultimately be resolved before
`maw run` starts.

A repository file cannot configure verifier shell commands, `danger-full-access`, C2C
executable paths, or worktree isolation. This is intentional: cloning a repository must not
be enough to silently widen local execution privileges.

## User config

The trusted user config may additionally set runtime policy:

```toml
[worker]
sandbox = "workspace-write"

[verification]
timeout_seconds = 1800
commands = ["python -m pytest"]

[standalone]
max_revisions = 3
isolate_worktree = true
inherit_codex_config = false

[standalone.manager]
model = "gpt-5.6-sol"
reasoning_effort = "high"

[standalone.reviewer]
model = "gpt-5.6-sol"
reasoning_effort = "high"

[c2c]
record_execution = false
command = "c2c"
inherit_codex_config = true
```

`verification.commands` in user config are trusted local shell commands. The project config
is forbidden from setting them.

## Model and reasoning-effort semantics

MAW does not maintain a model catalog or reasoning-effort enum. Values are passed to Codex
as-is:

```text
worker.model             -> codex exec --model <value>
worker.reasoning_effort  -> -c model_reasoning_effort="<value>"
```

If a field is absent, MAW omits that override entirely and Codex resolves the current
model/effort itself.

This design avoids replacing one soon-to-retire hard-coded Worker with another hard-coded
Worker.

## Commands

Interactive selection:

```powershell
maw config init --scope project --repo D:\project
```

Show effective configuration:

```powershell
maw config show --repo D:\project
maw config show --repo D:\project --json
```

Set/unset:

```powershell
maw config set worker.model gpt-5.6-terra --scope project --repo D:\project
maw config set worker.reasoning_effort high --scope project --repo D:\project
maw config unset worker.model --scope project --repo D:\project
```

Trusted user policy:

```powershell
maw config set worker.sandbox workspace-write --scope user
maw config set standalone.isolate_worktree true --scope user
maw config set c2c.record_execution true --scope user
```

`maw config set` supports scalar policy values. Arrays such as
`verification.commands` can be edited directly in the user TOML file, or verification
commands can be supplied per invocation with repeatable `--verify` flags.
