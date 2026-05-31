from __future__ import annotations

import os
import platform
import subprocess
import textwrap
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .controller import AgentController
from .dialogs import ask_hook_install_confirmation
from .hook_install import HookInstallPlan
from .models import AgentCandidate, AgentSessionCandidate, AgentStatus, STATUS_LABELS, StatusEvent
from .runtime_log import log_paths_summary


STATUS_COLORS: dict[AgentStatus, tuple[int, int, int, int]] = {
    AgentStatus.UNCONNECTED: (128, 128, 128, 255),
    AgentStatus.DISCONNECTED: (96, 96, 96, 255),
    AgentStatus.IDLE: (22, 163, 74, 255),
    AgentStatus.BUSY: (22, 163, 74, 255),
    AgentStatus.NEEDS_INTERACTION: (234, 179, 8, 255),
    AgentStatus.ERROR: (185, 28, 28, 255),
}

BUSY_BLINK_OFF_COLOR = (20, 83, 45, 255)
BLINK_INTERVAL_SECONDS = 0.5

LIGHT_LEGEND: tuple[str, ...] = (
    "绿灯闪烁: 程序正在执行中",
    "绿灯常亮: 程序已执行完成",
    "黄灯: 需要用户交互或授权",
    "红灯: 报错或异常停止",
    "灰灯: 未连接或已断开",
)


@dataclass
class TrayApp:
    controller: AgentController
    title: str = "Agent Beacon"
    initial_agent_id: str | None = None
    initial_session_id: str | None = None

    _icon: object | None = field(default=None, init=False)
    _status: AgentStatus = field(default=AgentStatus.UNCONNECTED, init=False)
    _message: str = field(default="未连接 Agent", init=False)
    _candidates: list[AgentCandidate] = field(default_factory=list, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _blink_stop_event: threading.Event = field(default_factory=threading.Event, init=False)
    _blink_thread: threading.Thread | None = field(default=None, init=False)
    _blink_on: bool = field(default=True, init=False)

    def run(self) -> None:
        try:
            import pystray
            from pystray import Menu
        except ImportError as exc:
            raise RuntimeError(
                "Tray UI requires pystray and Pillow. Install with: pip install -r requirements.txt"
            ) from exc

        self.controller.subscribe(self._on_status)
        self.controller.hook_consent_callback = self._confirm_hook_install
        menu = Menu(lambda: tuple(self._build_menu_items()))
        icon = pystray.Icon(
            "agent-traffic-light",
            icon=self._make_icon(self._status),
            title=self._tooltip(),
            menu=menu,
        )
        self._icon = icon
        icon.run(self._setup)

    def _setup(self, icon: object) -> None:
        self.refresh_candidates()
        if not self._connect_initial_selection():
            sessions = self._all_sessions()
            if len(sessions) == 1:
                self.controller.connect_session(sessions[0])
        icon.visible = True

    def _connect_initial_selection(self) -> bool:
        try:
            if self.initial_session_id:
                self.controller.connect_session_id(self.initial_session_id)
                return True
            if self.initial_agent_id:
                self.controller.connect(self.initial_agent_id)
                return True
        except ValueError as exc:
            self.controller.logger.record(
                "initial_connect_failed",
                agent_id=self.initial_agent_id,
                session_id=self.initial_session_id,
                error=str(exc),
            )
        return False

    def refresh_candidates(self) -> None:
        with self._lock:
            self._candidates = self.controller.rescan()
        self._update_menu()

    def _on_status(self, event: StatusEvent) -> None:
        with self._lock:
            self._status = event.status
            self._message = event.message
            self._blink_on = True
        if event.status == AgentStatus.BUSY:
            self._start_blinking()
        else:
            self._stop_blinking()
        self._update_icon()

    def _build_menu_items(self):
        import pystray
        from pystray import Menu, MenuItem

        with self._lock:
            candidates = tuple(self._candidates)
            active_session_id = self.controller.active_session_id
            active_session_label = self.controller.active_session_label
            status = self._status
            message = self._message
            monitor_id = self.controller.active_monitor_id
            hook_registration_count = self.controller.hook_registration_count
            runtime_log_path = self.controller.runtime_log_path

        def connect_action(session: AgentSessionCandidate):
            def _connect(icon, item):
                self.controller.connect_session(session)
                self.refresh_candidates()

            return _connect

        if candidates:
            detected_items = tuple(
                MenuItem(
                    f"{candidate.display_name} ({candidate.session_count})",
                    Menu(
                        *tuple(
                            MenuItem(
                                session.menu_label,
                                connect_action(session),
                                checked=lambda item, session_id=session.session_id: active_session_id
                                == session_id,
                                radio=True,
                            )
                            for session in candidate.sessions
                        )
                    ),
                )
                for candidate in candidates
            )
        else:
            detected_items = (
                MenuItem("未检测到活跃 Agent", lambda icon, item: None, enabled=False),
            )

        yield MenuItem(
            f"状态: {STATUS_LABELS[status]}",
            lambda icon, item: None,
            enabled=False,
        )
        yield MenuItem(
            f"接入: {active_session_label or '未选择 Session'}",
            lambda icon, item: None,
            enabled=False,
        )
        if monitor_id:
            yield MenuItem(
                f"监听 ID: {monitor_id}",
                lambda icon, item: None,
                enabled=False,
            )
        yield MenuItem(message, lambda icon, item: None, enabled=False)
        yield pystray.Menu.SEPARATOR
        for legend_item in LIGHT_LEGEND:
            yield MenuItem(legend_item, lambda icon, item: None, enabled=False)
        yield pystray.Menu.SEPARATOR
        yield MenuItem(
            f"Hook 登记: {hook_registration_count} 条",
            lambda icon, item: None,
            enabled=False,
        )
        yield MenuItem("一键取消所有监听 Hook", self._cancel_all_hooks)
        yield MenuItem(
            f"日志: {Path(runtime_log_path).name}",
            lambda icon, item: None,
            enabled=False,
        )
        yield MenuItem("打开日志目录", self._open_log_dir)
        yield pystray.Menu.SEPARATOR
        yield MenuItem("检测到的程序与 Session", Menu(*detected_items))
        yield MenuItem("重新扫描", lambda icon, item: self.refresh_candidates())
        yield MenuItem(
            "断开当前接入",
            lambda icon, item: self.controller.disconnect(),
            enabled=lambda item: self.controller.active_agent_id is not None,
        )
        yield pystray.Menu.SEPARATOR
        yield MenuItem("退出", self._quit)

    def _quit(self, icon, item) -> None:
        self._stop_blinking()
        self.controller.stop()
        icon.stop()

    def _cancel_all_hooks(self, icon, item) -> None:
        self.controller.cancel_all_hook_listeners()
        self.refresh_candidates()
        self._stop_blinking()
        self._update_icon()

    def _open_log_dir(self, icon, item) -> None:
        path = Path(self.controller.runtime_log_path).parent
        try:
            system = platform.system().lower()
            if system == "windows":
                os.startfile(path)  # type: ignore[attr-defined]
            elif system == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError:
            return

    def _confirm_hook_install(self, plan: HookInstallPlan) -> bool:
        files = "\n".join(f"- {path}" for path in plan.files)
        commands = "\n".join(f"- {command}" for command in plan.commands)
        body = textwrap.dedent(
            f"""
            Agent 灯塔需要为当前 Session 写入 Agent Hook，才能准确识别执行中、等待授权、已完成和异常状态。

            将写入或修改的文件：
            {files}

            将登记的 Hook 命令：
            {commands}

            这些内容会被标记为 Agent Beacon 管理项；之后可以在右键菜单中一键取消所有监听 Hook。
            """
        ).strip()

        self.controller.logger.record(
            "hook_consent_dialog_requested",
            agent_id=plan.agent_id,
            session_id=plan.session_id,
            monitor_id=plan.monitor_id,
            has_project_root=bool(plan.project_root),
            files=log_paths_summary(list(plan.files)),
        )
        allowed = ask_hook_install_confirmation(
            title="允许 Agent 灯塔写入 Agent Hook 配置吗？",
            body=body,
        )
        self.controller.logger.record(
            "hook_consent_dialog_result",
            agent_id=plan.agent_id,
            session_id=plan.session_id,
            monitor_id=plan.monitor_id,
            allowed=allowed,
        )
        return allowed

    def _all_sessions(self) -> tuple[AgentSessionCandidate, ...]:
        with self._lock:
            return tuple(
                session
                for candidate in self._candidates
                for session in candidate.sessions
            )

    def _update_icon(self) -> None:
        icon = self._icon
        if icon is None:
            return
        with self._lock:
            status = self._status
            blink_on = self._blink_on
        icon.icon = self._make_icon(status, blink_on=blink_on)
        icon.title = self._tooltip()
        self._update_menu()

    def _update_menu(self) -> None:
        icon = self._icon
        if icon is not None:
            icon.update_menu()

    def _tooltip(self) -> str:
        return f"{self.title}: {STATUS_LABELS[self._status]}"

    def _start_blinking(self) -> None:
        if self._blink_thread and self._blink_thread.is_alive():
            return
        self._blink_stop_event.clear()
        self._blink_thread = threading.Thread(
            target=self._blink_loop,
            name="agent-light-busy-blink",
            daemon=True,
        )
        self._blink_thread.start()

    def _stop_blinking(self) -> None:
        self._blink_stop_event.set()
        thread = self._blink_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=BLINK_INTERVAL_SECONDS * 2)
        self._blink_thread = None
        with self._lock:
            self._blink_on = True

    def _blink_loop(self) -> None:
        while not self._blink_stop_event.wait(BLINK_INTERVAL_SECONDS):
            with self._lock:
                if self._status != AgentStatus.BUSY:
                    self._blink_on = True
                    break
                self._blink_on = not self._blink_on
            self._update_icon()

    def _make_icon(self, status: AgentStatus, blink_on: bool = True):
        from PIL import Image, ImageDraw

        size = 64
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        fill = (
            BUSY_BLINK_OFF_COLOR
            if status == AgentStatus.BUSY and not blink_on
            else STATUS_COLORS[status]
        )
        draw.ellipse(
            (9, 9, size - 9, size - 9),
            fill=fill,
            outline=(24, 24, 27, 255),
            width=4,
        )
        if status == AgentStatus.BUSY and blink_on:
            draw.ellipse((4, 4, size - 4, size - 4), outline=(187, 247, 208, 255), width=3)
        if status in {AgentStatus.UNCONNECTED, AgentStatus.DISCONNECTED}:
            draw.line((20, 20, 44, 44), fill=(245, 245, 245, 255), width=5)
        return image
