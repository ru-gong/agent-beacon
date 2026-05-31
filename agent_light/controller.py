from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .definitions import AGENT_DEFINITIONS, get_definition
from .models import AgentCandidate, AgentStatus, StatusEvent
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
    def current_status(self) -> AgentStatus:
        return self._current_status

    def subscribe(self, callback: ControllerSubscriber) -> None:
        self._subscribers.append(callback)

    def rescan(self) -> list[AgentCandidate]:
        return self.scanner.scan()

    def connect(self, agent_id: str) -> None:
        definition = get_definition(agent_id)
        if definition is None:
            raise ValueError(f"Unknown agent id: {agent_id}")

        self.disconnect(emit=False)
        self._active_agent_id = agent_id
        self._listener = PollingStatusListener(
            definition=definition,
            process_source=self.process_source,
            status_provider=self.status_provider,
            callback=self._handle_status_event,
            poll_interval_seconds=self.poll_interval_seconds,
        )
        self._listener.start()

    def disconnect(self, emit: bool = True) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        previous_agent_id = self._active_agent_id
        self._active_agent_id = None
        self._current_status = AgentStatus.UNCONNECTED
        if emit and previous_agent_id is not None:
            self._publish(
                StatusEvent(
                    agent_id=previous_agent_id,
                    status=AgentStatus.UNCONNECTED,
                    message="已断开当前 Agent",
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


def known_agent_ids() -> tuple[str, ...]:
    return tuple(definition.agent_id for definition in AGENT_DEFINITIONS)
