import unittest

from agent_light.models import ProcessInfo
from agent_light.scanner import AgentScanner


class FakeProcessSource:
    def __init__(self, processes):
        self._processes = processes

    def snapshot(self):
        return self._processes


class ScannerTests(unittest.TestCase):
    def test_detects_codex_desktop_without_cli_false_positive(self):
        scanner = AgentScanner(
            process_source=FakeProcessSource(
                [
                    ProcessInfo(
                        pid=101,
                        name="Codex",
                        cmdline=("/Applications/Codex.app/Contents/MacOS/Codex",),
                    )
                ]
            )
        )

        candidates = scanner.scan()

        self.assertEqual([candidate.agent_id for candidate in candidates], ["codex_desktop"])

    def test_detects_codex_cli_launched_by_npx(self):
        scanner = AgentScanner(
            process_source=FakeProcessSource(
                [
                    ProcessInfo(
                        pid=202,
                        name="node",
                        cmdline=("node", "/usr/local/bin/codex", "@openai/codex"),
                    )
                ]
            )
        )

        candidates = scanner.scan()

        self.assertEqual(candidates[0].agent_id, "codex_cli")
        self.assertIn(202, candidates[0].pids)

    def test_detects_cloud_code_and_claude_code_alias(self):
        scanner = AgentScanner(
            process_source=FakeProcessSource(
                [
                    ProcessInfo(
                        pid=303,
                        name="node",
                        cmdline=("node", "/opt/bin/claude", "@anthropic-ai/claude-code"),
                    )
                ]
            )
        )

        candidates = scanner.scan()

        self.assertEqual(candidates[0].agent_id, "cloud_code_cli")


if __name__ == "__main__":
    unittest.main()
