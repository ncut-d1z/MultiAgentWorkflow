from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class BackendError(RuntimeError):
    pass


class AgentBackend(Protocol):
    def run(
        self,
        *,
        role: str,
        model: str | None,
        cwd: Path,
        prompt: str,
        sandbox: str,
        effort: str | None,
        output_schema: dict[str, Any] | None = None,
        inherit_user_config: bool = False,
    ) -> str: ...


@dataclass(slots=True)
class CodexCliBackend:
    codex_binary: str = "codex"
    timeout_seconds: int | None = None
    prefer_chatgpt_auth: bool = True

    def doctor(self) -> dict[str, Any]:
        binary = shutil.which(self.codex_binary)
        if binary is None:
            return {"ok": False, "error": f"{self.codex_binary!r} was not found on PATH"}
        version = self._run_simple([self.codex_binary, "--version"])
        doctor = self._run_simple([self.codex_binary, "doctor", "--json"], tolerate=True)
        return {
            "ok": version.returncode == 0,
            "binary": binary,
            "version": version.stdout.strip() or version.stderr.strip(),
            "doctor": _try_json(doctor.stdout) if doctor.returncode == 0 else None,
            "doctor_stderr": doctor.stderr.strip() if doctor.returncode != 0 else "",
        }

    def login(self) -> int:
        return subprocess.run(
            [self.codex_binary, "login"], env=self._env(), check=False
        ).returncode

    def run(
        self,
        *,
        role: str,
        model: str | None,
        cwd: Path,
        prompt: str,
        sandbox: str,
        effort: str | None,
        output_schema: dict[str, Any] | None = None,
        inherit_user_config: bool = False,
    ) -> str:
        if shutil.which(self.codex_binary) is None:
            raise BackendError(
                "Codex CLI was not found. Install it, then run `codex login` and choose "
                "Sign in with ChatGPT."
            )
        cwd = cwd.resolve()
        if not cwd.exists():
            raise BackendError(f"Working directory does not exist: {cwd}")

        with tempfile.TemporaryDirectory(prefix="maw-codex-") as td:
            temp_dir = Path(td)
            last_message = temp_dir / "last_message.txt"
            schema_path = temp_dir / "schema.json"
            command = [self.codex_binary, "--ask-for-approval", "never", "exec"]
            if not inherit_user_config:
                command.append("--ignore-user-config")
            command.extend(
                [
                    "--ephemeral",
                    "--sandbox",
                    sandbox,
                    "--cd",
                    str(cwd),
                    "--output-last-message",
                    str(last_message),
                ]
            )
            # Do not pin a model or reasoning effort unless the user/config explicitly
            # selected one. This keeps the harness resilient to model retirement.
            if model:
                command.extend(["--model", model])
            if effort:
                command.extend(["--config", f'model_reasoning_effort="{effort}"'])
            if output_schema is not None:
                schema_path.write_text(
                    json.dumps(output_schema, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                command.extend(["--output-schema", str(schema_path)])
            command.append("-")

            try:
                completed = subprocess.run(
                    command,
                    cwd=cwd,
                    input=prompt,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    env=self._env(),
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise BackendError(
                    f"{role} timed out after {self.timeout_seconds} seconds"
                ) from exc

            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout).strip()
                hint = ""
                lowered = detail.lower()
                if "model" in lowered and (
                    "does not exist" in lowered
                    or "do not have access" in lowered
                    or "not supported" in lowered
                    or "model not found" in lowered
                ):
                    hint = (
                        "\nThe selected model may not be enabled for the current ChatGPT/Codex "
                        "account route. Change worker.model (or the relevant role model) in "
                        ".maw.toml / user config, or omit it to let Codex choose its current default."
                    )
                if "unauthorized" in lowered or "401" in lowered:
                    hint += (
                        "\nRun `maw login` (or `codex login`) and choose Sign in with ChatGPT. "
                        "Also remove stale OPENAI_API_KEY/CODEX_API_KEY variables if you intend "
                        "to use ChatGPT authentication."
                    )
                raise BackendError(
                    f"Codex {role} failed with exit code {completed.returncode}:\n{detail}{hint}"
                )

            text = (
                last_message.read_text(encoding="utf-8").strip()
                if last_message.exists()
                else completed.stdout.strip()
            )
            if not text:
                raise BackendError(f"Codex {role} returned an empty final response")
            return text

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.prefer_chatgpt_auth:
            env.pop("OPENAI_API_KEY", None)
            env.pop("CODEX_API_KEY", None)
        env.setdefault("PYTHONUTF8", "1")
        return env

    def _run_simple(
        self, command: list[str], *, tolerate: bool = False
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                env=self._env(),
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            if tolerate:
                return subprocess.CompletedProcess(command, 1, "", str(exc))
            raise BackendError(str(exc)) from exc


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return text.strip() or None
