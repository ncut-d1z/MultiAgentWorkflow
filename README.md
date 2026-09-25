# MultiAgentWorkflow

A small, deterministic **Manager -> Worker -> Reviewer** coding workflow that uses the
OpenAI Codex CLI and its existing **Sign in with ChatGPT** session. No manually copied
OpenAI API key is required.

```text
User goal
   |
   v
Manager (default: gpt-5.6-sol, read-only)
   |
   v
TaskSpec[]
   |
   v
Worker (default: gpt-5.5, workspace-write)
   |
   v
Harness captures real git diff/status + verifier output
   |
   v
Reviewer (default: gpt-5.6-sol, read-only)
   | PASS
   +------------------> checkpoint -> next task
   |
   | REVISE
   v
Fresh Worker context + reviewer findings
```

The control loop lives in Python, not in a prompt. The Reviewer never receives only the
Worker's self-report: the harness independently gathers the git diff, repository status,
and configured verification command results.

## Authentication: no API key required

This project intentionally drives the **Codex CLI**, not the generic OpenAI Responses API.
Codex can authenticate using your ChatGPT account:

```powershell
codex login
```

Choose **Sign in with ChatGPT** in the browser flow. MultiAgentWorkflow then invokes
`codex exec` and reuses that stored Codex login state.

You can also run:

```powershell
maw login
```

Important distinction: **Sign in with ChatGPT is not a general replacement for an OpenAI
API key in arbitrary API clients.** It works here because Codex officially supports
ChatGPT-account authentication. If you rewrite this project to call `/v1/responses`
directly with the normal OpenAI SDK, use the API authentication required by that API.

MultiAgentWorkflow removes `OPENAI_API_KEY` and `CODEX_API_KEY` from Codex child-process
environments by default so stale API-key variables cannot override the stored ChatGPT
login.

## Requirements

- Windows 11, macOS, or Linux
- Python 3.10+
- Git
- Current OpenAI Codex CLI
- A ChatGPT plan/account that can use the selected Codex models

Install Codex using OpenAI's current installer, then sign in:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"
codex login
```

Install this project:

```powershell
git clone https://github.com/ncut-d1z/MultiAgentWorkflow.git
cd MultiAgentWorkflow
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
maw doctor
```

## Run

The safe default creates a new git branch + isolated worktree. The source checkout is not
modified directly.

```powershell
maw run `
  --repo D:\path\to\your\repo `
  --goal "Add frame-jump keyboard input and tests" `
  --verify "ctest --test-dir out/build --output-on-failure"
```

Default model assignment:

```text
Manager  = gpt-5.6-sol
Worker   = gpt-5.5
Reviewer = gpt-5.6-sol
```

Override any role independently:

```powershell
maw run `
  --repo D:\project `
  --goal-file .\task.txt `
  --manager-model gpt-5.6-sol `
  --worker-model gpt-5.6-terra `
  --reviewer-model gpt-5.6-sol `
  --max-revisions 4 `
  --verify "python -m pytest"
```

MultiAgentWorkflow **never silently substitutes a model**. If `gpt-5.5` is not enabled on
your current ChatGPT/Codex route, the run fails with an explicit error so you can choose a
model that `codex` can actually access.

### In-place mode

Only use this when you intentionally want the agents to edit your current checkout:

```powershell
maw run --repo . --goal "..." --in-place
```

The default isolated worktree mode is strongly preferred.

### Worker sandbox

Default:

```text
--worker-sandbox workspace-write
```

You can explicitly request Codex's broad mode:

```text
--worker-sandbox danger-full-access
```

Do this only in a disposable/isolated worktree. `danger-full-access` is not constrained to
that worktree by this Python program.

## Deterministic review gate

For each task the harness records:

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

`evidence.json` contains the actual git status/diff and verifier stdout/stderr/exit code.
If any configured verifier fails, the harness forces the task to `REVISE` even if the LLM
Reviewer incorrectly emits `PASS`.

When using the default isolated worktree, every accepted task becomes a local checkpoint
commit on a branch named `maw/<run-id>`.

## Why a CLI backend instead of direct API calls?

This project is aimed at users who already have a ChatGPT/Codex login but do not have (or
do not want to manage) an API key. The Codex CLI owns authentication, model access, coding
tools, and sandbox behavior; this project owns only orchestration, evidence capture, and
the review loop.

This also follows a pattern used by existing open-source coding-agent orchestrators: treat
agent CLIs as swappable execution backends and keep the loop deterministic outside the
model.

## Related open-source projects / design references

MultiAgentWorkflow is an independent implementation. The following projects were reviewed
for architectural ideas; no source files were copied into this repository.

- **master-workflow** — https://github.com/luckeyfaraday/master-workflow  
  Deterministic worker -> cross-model reviewer loop, real git diff as review evidence,
  fresh worker contexts, audit ledger, read-only reviewer.
- **Athena Loops** — https://github.com/luckeyfaraday/athena-loops  
  Python orchestrator -> worker -> reviewer harness, backend adapter seam, termination
  guards, CLI/MCP integration, optional git worktree isolation.
- **MCO** — https://github.com/mco-org/mco  
  CLI-first orchestration across multiple coding-agent providers/models with explicit
  read/write permission modes and raw artifact retention.
- **Agent Orchestrator** — https://github.com/Untrivial-ai/agent-orchestrator  
  One task/agent/worktree model with review, CI and feedback-loop supervision.
- **multiAgents** — https://github.com/1345191768/multiAgents  
  Local multi-agent coding orchestrator using isolated git worktrees and configurable
  model routing.
- **multiagent-code** — https://github.com/willsmanley/multiagent-code  
  Orchestrator/Manager/Worker coding hierarchy with Manager review of Worker output.
- **agentUniverse (Gitee)** — https://gitee.com/agentUniverse  
  Multi-agent pattern factory; its PEER pattern separates Plan, Execute, Express, Review.
- **emage.code (GitLab)** — https://gitlab.com/em-age/emage.code  
  Multi-platform AI development-team orchestration with plan/approve/execute and validation
  gates.

## Known upstream caveats (September 2026)

- Some users have recently reported that `gpt-5.5` is visible in Codex model metadata but
  fails on the ChatGPT-auth Codex route with model-not-found/access errors. This is an
  upstream entitlement/routing condition, not something this harness can bypass.
- Codex sandbox behavior depends on OS and Codex version. In particular, there have been
  recent Windows reports involving `workspace-write`. Keep the isolated-worktree default,
  run deterministic verification commands, and inspect the final branch before merging.

## Tests

The unit tests do not call an LLM. They use a fake backend and a temporary git repository:

```powershell
python -m unittest discover -s tests -v
```

## License

MIT.
