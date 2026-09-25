from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .backend import AgentBackend
from .gitops import (
    EvidenceBundle,
    checkpoint,
    collect_evidence,
    create_worktree,
    head_sha,
    repo_root,
)
from .schema import MANAGER_SCHEMA, REVIEW_SCHEMA, ManagerPlan, ReviewResult, TaskSpec


@dataclass(slots=True)
class WorkflowConfig:
    """Configuration for the original standalone Manager -> Worker -> Reviewer loop."""

    repo: Path
    manager_model: str | None = None
    worker_model: str | None = None
    reviewer_model: str | None = None
    manager_effort: str | None = None
    worker_effort: str | None = None
    reviewer_effort: str | None = None
    max_revisions: int = 3
    verify_commands: list[str] = field(default_factory=list)
    verify_timeout_seconds: int | None = 1800
    isolate_worktree: bool = True
    worker_sandbox: str = "workspace-write"
    inherit_codex_config: bool = False
    state_root: Path = field(
        default_factory=lambda: Path.home() / ".multiagent-workflow" / "runs"
    )
    worktree_root: Path = field(
        default_factory=lambda: Path.home() / ".multiagent-workflow" / "worktrees"
    )


@dataclass(slots=True)
class TaskOutcome:
    task: TaskSpec
    attempts: int
    review: ReviewResult
    checkpoint_sha: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task.to_dict(),
            "attempts": self.attempts,
            "review": self.review.to_dict(),
            "checkpoint_sha": self.checkpoint_sha,
        }


@dataclass(slots=True)
class WorkflowResult:
    run_id: str
    plan: ManagerPlan
    outcomes: list[TaskOutcome]
    workspace: Path
    branch: str | None
    state_dir: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "architecture": "standalone",
            "run_id": self.run_id,
            "plan": self.plan.to_dict(),
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "workspace": str(self.workspace),
            "branch": self.branch,
            "state_dir": str(self.state_dir),
        }


def run_workflow(*, goal: str, backend: AgentBackend, config: WorkflowConfig) -> WorkflowResult:
    """Run the original standalone Manager -> Worker -> Reviewer architecture."""

    if not goal.strip():
        raise ValueError("goal must not be empty")
    if config.max_revisions < 0:
        raise ValueError("max_revisions must be >= 0")

    source_repo = repo_root(config.repo)
    run_id = _make_run_id()
    state_dir = config.state_root / run_id
    state_dir.mkdir(parents=True, exist_ok=False)

    branch: str | None = None
    if config.isolate_worktree:
        branch = f"maw/{run_id}"
        workspace = create_worktree(
            source_repo,
            path=config.worktree_root / run_id,
            branch=branch,
        )
    else:
        workspace = source_repo

    _write_json(
        state_dir / "run.json",
        {
            "architecture": "standalone",
            "run_id": run_id,
            "goal": goal,
            "source_repo": str(source_repo),
            "workspace": str(workspace),
            "branch": branch,
            "config": _config_for_json(config),
            "status": "planning",
        },
    )

    manager_text = backend.run(
        role="manager",
        model=config.manager_model,
        cwd=workspace,
        prompt=_manager_prompt(goal),
        sandbox="read-only",
        effort=config.manager_effort,
        output_schema=MANAGER_SCHEMA,
        inherit_user_config=config.inherit_codex_config,
    )
    plan = ManagerPlan.from_dict(_parse_json_object(manager_text, "manager"))
    _write_json(state_dir / "plan.json", plan.to_dict())

    outcomes: list[TaskOutcome] = []
    completed_ids: set[str] = set()

    for task in plan.tasks:
        missing = [dep for dep in task.dependencies if dep not in completed_ids]
        if missing:
            raise RuntimeError(f"Task {task.id} has unsatisfied dependencies: {missing}")

        task_dir = state_dir / "tasks" / task.id
        task_dir.mkdir(parents=True, exist_ok=True)
        _write_json(task_dir / "task.json", task.to_dict())
        baseline = head_sha(workspace)
        feedback: ReviewResult | None = None
        final_review: ReviewResult | None = None
        attempts = 0

        for attempt in range(1, config.max_revisions + 2):
            attempts = attempt
            attempt_dir = task_dir / f"attempt-{attempt:02d}"
            attempt_dir.mkdir(parents=True, exist_ok=True)

            worker_text = backend.run(
                role="worker",
                model=config.worker_model,
                cwd=workspace,
                prompt=_worker_prompt(goal, task, feedback),
                sandbox=config.worker_sandbox,
                effort=config.worker_effort,
                output_schema=None,
                inherit_user_config=config.inherit_codex_config,
            )
            (attempt_dir / "worker.md").write_text(worker_text, encoding="utf-8")

            evidence = collect_evidence(
                workspace,
                baseline_sha=baseline,
                verify_commands=config.verify_commands,
                verify_timeout_seconds=config.verify_timeout_seconds,
            )
            _write_json(attempt_dir / "evidence.json", evidence.to_dict())
            (attempt_dir / "diff.patch").write_text(evidence.diff, encoding="utf-8")

            reviewer_text = backend.run(
                role="reviewer",
                model=config.reviewer_model,
                cwd=workspace,
                prompt=_reviewer_prompt(goal, task, evidence),
                sandbox="read-only",
                effort=config.reviewer_effort,
                output_schema=REVIEW_SCHEMA,
                inherit_user_config=config.inherit_codex_config,
            )
            final_review = ReviewResult.from_dict(
                _parse_json_object(reviewer_text, "reviewer")
            )

            if not evidence.verification_passed and final_review.decision == "PASS":
                failed = [v.command for v in evidence.verifications if not v.passed]
                final_review = ReviewResult(
                    decision="REVISE",
                    summary="Reviewer returned PASS, but deterministic verification failed.",
                    problems=[f"Verification failed: {command}" for command in failed],
                    required_changes=[
                        "Fix the implementation so every configured verification command exits 0."
                    ],
                )
            _write_json(attempt_dir / "review.json", final_review.to_dict())

            if final_review.decision == "PASS":
                break
            feedback = final_review

        assert final_review is not None
        if final_review.decision != "PASS":
            _update_run_status(state_dir, "failed", failed_task=task.id)
            raise RuntimeError(
                f"Task {task.id} failed review after {attempts} attempts: "
                f"{final_review.summary}"
            )

        commit_sha: str | None = None
        if config.isolate_worktree:
            commit_sha = checkpoint(
                workspace,
                message=f"maw: {task.id} {task.title}",
            )
        outcome = TaskOutcome(
            task=task,
            attempts=attempts,
            review=final_review,
            checkpoint_sha=commit_sha,
        )
        outcomes.append(outcome)
        completed_ids.add(task.id)
        _write_json(task_dir / "outcome.json", outcome.to_dict())

    result = WorkflowResult(
        run_id=run_id,
        plan=plan,
        outcomes=outcomes,
        workspace=workspace,
        branch=branch,
        state_dir=state_dir,
    )
    _write_json(state_dir / "result.json", result.to_dict())
    _update_run_status(state_dir, "passed")
    return result


def _manager_prompt(goal: str) -> str:
    return f"""You are the Manager in a Manager -> Worker -> Reviewer software-engineering workflow.

Inspect the repository in read-only mode as needed. Your only job is to create an ordered,
small, verifiable plan. Do not edit files and do not implement the solution.

Overall goal:
{goal}

Rules:
- Split work into the smallest sensible independently reviewable tasks.
- Every task must have objective acceptance criteria.
- Dependencies may refer only to earlier task IDs.
- Mention likely files/tests when useful, but do not invent paths you have not verified.
- Preserve the user's scope; do not add unrelated improvements.
- Return exactly the requested structured JSON.
"""


def _worker_prompt(goal: str, task: TaskSpec, feedback: ReviewResult | None) -> str:
    feedback_text = ""
    if feedback is not None:
        feedback_text = "\nReviewer feedback from the previous attempt:\n" + json.dumps(
            feedback.to_dict(), ensure_ascii=False, indent=2
        )
    return f"""You are the Worker in a deterministic Manager -> Worker -> Reviewer workflow.

Work inside the current repository. Execute exactly ONE assigned task. You may inspect and
edit files and run relevant tests permitted by the sandbox. Do not commit, push, rebase,
or rewrite git history; the harness owns checkpoints.

Overall goal:
{goal}

Task:
{json.dumps(task.to_dict(), ensure_ascii=False, indent=2)}
{feedback_text}

Rules:
- Satisfy every acceptance criterion.
- Continue from the current workspace state; do not discard correct prior changes.
- Do not expand scope unless required for correctness.
- Run focused tests when practical.
- At the end, summarize what you changed and what you actually verified.
- Never claim a command/test passed unless you observed it pass.
"""


def _reviewer_prompt(goal: str, task: TaskSpec, evidence: EvidenceBundle) -> str:
    return f"""You are the independent Reviewer in a Manager -> Worker -> Reviewer workflow.

You are read-only. Do not modify files. Judge the current task against every acceptance
criterion using the repository itself plus the evidence collected by the deterministic
harness. The worker's prose is intentionally NOT provided because git state and test
results are more reliable.

Overall goal:
{goal}

Task:
{json.dumps(task.to_dict(), ensure_ascii=False, indent=2)}

Harness evidence:
{json.dumps(evidence.to_dict(), ensure_ascii=False, indent=2)}

Decision rules:
- PASS only when every acceptance criterion is satisfied and configured verification passes.
- REVISE for any material defect, missing criterion, unsupported claim, or failing verifier.
- For REVISE, required_changes must be concrete and limited to this task.
- Inspect relevant repository files in read-only mode when the diff alone is insufficient.
- Return exactly the requested structured JSON.
"""


def _parse_json_object(text: str, role: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{role} returned invalid structured JSON: {text[:500]!r}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{role} structured output must be a JSON object")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _make_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def _config_for_json(config: WorkflowConfig) -> dict[str, Any]:
    value = asdict(config)
    for key in ("repo", "state_root", "worktree_root"):
        value[key] = str(value[key])
    return value


def _update_run_status(state_dir: Path, status: str, **extra: Any) -> None:
    path = state_dir / "run.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["status"] = status
    value.update(extra)
    _write_json(path, value)
