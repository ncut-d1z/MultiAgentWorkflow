from __future__ import annotations

import json
import os
import shlex
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .backend import AgentBackend
from .gitops import EvidenceBundle, collect_evidence, head_sha, repo_root


class C2CError(RuntimeError):
    pass


@dataclass(slots=True)
class C2CWorkflowConfig:
    repo: Path
    worker_model: str | None = None
    worker_effort: str | None = None
    worker_sandbox: str = "workspace-write"
    verify_commands: list[str] = field(default_factory=list)
    verify_timeout_seconds: int | None = 1800
    inherit_codex_config: bool = True
    record_execution: bool = False
    c2c_command: str = "c2c"
    task_id: str | None = None
    iteration: int | None = None
    state_root: Path = field(
        default_factory=lambda: Path.home() / ".multiagent-workflow" / "runs"
    )


@dataclass(slots=True)
class C2CWorkflowResult:
    run_id: str
    workspace: Path
    state_dir: Path
    worker_output: str
    evidence: EvidenceBundle
    c2c_recorded: bool
    task_id: str | None
    iteration: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "architecture": "c2c",
            "run_id": self.run_id,
            "workspace": str(self.workspace),
            "state_dir": str(self.state_dir),
            "worker_output": self.worker_output,
            "evidence": self.evidence.to_dict(),
            "c2c_recorded": self.c2c_recorded,
            "task_id": self.task_id,
            "iteration": self.iteration,
        }


def run_c2c_workflow(
    *,
    goal: str,
    backend: AgentBackend,
    config: C2CWorkflowConfig,
) -> C2CWorkflowResult:
    """Execute one C2C PLAN iteration using a configurable Worker.

    ChatGPT/codex-with-chatgpt remains the Manager + Reviewer. This function does
    not create a second manager/reviewer loop and deliberately works in the exact
    repository/worktree passed by the caller so the C2C read-only connector sees
    the same git state.
    """

    if not goal.strip():
        raise ValueError("goal must not be empty")
    if config.iteration is not None and config.iteration < 0:
        raise ValueError("C2C iteration must be >= 0")
    if config.record_execution and (not config.task_id or config.iteration is None):
        raise ValueError(
            "C2C recording requires both task_id and iteration. Pass "
            "--c2c-task-id and --c2c-iteration."
        )

    workspace = repo_root(config.repo)
    run_id = _make_run_id()
    state_dir = config.state_root / run_id
    state_dir.mkdir(parents=True, exist_ok=False)
    baseline = head_sha(workspace)

    _write_json(
        state_dir / "run.json",
        {
            "architecture": "c2c",
            "run_id": run_id,
            "goal": goal,
            "workspace": str(workspace),
            "status": "executing",
            "config": _config_for_json(config),
        },
    )

    worker_output = backend.run(
        role="worker",
        model=config.worker_model,
        cwd=workspace,
        prompt=_worker_prompt(goal),
        sandbox=config.worker_sandbox,
        effort=config.worker_effort,
        output_schema=None,
        inherit_user_config=config.inherit_codex_config,
    )
    (state_dir / "worker.md").write_text(worker_output, encoding="utf-8")

    evidence = collect_evidence(
        workspace,
        baseline_sha=baseline,
        verify_commands=config.verify_commands,
        verify_timeout_seconds=config.verify_timeout_seconds,
    )
    _write_json(state_dir / "evidence.json", evidence.to_dict())
    (state_dir / "diff.patch").write_text(evidence.diff, encoding="utf-8")

    c2c_recorded = False
    if config.record_execution:
        _record_c2c_execution(
            config=config,
            workspace=workspace,
            state_dir=state_dir,
            evidence=evidence,
        )
        c2c_recorded = True

    result = C2CWorkflowResult(
        run_id=run_id,
        workspace=workspace,
        state_dir=state_dir,
        worker_output=worker_output,
        evidence=evidence,
        c2c_recorded=c2c_recorded,
        task_id=config.task_id,
        iteration=config.iteration,
    )
    _write_json(state_dir / "result.json", result.to_dict())
    _update_run_status(state_dir, "executed")
    return result


def _worker_prompt(goal: str) -> str:
    return f"""You are the Worker execution layer inside a codex-with-chatgpt workflow.

ChatGPT already owns high-level planning and will independently review the resulting
workspace through its read-only connector. Execute the supplied PLAN/instruction in the
current workspace. Do not create a competing high-level plan and do not act as the final
reviewer.

PLAN / instruction:
{goal}

Rules:
- Inspect the repository as needed and implement the requested changes.
- Keep changes within the supplied scope.
- Run focused tests when practical, but the outer deterministic harness may also run
  configured verification commands after you finish.
- Do not commit, push, rebase, or rewrite git history.
- Leave the working tree changes visible for the external ChatGPT Reviewer.
- At the end, report what you changed and what you actually verified.
- Never claim a command/test passed unless you observed it pass.
"""


def _record_c2c_execution(
    *,
    config: C2CWorkflowConfig,
    workspace: Path,
    state_dir: Path,
    evidence: EvidenceBundle,
) -> None:
    assert config.task_id is not None
    assert config.iteration is not None

    changed_count = len([line for line in evidence.status.splitlines() if line.strip()])
    tests_summary = _tests_summary(evidence)
    status = "ok" if evidence.verification_passed else "failed"
    command = _split_command(config.c2c_command)
    args = [
        *command,
        "record",
        "-w",
        str(workspace),
        "--task",
        config.task_id,
        "--iteration",
        str(config.iteration),
        "--changed-files",
        str(changed_count),
        "--tests",
        tests_summary,
        "--exit-status",
        status,
    ]

    selected = _select_verification_for_release(evidence)
    if selected is not None:
        log_path = state_dir / "verification-output.txt"
        log_path.write_text(
            f"$ {selected.command}\n\nSTDOUT\n{selected.stdout}\n\nSTDERR\n{selected.stderr}",
            encoding="utf-8",
        )
        args.extend(
            [
                "--command",
                selected.command,
                "--output-file",
                str(log_path),
                "--exit-code",
                str(selected.returncode),
            ]
        )

    try:
        completed = subprocess.run(
            args,
            cwd=workspace,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            env=dict(os.environ) | {"PYTHONUTF8": "1"},
        )
    except OSError as exc:
        raise C2CError(
            f"Unable to run C2C recorder command {config.c2c_command!r}: {exc}"
        ) from exc
    if completed.returncode != 0:
        _update_run_status(state_dir, "record_failed")
        detail = (completed.stderr or completed.stdout).strip()
        raise C2CError(
            f"c2c record failed with exit code {completed.returncode}:\n{detail}"
        )


def _tests_summary(evidence: EvidenceBundle) -> str:
    if not evidence.verifications:
        return "No harness verification command configured"
    parts = [
        f"{item.command}: {'passed' if item.passed else f'failed({item.returncode})'}"
        for item in evidence.verifications
    ]
    text = "; ".join(parts)
    return text[:1000]


def _select_verification_for_release(evidence: EvidenceBundle):
    if not evidence.verifications:
        return None
    for item in evidence.verifications:
        if not item.passed:
            return item
    return evidence.verifications[-1]


def _split_command(value: str) -> list[str]:
    candidate = Path(value).expanduser()
    if candidate.exists():
        return [str(candidate)]
    parts = shlex.split(value, posix=os.name != "nt")
    if not parts:
        raise ValueError("c2c.command must not be empty")
    return parts


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _make_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def _config_for_json(config: C2CWorkflowConfig) -> dict[str, Any]:
    value = asdict(config)
    value["repo"] = str(value["repo"])
    value["state_root"] = str(value["state_root"])
    return value


def _update_run_status(state_dir: Path, status: str) -> None:
    path = state_dir / "run.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["status"] = status
    _write_json(path, value)
