import json
import tempfile
import unittest
from pathlib import Path

from agent_light.models import AgentStatus, StatusEvent
from agent_light.runtime_log import RuntimeLogger


class RuntimeLogTests(unittest.TestCase):
    def test_runtime_logger_writes_json_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = RuntimeLogger(log_dir=Path(tmpdir), runtime_id="runtime-test")
            logger.record(
                "status_event",
                event=StatusEvent(
                    agent_id="codex_cli",
                    status=AgentStatus.BUSY,
                    message="working",
                    monitor_id="m1",
                ),
            )
            lines = logger.log_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(len(lines), 2)
        payload = json.loads(lines[-1])
        self.assertEqual(payload["event_type"], "status_event")
        self.assertEqual(payload["payload"]["event"]["status"], "busy")
        self.assertEqual(payload["payload"]["event"]["monitor_id"], "m1")


if __name__ == "__main__":
    unittest.main()
