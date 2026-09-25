# MultiAgentWorkflow

MultiAgentWorkflow (MAW) is a deterministic coding-agent harness built around the OpenAI
Codex CLI and its **Sign in with ChatGPT** authentication. It supports two deliberately
separate workflow architectures:

1. **`standalone`** — MAW owns the full `Manager -> Worker -> Reviewer` loop.
2. **`c2c`** — designed for [`codex-with-chatgpt`](https://github.com/XiaoDuoYa/codex-with-chatgpt):
   ChatGPT owns planning/review and MAW executes one Worker iteration plus deterministic
   verification/evidence collection.

The user explicitly selects the architecture. MAW does **not** auto-detect or silently
switch architectures.

## No hard-coded Worker model

MAW intentionally does not ship with a fixed Worker model such as `gpt-5.5`.

- Set a Worker model explicitly when you want a pinned model.
- Set a Worker reasoning effort explicitly when you want a pinned effort.
- Leave either value unset to let the current Codex installation/account choose its
  current default.

This means model retirement does not require a MAW code release just to change a default.
Model IDs and reasoning-effort names are treated as open-ended strings and are forwarded to
Codex without maintaining a stale allow-list in MAW.

## Authentication: no API key required

MAW drives the **Codex CLI**, not the generic OpenAI Responses API. Sign in once:

```powershell
codex login
```

Choose **Sign in with ChatGPT**. You can also run:

```powershell
maw login
```

MAW removes `OPENAI_API_KEY` and `CODEX_API_KEY` from Codex child-process environments by
default so a stale API-key environment variable does not unexpectedly override the stored
ChatGPT login.

## Install

Requirements:

- Windows 11, macOS, or Linux
- Python 3.10+
- Git
- Current OpenAI Codex CLI

```powershell
git clone https://github.com/ncut-d1z/MultiAgentWorkflow.git
cd MultiAgentWorkflow
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
maw doctor
```

## Configuration decision

MAW uses **persistent TOML configuration plus CLI overrides**.

Configuration precedence is:

```text
CLI arguments
    > project .maw.toml
    > user ~/.multiagent-workflow/config.toml
    > MAW non-model runtime defaults
```

On Windows, the user config is normally:

```text
%USERPROFILE%\.multiagent-workflow\config.toml
```

The project config is:

```text
<repository>\.maw.toml
```

### Why two persistent config files?

The project file stores only the three preferences that naturally belong to a repository:

```toml
[workflow]
architecture = "c2c"

[worker]
model = "your-worker-model"
reasoning_effort = "high"
```

For safety, a project `.maw.toml` is **not allowed** to set command-execution or privilege
settings. It cannot disable worktree isolation, request `danger-full-access`, inject
verification commands, or replace the `c2c` executable. Those settings belong to the
trusted user config or an explicit CLI invocation.

This prevents a cloned repository from using its committed `.maw.toml` to silently widen
agent permissions or execute arbitrary local commands.

### Interactive first setup

For a project:

```powershell
maw config init --scope project --repo D:\path\to\repo
```

MAW asks for:

```text
Workflow architecture [standalone/c2c]:
Worker model [blank = current Codex default]:
Worker reasoning effort [blank = current Codex default]:
```

The architecture must be selected before `maw run` can proceed. Worker model and effort may
be blank intentionally.

You can also set values individually:

```powershell
maw config set workflow.architecture standalone --scope project --repo D:\project
maw config set worker.model gpt-5.6-terra --scope project --repo D:\project
maw config set worker.reasoning_effort high --scope project --repo D:\project
```

Remove a pin and go back to the Codex default:

```powershell
maw config unset worker.model --scope project --repo D:\project
maw config unset worker.reasoning_effort --scope project --repo D:\project
```

Inspect the effective configuration and both file locations:

```powershell
maw config show --repo D:\project
maw config path --repo D:\project
```

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for all supported settings.

---

# Architecture 1: standalone

This preserves the original MAW design:

```text
User goal
   |
   v
Manager (read-only Codex session)
   |
   v
TaskSpec[]
   |
   v
Worker (selected model + selected effort, workspace-write)
   |
   v
Harness: real git diff/status + verifier output
   |
   v
Reviewer (read-only Codex session)
   | PASS
   +------------------> checkpoint -> next task
   |
   | REVISE
   v
Worker retry with reviewer findings
```

Run:

```powershell
maw run `
  --repo D:\path\to\repo `
  --architecture standalone `
  --goal "Add frame-jump keyboard input and tests" `
  --verify "ctest --test-dir out/build --output-on-failure"
```

Normally `--architecture` can be omitted after it has been saved in `.maw.toml`.

Standalone creates an isolated git worktree by default. Every accepted task gets a local
checkpoint commit. Use `--in-place` only when you deliberately want to modify the supplied
checkout directly.

Manager and Reviewer models are also configurable, but are intentionally not project-level
settings:

```powershell
maw config set standalone.manager.model gpt-5.6-sol --scope user
maw config set standalone.manager.reasoning_effort high --scope user
maw config set standalone.reviewer.model gpt-5.6-sol --scope user
maw config set standalone.reviewer.reasoning_effort high --scope user
```

If these are unset, Codex chooses its current defaults.

### Deterministic gate

The Reviewer does not receive only the Worker's self-report. MAW independently records the
actual git status/diff and verifier stdout/stderr/exit codes. In `standalone`, a failing
configured verifier forces `REVISE` even if the LLM Reviewer mistakenly returns `PASS`.

---

# Architecture 2: c2c

This architecture is specifically designed to coexist with the
[`codex-with-chatgpt`](https://github.com/XiaoDuoYa/codex-with-chatgpt) Skill.

```text
ChatGPT Web / Sol
  Manager + Reviewer
        |
        | C2C PLAN / REVIEW
        v
Outer Codex session + codex-with-chatgpt Skill
        |
        | execute current PLAN
        v
MultiAgentWorkflow (architecture=c2c)
        |
        v
Worker (user-selected model + effort)
        |
        v
workspace changes + deterministic verification
        |
        +---- optional `c2c record` metadata/output ----+
        |                                               |
        +---------------- C2C read-only MCP ------------+
                                                        v
                                                ChatGPT Reviewer
```

Important differences from `standalone`:

- MAW does **not** create another Manager.
- MAW does **not** create another Reviewer.
- MAW does **not** internally loop on review feedback.
- MAW executes exactly one already-produced C2C PLAN/instruction.
- MAW works in the exact repository/worktree supplied to it.
- MAW does not checkpoint-commit C2C changes, so ChatGPT can inspect the current diff.
- The C2C/ChatGPT layer decides `PLAN`, `DONE`, or `BLOCKED` for the next protocol step.

Example single C2C execution iteration:

```powershell
maw run `
  --repo D:\project `
  --architecture c2c `
  --goal-file .\current-c2c-plan.txt `
  --worker-model gpt-5.6-terra `
  --worker-effort high `
  --verify "python -m pytest"
```

If the outer Skill supplies the current C2C task ID and iteration, MAW can also perform the
required `c2c record` step:

```powershell
maw run `
  --repo D:\project `
  --architecture c2c `
  --goal-file .\current-c2c-plan.txt `
  --c2c-record `
  --c2c-task-id c2c_f81a `
  --c2c-iteration 2 `
  --verify "python -m pytest"
```

MAW records changed-file count, verification summary, exit status and one selected verifier
output through `c2c record`. It never pastes the diff/log into a ChatGPT prompt; the external
ChatGPT Reviewer continues to inspect the workspace through C2C's read-only MCP tools.

See [docs/CODEX_WITH_CHATGPT.md](docs/CODEX_WITH_CHATGPT.md) for the intended integration.

## Runtime state

Standalone:

```text
~/.multiagent-workflow/runs/<run-id>/
  run.json
  plan.json
  result.json
  tasks/
    T01/
      task.json
      attempt-01/
        worker.md
        evidence.json
        diff.patch
        review.json
      outcome.json
```

C2C execution iteration:

```text
~/.multiagent-workflow/runs/<run-id>/
  run.json
  worker.md
  evidence.json
  diff.patch
  verification-output.txt   # when a verifier output is released to c2c
  result.json
```

## CLI overrides

Persistent settings are conveniences, not locks. For one run:

```powershell
maw run `
  --repo D:\project `
  --architecture standalone `
  --worker-model some-new-model `
  --worker-effort some-new-effort `
  --goal "..."
```

MAW deliberately does not validate Worker model IDs or reasoning-effort names against a
hard-coded catalog. Codex is the authority; if Codex rejects a value, MAW surfaces that
error instead of silently switching to another model.

## Related projects / design references

- **codex-with-chatgpt** — https://github.com/XiaoDuoYa/codex-with-chatgpt  
  ChatGPT plans/reviews through a read-only MCP bridge; Codex owns execution.
- **master-workflow** — https://github.com/luckeyfaraday/master-workflow  
  Deterministic worker/reviewer loop, real git diff review evidence, audit trail.
- **Athena Loops** — https://github.com/luckeyfaraday/athena-loops  
  Python orchestrator/worker/reviewer harness with backend adapter seam and worktrees.
- **MCO** — https://github.com/mco-org/mco  
  CLI-first multi-agent/provider orchestration with explicit permission modes.

MAW is an independent implementation; no source files from these projects are copied here.

## Tests

The tests use fake model backends and temporary git repositories; they do not consume model
quota:

```powershell
python -m unittest discover -s tests -v
```

Current tests cover both workflow architectures, config precedence/security, model/effort
forwarding, worktree isolation, C2C metadata recording and schema validation.

## License

MIT.
