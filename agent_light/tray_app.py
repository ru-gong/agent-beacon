from __future__ import annotations

import threading
from dataclasses import dataclass, field

from .controller import AgentController
from .models import AgentCandidate, AgentStatus, STATUS_LABELS, StatusEvent


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
    title: str = "Agent Traffic Light"

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
        if len(self._candidates) == 1:
            self.controller.connect(self._candidates[0].agent_id)
        icon.visible = True

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
            active_agent_id = self.controller.active_agent_id
            status = self._status
            message = self._message

        def connect_action(agent_id: str):
            def _connect(icon, item):
                self.controller.connect(agent_id)
                self.refresh_candidates()

            return _connect

        if candidates:
            detected_items = tuple(
                MenuItem(
                    f"{candidate.display_name}  PID:{','.join(str(pid) for pid in candidate.pids)}",
                    connect_action(candidate.agent_id),
                    checked=lambda item, agent_id=candidate.agent_id: active_agent_id
                    == agent_id,
                    radio=True,
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
        yield MenuItem(message, lambda icon, item: None, enabled=False)
        yield pystray.Menu.SEPARATOR
        for legend_item in LIGHT_LEGEND:
            yield MenuItem(legend_item, lambda icon, item: None, enabled=False)
        yield pystray.Menu.SEPARATOR
        yield MenuItem("检测到的程序", Menu(*detected_items))
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
