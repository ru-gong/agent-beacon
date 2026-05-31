from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from time import time
from typing import Any, Mapping

from .models import AgentStatus, StatusEvent
from .paths import ensure_app_state_dir, safe_filename_component
from .runtime_log import RuntimeLogger, get_runtime_logger


BUSY_EVENTS = frozenset(
    {
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "SubagentStart",
        "TaskCreated",
    }
)
INTERACTION_EVENTS = frozenset(
    {
        "PermissionRequest",
        "PermissionDenied",
        "Notification",
        "Elicitation",
    }
)
IDLE_EVENTS = frozenset(
    {
        "Stop",
        "SessionEnd",
        "SubagentStop",
        "TaskCompleted",
    }
)
ERROR_EVENTS = frozenset(
    {
        "StopFailure",
        "PostToolUseFailure",
        "TaskFailed",
        "Error",
    }
)

STATUS_FILE_PREFIXES = {
    "codex_cli": "codex-cli",
    "codex_desktop": "codex-desktop",
    "cloud_code_cli": "cloud-code",
}


@dataclass(frozen=True)
class HookEventWriteResult:
    wrote_status: bool
    status_path: Path | None
    status_event: StatusEvent | None
    hook_event_name: str


def status_for_hook_event(
    hook_event_name: str,
    payload: Mapping[str, Any],
) -> AgentStatus | None:
    explicit_status = payload.get("agent_beacon_status") or payload.get("status")
    if isinstance(explicit_status, str):
        normalized = explicit_status.casefold().replace("-", "_").replace(" ", "_")
        if normalized in {"error", "failed", "failure"}:
            return AgentStatus.ERROR
        if normalized in {"needs_interaction", "permission_required", "approval_required"}:
            return AgentStatus.NEEDS_INTERACTION
        if normalized in {"busy", "running", "working"}:
            return AgentStatus.BUSY
        if normalized in {"idle", "done", "complete", "completed", "success"}:
            return AgentStatus.IDLE

    if _payload_has_failure(payload):
        return AgentStatus.ERROR
    if hook_event_name in ERROR_EVENTS:
        return AgentStatus.ERROR
    if hook_event_name in INTERACTION_EVENTS:
        return AgentStatus.NEEDS_INTERACTION
    if hook_event_name in BUSY_EVENTS:
        return AgentStatus.BUSY
    if hook_event_name in IDLE_EVENTS:
        return AgentStatus.IDLE
    return None


def write_hook_event_status(
    *,
    agent_id: str,
    payload: Mapping[str, Any],
    provider: str | None = None,
    monitor_id: str | None = None,
    session_root_pid: int | None = None,
    state_dir: Path | None = None,
    logger: RuntimeLogger | None = None,
    event_name: str | None = None,
) -> HookEventWriteResult:
    logger = logger or get_runtime_logger()
    hook_event_name = str(
        event_name
        or payload.get("hook_event_name")
        or payload.get("event")
        or payload.get("event_name")
        or "unknown"
    )
    status = status_for_hook_event(hook_event_name, payload)
    hook_session_id = _string_or_none(payload.get("session_id"))
    timestamp = float(payload.get("timestamp") or time())
    logger.record(
        "hook_event_received",
        agent_id=agent_id,
        provider=provider,
        monitor_id=monitor_id,
        session_root_pid=session_root_pid,
        hook_session_id=hook_session_id,
        hook_event_name=hook_event_name,
        mapped_status=status,
        payload=payload,
    )
    if status is None:
        return HookEventWriteResult(
            wrote_status=False,
            status_path=None,
            status_event=None,
            hook_event_name=hook_event_name,
        )

    message = _message_for_status(agent_id, hook_event_name, status, payload)
    event = StatusEvent(
        agent_id=agent_id,
        status=status,
        message=message,
        milestone=status in {AgentStatus.IDLE, AgentStatus.NEEDS_INTERACTION, AgentStatus.ERROR},
        timestamp=timestamp,
        monitor_id=monitor_id,
        hook_session_id=hook_session_id,
        hook_event_name=hook_event_name,
        source=provider or "hook",
    )
    status_payload = {
        "agent_id": agent_id,
        "status": status.value,
        "state": status.value,
        "message": message,
        "milestone": event.milestone,
        "timestamp": timestamp,
        "monitor_id": monitor_id,
        "session_root_pid": session_root_pid,
        "hook_session_id": hook_session_id,
        "hook_event_name": hook_event_name,
        "source": provider or "hook",
    }
    target_dir = state_dir or ensure_app_state_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    prefix = STATUS_FILE_PREFIXES.get(agent_id, safe_filename_component(agent_id))
    monitor_part = safe_filename_component(monitor_id, fallback="hook")
    status_path = target_dir / f"{prefix}-{monitor_part}.json"
    _atomic_write_json(status_path, status_payload)
    logger.record(
        "hook_status_written",
        agent_id=agent_id,
        provider=provider,
        monitor_id=monitor_id,
        hook_event_name=hook_event_name,
        status=status,
        status_path=status_path,
    )
    return HookEventWriteResult(
        wrote_status=True,
        status_path=status_path,
        status_event=event,
        hook_event_name=hook_event_name,
    )


def parse_hook_stdin(text: str) -> Mapping[str, Any]:
    stripped = text.strip()
    if not stripped:
        return {}
    payload = json.loads(stripped)
    if not isinstance(payload, dict):
        raise ValueError("Hook stdin must be a JSON object.")
    return payload


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _payload_has_failure(payload: Mapping[str, Any]) -> bool:
    for key in ("is_error", "isError", "failed", "failure", "error"):
        value = payload.get(key)
        if value is True:
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False


def _message_for_status(
    agent_id: str,
    hook_event_name: str,
    status: AgentStatus,
    payload: Mapping[str, Any],
) -> str:
    if isinstance(payload.get("message"), str) and payload["message"].strip():
        return payload["message"].strip()
    display_agent = {
        "codex_cli": "Codex CLI",
        "codex_desktop": "Codex Desktop",
        "cloud_code_cli": "Claude/Cloud Code CLI",
    }.get(agent_id, agent_id)
    status_text = {
        AgentStatus.BUSY: "正在执行",
        AgentStatus.IDLE: "已完成",
        AgentStatus.NEEDS_INTERACTION: "需要交互",
        AgentStatus.ERROR: "异常停止",
        AgentStatus.DISCONNECTED: "已断开",
        AgentStatus.UNCONNECTED: "未连接",
    }[status]
    return f"{display_agent}: {hook_event_name} -> {status_text}"


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
