from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field, replace
from glob import glob
from pathlib import Path
from time import time
from typing import Callable, Protocol, Sequence

from .models import AgentDefinition, AgentStatus, ProcessInfo, StatusEvent
from .process_source import ProcessSource
from .scanner import AgentMatcher


class StatusProvider(Protocol):
    def evaluate(
        self, definition: AgentDefinition, processes: Sequence[ProcessInfo]
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

    stale_after_seconds: float = 10.0

    def evaluate(
        self, definition: AgentDefinition, processes: Sequence[ProcessInfo]
    ) -> StatusEvent | None:
        newest: tuple[float, Path] | None = None
        for pattern in definition.status_file_globs:
            for filename in glob(str(Path(pattern).expanduser())):
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
    """Best-effort fallback when an agent does not expose exact state."""

    busy_cpu_percent: float = 1.0
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

        if any(
            process.cpu_percent is not None
            and process.cpu_percent >= self.busy_cpu_percent
            for process in processes
        ):
            return StatusEvent(
                agent_id=definition.agent_id,
                status=AgentStatus.BUSY,
                message=f"{definition.display_name} 正在执行任务",
            )

        return StatusEvent(
            agent_id=definition.agent_id,
            status=AgentStatus.IDLE,
            message=f"{definition.display_name} 当前空闲",
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
    session_id: str | None = None
    session_root_pid: int | None = None
    session_label: str | None = None
    matcher: AgentMatcher = field(default_factory=AgentMatcher)

    _stop_event: threading.Event = field(default_factory=threading.Event, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)

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
        while not self._stop_event.is_set():
            snapshot = list(self.process_source.snapshot())
            process_by_pid = {process.pid: process for process in snapshot}
            matches = self.matcher.matches_for_definition(self.definition, snapshot)
            processes = [
                match.process
                for match in matches
            ]
            processes = self._filter_session_processes(processes, process_by_pid)
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

    def _with_session(self, event: StatusEvent) -> StatusEvent:
        return replace(
            event,
            session_id=self.session_id,
            session_label=self.session_label,
        )
