from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from multiagent_workflow.c2c import C2CWorkflowConfig, run_c2c_workflow


class WorkerOnlyBackend:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(
        self,
        *,
        role,
        model,
        cwd,
        prompt,
        sandbox,
        effort,
        output_schema=None,
        inherit_user_config=False,
    ):
        if role != "worker":
            raise AssertionError(f"C2C architecture must not call {role}")
        self.calls.append(
            {
                "role": role,
                "model": model,
                "effort": effort,
                "inherit_user_config": inherit_user_config,
            }
        )
        (cwd / "marker.txt").write_text("ok\n", encoding="utf-8")
        return "implemented the supplied C2C plan"


class C2CTests(unittest.TestCase):
    def test_c2c_runs_only_worker_in_exact_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = init_repo(root / "repo")
            original_head = git(repo, "rev-parse", "HEAD").strip()
            backend = WorkerOnlyBackend()
            config = C2CWorkflowConfig(
                repo=repo,
                worker_model="chosen-worker",
                worker_effort="medium",
                verify_commands=[
                    f'{shlex.quote(sys.executable)} -c "from pathlib import Path; assert Path(\'marker.txt\').read_text().strip() == \'ok\'"'
                ],
                state_root=root / "state",
            )

            result = run_c2c_workflow(
                goal="Implement the already-approved PLAN.",
                backend=backend,
                config=config,
            )

            self.assertEqual(result.workspace, repo.resolve())
            self.assertTrue((repo / "marker.txt").exists())
            self.assertEqual(git(repo, "rev-parse", "HEAD").strip(), original_head)
            self.assertTrue(result.evidence.verification_passed)
            self.assertIn("?? marker.txt", result.evidence.status)
            self.assertEqual(len(backend.calls), 1)
            self.assertEqual(backend.calls[0]["model"], "chosen-worker")
            self.assertEqual(backend.calls[0]["effort"], "medium")
            self.assertTrue(backend.calls[0]["inherit_user_config"])

    def test_c2c_can_record_execution_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = init_repo(root / "repo")
            backend = WorkerOnlyBackend()
            capture = root / "c2c-args.json"
            fake = root / "fake_c2c.py"
            fake.write_text(
                "import json, pathlib, sys\n"
                f"pathlib.Path({str(capture)!r}).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n",
                encoding="utf-8",
            )
            config = C2CWorkflowConfig(
                repo=repo,
                state_root=root / "state",
                record_execution=True,
                task_id="c2c_ab12",
                iteration=2,
                c2c_command=f'{shlex.quote(sys.executable)} {shlex.quote(str(fake))}',
            )

            result = run_c2c_workflow(
                goal="Implement plan",
                backend=backend,
                config=config,
            )
            self.assertTrue(result.c2c_recorded)
            args = json.loads(capture.read_text(encoding="utf-8"))
            self.assertEqual(args[0], "record")
            self.assertIn("--task", args)
            self.assertEqual(args[args.index("--task") + 1], "c2c_ab12")
            self.assertEqual(args[args.index("--iteration") + 1], "2")


def init_repo(repo: Path) -> Path:
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(
        [
            "git", "-C", str(repo),
            "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "seed",
        ],
        check=True,
        capture_output=True,
    )
    return repo


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
    ).stdout


if __name__ == "__main__":
    unittest.main()
