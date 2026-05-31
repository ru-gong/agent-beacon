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
    cmdline: tuple[str, ...] = ()
    status: str | None = None
    cpu_percent: float | None = None
    create_time: float | None = None

    @property
    def command_text(self) -> str:
        return " ".join(self.cmdline)


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
class AgentCandidate:
    definition: AgentDefinition
    processes: tuple[ProcessInfo, ...]
    matched_by: tuple[str, ...]
    confidence: int

    @property
    def agent_id(self) -> str:
        return self.definition.agent_id

    @property
    def display_name(self) -> str:
        return self.definition.display_name

    @property
    def pids(self) -> tuple[int, ...]:
        return tuple(sorted(process.pid for process in self.processes))


@dataclass(frozen=True)
class StatusEvent:
    agent_id: str
    status: AgentStatus
    message: str
    milestone: bool = False
    timestamp: float = field(default_factory=time)
