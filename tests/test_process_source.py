import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_light.process_source import SubprocessProcessSource


class ProcessSourceTests(unittest.TestCase):
    def test_windows_fallback_parses_parent_pid_and_command_line(self):
        stdout = (
            '"ProcessId","ParentProcessId","Name","CommandLine"\n'
            '"100","10","codex.exe","C:\\Users\\Ada\\AppData\\Local\\Programs\\Codex\\codex.exe"\n'
            '"101","100","node.exe","node C:\\Users\\Ada\\AppData\\Roaming\\npm\\node_modules\\@openai\\codex\\bin.js"\n'
        )

        with patch("agent_light.process_source.shutil.which", return_value="powershell"):
            with patch(
                "agent_light.process_source.subprocess.run",
                return_value=SimpleNamespace(stdout=stdout),
            ):
                processes = SubprocessProcessSource()._snapshot_windows()

        self.assertEqual(processes[0].pid, 100)
        self.assertEqual(processes[0].ppid, 10)
        self.assertEqual(processes[0].name, "codex.exe")
        self.assertIn("C:\\Users\\Ada", processes[0].command_text)
        self.assertEqual(processes[1].ppid, 100)


if __name__ == "__main__":
    unittest.main()
