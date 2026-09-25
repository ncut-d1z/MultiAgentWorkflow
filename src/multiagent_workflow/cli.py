from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .backend import BackendError, CodexCliBackend
from .orchestrator import WorkflowConfig, run_workflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maw",
        description="Manager -> Worker -> Reviewer workflow over Codex CLI",
    )
    parser.add_argument("--codex-binary", default="codex")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="Run Codex Sign in with ChatGPT login flow")
    sub.add_parser("doctor", help="Check Codex CLI and authentication diagnostics")

    run = sub.add_parser("run", help="Run a Manager -> Worker -> Reviewer workflow")
    run.add_argument("--repo", type=Path, default=Path.cwd())
    goal_group = run.add_mutually_exclusive_group(required=True)
    goal_group.add_argument("--goal")
    goal_group.add_argument("--goal-file", type=Path)
    run.add_argument("--manager-model", default="gpt-5.6-sol")
    run.add_argument("--worker-model", default="gpt-5.5")
    run.add_argument("--reviewer-model", default="gpt-5.6-sol")
    run.add_argument("--manager-effort", default="high")
    run.add_argument("--worker-effort", default="high")
    run.add_argument("--reviewer-effort", default="high")
    run.add_argument("--max-revisions", type=int, default=3)
    run.add_argument(
        "--verify",
        action="append",
        default=[],
        help="Verification command. Repeat for multiple gates.",
    )
    run.add_argument("--verify-timeout", type=int, default=1800)
    run.add_argument(
        "--in-place",
        action="store_true",
        help="Modify the supplied checkout directly instead of creating an isolated git worktree.",
    )
    run.add_argument(
        "--worker-sandbox",
        choices=["workspace-write", "danger-full-access"],
        default="workspace-write",
    )
    run.add_argument("--json", action="store_true", help="Print final result as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    backend = CodexCliBackend(codex_binary=args.codex_binary)

    try:
        if args.command == "login":
            return backend.login()

        if args.command == "doctor":
            result = backend.doctor()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("ok") else 1

        if args.command == "run":
            goal = _load_goal(args)
            config = WorkflowConfig(
                repo=args.repo,
                manager_model=args.manager_model,
                worker_model=args.worker_model,
                reviewer_model=args.reviewer_model,
                manager_effort=args.manager_effort,
                worker_effort=args.worker_effort,
                reviewer_effort=args.reviewer_effort,
                max_revisions=args.max_revisions,
                verify_commands=args.verify,
                verify_timeout_seconds=args.verify_timeout,
                isolate_worktree=not args.in_place,
                worker_sandbox=args.worker_sandbox,
            )
            result = run_workflow(goal=goal, backend=backend, config=config)
            if args.json:
                print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            else:
                print(f"PASS run_id={result.run_id}")
                print(f"workspace={result.workspace}")
                if result.branch:
                    print(f"branch={result.branch}")
                print(f"state={result.state_dir}")
                for outcome in result.outcomes:
                    print(
                        f"{outcome.task.id}: PASS attempts={outcome.attempts} "
                        f"checkpoint={outcome.checkpoint_sha or '-'}"
                    )
            return 0

        raise AssertionError(f"Unhandled command: {args.command}")
    except (BackendError, RuntimeError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def _load_goal(args: argparse.Namespace) -> str:
    if args.goal is not None:
        return args.goal.strip()
    assert args.goal_file is not None
    return args.goal_file.read_text(encoding="utf-8").strip()


if __name__ == "__main__":
    raise SystemExit(main())
