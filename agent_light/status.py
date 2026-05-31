from __future__ import annotations

import json
import os
import platform
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


class StatusProvider(Protocol):
    def evaluate(
        self, definition: AgentDefinition, processes: Sequence[ProcessInfo]
    ) -> StatusEvent | None:
        """Return a status event, or None if this provider has no signal."""


CLI_ACTIVITY_AGENT_IDS = frozenset({"codex_cli", "cloud_code_cli"})


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

    stale_after_seconds: float = 10.0

    def evaluate(
        self, definition: AgentDefinition, processes: Sequence[ProcessInfo]
    ) -> StatusEvent | None:
        newest: tuple[float, Path] | None = None
        for pattern in expand_path_patterns(definition.status_file_globs):
            for filename in glob(pattern):
                path = Path(filename)
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if newest is None or mtime > newest[0]:
                    newest = (mtime, path)

        if newest is None or time() - newest[0] > self.stale_after_seconds:
            return None

        try:
            payload = json.loads(newest[1].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        payload_agent_id = payload.get("agent_id")
        if payload_agent_id and payload_agent_id != definition.agent_id:
            return None

        raw_status = str(payload.get("status") or payload.get("state") or "")
        status = status_from_text(raw_status)
        if status is None:
            return None

        message = str(payload.get("message") or f"{definition.display_name}: {status.value}")
        return StatusEvent(
            agent_id=definition.agent_id,
            status=status,
            message=message,
            milestone=bool(payload.get("milestone")),
            timestamp=float(payload.get("timestamp") or time()),
        )


@dataclass
class HeuristicStatusProvider:
    """Conservative fallback when an agent does not expose exact state.

    CPU activity is intentionally opt-in. Background Agent processes can wake up
    for indexing, telemetry, IPC, or UI work even when no user task is running,
    so defaulting CPU activity to BUSY creates false blinking.
    """

    busy_cpu_percent: float | None = None
    cli_busy_cpu_percent: float | None = 0.5
    active_cli_statuses: frozenset[str] = frozenset({"running"})
    stopped_statuses: frozenset[str] = frozenset({"stopped", "tracing-stop"})

    def evaluate(
        self, definition: AgentDefinition, processes: Sequence[ProcessInfo]
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

        if self._has_busy_signal(definition, processes):
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

    def _has_busy_signal(
        self, definition: AgentDefinition, processes: Sequence[ProcessInfo]
    ) -> bool:
        if self.busy_cpu_percent is not None and any(
            process.cpu_percent is not None
            and process.cpu_percent >= self.busy_cpu_percent
            for process in processes
        ):
            return True

        if definition.agent_id not in CLI_ACTIVITY_AGENT_IDS:
            return False

        if self.cli_busy_cpu_percent is not None and any(
            process.cpu_percent is not None
            and process.cpu_percent >= self.cli_busy_cpu_percent
            for process in processes
        ):
            return True

        return any(
            (process.status or "").casefold() in self.active_cli_statuses
            for process in processes
        )


@dataclass
class CompositeStatusProvider:
    providers: tuple[StatusProvider, ...] = (
        JsonStatusFileProvider(),
        HeuristicStatusProvider(),
    )

    def evaluate(
        self, definition: AgentDefinition, processes: Sequence[ProcessInfo]
    ) -> StatusEvent | None:
        for provider in self.providers:
            event = provider.evaluate(definition, processes)
            if event is not None:
                return event
        return None


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

            if self.session_root_pid is not None and not _pid_exists(self.session_root_pid):
                processes = []
                process_by_pid = {}
                next_process_scan_at = now + self.process_scan_interval_seconds
            elif processes:
                processes = self._refresh_process_metrics(processes)

            if self.session_root_pid is not None and not processes:
                event = self._with_session(
                    StatusEvent(
                        agent_id=self.definition.agent_id,
                        status=AgentStatus.DISCONNECTED,
                        message=f"{self.definition.display_name} session {self.session_root_pid} 已断开",
                    )
                )
            else:
                event = self.status_provider.evaluate(self.definition, processes)
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
        )


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
