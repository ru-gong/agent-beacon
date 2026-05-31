from __future__ import annotations

import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import ensure_app_state_dir, safe_filename_component
from .hook_registry import (
    AGENT_BEACON_MARKER,
    HookFileRecord,
    HookRegistration,
    HookRegistry,
)
from .models import AgentSessionCandidate
from .runtime_log import RuntimeLogger, get_runtime_logger, log_file_basename


CODEX_HOOK_EVENTS = (
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "Stop",
    "SubagentStop",
)

CLAUDE_HOOK_EVENTS = (
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PermissionDenied",
    "Notification",
    "SubagentStart",
    "SubagentStop",
    "TaskCreated",
    "TaskCompleted",
    "Stop",
    "StopFailure",
    "SessionEnd",
    "Elicitation",
)


@dataclass(frozen=True)
class HookInstallPlan:
    agent_id: str
    session_id: str
    monitor_id: str
    project_root: Path
    files: tuple[Path, ...]
    commands: tuple[str, ...]
    reason: str
    command_parts: tuple[tuple[str, ...], ...] = ()
    wrapper_path: Path | None = None

    @property
    def has_project_root(self) -> bool:
        return bool(self.project_root)


@dataclass(frozen=True)
class HookInstallResult:
    installed: bool
    registration: HookRegistration | None = None
    message: str = ""


class HookInstaller:
    def __init__(
        self,
        registry: HookRegistry,
        logger: RuntimeLogger | None = None,
    ) -> None:
        self.registry = registry
        self.logger = logger or get_runtime_logger()

    def plan(
        self,
        session: AgentSessionCandidate,
        monitor_id: str,
    ) -> HookInstallPlan | None:
        if session.agent_id not in {"codex_cli", "codex_desktop", "cloud_code_cli"}:
            return None
        if not session.project_root:
            self.logger.record(
                "hook_install_skipped",
                agent_id=session.agent_id,
                session_id=session.session_id,
                monitor_id=monitor_id,
                reason="missing_project_root",
            )
            return None

        project_root = Path(session.project_root).expanduser()
        hook_path = _hook_path_for_agent(session.agent_id, project_root)
        command_parts = _build_hook_command_parts(
            agent_id=session.agent_id,
            monitor_id=monitor_id,
            session_root_pid=session.root_pid,
            provider=_provider_for_agent(session.agent_id),
        )
        command = _format_hook_command(command_parts)
        files = (hook_path,)
        wrapper_path: Path | None = None
        if session.agent_id == "cloud_code_cli":
            wrapper_path = _hook_wrapper_path(
                agent_id=session.agent_id,
                monitor_id=monitor_id,
            )
            command = _format_hook_wrapper_command(wrapper_path)
            files = (hook_path, wrapper_path)
        return HookInstallPlan(
            agent_id=session.agent_id,
            session_id=session.session_id,
            monitor_id=monitor_id,
            project_root=project_root,
            files=files,
            commands=(command,),
            reason="为 Agent 生命周期事件写入 Agent Beacon 状态文件，用于绿灯闪烁、黄灯授权和完成状态同步。",
            command_parts=(tuple(command_parts),),
            wrapper_path=wrapper_path,
        )

    def install(self, plan: HookInstallPlan) -> HookInstallResult:
        hook_path = plan.files[0]
        created = not hook_path.exists()
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        payload = _read_json_object(hook_path)
        _remove_managed_hooks(payload)
        hook_records = [
            HookFileRecord(
                path=str(hook_path),
                created_by_agent_beacon=created,
                cleanup_strategy="json_managed_entries",
            )
        ]
        if plan.agent_id == "cloud_code_cli":
            command_parts = (
                plan.command_parts[0]
                if plan.command_parts
                else tuple(shlex.split(plan.commands[0]))
            )
            wrapper_path = plan.wrapper_path or _hook_wrapper_path(
                agent_id=plan.agent_id,
                monitor_id=plan.monitor_id,
            )
            wrapper_created = not wrapper_path.exists()
            _write_hook_wrapper(wrapper_path, command_parts)
            wrapper_command = (
                plan.commands[0]
                if plan.commands
                else _format_hook_wrapper_command(wrapper_path)
            )
            _add_claude_hooks(payload, wrapper_command)
            hook_records.append(
                HookFileRecord(
                    path=str(wrapper_path),
                    created_by_agent_beacon=wrapper_created,
                    cleanup_strategy="delete_file",
                )
            )
        else:
            _add_codex_hooks(payload, plan.commands[0])
        hook_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        registration = HookRegistration(
            agent_id=plan.agent_id,
            session_id=plan.session_id,
            monitor_id=plan.monitor_id,
            project_root=str(plan.project_root),
            files=tuple(hook_records),
            note="Agent lifecycle hook bridge",
        )
        self.registry.register(registration)
        self.logger.record(
            "hook_installed",
            agent_id=plan.agent_id,
            session_id=plan.session_id,
            monitor_id=plan.monitor_id,
            has_project_root=bool(plan.project_root),
            hook_file=log_file_basename(hook_path),
            wrapper_file=(
                log_file_basename(plan.wrapper_path)
                if plan.wrapper_path is not None
                else None
            ),
        )
        return HookInstallResult(
            installed=True,
            registration=registration,
            message=f"已写入 {log_file_basename(hook_path)}",
        )


def _build_hook_command_parts(
    *,
    agent_id: str,
    monitor_id: str,
    session_root_pid: int,
    provider: str,
) -> list[str]:
    return _hook_bridge_command_prefix() + [
        "--hook-event",
        "--agent",
        agent_id,
        "--provider",
        provider,
        "--monitor-id",
        monitor_id,
        "--session-root-pid",
        str(session_root_pid),
    ]


def _format_hook_command(args: list[str] | tuple[str, ...]) -> str:
    if sys.platform.startswith("win"):
        return f"{subprocess.list2cmdline(args)} & rem {AGENT_BEACON_MARKER}"
    return f"{' '.join(shlex.quote(arg) for arg in args)} # {AGENT_BEACON_MARKER}"


def _format_hook_wrapper_command(wrapper_path: Path) -> str:
    if sys.platform.startswith("win"):
        return f"{subprocess.list2cmdline([str(wrapper_path)])} & rem {AGENT_BEACON_MARKER}"
    return f"{shlex.quote(str(wrapper_path))} # {AGENT_BEACON_MARKER}"


def _hook_bridge_command_prefix() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    bridge_path = _ensure_source_hook_bridge()
    return [sys.executable, str(bridge_path)]


def _ensure_source_hook_bridge() -> Path:
    bridge_dir = ensure_app_state_dir("hooks")
    bridge_path = bridge_dir / "agent_beacon_hook.py"
    source_root = Path(__file__).resolve().parents[1]
    script = f"""\
from __future__ import annotations

import sys
from pathlib import Path

SOURCE_ROOT = Path({str(source_root)!r})
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from agent_light.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
"""
    bridge_path.write_text(script, encoding="utf-8")
    return bridge_path


def _hook_wrapper_path(*, agent_id: str, monitor_id: str) -> Path:
    extension = ".cmd" if sys.platform.startswith("win") else ".sh"
    filename = "-".join(
        (
            AGENT_BEACON_MARKER,
            safe_filename_component(agent_id, fallback="agent"),
            safe_filename_component(monitor_id, fallback="monitor"),
        )
    )
    return ensure_app_state_dir("hooks") / f"{filename}{extension}"


def _write_hook_wrapper(wrapper_path: Path, command_parts: tuple[str, ...]) -> None:
    if not command_parts:
        raise ValueError("Hook wrapper command must not be empty")
    wrapper_path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        command = subprocess.list2cmdline(command_parts)
        wrapper_path.write_text(
            "\r\n".join(
                (
                    "@echo off",
                    f"rem {AGENT_BEACON_MARKER}:start",
                    f"{command} %*",
                    f"rem {AGENT_BEACON_MARKER}:end",
                    "",
                )
            ),
            encoding="utf-8",
        )
        return

    command = " ".join(shlex.quote(part) for part in command_parts)
    wrapper_path.write_text(
        "\n".join(
            (
                "#!/bin/sh",
                f"# {AGENT_BEACON_MARKER}:start",
                f"exec {command} \"$@\"",
                f"# {AGENT_BEACON_MARKER}:end",
                "",
            )
        ),
        encoding="utf-8",
    )
    wrapper_path.chmod(wrapper_path.stat().st_mode | 0o755)


def _hook_path_for_agent(agent_id: str, project_root: Path) -> Path:
    if agent_id == "cloud_code_cli":
        return project_root / ".claude" / "settings.local.json"
    return project_root / ".codex" / "hooks.json"


def _provider_for_agent(agent_id: str) -> str:
    return {
        "codex_desktop": "codex-desktop",
        "codex_cli": "codex-cli",
        "cloud_code_cli": "claude-code",
    }[agent_id]


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"hooks": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{log_file_basename(path)} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{log_file_basename(path)} must contain a JSON object")
    hooks = payload.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"{log_file_basename(path)} field 'hooks' must be an object")
    return payload


def _add_codex_hooks(payload: dict[str, Any], command: str) -> None:
    hooks = payload.setdefault("hooks", {})
    for event in CODEX_HOOK_EVENTS:
        group: dict[str, Any] = {
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                    "statusMessage": "Agent Beacon status bridge",
                    "timeout": 5,
                }
            ]
        }
        if event in {"PreToolUse", "PermissionRequest", "SubagentStop"}:
            group["matcher"] = "*"
        hooks.setdefault(event, [])
        if not isinstance(hooks[event], list):
            raise ValueError(f"hooks.{event} must be a list")
        hooks[event].append(group)


def _add_claude_hooks(payload: dict[str, Any], command: str) -> None:
    hooks = payload.setdefault("hooks", {})
    if not command:
        raise ValueError("Claude hook command must not be empty")
    for event in CLAUDE_HOOK_EVENTS:
        group: dict[str, Any] = {
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                    "timeout": 5,
                }
            ]
        }
        if event in {
            "PreToolUse",
            "PermissionRequest",
            "PermissionDenied",
            "Notification",
            "SubagentStart",
            "SubagentStop",
            "StopFailure",
            "Elicitation",
        }:
            group["matcher"] = "*"
        hooks.setdefault(event, [])
        if not isinstance(hooks[event], list):
            raise ValueError(f"hooks.{event} must be a list")
        hooks[event].append(group)


def _remove_managed_hooks(payload: dict[str, Any]) -> None:
    hooks = payload.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event, groups in tuple(hooks.items()):
        if not isinstance(groups, list):
            continue
        cleaned_groups: list[Any] = []
        for group in groups:
            if not isinstance(group, dict):
                cleaned_groups.append(group)
                continue
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                cleaned_groups.append(group)
                continue
            cleaned_handlers = [
                handler
                for handler in handlers
                if not _is_managed_hook_handler(handler)
            ]
            if cleaned_handlers:
                updated = dict(group)
                updated["hooks"] = cleaned_handlers
                cleaned_groups.append(updated)
        if cleaned_groups:
            hooks[event] = cleaned_groups
        else:
            hooks.pop(event, None)


def _is_managed_hook_handler(handler: Any) -> bool:
    if not isinstance(handler, dict):
        return False
    if handler.get("agent_beacon_managed") is True:
        return True
    if handler.get("managed_by") == "Agent Beacon":
        return True
    for key in (
        "description",
        "statusMessage",
        "command",
        "commandWindows",
        "command_windows",
    ):
        value = handler.get(key)
        if isinstance(value, str) and AGENT_BEACON_MARKER in value:
            return True
    args = handler.get("args")
    if isinstance(args, list) and any(AGENT_BEACON_MARKER in str(arg) for arg in args):
        return True
    return False
