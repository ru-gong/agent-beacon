import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_light.dialogs import ask_hook_install_confirmation


class DialogTests(unittest.TestCase):
    def test_macos_confirmation_uses_osascript_arguments(self):
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            return SimpleNamespace(returncode=0, stdout="允许写入\n")

        with patch("agent_light.dialogs.platform.system", return_value="Darwin"):
            with patch("agent_light.dialogs.subprocess.run", side_effect=fake_run):
                allowed = ask_hook_install_confirmation(
                    title="标题",
                    body="第一行\n第二行",
                )

        self.assertTrue(allowed)
        self.assertEqual(captured["args"][0], "osascript")
        self.assertIn("第一行\n第二行", captured["args"])
        self.assertIn("标题", captured["args"])


if __name__ == "__main__":
    unittest.main()
