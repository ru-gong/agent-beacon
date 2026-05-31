import unittest

from agent_light.controller import AgentController
from agent_light.models import AgentStatus, StatusEvent


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


if __name__ == "__main__":
    unittest.main()
