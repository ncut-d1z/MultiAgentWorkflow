from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


from .backend import BackendError, CodexCliBackend
from .c2c import C2CError, C2CWorkflowConfig, run_c2c_workflow
from .orchestrator import WorkflowConfig, run_workflow
from .settings import (
    ARCHITECTURES,
    config_paths,
    get_setting,
    load_effective_settings,
    read_scope_config,
    set_setting,
    unset_setting,
    write_scope_config,
    dumps_toml,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maw",
        description="Configurable standalone and Codex-with-ChatGPT workflows",
    )
    parser.add_argument("--codex-binary", default="codex")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="Run Codex Sign in with ChatGPT login flow")
    sub.add_parser("doctor", help="Check Codex CLI and authentication diagnostics")

    config = sub.add_parser("config", help="View or edit persistent MAW configuration")
    config_sub = config.add_subparsers(dest="config_command", required=True)

    config_show = config_sub.add_parser("show", help="Show merged effective configuration")
    config_show.add_argument("--repo", type=Path, default=Path.cwd())
    config_show.add_argument("--json", action="store_true")

    config_path = config_sub.add_parser("path", help="Show user/project config paths")
    config_path.add_argument("--repo", type=Path, default=Path.cwd())

    config_init = config_sub.add_parser(
        "init", help="Interactively select architecture, Worker model and reasoning effort"
    )
    config_init.add_argument("--repo", type=Path, default=Path.cwd())
    config_init.add_argument("--scope", choices=["user", "project"], default="project")

    config_set = config_sub.add_parser("set", help="Set one persistent configuration key")
    config_set.add_argument("key")
    config_set.add_argument("value")
    config_set.add_argument("--repo", type=Path, default=Path.cwd())
    config_set.add_argument("--scope", choices=["user", "project"], default="project")

    config_unset = config_sub.add_parser("unset", help="Remove one persistent configuration key")
    config_unset.add_argument("key")
    config_unset.add_argument("--repo", type=Path, default=Path.cwd())
    config_unset.add_argument("--scope", choices=["user", "project"], default="project")

    run = sub.add_parser("run", help="Run the selected workflow architecture")
    run.add_argument("--repo", type=Path, default=Path.cwd())
    goal_group = run.add_mutually_exclusive_group(required=True)
    goal_group.add_argument("--goal")
    goal_group.add_argument("--goal-file", type=Path)

    run.add_argument("--architecture", choices=sorted(ARCHITECTURES), default=None)
    run.add_argument("--worker-model", default=None)
    run.add_argument("--worker-effort", default=None)
    run.add_argument(
        "--worker-sandbox",
        choices=["workspace-write", "danger-full-access"],
        default=None,
    )
    run.add_argument(
        "--inherit-codex-config",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Let child Codex sessions inherit ~/.codex/config.toml.",
    )

    # Standalone-only overrides. They remain available for compatibility and control.
    run.add_argument("--manager-model", default=None)
    run.add_argument("--reviewer-model", default=None)
    run.add_argument("--manager-effort", default=None)
    run.add_argument("--reviewer-effort", default=None)
    run.add_argument("--max-revisions", type=int, default=None)
    run.add_argument(
        "--in-place",
        action="store_true",
        default=None,
        help="Standalone only: modify the supplied checkout directly instead of a worktree.",
    )

    run.add_argument(
        "--verify",
        action="append",
        default=None,
        help="Verification command. Repeat for multiple gates. CLI values replace config values.",
    )
    run.add_argument("--verify-timeout", type=int, default=None)

    # C2C-only metadata. The C2C Skill owns INIT/PLAN/REVIEW/DONE; MAW executes one PLAN.
    run.add_argument("--c2c-task-id", default=None)
    run.add_argument("--c2c-iteration", type=int, default=None)
    run.add_argument(
        "--c2c-record",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Record this Worker iteration through `c2c record` for ChatGPT review.",
    )
    run.add_argument("--c2c-command", default=None)

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

        if args.command == "config":
            return _run_config_command(args)

        if args.command == "run":
            return _run_selected_architecture(args, backend)

        raise AssertionError(f"Unhandled command: {args.command}")
    except (BackendError, C2CError, RuntimeError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def _run_selected_architecture(args: argparse.Namespace, backend: CodexCliBackend) -> int:
    goal = _load_goal(args)
    settings = load_effective_settings(args.repo)
    architecture = args.architecture or get_setting(settings, "workflow.architecture")
    if architecture not in ARCHITECTURES:
        raise ValueError(
            "No workflow architecture selected. Run `maw config init --scope project` "
            "or pass `--architecture standalone|c2c`."
        )

    worker_model = _coalesce(args.worker_model, get_setting(settings, "worker.model"))
    worker_effort = _coalesce(
        args.worker_effort, get_setting(settings, "worker.reasoning_effort")
    )
    worker_sandbox = _coalesce(
        args.worker_sandbox, get_setting(settings, "worker.sandbox", "workspace-write")
    )
    verify_commands = (
        list(args.verify)
        if args.verify is not None
        else list(get_setting(settings, "verification.commands", []))
    )
    verify_timeout = _coalesce(
        args.verify_timeout,
        get_setting(settings, "verification.timeout_seconds", 1800),
    )

    if architecture == "standalone":
        inherit = _coalesce(
            args.inherit_codex_config,
            get_setting(settings, "standalone.inherit_codex_config", False),
        )
        isolate = bool(get_setting(settings, "standalone.isolate_worktree", True))
        if args.in_place is True:
            isolate = False
        config = WorkflowConfig(
            repo=args.repo,
            manager_model=_coalesce(
                args.manager_model, get_setting(settings, "standalone.manager.model")
            ),
            worker_model=worker_model,
            reviewer_model=_coalesce(
                args.reviewer_model, get_setting(settings, "standalone.reviewer.model")
            ),
            manager_effort=_coalesce(
                args.manager_effort,
                get_setting(settings, "standalone.manager.reasoning_effort"),
            ),
            worker_effort=worker_effort,
            reviewer_effort=_coalesce(
                args.reviewer_effort,
                get_setting(settings, "standalone.reviewer.reasoning_effort"),
            ),
            max_revisions=int(
                _coalesce(
                    args.max_revisions,
                    get_setting(settings, "standalone.max_revisions", 3),
                )
            ),
            verify_commands=verify_commands,
            verify_timeout_seconds=verify_timeout,
            isolate_worktree=isolate,
            worker_sandbox=worker_sandbox,
            inherit_codex_config=bool(inherit),
        )
        result = run_workflow(goal=goal, backend=backend, config=config)
        _print_standalone_result(result, as_json=args.json)
        return 0

    inherit = _coalesce(
        args.inherit_codex_config,
        get_setting(settings, "c2c.inherit_codex_config", True),
    )
    record_execution = _coalesce(
        args.c2c_record,
        get_setting(settings, "c2c.record_execution", False),
    )
    config = C2CWorkflowConfig(
        repo=args.repo,
        worker_model=worker_model,
        worker_effort=worker_effort,
        worker_sandbox=worker_sandbox,
        verify_commands=verify_commands,
        verify_timeout_seconds=verify_timeout,
        inherit_codex_config=bool(inherit),
        record_execution=bool(record_execution),
        c2c_command=_coalesce(args.c2c_command, get_setting(settings, "c2c.command", "c2c")),
        task_id=args.c2c_task_id,
        iteration=args.c2c_iteration,
    )
    result = run_c2c_workflow(goal=goal, backend=backend, config=config)
    _print_c2c_result(result, as_json=args.json)
    return 0


def _run_config_command(args: argparse.Namespace) -> int:
    if args.config_command == "show":
        settings = load_effective_settings(args.repo)
        paths = config_paths(args.repo)
        if args.json:
            print(
                json.dumps(
                    {
                        "user_config": str(paths.user),
                        "project_config": str(paths.project),
                        "effective": settings,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(f"user_config = {paths.user}")
            print(f"project_config = {paths.project}")
            print("\n# effective configuration")
            print(dumps_toml(settings), end="")
        return 0

    if args.config_command == "path":
        paths = config_paths(args.repo)
        print(f"user:    {paths.user}")
        print(f"project: {paths.project}")
        return 0

    if args.config_command == "init":
        path, data = read_scope_config(args.repo, args.scope)
        architecture = _prompt_choice(
            "Workflow architecture [standalone/c2c]: ", ARCHITECTURES
        )
        model = input("Worker model [blank = current Codex default]: ").strip()
        effort = input("Worker reasoning effort [blank = current Codex default]: ").strip()
        set_setting(data, "workflow.architecture", architecture)
        if model:
            set_setting(data, "worker.model", model)
        else:
            unset_setting(data, "worker.model")
        if effort:
            set_setting(data, "worker.reasoning_effort", effort)
        else:
            unset_setting(data, "worker.reasoning_effort")
        written = write_scope_config(args.repo, args.scope, data)
        print(f"Saved: {written}")
        return 0

    if args.config_command == "set":
        _, data = read_scope_config(args.repo, args.scope)
        set_setting(data, args.key, args.value)
        written = write_scope_config(args.repo, args.scope, data)
        print(f"Saved: {written}")
        return 0

    if args.config_command == "unset":
        _, data = read_scope_config(args.repo, args.scope)
        removed = unset_setting(data, args.key)
        written = write_scope_config(args.repo, args.scope, data)
        print(f"Saved: {written}")
        if not removed:
            print(f"Note: {args.key} was not set in the {args.scope} config.")
        return 0

    raise AssertionError(f"Unhandled config command: {args.config_command}")


def _print_standalone_result(result, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return
    print(f"PASS architecture=standalone run_id={result.run_id}")
    print(f"workspace={result.workspace}")
    if result.branch:
        print(f"branch={result.branch}")
    print(f"state={result.state_dir}")
    for outcome in result.outcomes:
        print(
            f"{outcome.task.id}: PASS attempts={outcome.attempts} "
            f"checkpoint={outcome.checkpoint_sha or '-'}"
        )


def _print_c2c_result(result, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return
    verification = "PASS" if result.evidence.verification_passed else "FAIL"
    changed = len([line for line in result.evidence.status.splitlines() if line.strip()])
    print(f"EXECUTED architecture=c2c run_id={result.run_id}")
    print(f"workspace={result.workspace}")
    print(f"changed_files={changed}")
    print(f"verification={verification}")
    print(f"c2c_recorded={'yes' if result.c2c_recorded else 'no'}")
    print(f"state={result.state_dir}")


def _prompt_choice(prompt: str, choices: set[str]) -> str:
    while True:
        value = input(prompt).strip().lower()
        if value in choices:
            return value
        print(f"Choose one of: {', '.join(sorted(choices))}")


def _load_goal(args: argparse.Namespace) -> str:
    if args.goal is not None:
        return args.goal.strip()
    assert args.goal_file is not None
    return args.goal_file.read_text(encoding="utf-8").strip()


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


if __name__ == "__main__":
    raise SystemExit(main())
