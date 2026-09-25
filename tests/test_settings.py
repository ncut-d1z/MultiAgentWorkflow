from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from multiagent_workflow.settings import (
    dumps_toml,
    load_effective_settings,
    set_setting,
    unset_setting,
    write_scope_config,
)


class SettingsTests(unittest.TestCase):
    def test_project_overrides_user_but_inherits_other_values(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            user = root / "user.toml"
            project = repo / ".maw.toml"
            user.write_text(
                '[workflow]\narchitecture = "standalone"\n\n[worker]\nmodel = "user-model"\n',
                encoding="utf-8",
            )
            project.write_text(
                '[workflow]\narchitecture = "c2c"\n\n[worker]\nreasoning_effort = "high"\n',
                encoding="utf-8",
            )

            settings = load_effective_settings(
                repo,
                user_path=user,
                project_path=project,
            )
            self.assertEqual(settings["workflow"]["architecture"], "c2c")
            self.assertEqual(settings["worker"]["model"], "user-model")
            self.assertEqual(settings["worker"]["reasoning_effort"], "high")

    def test_model_and_effort_are_not_hardcoded_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            settings = load_effective_settings(
                repo,
                user_path=repo / "missing-user.toml",
                project_path=repo / "missing-project.toml",
            )
            self.assertNotIn("model", settings["worker"])
            self.assertNotIn("reasoning_effort", settings["worker"])
            self.assertNotIn("architecture", settings["workflow"])

    def test_set_unset_and_toml_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            project_path = repo / ".maw.toml"
            data = {}
            set_setting(data, "workflow.architecture", "standalone")
            set_setting(data, "worker.model", "future-worker-model")
            set_setting(data, "worker.reasoning_effort", "ultra-future")
            self.assertIn("future-worker-model", dumps_toml(data))
            write_scope_config(repo, "project", data, project_path=project_path)
            loaded = load_effective_settings(
                repo,
                user_path=root / "none.toml",
                project_path=project_path,
            )
            self.assertEqual(loaded["worker"]["model"], "future-worker-model")
            self.assertEqual(loaded["worker"]["reasoning_effort"], "ultra-future")
            self.assertTrue(unset_setting(data, "worker.model"))
            self.assertNotIn("model", data["worker"])

    def test_project_config_rejects_permission_or_command_settings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            project = repo / ".maw.toml"
            project.write_text(
                '[worker]\nsandbox = "danger-full-access"\n',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_effective_settings(
                    repo,
                    user_path=root / "none.toml",
                    project_path=project,
                )


if __name__ == "__main__":
    unittest.main()
