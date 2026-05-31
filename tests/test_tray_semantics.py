import unittest

from agent_light.models import AgentStatus
from agent_light.tray_app import LIGHT_LEGEND, STATUS_COLORS, TrayApp


class TraySemanticsTests(unittest.TestCase):
    def test_idle_is_green_and_error_is_red(self):
        self.assertEqual(STATUS_COLORS[AgentStatus.IDLE], (22, 163, 74, 255))
        self.assertEqual(STATUS_COLORS[AgentStatus.ERROR], (185, 28, 28, 255))

    def test_menu_legend_contains_new_light_contract(self):
        legend = "\n".join(LIGHT_LEGEND)

        self.assertIn("绿灯闪烁: 程序正在执行中", legend)
        self.assertIn("绿灯常亮: 程序已执行完成", legend)
        self.assertIn("黄灯: 需要用户交互或授权", legend)
        self.assertIn("红灯: 报错或异常停止", legend)

    def test_runtime_tray_icon_remains_plain_status_light(self):
        app = TrayApp(controller=object())
        image = app._make_icon(AgentStatus.ERROR)
        center = image.getpixel((32, 32))
        corner = image.getpixel((4, 4))

        self.assertEqual(image.size, (64, 64))
        self.assertEqual(center, STATUS_COLORS[AgentStatus.ERROR])
        self.assertEqual(corner[3], 0)


if __name__ == "__main__":
    unittest.main()
