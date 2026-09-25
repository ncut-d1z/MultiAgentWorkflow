from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised only on Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]



ARCHITECTURES = {"standalone", "c2c"}
SANDBOX_MODES = {"workspace-write", "danger-full-access"}
PROJECT_ALLOWED_KEYS = {
    "workflow.architecture",
    "worker.model",
    "worker.reasoning_effort",
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "workflow": {},
    "worker": {
        # model / reasoning_effort intentionally omitted: when absent, Codex chooses.
        "sandbox": "workspace-write",
    },
    "verification": {
        "commands": [],
        "timeout_seconds": 1800,
    },
    "standalone": {
        "max_revisions": 3,
        "isolate_worktree": True,
        "inherit_codex_config": False,
        "manager": {},
        "reviewer": {},
    },
    "c2c": {
        # C2C must see the exact workspace its connector is bound to.
        "record_execution": False,
        "command": "c2c",
        "inherit_codex_config": True,
    },
}


@dataclass(frozen=True, slots=True)
class ConfigPaths:
    user: Path
    project: Path


def user_config_path() -> Path:
    return Path.home() / ".multiagent-workflow" / "config.toml"


def discover_project_root(path: Path) -> Path:
    current = path.expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def project_config_path(repo: Path) -> Path:
    return discover_project_root(repo) / ".maw.toml"


def config_paths(repo: Path, *, user_path: Path | None = None) -> ConfigPaths:
    return ConfigPaths(
        user=(user_path or user_config_path()).expanduser().resolve(),
        project=project_config_path(repo),
    )


def load_effective_settings(
    repo: Path,
    *,
    user_path: Path | None = None,
    project_path: Path | None = None,
) -> dict[str, Any]:
    merged = copy.deepcopy(DEFAULT_SETTINGS)
    paths = config_paths(repo, user_path=user_path)
    effective_project = project_path or paths.project
    _deep_merge(merged, _read_toml(paths.user))
    project_data = _read_toml(effective_project)
    _validate_project_scope(project_data)
    _deep_merge(merged, project_data)
    _validate_settings(merged)
    return merged


def read_scope_config(
    repo: Path,
    scope: str,
    *,
    user_path: Path | None = None,
    project_path: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    path = _scope_path(
        repo,
        scope,
        user_path=user_path,
        project_path=project_path,
    )
    return path, _read_toml(path)


def write_scope_config(
    repo: Path,
    scope: str,
    data: dict[str, Any],
    *,
    user_path: Path | None = None,
    project_path: Path | None = None,
) -> Path:
    _validate_partial_settings(data)
    if scope == "project":
        _validate_project_scope(data)
    path = _scope_path(
        repo,
        scope,
        user_path=user_path,
        project_path=project_path,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(dumps_toml(data), encoding="utf-8")
    temp.replace(path)
    return path


def set_setting(data: dict[str, Any], dotted_key: str, raw_value: str) -> None:
    value = parse_setting_value(dotted_key, raw_value)
    parts = dotted_key.split(".")
    if len(parts) < 2:
        raise ValueError("Configuration keys must be dotted, e.g. workflow.architecture")
    node: dict[str, Any] = data
    for part in parts[:-1]:
        child = node.setdefault(part, {})
        if not isinstance(child, dict):
            raise ValueError(f"Cannot set {dotted_key}: {part} is not a table")
        node = child
    node[parts[-1]] = value
    _validate_partial_settings(data)


def unset_setting(data: dict[str, Any], dotted_key: str) -> bool:
    parts = dotted_key.split(".")
    node: dict[str, Any] = data
    parents: list[tuple[dict[str, Any], str]] = []
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            return False
        parents.append((node, part))
        node = child
    removed = node.pop(parts[-1], None) is not None
    for parent, key in reversed(parents):
        child = parent.get(key)
        if isinstance(child, dict) and not child:
            parent.pop(key, None)
    return removed


def get_setting(data: dict[str, Any], dotted_key: str, default: Any = None) -> Any:
    node: Any = data
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def parse_setting_value(dotted_key: str, raw_value: str) -> Any:
    bool_keys = {
        "standalone.isolate_worktree",
        "standalone.inherit_codex_config",
        "c2c.record_execution",
        "c2c.inherit_codex_config",
    }
    int_keys = {
        "standalone.max_revisions",
        "verification.timeout_seconds",
    }
    known_string_keys = {
        "workflow.architecture",
        "worker.model",
        "worker.reasoning_effort",
        "worker.sandbox",
        "standalone.manager.model",
        "standalone.manager.reasoning_effort",
        "standalone.reviewer.model",
        "standalone.reviewer.reasoning_effort",
        "c2c.command",
    }
    if dotted_key in bool_keys:
        lowered = raw_value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
        raise ValueError(f"{dotted_key} expects true/false")
    if dotted_key in int_keys:
        try:
            value = int(raw_value)
        except ValueError as exc:
            raise ValueError(f"{dotted_key} expects an integer") from exc
        if value < 0:
            raise ValueError(f"{dotted_key} must be >= 0")
        return value
    if dotted_key in known_string_keys:
        value = raw_value.strip()
        if not value:
            raise ValueError(f"{dotted_key} must not be empty; use `maw config unset` instead")
        if dotted_key == "workflow.architecture" and value not in ARCHITECTURES:
            raise ValueError("workflow.architecture must be standalone or c2c")
        if dotted_key == "worker.sandbox" and value not in SANDBOX_MODES:
            raise ValueError(
                "worker.sandbox must be workspace-write or danger-full-access"
            )
        # Model IDs and reasoning-effort names deliberately remain open-ended so the
        # harness does not need a release merely because Codex adds/retires a value.
        return value
    raise ValueError(f"Unsupported configuration key: {dotted_key}")


def dumps_toml(data: dict[str, Any]) -> str:
    """Serialize the small MAW config subset without adding a runtime writer dependency."""

    lines: list[str] = []

    def emit_table(prefix: tuple[str, ...], table: dict[str, Any]) -> None:
        scalars: list[tuple[str, Any]] = []
        children: list[tuple[str, dict[str, Any]]] = []
        for key, value in table.items():
            if isinstance(value, dict):
                if value:
                    children.append((key, value))
            else:
                scalars.append((key, value))

        if prefix and scalars:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"[{'.'.join(prefix)}]")
        for key, value in scalars:
            lines.append(f"{key} = {_toml_value(value)}")
        for key, child in children:
            emit_table((*prefix, key), child)

    emit_table((), data)
    return "\n".join(lines).rstrip() + "\n"


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        import json

        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        import json

        return "[" + ", ".join(json.dumps(item, ensure_ascii=False) for item in value) + "]"
    raise ValueError(f"Unsupported TOML value: {value!r}")


def _validate_project_scope(data: dict[str, Any]) -> None:
    unsafe = sorted(key for key in _flatten_keys(data) if key not in PROJECT_ALLOWED_KEYS)
    if unsafe:
        raise ValueError(
            "Project .maw.toml may only set workflow.architecture, worker.model, and "
            "worker.reasoning_effort. Move runtime/permission settings to the user config "
            f"or CLI. Disallowed keys: {', '.join(unsafe)}"
        )


def _flatten_keys(data: dict[str, Any], prefix: tuple[str, ...] = ()) -> list[str]:
    keys: list[str] = []
    for key, value in data.items():
        current = (*prefix, key)
        if isinstance(value, dict):
            keys.extend(_flatten_keys(value, current))
        else:
            keys.append(".".join(current))
    return keys


def _scope_path(
    repo: Path,
    scope: str,
    *,
    user_path: Path | None,
    project_path: Path | None,
) -> Path:
    if scope == "user":
        return (user_path or user_config_path()).expanduser().resolve()
    if scope == "project":
        return (project_path or project_config_path(repo)).expanduser().resolve()
    raise ValueError("scope must be 'user' or 'project'")


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        value = tomllib.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Invalid TOML root in {path}")
    return value


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> None:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)


def _validate_settings(value: dict[str, Any]) -> None:
    _validate_partial_settings(value)
    commands = get_setting(value, "verification.commands", [])
    if not isinstance(commands, list) or not all(isinstance(item, str) for item in commands):
        raise ValueError("verification.commands must be an array of strings")


def _validate_partial_settings(value: dict[str, Any]) -> None:
    architecture = get_setting(value, "workflow.architecture")
    if architecture is not None and architecture not in ARCHITECTURES:
        raise ValueError("workflow.architecture must be standalone or c2c")

    sandbox = get_setting(value, "worker.sandbox")
    if sandbox is not None and sandbox not in SANDBOX_MODES:
        raise ValueError("worker.sandbox must be workspace-write or danger-full-access")

    for key in (
        "worker.model",
        "worker.reasoning_effort",
        "standalone.manager.model",
        "standalone.manager.reasoning_effort",
        "standalone.reviewer.model",
        "standalone.reviewer.reasoning_effort",
        "c2c.command",
    ):
        item = get_setting(value, key)
        if item is not None and (not isinstance(item, str) or not item.strip()):
            raise ValueError(f"{key} must be a non-empty string")

    for key in (
        "standalone.isolate_worktree",
        "standalone.inherit_codex_config",
        "c2c.record_execution",
        "c2c.inherit_codex_config",
    ):
        item = get_setting(value, key)
        if item is not None and not isinstance(item, bool):
            raise ValueError(f"{key} must be true or false")

    for key in ("standalone.max_revisions", "verification.timeout_seconds"):
        item = get_setting(value, key)
        if item is not None and (not isinstance(item, int) or isinstance(item, bool) or item < 0):
            raise ValueError(f"{key} must be a non-negative integer")
