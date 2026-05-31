import json
import tempfile
import unittest
from pathlib import Path

from agent_light.definitions import get_definition
from agent_light.models import AgentStatus, ProcessInfo
from agent_light.status import HeuristicStatusProvider, JsonStatusFileProvider, PollingStatusListener


class FakeProcessSource:
    def snapshot(self):
        return []


class StatusTests(unittest.TestCase):
    def test_heuristic_reports_disconnected_without_processes(self):
        definition = get_definition("codex_cli")
        event = HeuristicStatusProvider().evaluate(definition, [])

        self.assertEqual(event.status, AgentStatus.DISCONNECTED)

    def test_heuristic_reports_busy_on_cpu_activity(self):
        definition = get_definition("codex_cli")
        event = HeuristicStatusProvider(busy_cpu_percent=1.0).evaluate(
            definition,
            [ProcessInfo(pid=1, name="codex", cpu_percent=4.2)],
        )

        self.assertEqual(event.status, AgentStatus.BUSY)

    def test_heuristic_defaults_to_idle_despite_cpu_activity(self):
        definition = get_definition("codex_cli")
        event = HeuristicStatusProvider().evaluate(
            definition,
            [ProcessInfo(pid=1, name="codex", cpu_percent=4.2)],
        )

        self.assertEqual(event.status, AgentStatus.IDLE)

    def test_json_status_file_overrides_heuristic_signal(self):
        definition = get_definition("codex_cli")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "codex-cli.json"
            path.write_text(
                json.dumps(
                    {
                        "agent_id": "codex_cli",
                        "status": "needs_interaction",
                        "message": "等待授权",
                        "milestone": True,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            patched_definition = definition.__class__(
                **{
                    **definition.__dict__,
                    "status_file_globs": (str(path),),
                }
            )

            event = JsonStatusFileProvider().evaluate(patched_definition, [])

        self.assertEqual(event.status, AgentStatus.NEEDS_INTERACTION)
        self.assertEqual(event.message, "等待授权")
        self.assertTrue(event.milestone)

    def test_listener_filters_to_selected_session_tree(self):
        definition = get_definition("codex_cli")
        listener = PollingStatusListener(
            definition=definition,
            process_source=FakeProcessSource(),
            status_provider=HeuristicStatusProvider(),
            callback=lambda event: None,
            session_root_pid=10,
        )
        processes = [
            ProcessInfo(pid=10, name="node", cmdline=("codex",)),
            ProcessInfo(pid=11, name="node", ppid=10, cmdline=("codex child",)),
            ProcessInfo(pid=20, name="node", cmdline=("codex other",)),
        ]

        filtered = listener._filter_session_processes(
            processes,
            {process.pid: process for process in processes},
        )

        self.assertEqual([process.pid for process in filtered], [10, 11])


if __name__ == "__main__":
    unittest.main()
