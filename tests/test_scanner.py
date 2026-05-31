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
        self.assertEqual(candidates[0].session_count, 1)

    def test_groups_codex_desktop_helpers_into_one_app_session(self):
        scanner = AgentScanner(
            process_source=FakeProcessSource(
                [
                    ProcessInfo(
                        pid=101,
                        name="Codex",
                        cmdline=("/Applications/Codex.app/Contents/MacOS/Codex",),
                    ),
                    ProcessInfo(
                        pid=102,
                        name="node",
                        cmdline=(
                            "/Applications/Codex.app/Contents/Resources/node",
                            "--worker",
                        ),
                    ),
                ]
            )
        )

        candidates = scanner.scan()

        self.assertEqual(candidates[0].agent_id, "codex_desktop")
        self.assertEqual(candidates[0].session_count, 1)
        self.assertEqual(candidates[0].sessions[0].pids, (101, 102))

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
        self.assertEqual(candidates[0].session_count, 1)

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

    def test_does_not_detect_generic_cloud_processes_as_cloud_code(self):
        scanner = AgentScanner(
            process_source=FakeProcessSource(
                [
                    ProcessInfo(pid=601, name="cloudd", cmdline=("cloudd",)),
                    ProcessInfo(
                        pid=602,
                        name="CloudTelemetrySe",
                        cmdline=("CloudTelemetryService",),
                    ),
                    ProcessInfo(
                        pid=603,
                        name="Creative Cloud",
                        cmdline=("Adobe Creative Cloud",),
                    ),
                ]
            )
        )

        candidates = scanner.scan()

        self.assertEqual(candidates, [])

    def test_splits_independent_cli_processes_into_sessions(self):
        scanner = AgentScanner(
            process_source=FakeProcessSource(
                [
                    ProcessInfo(
                        pid=401,
                        name="node",
                        ppid=10,
                        cmdline=("node", "/opt/bin/codex", "@openai/codex"),
                    ),
                    ProcessInfo(
                        pid=402,
                        name="node",
                        ppid=11,
                        cmdline=("node", "/opt/bin/codex", "@openai/codex"),
                    ),
                ]
            )
        )

        candidates = scanner.scan()

        self.assertEqual(candidates[0].agent_id, "codex_cli")
        self.assertEqual(
            [session.session_id for session in candidates[0].sessions],
            ["codex_cli:401", "codex_cli:402"],
        )

    def test_groups_matching_child_process_under_parent_session(self):
        scanner = AgentScanner(
            process_source=FakeProcessSource(
                [
                    ProcessInfo(
                        pid=501,
                        name="npm",
                        ppid=10,
                        cmdline=("npm", "exec", "@openai/codex"),
                    ),
                    ProcessInfo(
                        pid=502,
                        name="node",
                        ppid=501,
                        cmdline=("node", "/opt/bin/codex", "@openai/codex"),
                    ),
                ]
            )
        )

        candidates = scanner.scan()

        self.assertEqual(candidates[0].session_count, 1)
        self.assertEqual(candidates[0].sessions[0].session_id, "codex_cli:501")
        self.assertEqual(candidates[0].sessions[0].pids, (501, 502))


if __name__ == "__main__":
    unittest.main()
