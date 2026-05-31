from __future__ import annotations

import json
import os
import platform
import re
import sqlite3
import subprocess
import threading
from dataclasses import dataclass, field, replace
from glob import glob
from pathlib import Path
from time import monotonic, time
from typing import Callable, Protocol, Sequence

from .models import AgentDefinition, AgentStatus, ProcessInfo, StatusEvent
from .paths import expand_path_patterns
from .process_source import ProcessSource
from .scanner import AgentMatcher


@dataclass(frozen=True)
class StatusContext:
    session_id: str | None = None
    session_root_pid: int | None = None
    session_label: str | None = None
    monitor_id: str | None = None
    project_root: str | None = None


class StatusProvider(Protocol):
    def evaluate(
        self,
        definition: AgentDefinition,
        processes: Sequence[ProcessInfo],
        context: StatusContext | None = None,
    ) -> StatusEvent | None:
        """Return a status event, or None if this provider has no signal."""


def status_from_text(value: str) -> AgentStatus | None:
    normalized = value.casefold().replace("-", "_").replace(" ", "_")
    if normalized in {"busy", "running", "working", "executing", "generating"}:
        return AgentStatus.BUSY
    if normalized in {
        "needs_interaction",
        "need_interaction",
        "waiting_for_user",
        "awaiting_user",
        "permission_required",
        "approval_required",
        "paused",
        "blocked",
    }:
        return AgentStatus.NEEDS_INTERACTION
    if normalized in {"idle", "done", "complete", "completed", "success"}:
        return AgentStatus.IDLE
    if normalized in {"disconnected", "offline", "missing"}:
        return AgentStatus.DISCONNECTED
    if normalized in {"error", "failed", "failure"}:
        return AgentStatus.ERROR
    return None


@dataclass
class JsonStatusFileProvider:
    """Reads optional sidecar JSON status files for sub-500 ms exact updates.

    Expected JSON fields:
      agent_id: optional agent id
      status/state: busy | idle | needs_interaction | error | disconnected
      message: optional human-readable message
      milestone: optional boolean
    """

    stale_after_seconds: float = 6 * 60 * 60

    def evaluate(
        self,
        definition: AgentDefinition,
        processes: Sequence[ProcessInfo],
        context: StatusContext | None = None,
    ) -> StatusEvent | None:
        candidates: list[tuple[float, Path]] = []
        for pattern in expand_path_patterns(definition.status_file_globs):
            for filename in glob(pattern):
                path = Path(filename)
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if time() - mtime <= self.stale_after_seconds:
                    candidates.append((mtime, path))

        if not candidates:
            return None

        for mtime, path in sorted(candidates, reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            payload_agent_id = payload.get("agent_id")
            if payload_agent_id and payload_agent_id != definition.agent_id:
                continue

            if not _payload_matches_context(payload, context):
                continue

            raw_status = str(payload.get("status") or payload.get("state") or "")
            status = status_from_text(raw_status)
            if status is None:
                continue

            message = str(payload.get("message") or f"{definition.display_name}: {status.value}")
            return StatusEvent(
                agent_id=definition.agent_id,
                status=status,
                message=message,
                milestone=bool(payload.get("milestone")),
                timestamp=float(payload.get("timestamp") or mtime),
                monitor_id=_string_or_none(payload.get("monitor_id")),
                hook_session_id=_string_or_none(payload.get("hook_session_id")),
                hook_event_name=_string_or_none(payload.get("hook_event_name")),
                source=_string_or_none(payload.get("source")),
            )
        return None


_CODEX_LOG_EVENT_RE = re.compile(r'(?:event\.kind=|"type":")([A-Za-z0-9_.-]+)')
_CODEX_LOG_BUSY_EVENTS = frozenset(
    {
        "response.created",
        "response.in_progress",
        "response.output_item.added",
        "response.output_item.done",
        "response.function_call_arguments.delta",
        "response.function_call_arguments.done",
        "response.reasoning_summary_text.delta",
        "response.reasoning_summary_text.done",
        "response.reasoning_text.delta",
        "response.reasoning_text.done",
    }
)
_CODEX_LOG_IDLE_EVENTS = frozenset({"response.completed"})
_CODEX_LOG_ERROR_EVENTS = frozenset(
    {
        "response.failed",
        "response.incomplete",
        "response.error",
    }
)
_CODEX_LOG_INTERACTION_MARKERS = (
    "PermissionRequest",
    "approval_required",
    "permission_required",
    "needs_interaction",
)
_CODEX_LOG_TOOL_MARKERS = (
    "ToolCall:",
    "tool_name=",
    "handle_tool_call",
    "dispatch_tool_call",
    "codex.tool_result",
)


def _default_codex_logs_path() -> Path:
    return Path.home() / ".codex" / "logs_2.sqlite"


@dataclass
class CodexLogStatusProvider:
    """Reads Codex's local event log as an exact fallback for active turns.

    Codex Desktop can keep an already-open conversation alive without reloading
    newly written project hooks. The local event log still records response
    lifecycle events immediately, so it is a reliable non-CPU fallback for the
    selected project.
    """

    logs_path: Path = field(default_factory=_default_codex_logs_path)
    lookback_seconds: float = 15 * 60
    max_rows: int = 500

    def evaluate(
        self,
        definition: AgentDefinition,
        processes: Sequence[ProcessInfo],
        context: StatusContext | None = None,
    ) -> StatusEvent | None:
        if definition.agent_id not in {"codex_desktop", "codex_cli"}:
            return None
        if context is None or not context.project_root:
            return None

        rows = self._recent_rows(context.project_root)
        if not rows:
            return None

        last_status: AgentStatus | None = None
        last_timestamp = 0.0
        last_message = ""
        for timestamp, body in rows:
            status = _classify_codex_log_body(body)
            if status is None:
                continue
            last_status = status
            last_timestamp = timestamp
            last_message = _codex_log_message(definition.display_name, status)

        if last_status is None:
            return None

        return StatusEvent(
            agent_id=definition.agent_id,
            status=last_status,
            message=last_message,
            milestone=last_status
            in {AgentStatus.IDLE, AgentStatus.NEEDS_INTERACTION, AgentStatus.ERROR},
            timestamp=last_timestamp,
            source="codex-log",
        )

    def _recent_rows(self, project_root: str) -> list[tuple[float, str]]:
        if not self.logs_path.exists():
            return []
        marker = f"cwd={project_root}"
        since = int(time() - self.lookback_seconds)
        uri = self.logs_path.resolve().as_uri()
        try:
            connection = sqlite3.connect(
                f"{uri}?mode=ro",
                uri=True,
                timeout=0.05,
            )
        except sqlite3.Error:
            return []
        try:
            rows = connection.execute(
                """
                SELECT ts, ts_nanos, feedback_log_body
                FROM logs
                WHERE ts >= ?
                  AND instr(coalesce(feedback_log_body, ''), ?) > 0
                ORDER BY ts DESC, ts_nanos DESC, id DESC
                LIMIT ?
                """,
                (since, marker, self.max_rows),
            ).fetchall()
        except sqlite3.Error:
            return []
        finally:
            connection.close()

        parsed: list[tuple[float, str]] = []
        for ts, ts_nanos, body in reversed(rows):
            body_text = str(body or "")
            if not _codex_log_body_matches_project_root(body_text, project_root):
                continue
            try:
                timestamp = float(ts) + (float(ts_nanos or 0) / 1_000_000_000)
            except (TypeError, ValueError):
                timestamp = time()
            parsed.append((timestamp, body_text))
        return parsed


def _codex_log_body_matches_project_root(body: str, project_root: str) -> bool:
    escaped = re.escape(project_root)
    return bool(
        re.search(
            rf"run_sampling_request\{{[^}}]*\bcwd={escaped}(?=[}}\s])",
            body,
        )
    )


def _classify_codex_log_body(body: str) -> AgentStatus | None:
    event_types = set(_CODEX_LOG_EVENT_RE.findall(body))
    if event_types & _CODEX_LOG_ERROR_EVENTS or '"status":"failed"' in body:
        return AgentStatus.ERROR
    if any(marker in body for marker in _CODEX_LOG_INTERACTION_MARKERS):
        return AgentStatus.NEEDS_INTERACTION
    if event_types & _CODEX_LOG_IDLE_EVENTS:
        return AgentStatus.IDLE
    if event_types & _CODEX_LOG_BUSY_EVENTS:
        return AgentStatus.BUSY
    if 'websocket request: {"type":"response.create"' in body:
        return AgentStatus.BUSY
    if any(marker in body for marker in _CODEX_LOG_TOOL_MARKERS):
        return AgentStatus.BUSY
    return None


def _codex_log_message(display_name: str, status: AgentStatus) -> str:
    return {
        AgentStatus.BUSY: f"{display_name} 正在执行任务（Codex 本地事件日志）",
        AgentStatus.IDLE: f"{display_name} 已完成/空闲（Codex 本地事件日志）",
        AgentStatus.NEEDS_INTERACTION: f"{display_name} 需要用户交互（Codex 本地事件日志）",
        AgentStatus.ERROR: f"{display_name} 异常停止（Codex 本地事件日志）",
    }.get(status, f"{display_name}: {status.value}")


@dataclass
class HeuristicStatusProvider:
    """Conservative fallback when an agent does not expose exact state.

    CPU activity is intentionally opt-in. Background Agent processes can wake up
    for indexing, telemetry, IPC, or UI work even when no user task is running,
    so defaulting CPU activity to BUSY creates false blinking.
    """

    busy_cpu_percent: float | None = None
    stopped_statuses: frozenset[str] = frozenset({"stopped", "tracing-stop"})

    def evaluate(
        self,
        definition: AgentDefinition,
        processes: Sequence[ProcessInfo],
        context: StatusContext | None = None,
    ) -> StatusEvent | None:
        if not processes:
            return StatusEvent(
                agent_id=definition.agent_id,
                status=AgentStatus.DISCONNECTED,
                message=f"{definition.display_name} 未检测到运行中的进程",
            )

        normalized_statuses = {
            (process.status or "").casefold() for process in processes if process.status
        }
        if normalized_statuses & self.stopped_statuses:
            return StatusEvent(
                agent_id=definition.agent_id,
                status=AgentStatus.NEEDS_INTERACTION,
                message=f"{definition.display_name} 已暂停，可能需要用户交互",
            )

        if self._has_busy_signal(processes):
            return StatusEvent(
                agent_id=definition.agent_id,
                status=AgentStatus.BUSY,
                message=f"{definition.display_name} 正在执行任务",
            )

        return StatusEvent(
            agent_id=definition.agent_id,
            status=AgentStatus.IDLE,
            message=f"{definition.display_name} 当前空闲（未收到明确执行状态）",
        )

    def _has_busy_signal(self, processes: Sequence[ProcessInfo]) -> bool:
        return self.busy_cpu_percent is not None and any(
            process.cpu_percent is not None
            and process.cpu_percent >= self.busy_cpu_percent
            for process in processes
        )


@dataclass
class CompositeStatusProvider:
    providers: tuple[StatusProvider, ...] = (
        JsonStatusFileProvider(),
        CodexLogStatusProvider(),
        HeuristicStatusProvider(),
    )
    blocking_priority_window_seconds: float = 30.0

    def evaluate(
        self,
        definition: AgentDefinition,
        processes: Sequence[ProcessInfo],
        context: StatusContext | None = None,
    ) -> StatusEvent | None:
        exact_events: list[StatusEvent] = []
        fallback_event: StatusEvent | None = None
        for provider in self.providers:
            event = provider.evaluate(definition, processes, context)
            if event is None:
                continue
            if isinstance(provider, HeuristicStatusProvider):
                if fallback_event is None:
                    fallback_event = event
            else:
                exact_events.append(event)

        if exact_events:
            return self._select_exact_event(exact_events)
        return fallback_event

    def _select_exact_event(self, events: Sequence[StatusEvent]) -> StatusEvent:
        newest = max(events, key=lambda event: event.timestamp)
        blocking_events = [
            event
            for event in events
            if event.status in {AgentStatus.NEEDS_INTERACTION, AgentStatus.ERROR}
            and newest.timestamp - event.timestamp
            <= self.blocking_priority_window_seconds
        ]
        if blocking_events:
            return max(
                blocking_events,
                key=lambda event: (
                    event.timestamp,
                    event.status == AgentStatus.ERROR,
                ),
            )
        return newest


StatusCallback = Callable[[StatusEvent], None]


@dataclass
class PollingStatusListener:
    definition: AgentDefinition
    process_source: ProcessSource
    status_provider: StatusProvider
    callback: StatusCallback
    poll_interval_seconds: float = 0.25
    process_scan_interval_seconds: float = 2.0
    session_id: str | None = None
    session_root_pid: int | None = None
    session_label: str | None = None
    monitor_id: str | None = None
    project_root: str | None = None
    matcher: AgentMatcher = field(default_factory=AgentMatcher)

    _stop_event: threading.Event = field(default_factory=threading.Event, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)
    _metric_processes: dict[int, object] = field(default_factory=dict, init=False)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"agent-light-{self.definition.agent_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(1.0, self.poll_interval_seconds * 4))

    def _run(self) -> None:
        previous_key: tuple[AgentStatus, str, bool] | None = None
        processes: list[ProcessInfo] = []
        process_by_pid: dict[int, ProcessInfo] = {}
        next_process_scan_at = 0.0
        while not self._stop_event.is_set():
            now = monotonic()
            if now >= next_process_scan_at:
                snapshot = list(self.process_source.snapshot())
                process_by_pid = {process.pid: process for process in snapshot}
                matches = self.matcher.matches_for_definition(self.definition, snapshot)
                processes = [match.process for match in matches]
                processes = self._filter_session_processes(processes, process_by_pid)
                next_process_scan_at = now + self.process_scan_interval_seconds

            root_pid_missing = (
                self.session_root_pid is not None
                and not _pid_exists(self.session_root_pid)
            )
            if root_pid_missing:
                processes = []
                process_by_pid = {}
                next_process_scan_at = now + self.process_scan_interval_seconds
            elif processes:
                processes = self._refresh_process_metrics(processes)

            if root_pid_missing:
                event = self._with_session(
                    StatusEvent(
                        agent_id=self.definition.agent_id,
                        status=AgentStatus.DISCONNECTED,
                        message=f"{self.definition.display_name} session {self.session_root_pid} 已断开",
                    )
                )
            else:
                context = StatusContext(
                    session_id=self.session_id,
                    session_root_pid=self.session_root_pid,
                    session_label=self.session_label,
                    monitor_id=self.monitor_id,
                    project_root=self.project_root,
                )
                event = self.status_provider.evaluate(self.definition, processes, context)
                if (
                    event is None
                    and self.session_root_pid is not None
                    and not processes
                ):
                    event = StatusEvent(
                        agent_id=self.definition.agent_id,
                        status=AgentStatus.DISCONNECTED,
                        message=f"{self.definition.display_name} session {self.session_root_pid} 已断开",
                    )
                if event is not None:
                    event = self._with_session(event)
            if event is not None:
                key = (event.status, event.message, event.milestone)
                if key != previous_key:
                    previous_key = key
                    self.callback(event)
            self._stop_event.wait(self.poll_interval_seconds)

    def _filter_session_processes(
        self,
        processes: Sequence[ProcessInfo],
        process_by_pid: dict[int, ProcessInfo],
    ) -> list[ProcessInfo]:
        if self.session_root_pid is None:
            return list(processes)
        return [
            process
            for process in processes
            if process.pid == self.session_root_pid
            or self._has_ancestor(process.pid, self.session_root_pid, process_by_pid)
        ]

    def _has_ancestor(
        self,
        pid: int,
        ancestor_pid: int,
        process_by_pid: dict[int, ProcessInfo],
    ) -> bool:
        seen: set[int] = set()
        current = process_by_pid.get(pid)
        while current is not None and current.ppid is not None and current.pid not in seen:
            seen.add(current.pid)
            if current.ppid == ancestor_pid:
                return True
            current = process_by_pid.get(current.ppid)
        return False

    def _refresh_process_metrics(
        self, processes: Sequence[ProcessInfo]
    ) -> list[ProcessInfo]:
        try:
            import psutil
        except ImportError:
            return list(processes)

        refreshed: list[ProcessInfo] = []
        live_pids = {process.pid for process in processes}
        for pid in tuple(self._metric_processes):
            if pid not in live_pids:
                self._metric_processes.pop(pid, None)

        for process in processes:
            try:
                metric_process = self._metric_processes.get(process.pid)
                if metric_process is None:
                    metric_process = psutil.Process(process.pid)
                    metric_process.cpu_percent(None)
                    self._metric_processes[process.pid] = metric_process
                    cpu_percent = process.cpu_percent
                else:
                    cpu_percent = float(metric_process.cpu_percent(None))
                refreshed.append(
                    replace(
                        process,
                        status=str(metric_process.status() or "") or process.status,
                        cpu_percent=cpu_percent,
                    )
                )
            except (psutil.Error, OSError, TypeError, ValueError):
                refreshed.append(process)
        return refreshed

    def _with_session(self, event: StatusEvent) -> StatusEvent:
        return replace(
            event,
            session_id=self.session_id,
            session_label=self.session_label,
            monitor_id=event.monitor_id or self.monitor_id,
        )


def _payload_matches_context(
    payload: dict[str, object],
    context: StatusContext | None,
) -> bool:
    if context is None:
        return True

    payload_monitor_id = _string_or_none(payload.get("monitor_id"))
    if context.monitor_id and payload_monitor_id != context.monitor_id:
        return False

    payload_session_root_pid = _int_or_none(payload.get("session_root_pid"))
    if (
        context.session_root_pid is not None
        and payload_session_root_pid is not None
        and payload_session_root_pid != context.session_root_pid
    ):
        return False

    return True


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pid_exists(pid: int) -> bool:
    try:
        import psutil

        return bool(psutil.pid_exists(pid))
    except ImportError:
        pass

    if platform.system().lower() == "windows":
        try:
            result = subprocess.run(
                [
                    "tasklist",
                    "/FI",
                    f"PID eq {pid}",
                    "/NH",
                ],
                capture_output=True,
                text=True,
                timeout=0.5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return True
        return str(pid) in result.stdout

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
