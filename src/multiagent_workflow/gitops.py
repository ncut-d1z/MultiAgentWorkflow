from __future__ import annotations

import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


@dataclass(slots=True)
class VerificationResult:
    command: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def passed(self) -> bool:
        return self.returncode == 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self) | {"passed": self.passed}


@dataclass(slots=True)
class EvidenceBundle:
    baseline_sha: str
    status: str
    diff_stat: str
    diff: str
    verifications: list[VerificationResult]

    @property
    def verification_passed(self) -> bool:
        return all(result.passed for result in self.verifications)

    def to_dict(self) -> dict[str, object]:
        return {
            "baseline_sha": self.baseline_sha,
            "status": self.status,
            "diff_stat": self.diff_stat,
            "diff": self.diff,
            "verification_passed": self.verification_passed,
            "verifications": [item.to_dict() for item in self.verifications],
        }


def repo_root(path: Path) -> Path:
    result = git(path, "rev-parse", "--show-toplevel")
    return Path(result.stdout.strip()).resolve()


def head_sha(path: Path) -> str:
    return git(path, "rev-parse", "HEAD").stdout.strip()


def create_worktree(repo: Path, *, path: Path, branch: str) -> Path:
    repo = repo_root(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise GitError(f"Worktree path already exists: {path}")
    git(repo, "worktree", "add", "-b", branch, str(path), "HEAD")
    return path.resolve()


def collect_evidence(
    cwd: Path,
    *,
    baseline_sha: str,
    verify_commands: list[str],
    verify_timeout_seconds: int | None,
) -> EvidenceBundle:
    status = git(cwd, "status", "--short", "--untracked-files=all").stdout
    diff_stat = git(cwd, "diff", "--stat", baseline_sha, "--").stdout
    diff = git(cwd, "diff", "--binary", baseline_sha, "--").stdout
    verifications = [
        run_verifier(cwd, command, timeout_seconds=verify_timeout_seconds)
        for command in verify_commands
    ]
    return EvidenceBundle(
        baseline_sha=baseline_sha,
        status=status,
        diff_stat=diff_stat,
        diff=diff,
        verifications=verifications,
    )


def checkpoint(cwd: Path, *, message: str) -> str | None:
    git(cwd, "add", "-A")
    staged = subprocess.run(
        ["git", "-C", str(cwd), "diff", "--cached", "--quiet"],
        capture_output=True,
        check=False,
    )
    if staged.returncode == 0:
        return None
    if staged.returncode != 1:
        raise GitError("Unable to inspect staged changes")
    git(
        cwd,
        "-c", "user.name=MultiAgentWorkflow",
        "-c", "user.email=multiagentworkflow@localhost",
        "commit", "-m", message,
    )
    return head_sha(cwd)


def run_verifier(
    cwd: Path, command: str, *, timeout_seconds: int | None
) -> VerificationResult:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=dict(os.environ) | {"PYTHONUTF8": "1"},
        )
        return VerificationResult(
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
    except subprocess.TimeoutExpired as exc:
        return VerificationResult(
            command=command,
            returncode=124,
            stdout=exc.stdout or "",
            stderr=(exc.stderr or "") + f"\nTimed out after {timeout_seconds} seconds.",
        )


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), *args],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise GitError("git executable was not found") from exc
    if completed.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed with exit code {completed.returncode}:\n"
            f"{completed.stderr.strip()}"
        )
    return completed
