# Using MAW with codex-with-chatgpt

This integration deliberately keeps the two projects' responsibilities separate.

## Ownership

`codex-with-chatgpt` owns:

- ChatGPT connection/setup
- C2C `INIT -> PLAN -> EXECUTED -> REVIEW -> PLAN|DONE|BLOCKED`
- high-level planning
- independent ChatGPT review through read-only MCP
- C2C session/checkpoint state

MAW `architecture=c2c` owns:

- selecting/invoking the configured Worker model
- selecting/invoking the configured Worker reasoning effort
- applying one PLAN/instruction
- deterministic verification commands
- local execution evidence/audit artifacts
- optional `c2c record`

MAW does not start another Manager or Reviewer in this architecture.

## Why C2C runs in-place

The C2C connector is bound to one workspace security boundary. If MAW silently created a
second worktree, the Worker would edit one directory while ChatGPT reviewed another.
Therefore C2C mode always operates on the exact `--repo` supplied by the outer Codex/C2C
session.

If worktree isolation is desired, create/open the worktree **before** binding C2C, then pass
that same worktree path to MAW.

## Recommended outer Codex instruction

When you want the installed `codex-with-chatgpt` Skill to use MAW as its Worker harness, a
prompt can say:

```text
使用 Codex with ChatGPT 完成这个任务。ChatGPT 继续负责规划和 Review；每次收到
C2C PLAN 后，使用 MultiAgentWorkflow 的 c2c 架构执行该 PLAN。Worker 模型和推理
强度从 MAW 配置读取，不要在提示词中写死模型。执行完成后使用 MAW 的验证结果和
c2c record，再按原 C2C 协议发送 EXECUTED。
```

The outer Codex session can write the current PLAN to a temporary file and invoke:

```powershell
maw run `
  --architecture c2c `
  --repo . `
  --goal-file <plan-file> `
  --c2c-record `
  --c2c-task-id <TASK_ID> `
  --c2c-iteration <ITERATION> `
  --verify "<deterministic verification command>"
```

If C2C recording is left to the Skill itself, omit `--c2c-record` and the C2C metadata
arguments. MAW will still persist `evidence.json` and report verification status.

## No nested review loop

Do not use `architecture=standalone` from inside a C2C PLAN execution merely to obtain a
Worker. That would create:

```text
ChatGPT C2C Manager
  -> MAW Manager
    -> Worker
      -> MAW Reviewer
        -> ChatGPT C2C Reviewer
```

and produce two independent retry loops.

Use `architecture=c2c` so there is one high-level loop only:

```text
ChatGPT PLAN
  -> MAW Worker execution
  -> deterministic evidence / c2c record
  -> ChatGPT REVIEW
```

## C2C recording

When `--c2c-record` is enabled MAW calls the configured C2C command with metadata equivalent
to:

```text
c2c record -w <workspace>
  --task <task-id>
  --iteration <n>
  --changed-files <count>
  --tests <verification-summary>
  --exit-status ok|failed
```

When verifier output exists, MAW nominates one verifier log with `--command`,
`--output-file` and `--exit-code`. The C2C project's own sanitizer/security policy decides
whether ChatGPT may read that output body.

MAW never embeds repository diffs or verifier logs into a ChatGPT prompt in C2C mode.
