from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from .definitions import AGENT_DEFINITIONS, get_definition
from .hook_install import HookInstallPlan, HookInstaller
from .hook_registry import HookCleanupResult, HookRegistry
from .models import AgentCandidate, AgentSessionCandidate, AgentStatus, StatusEvent
from .notify import NativeNotifier, Notifier
from .process_source import ProcessSource, build_default_process_source
from .runtime_log import RuntimeLogger, get_runtime_logger, log_paths_summary
from .scanner import AgentScanner
from .status import CompositeStatusProvider, PollingStatusListener, StatusProvider


ControllerSubscriber = Callable[[StatusEvent], None]
HookConsentCallback = Callable[[HookInstallPlan], bool]


@dataclass
class AgentController:
    scanner: AgentScanner
    process_source: ProcessSource
    status_provider: StatusProvider = field(default_factory=CompositeStatusProvider)
    notifier: Notifier = field(default_factory=NativeNotifier)
    logger: RuntimeLogger = field(default_factory=get_runtime_logger)
    hook_registry: HookRegistry | None = None
    hook_consent_callback: HookConsentCallback | None = None
    poll_interval_seconds: float = 0.25

    _listener: PollingStatusListener | None = field(default=None, init=False)
    _subscribers: list[ControllerSubscriber] = field(default_factory=list, init=False)
    _active_agent_id: str | None = field(default=None, init=False)
    _active_session_id: str | None = field(default=None, init=False)
    _active_session_label: str | None = field(default=None, init=False)
    _active_monitor_id: str | None = field(default=None, init=False)
    _current_status: AgentStatus = field(
        default=AgentStatus.UNCONNECTED, init=False
    )

    def __post_init__(self) -> None:
        if self.hook_registry is None:
            self.hook_registry = HookRegistry(logger=self.logger)
        self.hook_installer = HookInstaller(
            registry=self.hook_registry,
            logger=self.logger,
        )
        self.logger.record("controller_started")

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
    def active_monitor_id(self) -> str | None:
        return self._active_monitor_id

    @property
    def current_status(self) -> AgentStatus:
        return self._current_status

    @property
    def runtime_log_path(self) -> str:
        return self.logger.display_path

    @property
    def hook_registration_count(self) -> int:
        registry = self.hook_registry
        return registry.registration_count() if registry is not None else 0

    def subscribe(self, callback: ControllerSubscriber) -> None:
        self._subscribers.append(callback)

    def rescan(self) -> list[AgentCandidate]:
        candidates = self.scanner.scan()
        self.logger.record(
            "scan_completed",
            candidates=[
                {
                    "agent_id": candidate.agent_id,
                    "display_name": candidate.display_name,
                    "sessions": [
                        {
                            "session_id": session.session_id,
                            "root_pid": session.root_pid,
                            "has_project_root": session.project_root is not None,
                            "pids": session.pids,
                            "process_count": len(session.processes),
                        }
                        for session in candidate.sessions
                    ],
                }
                for candidate in candidates
            ],
        )
        return candidates

    def connect(self, agent_id: str) -> None:
        session = self._first_session_for_agent(agent_id)
        if session is None:
            raise ValueError(f"No running session found for agent id: {agent_id}")
        self.connect_session(session)

    def connect_session(self, session: AgentSessionCandidate) -> None:
        self.disconnect(emit=False)
        monitor_id = session.session_id
        self._active_agent_id = session.agent_id
        self._active_session_id = session.session_id
        self._active_session_label = session.menu_label
        self._active_monitor_id = monitor_id
        self.logger.record(
            "session_connected",
            agent_id=session.agent_id,
            session_id=session.session_id,
            session_label=_session_label_log_value(session),
            has_project_root=session.project_root is not None,
            root_pid=session.root_pid,
            process_count=len(session.processes),
            monitor_id=monitor_id,
        )
        self._listener = PollingStatusListener(
            definition=session.definition,
            process_source=self.process_source,
            status_provider=self.status_provider,
            callback=self._handle_status_event,
            poll_interval_seconds=self.poll_interval_seconds,
            session_id=session.session_id,
            session_root_pid=session.root_pid,
            session_label=session.menu_label,
            monitor_id=monitor_id,
            project_root=session.project_root,
        )
        self._listener.start()
        self._start_hook_install(session, monitor_id)

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
        previous_monitor_id = self._active_monitor_id
        self._active_agent_id = None
        self._active_session_id = None
        self._active_session_label = None
        self._active_monitor_id = None
        self._current_status = AgentStatus.UNCONNECTED
        self.logger.record(
            "session_disconnected",
            agent_id=previous_agent_id,
            session_id=previous_session_id,
            had_session_label=previous_session_label is not None,
            monitor_id=previous_monitor_id,
            emitted=emit,
        )
        if emit and previous_agent_id is not None:
            self._publish(
                StatusEvent(
                    agent_id=previous_agent_id,
                    status=AgentStatus.UNCONNECTED,
                    message="已断开当前 Agent",
                    session_id=previous_session_id,
                    session_label=previous_session_label,
                    monitor_id=previous_monitor_id,
                )
            )

    def stop(self) -> None:
        self.disconnect(emit=False)
        self.logger.record("controller_stopped")

    def cancel_all_hook_listeners(self) -> HookCleanupResult:
        previous_agent_id = self._active_agent_id or "agent_beacon"
        previous_session_id = self._active_session_id
        previous_session_label = self._active_session_label
        previous_monitor_id = self._active_monitor_id
        self.disconnect(emit=False)
        registry = self.hook_registry
        result = (
            registry.cleanup_all()
            if registry is not None
            else HookCleanupResult(0, 0, 0, 0)
        )
        message = (
            "已取消所有 Agent Beacon Hook 监听"
            f"（登记 {result.registrations} 条，清理 {result.touched_files + result.removed_files} 个文件）"
        )
        self._publish(
            StatusEvent(
                agent_id=previous_agent_id,
                status=AgentStatus.UNCONNECTED,
                message=message,
                session_id=previous_session_id,
                session_label=previous_session_label,
                monitor_id=previous_monitor_id,
            )
        )
        self.logger.record(
            "hook_listeners_cancelled_from_controller",
            registrations=result.registrations,
            touched_files=result.touched_files,
            removed_files=result.removed_files,
            skipped_files=result.skipped_files,
            previous_agent_id=previous_agent_id,
            previous_session_id=previous_session_id,
            previous_monitor_id=previous_monitor_id,
        )
        return result

    def _handle_status_event(self, event: StatusEvent) -> None:
        previous_status = self._current_status
        self._current_status = event.status
        logger = getattr(self, "logger", None)
        if logger is not None:
            logger.record(
                "status_event",
                previous_status=previous_status,
                event=_status_event_log_payload(event),
            )
        self._publish(event)

        if event.status == AgentStatus.NEEDS_INTERACTION and previous_status != event.status:
            self.notifier.notify("Agent 需要交互", event.message)
        elif event.milestone:
            self.notifier.notify("Agent 里程碑", event.message)
        elif event.status == AgentStatus.DISCONNECTED and previous_status != event.status:
            self.notifier.notify("Agent 已断开", event.message)

    def _install_hooks_if_allowed(
        self,
        session: AgentSessionCandidate,
        monitor_id: str,
    ) -> None:
        plan = self.hook_installer.plan(session, monitor_id)
        if plan is None:
            return
        if self.hook_consent_callback is None:
            self.logger.record(
                "hook_install_skipped",
                agent_id=session.agent_id,
                session_id=session.session_id,
                monitor_id=monitor_id,
                reason="missing_consent_callback",
            )
            return
        if not self.hook_consent_callback(plan):
            self.logger.record(
                "hook_install_declined",
                agent_id=session.agent_id,
                session_id=session.session_id,
                monitor_id=monitor_id,
                has_project_root=bool(plan.project_root),
                files=log_paths_summary(list(plan.files)),
            )
            return
        if self._active_monitor_id != monitor_id:
            self.logger.record(
                "hook_install_skipped",
                agent_id=session.agent_id,
                session_id=session.session_id,
                monitor_id=monitor_id,
                reason="session_no_longer_active",
            )
            return
        try:
            result = self.hook_installer.install(plan)
        except (OSError, ValueError) as exc:
            self.logger.record(
                "hook_install_failed",
                agent_id=session.agent_id,
                session_id=session.session_id,
                monitor_id=monitor_id,
                error=str(exc),
            )
            self.notifier.notify("Hook 安装失败", str(exc))
            return
        if result.installed:
            self.notifier.notify("Hook 已安装", result.message)

    def _start_hook_install(
        self,
        session: AgentSessionCandidate,
        monitor_id: str,
    ) -> None:
        thread = threading.Thread(
            target=self._install_hooks_if_allowed,
            args=(session, monitor_id),
            name=f"agent-light-hook-install-{session.agent_id}",
            daemon=True,
        )
        thread.start()

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


def _session_label_log_value(session: AgentSessionCandidate) -> str:
    return f"{session.definition.display_name} session"


def _status_event_log_payload(event: StatusEvent) -> dict[str, object]:
    return {
        "agent_id": event.agent_id,
        "status": event.status,
        "message": event.message,
        "session_id": event.session_id,
        "monitor_id": event.monitor_id,
        "hook_session_id": event.hook_session_id,
        "hook_event_name": event.hook_event_name,
        "source": event.source,
        "milestone": event.milestone,
        "timestamp": event.timestamp,
    }
