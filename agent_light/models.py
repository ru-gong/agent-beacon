from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from time import time
from typing import Mapping


class AgentStatus(StrEnum):
    """Status values used by the tray traffic light."""

    UNCONNECTED = "unconnected"
    DISCONNECTED = "disconnected"
    IDLE = "idle"
    BUSY = "busy"
    NEEDS_INTERACTION = "needs_interaction"
    ERROR = "error"


STATUS_LABELS: Mapping[AgentStatus, str] = {
    AgentStatus.UNCONNECTED: "未连接",
    AgentStatus.DISCONNECTED: "已断开",
    AgentStatus.IDLE: "已完成/空闲",
    AgentStatus.BUSY: "执行中（绿灯闪烁）",
    AgentStatus.NEEDS_INTERACTION: "需要交互",
    AgentStatus.ERROR: "错误/异常停止",
}


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    name: str
    ppid: int | None = None
    cmdline: tuple[str, ...] = ()
    cwd: str | None = None
    status: str | None = None
    cpu_percent: float | None = None
    create_time: float | None = None

    @property
    def command_text(self) -> str:
        return " ".join(self.cmdline)

    @property
    def short_command(self) -> str:
        command = self.command_text or self.name
        command = " ".join(command.split())
        return command[:72] + "..." if len(command) > 75 else command


@dataclass(frozen=True)
class AgentDefinition:
    """Static feature definition for one agent family."""

    agent_id: str
    display_name: str
    process_name_keywords: tuple[str, ...]
    cmdline_keywords: tuple[str, ...]
    exclude_keywords: tuple[str, ...] = ()
    default_ports: tuple[int, ...] = ()
    ipc_hints: tuple[str, ...] = ()
    status_file_globs: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class AgentSessionCandidate:
    session_id: str
    definition: AgentDefinition
    root_pid: int
    processes: tuple[ProcessInfo, ...]
    matched_by: tuple[str, ...]
    confidence: int
    project_root: str | None = None

    @property
    def agent_id(self) -> str:
        return self.definition.agent_id

    @property
    def display_name(self) -> str:
        return self.definition.display_name

    @property
    def pids(self) -> tuple[int, ...]:
        return tuple(sorted(process.pid for process in self.processes))

    @property
    def root_process(self) -> ProcessInfo:
        for process in self.processes:
            if process.pid == self.root_pid:
                return process
        return self.processes[0]

    @property
    def menu_label(self) -> str:
        process_count = len(self.processes)
        suffix = f"{process_count} processes" if process_count > 1 else "1 process"
        project = f" · {self.project_root}" if self.project_root else ""
        return f"Session {self.root_pid}{project} · {suffix} · {self.root_process.short_command}"


@dataclass(frozen=True)
class AgentCandidate:
    definition: AgentDefinition
    sessions: tuple[AgentSessionCandidate, ...]
    matched_by: tuple[str, ...]
    confidence: int

    @property
    def agent_id(self) -> str:
        return self.definition.agent_id

    @property
    def display_name(self) -> str:
        return self.definition.display_name

    @property
    def processes(self) -> tuple[ProcessInfo, ...]:
        return tuple(process for session in self.sessions for process in session.processes)

    @property
    def pids(self) -> tuple[int, ...]:
        return tuple(sorted({process.pid for process in self.processes}))

    @property
    def session_count(self) -> int:
        return len(self.sessions)


@dataclass(frozen=True)
class StatusEvent:
    agent_id: str
    status: AgentStatus
    message: str
    session_id: str | None = None
    session_label: str | None = None
    milestone: bool = False
    timestamp: float = field(default_factory=time)
    monitor_id: str | None = None
    hook_session_id: str | None = None
    hook_event_name: str | None = None
    source: str | None = None
