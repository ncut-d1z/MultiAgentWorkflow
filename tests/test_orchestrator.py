from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from multiagent_workflow.orchestrator import WorkflowConfig, run_workflow
from multiagent_workflow.schema import MANAGER_SCHEMA, REVIEW_SCHEMA


class FakeBackend:
    def __init__(self) -> None:
        self.reviews = 0

    def run(self, *, role, model, cwd, prompt, sandbox, effort, output_schema=None):
        if role == "manager":
            self.assert_schema(output_schema, MANAGER_SCHEMA)
            return json.dumps(
                {
                    "summary": "one task",
                    "tasks": [
                        {
                            "id": "T01",
                            "title": "write marker",
                            "instruction": "create marker.txt",
                            "acceptance_criteria": ["marker.txt contains ok"],
                            "dependencies": [],
                            "suggested_files": ["marker.txt"],
                            "suggested_tests": [],
                        }
                    ],
                }
            )
        if role == "worker":
            (cwd / "marker.txt").write_text("ok\n", encoding="utf-8")
            return "created marker.txt"
        if role == "reviewer":
            self.assert_schema(output_schema, REVIEW_SCHEMA)
            self.reviews += 1
            return json.dumps(
                {
                    "decision": "PASS",
                    "summary": "criterion satisfied",
                    "problems": [],
                    "required_changes": [],
                }
            )
        raise AssertionError(role)

    @staticmethod
    def assert_schema(actual, expected):
        if actual != expected:
            raise AssertionError("unexpected output schema")


class OrchestratorTests(unittest.TestCase):
    def test_end_to_end_with_isolated_worktree(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(
                ["git", "init", "-b", "main", str(repo)],
                check=True,
                capture_output=True,
            )
            (repo / "README.md").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-m",
                    "seed",
                ],
                check=True,
                capture_output=True,
            )

            backend = FakeBackend()
            config = WorkflowConfig(
                repo=repo,
                state_root=root / "state",
                worktree_root=root / "worktrees",
                verify_commands=[],
            )
            result = run_workflow(goal="create marker", backend=backend, config=config)

            self.assertTrue((result.workspace / "marker.txt").exists())
            self.assertFalse((repo / "marker.txt").exists())
            self.assertEqual(result.outcomes[0].review.decision, "PASS")
            self.assertIsNotNone(result.outcomes[0].checkpoint_sha)
            self.assertEqual(backend.reviews, 1)


if __name__ == "__main__":
    unittest.main()
