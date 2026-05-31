import json
import tempfile
import unittest
from pathlib import Path

from agent_light.controller import AgentController
from agent_light.models import AgentStatus, StatusEvent
from agent_light.runtime_log import RuntimeLogger


class CaptureNotifier:
    def __init__(self):
        self.messages = []

    def notify(self, title, body):
        self.messages.append((title, body))


class ControllerTests(unittest.TestCase):
    def test_notifies_when_agent_needs_interaction(self):
        controller = AgentController.__new__(AgentController)
        controller._current_status = AgentStatus.IDLE
        controller._subscribers = []
        controller.notifier = CaptureNotifier()

        controller._handle_status_event(
            StatusEvent(
                agent_id="codex_cli",
                status=AgentStatus.NEEDS_INTERACTION,
                message="等待权限审批",
            )
        )

        self.assertEqual(controller.notifier.messages, [("Agent 需要交互", "等待权限审批")])

    def test_notifies_for_milestone(self):
        controller = AgentController.__new__(AgentController)
        controller._current_status = AgentStatus.BUSY
        controller._subscribers = []
        controller.notifier = CaptureNotifier()

        controller._handle_status_event(
            StatusEvent(
                agent_id="codex_cli",
                status=AgentStatus.BUSY,
                message="完成阶段性分析",
                milestone=True,
            )
        )

        self.assertEqual(controller.notifier.messages, [("Agent 里程碑", "完成阶段性分析")])

    def test_status_event_log_omits_session_label_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            controller = AgentController.__new__(AgentController)
            controller._current_status = AgentStatus.BUSY
            controller._subscribers = []
            controller.notifier = CaptureNotifier()
            controller.logger = RuntimeLogger(
                log_dir=Path(tmpdir) / "logs",
                runtime_id="test",
            )

            controller._handle_status_event(
                StatusEvent(
                    agent_id="codex_cli",
                    status=AgentStatus.IDLE,
                    message="done",
                    session_id="codex_cli:100",
                    session_label=(
                        "Session 100 · /Users/ada/ProjectNightjar · 1 process · "
                        "codex --project /Users/ada/ProjectNightjar"
                    ),
                )
            )
            log_text = controller.logger.log_path.read_text(encoding="utf-8")
            records = [
                json.loads(line)
                for line in log_text.splitlines()
                if json.loads(line)["event_type"] == "status_event"
            ]

        self.assertEqual(len(records), 1)
        self.assertNotIn("session_label", records[0]["payload"]["event"])
        self.assertNotIn("ProjectNightjar", log_text)
        self.assertNotIn("/Users/ada", log_text)


if __name__ == "__main__":
    unittest.main()
