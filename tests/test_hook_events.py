import json
import tempfile
import unittest
from pathlib import Path

from agent_light.hook_events import status_for_hook_event, write_hook_event_status
from agent_light.models import AgentStatus
from agent_light.runtime_log import RuntimeLogger


class HookEventTests(unittest.TestCase):
    def test_maps_core_hook_events_to_light_statuses(self):
        self.assertEqual(status_for_hook_event("PreToolUse", {}), AgentStatus.BUSY)
        self.assertEqual(
            status_for_hook_event("PermissionRequest", {}),
            AgentStatus.NEEDS_INTERACTION,
        )
        self.assertEqual(status_for_hook_event("Stop", {}), AgentStatus.IDLE)
        self.assertEqual(status_for_hook_event("StopFailure", {}), AgentStatus.ERROR)

    def test_writes_filtered_status_file_with_monitor_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = RuntimeLogger(log_dir=Path(tmpdir) / "logs", runtime_id="test")
            result = write_hook_event_status(
                agent_id="codex_cli",
                payload={"hook_event_name": "PermissionRequest", "session_id": "abc"},
                provider="codex-cli",
                monitor_id="monitor-1",
                session_root_pid=123,
                state_dir=Path(tmpdir),
                logger=logger,
            )

            self.assertTrue(result.wrote_status)
            payload = json.loads(result.status_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["agent_id"], "codex_cli")
        self.assertEqual(payload["status"], "needs_interaction")
        self.assertEqual(payload["monitor_id"], "monitor-1")
        self.assertEqual(payload["session_root_pid"], 123)
        self.assertEqual(payload["hook_session_id"], "abc")

    def test_runtime_log_redacts_hook_payload_contents(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = RuntimeLogger(log_dir=Path(tmpdir) / "logs", runtime_id="test")
            result = write_hook_event_status(
                agent_id="codex_cli",
                payload={
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "abc",
                    "prompt": "private roadmap for Project Nightjar",
                    "message": "private roadmap for Project Nightjar",
                    "tool_input": {"command": "cat /Users/ada/ProjectNightjar/.env"},
                    "transcript_path": "/Users/ada/ProjectNightjar/transcript.jsonl",
                },
                provider="codex-cli",
                monitor_id="monitor-1",
                session_root_pid=123,
                state_dir=Path(tmpdir),
                logger=logger,
            )
            log_text = logger.log_path.read_text(encoding="utf-8")
            status_payload = json.loads(result.status_path.read_text(encoding="utf-8"))
            records = [
                json.loads(line)
                for line in log_text.splitlines()
                if json.loads(line)["event_type"] == "hook_event_received"
            ]

        self.assertEqual(len(records), 1)
        summary = records[0]["payload"]["payload_summary"]
        self.assertIn("prompt", summary["redacted_keys"])
        self.assertIn("tool_input", summary["redacted_keys"])
        self.assertIn("transcript_path", summary["redacted_keys"])
        self.assertNotIn("Project Nightjar", log_text)
        self.assertNotIn("ProjectNightjar", log_text)
        self.assertNotIn("/Users/ada", log_text)
        self.assertNotIn("Project Nightjar", json.dumps(status_payload, ensure_ascii=False))
        self.assertNotIn("ProjectNightjar", json.dumps(status_payload, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
