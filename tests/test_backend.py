from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multiagent_workflow.backend import CodexCliBackend


class BackendTests(unittest.TestCase):
    def test_unset_model_and_effort_are_not_pinned(self):
        commands: list[list[str]] = []

        def fake_run(command, **kwargs):
            commands.append(list(command))
            if "--output-last-message" in command:
                path = Path(command[command.index("--output-last-message") + 1])
                path.write_text("ok", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as td, patch(
            "multiagent_workflow.backend.shutil.which", return_value="codex"
        ), patch("multiagent_workflow.backend.subprocess.run", side_effect=fake_run):
            CodexCliBackend().run(
                role="worker",
                model=None,
                cwd=Path(td),
                prompt="do work",
                sandbox="workspace-write",
                effort=None,
                inherit_user_config=True,
            )

        command = commands[0]
        self.assertNotIn("--model", command)
        self.assertFalse(any("model_reasoning_effort" in item for item in command))
        self.assertNotIn("--ignore-user-config", command)

    def test_explicit_model_and_effort_are_forwarded(self):
        commands: list[list[str]] = []

        def fake_run(command, **kwargs):
            commands.append(list(command))
            path = Path(command[command.index("--output-last-message") + 1])
            path.write_text("ok", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as td, patch(
            "multiagent_workflow.backend.shutil.which", return_value="codex"
        ), patch("multiagent_workflow.backend.subprocess.run", side_effect=fake_run):
            CodexCliBackend().run(
                role="worker",
                model="chosen-model",
                cwd=Path(td),
                prompt="do work",
                sandbox="workspace-write",
                effort="chosen-effort",
                inherit_user_config=False,
            )

        command = commands[0]
        self.assertIn("--model", command)
        self.assertEqual(command[command.index("--model") + 1], "chosen-model")
        self.assertIn('model_reasoning_effort="chosen-effort"', command)
        self.assertIn("--ignore-user-config", command)


if __name__ == "__main__":
    unittest.main()
