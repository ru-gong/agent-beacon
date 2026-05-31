import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_light.definitions import get_definition
from agent_light.models import AgentDefinition
from agent_light.paths import expand_path_pattern, expand_path_patterns
from agent_light.status import JsonStatusFileProvider


class PathExpansionTests(unittest.TestCase):
    def test_expands_windows_percent_variables_on_any_platform(self):
        with patch.dict(os.environ, {"APPDATA": r"C:\Users\Ada\AppData\Roaming"}):
            expanded = expand_path_pattern(
                r"%APPDATA%\Agent Beacon\codex-cli*.json"
            )

        self.assertEqual(
            expanded,
            r"C:\Users\Ada\AppData\Roaming\Agent Beacon\codex-cli*.json",
        )

    def test_skips_unresolved_environment_patterns(self):
        with patch.dict(os.environ, {}, clear=True):
            expanded = expand_path_pattern(
                r"%AGENT_BEACON_MISSING%\Agent Beacon\codex-cli*.json"
            )

        self.assertIsNone(expanded)

    def test_deduplicates_expanded_patterns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"AGENT_BEACON_TEST": tmpdir}):
                patterns = list(
                    expand_path_patterns(
                        [
                            "$AGENT_BEACON_TEST/codex-cli*.json",
                            f"{tmpdir}/codex-cli*.json",
                        ]
                    )
                )

        self.assertEqual(patterns, [f"{tmpdir}/codex-cli*.json"])

    def test_json_status_provider_reads_expanded_env_path(self):
        definition = get_definition("codex_cli")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "Agent Beacon" / "codex-cli-status.json"
            path.parent.mkdir()
            path.write_text(
                '{"agent_id":"codex_cli","status":"idle","message":"done"}',
                encoding="utf-8",
            )
            patched_definition = AgentDefinition(
                **{
                    **definition.__dict__,
                    "status_file_globs": (
                        "$AGENT_BEACON_TEST/Agent Beacon/codex-cli*.json",
                    ),
                }
            )
            with patch.dict(os.environ, {"AGENT_BEACON_TEST": tmpdir}):
                event = JsonStatusFileProvider().evaluate(patched_definition, [])

        self.assertEqual(event.message, "done")


if __name__ == "__main__":
    unittest.main()
