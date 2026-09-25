from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


MANAGER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "instruction": {"type": "string"},
                    "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                    "dependencies": {"type": "array", "items": {"type": "string"}},
                    "suggested_files": {"type": "array", "items": {"type": "string"}},
                    "suggested_tests": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "id", "title", "instruction", "acceptance_criteria",
                    "dependencies", "suggested_files", "suggested_tests"
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "tasks"],
    "additionalProperties": False,
}

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["PASS", "REVISE"]},
        "summary": {"type": "string"},
        "problems": {"type": "array", "items": {"type": "string"}},
        "required_changes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["decision", "summary", "problems", "required_changes"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class TaskSpec:
    id: str
    title: str
    instruction: str
    acceptance_criteria: list[str]
    dependencies: list[str] = field(default_factory=list)
    suggested_files: list[str] = field(default_factory=list)
    suggested_tests: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TaskSpec":
        return cls(
            id=_required_str(value, "id"),
            title=_required_str(value, "title"),
            instruction=_required_str(value, "instruction"),
            acceptance_criteria=_str_list(value, "acceptance_criteria", required=True),
            dependencies=_str_list(value, "dependencies"),
            suggested_files=_str_list(value, "suggested_files"),
            suggested_tests=_str_list(value, "suggested_tests"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ManagerPlan:
    summary: str
    tasks: list[TaskSpec]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ManagerPlan":
        raw_tasks = value.get("tasks")
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise ValueError("Manager must return a non-empty tasks array")
        tasks = [TaskSpec.from_dict(item) for item in raw_tasks if isinstance(item, dict)]
        if len(tasks) != len(raw_tasks):
            raise ValueError("Every manager task must be an object")
        ids = [task.id for task in tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("Manager returned duplicate task ids")
        known: set[str] = set()
        for task in tasks:
            unknown = [dep for dep in task.dependencies if dep not in known]
            if unknown:
                raise ValueError(
                    f"Task {task.id} has dependencies that are not earlier tasks: {unknown}"
                )
            known.add(task.id)
        return cls(summary=_required_str(value, "summary"), tasks=tasks)

    def to_dict(self) -> dict[str, Any]:
        return {"summary": self.summary, "tasks": [task.to_dict() for task in self.tasks]}


@dataclass(slots=True)
class ReviewResult:
    decision: Literal["PASS", "REVISE"]
    summary: str
    problems: list[str]
    required_changes: list[str]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReviewResult":
        decision = value.get("decision")
        if decision not in {"PASS", "REVISE"}:
            raise ValueError(f"Invalid review decision: {decision!r}")
        return cls(
            decision=decision,
            summary=_required_str(value, "summary"),
            problems=_str_list(value, "problems"),
            required_changes=_str_list(value, "required_changes"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _required_str(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key!r} must be a non-empty string")
    return item.strip()


def _str_list(value: dict[str, Any], key: str, *, required: bool = False) -> list[str]:
    raw = value.get(key, [])
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError(f"{key!r} must be a list of strings")
    cleaned = [item.strip() for item in raw if item.strip()]
    if required and not cleaned:
        raise ValueError(f"{key!r} must not be empty")
    return cleaned
