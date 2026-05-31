import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from agent_light.definitions import get_definition
from agent_light.models import AgentStatus, ProcessInfo, StatusEvent
from agent_light.status import (
    CodexLogStatusProvider,
    CompositeStatusProvider,
    HeuristicStatusProvider,
    JsonStatusFileProvider,
    PollingStatusListener,
    StatusContext,
)


class FakeProcessSource:
    def snapshot(self):
        return []


class CountingProcessSource:
    def __init__(self):
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        return [ProcessInfo(pid=1, name="codex", cmdline=("codex",))]


class StaticStatusProvider:
    def evaluate(self, definition, processes, context=None):
        return StatusEvent(
            agent_id=definition.agent_id,
            status=AgentStatus.IDLE,
            message="stable",
        )


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
        definition = get_definition("codex_desktop")
        event = HeuristicStatusProvider().evaluate(
            definition,
            [ProcessInfo(pid=1, name="Codex", status="running", cpu_percent=4.2)],
        )

        self.assertEqual(event.status, AgentStatus.IDLE)

    def test_heuristic_does_not_infer_cli_busy_from_cpu_activity(self):
        definition = get_definition("cloud_code_cli")
        event = HeuristicStatusProvider().evaluate(
            definition,
            [ProcessInfo(pid=1, name="claude", cpu_percent=0.6)],
        )

        self.assertEqual(event.status, AgentStatus.IDLE)

    def test_heuristic_does_not_infer_cli_busy_from_running_process(self):
        definition = get_definition("cloud_code_cli")
        event = HeuristicStatusProvider().evaluate(
            definition,
            [ProcessInfo(pid=1, name="claude", status="running", cpu_percent=0.0)],
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

    def test_json_status_file_filters_by_monitor_id_before_newest_wins(self):
        definition = get_definition("codex_cli")
        with tempfile.TemporaryDirectory() as tmpdir:
            old_path = Path(tmpdir) / "codex-cli-old.json"
            new_path = Path(tmpdir) / "codex-cli-new.json"
            old_path.write_text(
                json.dumps(
                    {
                        "agent_id": "codex_cli",
                        "status": "needs_interaction",
                        "message": "current monitor",
                        "monitor_id": "current",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            time.sleep(0.01)
            new_path.write_text(
                json.dumps(
                    {
                        "agent_id": "codex_cli",
                        "status": "error",
                        "message": "other monitor",
                        "monitor_id": "other",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            patched_definition = definition.__class__(
                **{
                    **definition.__dict__,
                    "status_file_globs": (str(Path(tmpdir) / "codex-cli*.json"),),
                }
            )

            event = JsonStatusFileProvider().evaluate(
                patched_definition,
                [],
                StatusContext(monitor_id="current"),
            )

        self.assertEqual(event.status, AgentStatus.NEEDS_INTERACTION)
        self.assertEqual(event.message, "current monitor")

    def test_json_status_file_uses_mtime_when_timestamp_is_missing(self):
        definition = get_definition("codex_cli")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "codex-cli.json"
            path.write_text(
                json.dumps(
                    {
                        "agent_id": "codex_cli",
                        "status": "idle",
                    }
                ),
                encoding="utf-8",
            )
            old_time = time.time() - 120
            path.touch()
            os.utime(path, (old_time, old_time))
            patched_definition = definition.__class__(
                **{
                    **definition.__dict__,
                    "status_file_globs": (str(path),),
                }
            )

            event = JsonStatusFileProvider().evaluate(patched_definition, [])

        self.assertLess(event.timestamp, time.time() - 30)

    def test_codex_log_provider_reports_busy_from_response_created(self):
        definition = get_definition("codex_desktop")
        with tempfile.TemporaryDirectory() as tmpdir:
            logs_path = Path(tmpdir) / "logs_2.sqlite"
            _create_codex_log_db(logs_path)
            _insert_codex_log(
                logs_path,
                1,
                'run_sampling_request{cwd=/tmp/project}: event.kind=response.created',
            )

            event = CodexLogStatusProvider(logs_path=logs_path).evaluate(
                definition,
                [],
                StatusContext(project_root="/tmp/project"),
            )

        self.assertEqual(event.status, AgentStatus.BUSY)
        self.assertEqual(event.source, "codex-log")

    def test_codex_log_provider_requires_exact_project_root_match(self):
        definition = get_definition("codex_cli")
        with tempfile.TemporaryDirectory() as tmpdir:
            logs_path = Path(tmpdir) / "logs_2.sqlite"
            _create_codex_log_db(logs_path)
            _insert_codex_log(
                logs_path,
                1,
                'run_sampling_request{cwd=/Users/ada/project}: event.kind=response.created',
            )

            event = CodexLogStatusProvider(logs_path=logs_path).evaluate(
                definition,
                [],
                StatusContext(project_root="/Users/ada"),
            )

        self.assertIsNone(event)

    def test_codex_log_provider_ignores_embedded_cwd_in_tool_arguments(self):
        definition = get_definition("codex_cli")
        with tempfile.TemporaryDirectory() as tmpdir:
            logs_path = Path(tmpdir) / "logs_2.sqlite"
            _create_codex_log_db(logs_path)
            _insert_codex_log(
                logs_path,
                1,
                (
                    "run_sampling_request{cwd=/Users/ada/project}: "
                    'ToolCall: exec_command {"cmd":"echo cwd=/Users/ada}"} '
                    "event.kind=response.created"
                ),
            )

            event = CodexLogStatusProvider(logs_path=logs_path).evaluate(
                definition,
                [],
                StatusContext(project_root="/Users/ada"),
            )

        self.assertIsNone(event)

    def test_codex_log_provider_reports_idle_after_response_completed(self):
        definition = get_definition("codex_desktop")
        with tempfile.TemporaryDirectory() as tmpdir:
            logs_path = Path(tmpdir) / "logs_2.sqlite"
            _create_codex_log_db(logs_path)
            _insert_codex_log(
                logs_path,
                1,
                'run_sampling_request{cwd=/tmp/project}: event.kind=response.created',
            )
            _insert_codex_log(
                logs_path,
                2,
                'run_sampling_request{cwd=/tmp/project}: event.kind=response.completed',
            )

            event = CodexLogStatusProvider(logs_path=logs_path).evaluate(
                definition,
                [],
                StatusContext(project_root="/tmp/project"),
            )

        self.assertEqual(event.status, AgentStatus.IDLE)

    def test_codex_log_provider_treats_tool_execution_after_completion_as_busy(self):
        definition = get_definition("codex_desktop")
        with tempfile.TemporaryDirectory() as tmpdir:
            logs_path = Path(tmpdir) / "logs_2.sqlite"
            _create_codex_log_db(logs_path)
            _insert_codex_log(
                logs_path,
                1,
                'run_sampling_request{cwd=/tmp/project}: event.kind=response.completed',
            )
            _insert_codex_log(
                logs_path,
                2,
                'run_sampling_request{cwd=/tmp/project}: dispatch_tool_call tool_name=exec_command',
            )

            event = CodexLogStatusProvider(logs_path=logs_path).evaluate(
                definition,
                [],
                StatusContext(project_root="/tmp/project"),
            )

        self.assertEqual(event.status, AgentStatus.BUSY)

    def test_composite_newer_codex_log_busy_overrides_older_hook_idle(self):
        definition = get_definition("codex_desktop")
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "codex-desktop.json"
            logs_path = Path(tmpdir) / "logs_2.sqlite"
            status_path.write_text(
                json.dumps(
                    {
                        "agent_id": "codex_desktop",
                        "status": "idle",
                        "monitor_id": "m1",
                    }
                ),
                encoding="utf-8",
            )
            old_time = time.time() - 90
            os.utime(status_path, (old_time, old_time))
            _create_codex_log_db(logs_path)
            _insert_codex_log(
                logs_path,
                1,
                'run_sampling_request{cwd=/tmp/project}: event.kind=response.created',
            )
            patched_definition = definition.__class__(
                **{
                    **definition.__dict__,
                    "status_file_globs": (str(status_path),),
                }
            )
            provider = CompositeStatusProvider(
                providers=(
                    JsonStatusFileProvider(),
                    CodexLogStatusProvider(logs_path=logs_path),
                    HeuristicStatusProvider(),
                )
            )

            event = provider.evaluate(
                patched_definition,
                [ProcessInfo(pid=1, name="codex")],
                StatusContext(monitor_id="m1", project_root="/tmp/project"),
            )

        self.assertEqual(event.status, AgentStatus.BUSY)

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

    def test_listener_throttles_full_process_scans(self):
        definition = get_definition("codex_cli")
        source = CountingProcessSource()
        listener = PollingStatusListener(
            definition=definition,
            process_source=source,
            status_provider=StaticStatusProvider(),
            callback=lambda event: None,
            poll_interval_seconds=0.02,
            process_scan_interval_seconds=10.0,
        )

        listener.start()
        try:
            time.sleep(0.12)
        finally:
            listener.stop()

        self.assertEqual(source.calls, 1)


def _create_codex_log_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE logs (
                id INTEGER PRIMARY KEY,
                ts INTEGER NOT NULL,
                ts_nanos INTEGER NOT NULL,
                level TEXT NOT NULL,
                target TEXT NOT NULL,
                feedback_log_body TEXT,
                module_path TEXT,
                file TEXT,
                line INTEGER,
                thread_id TEXT,
                process_uuid TEXT,
                estimated_bytes INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        connection.commit()
    finally:
        connection.close()


def _insert_codex_log(path: Path, row_id: int, body: str) -> None:
    now = int(time.time())
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            INSERT INTO logs (
                id, ts, ts_nanos, level, target, feedback_log_body
            ) VALUES (?, ?, ?, 'INFO', 'codex_test', ?)
            """,
            (row_id, now + row_id, row_id, body),
        )
        connection.commit()
    finally:
        connection.close()


if __name__ == "__main__":
    unittest.main()
