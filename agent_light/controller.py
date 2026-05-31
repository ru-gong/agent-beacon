from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .definitions import AGENT_DEFINITIONS, get_definition
from .models import AgentCandidate, AgentSessionCandidate, AgentStatus, StatusEvent
from .notify import NativeNotifier, Notifier
from .process_source import ProcessSource, build_default_process_source
from .scanner import AgentScanner
from .status import CompositeStatusProvider, PollingStatusListener, StatusProvider


ControllerSubscriber = Callable[[StatusEvent], None]


@dataclass
class AgentController:
    scanner: AgentScanner
    process_source: ProcessSource
    status_provider: StatusProvider = field(default_factory=CompositeStatusProvider)
    notifier: Notifier = field(default_factory=NativeNotifier)
    poll_interval_seconds: float = 0.25

    _listener: PollingStatusListener | None = field(default=None, init=False)
    _subscribers: list[ControllerSubscriber] = field(default_factory=list, init=False)
    _active_agent_id: str | None = field(default=None, init=False)
    _active_session_id: str | None = field(default=None, init=False)
    _active_session_label: str | None = field(default=None, init=False)
    _current_status: AgentStatus = field(
        default=AgentStatus.UNCONNECTED, init=False
    )

    @classmethod
    def build_default(cls) -> "AgentController":
        process_source = build_default_process_source()
        scanner = AgentScanner(process_source=process_source)
        return cls(scanner=scanner, process_source=process_source)

    @property
    def active_agent_id(self) -> str | None:
        return self._active_agent_id

    @property
    def active_session_id(self) -> str | None:
        return self._active_session_id

    @property
    def active_session_label(self) -> str | None:
        return self._active_session_label

    @property
    def current_status(self) -> AgentStatus:
        return self._current_status

    def subscribe(self, callback: ControllerSubscriber) -> None:
        self._subscribers.append(callback)

    def rescan(self) -> list[AgentCandidate]:
        return self.scanner.scan()

    def connect(self, agent_id: str) -> None:
        session = self._first_session_for_agent(agent_id)
        if session is None:
            raise ValueError(f"No running session found for agent id: {agent_id}")
        self.connect_session(session)

    def connect_session(self, session: AgentSessionCandidate) -> None:
        self.disconnect(emit=False)
        self._active_agent_id = session.agent_id
        self._active_session_id = session.session_id
        self._active_session_label = session.menu_label
        self._listener = PollingStatusListener(
            definition=session.definition,
            process_source=self.process_source,
            status_provider=self.status_provider,
            callback=self._handle_status_event,
            poll_interval_seconds=self.poll_interval_seconds,
            session_id=session.session_id,
            session_root_pid=session.root_pid,
            session_label=session.menu_label,
        )
        self._listener.start()

    def connect_session_id(self, session_id: str) -> None:
        session = self._find_session(session_id)
        if session is None:
            raise ValueError(f"No running session found for session id: {session_id}")
        self.connect_session(session)

    def disconnect(self, emit: bool = True) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        previous_agent_id = self._active_agent_id
        previous_session_id = self._active_session_id
        previous_session_label = self._active_session_label
        self._active_agent_id = None
        self._active_session_id = None
        self._active_session_label = None
        self._current_status = AgentStatus.UNCONNECTED
        if emit and previous_agent_id is not None:
            self._publish(
                StatusEvent(
                    agent_id=previous_agent_id,
                    status=AgentStatus.UNCONNECTED,
                    message="已断开当前 Agent",
                    session_id=previous_session_id,
                    session_label=previous_session_label,
                )
            )

    def stop(self) -> None:
        self.disconnect(emit=False)

    def _handle_status_event(self, event: StatusEvent) -> None:
        previous_status = self._current_status
        self._current_status = event.status
        self._publish(event)

        if event.status == AgentStatus.NEEDS_INTERACTION and previous_status != event.status:
            self.notifier.notify("Agent 需要交互", event.message)
        elif event.milestone:
            self.notifier.notify("Agent 里程碑", event.message)
        elif event.status == AgentStatus.DISCONNECTED and previous_status != event.status:
            self.notifier.notify("Agent 已断开", event.message)

    def _publish(self, event: StatusEvent) -> None:
        for subscriber in tuple(self._subscribers):
            subscriber(event)

    def _first_session_for_agent(self, agent_id: str) -> AgentSessionCandidate | None:
        if get_definition(agent_id) is None:
            raise ValueError(f"Unknown agent id: {agent_id}")
        for candidate in self.rescan():
            if candidate.agent_id == agent_id and candidate.sessions:
                return candidate.sessions[0]
        return None

    def _find_session(self, session_id: str) -> AgentSessionCandidate | None:
        for candidate in self.rescan():
            for session in candidate.sessions:
                if session.session_id == session_id:
                    return session
        return None


def known_agent_ids() -> tuple[str, ...]:
    return tuple(definition.agent_id for definition in AGENT_DEFINITIONS)
