import json
import tempfile
import unittest
from pathlib import Path

from agent_light.hook_registry import HookFileRecord, HookRegistration, HookRegistry
from agent_light.runtime_log import RuntimeLogger


class HookRegistryTests(unittest.TestCase):
    def test_cleanup_all_removes_only_managed_json_hook_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / ".claude" / "settings.local.json"
            settings_path.parent.mkdir()
            settings_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "matcher": "*",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "agent-light --hook-event # agent-beacon-managed",
                                            "agent_beacon_managed": True,
                                        },
                                        {
                                            "type": "command",
                                            "command": "echo keep-me",
                                        },
                                    ],
                                }
                            ]
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            registry = HookRegistry(
                manifest_path=Path(tmpdir) / "manifest.json",
                logger=RuntimeLogger(log_dir=Path(tmpdir) / "logs", runtime_id="test"),
            )
            registry.register(
                HookRegistration(
                    agent_id="cloud_code_cli",
                    project_root=tmpdir,
                    files=(
                        HookFileRecord(
                            path=str(settings_path),
                            cleanup_strategy="json_managed_entries",
                        ),
                    ),
                )
            )

            result = registry.cleanup_all()
            payload = json.loads(settings_path.read_text(encoding="utf-8"))

        hooks = payload["hooks"]["PreToolUse"][0]["hooks"]
        self.assertEqual(result.registrations, 1)
        self.assertEqual(result.touched_files, 1)
        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0]["command"], "echo keep-me")

    def test_cleanup_all_removes_managed_marker_blocks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / ".codex" / "config.toml"
            config_path.parent.mkdir()
            config_path.write_text(
                "\n".join(
                    [
                        "[features]",
                        "hooks = true",
                        "# agent-beacon-managed:start",
                        "[[hooks.Stop]]",
                        "command = 'agent-light --hook-event'",
                        "# agent-beacon-managed:end",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            registry = HookRegistry(
                manifest_path=Path(tmpdir) / "manifest.json",
                logger=RuntimeLogger(log_dir=Path(tmpdir) / "logs", runtime_id="test"),
            )
            registry.register(
                HookRegistration(
                    agent_id="codex_cli",
                    project_root=tmpdir,
                    files=(HookFileRecord(path=str(config_path), cleanup_strategy="marker_block"),),
                )
            )

            result = registry.cleanup_all()
            text = config_path.read_text(encoding="utf-8")

        self.assertEqual(result.touched_files, 1)
        self.assertIn("hooks = true", text)
        self.assertNotIn("agent-beacon-managed", text)
        self.assertNotIn("[[hooks.Stop]]", text)

    def test_cleanup_all_removes_managed_exec_form_json_hook_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / ".claude" / "settings.local.json"
            settings_path.parent.mkdir()
            settings_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "UserPromptSubmit": [
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "/usr/bin/python3",
                                            "args": ["bridge.py", "--hook-event"],
                                            "agent_beacon_managed": True,
                                        }
                                    ]
                                }
                            ],
                            "Stop": [
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "echo keep-me",
                                        }
                                    ]
                                }
                            ],
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            registry = HookRegistry(
                manifest_path=Path(tmpdir) / "manifest.json",
                logger=RuntimeLogger(log_dir=Path(tmpdir) / "logs", runtime_id="test"),
            )
            registry.register(
                HookRegistration(
                    agent_id="cloud_code_cli",
                    project_root=tmpdir,
                    files=(
                        HookFileRecord(
                            path=str(settings_path),
                            cleanup_strategy="json_managed_entries",
                        ),
                    ),
                )
            )

            result = registry.cleanup_all()
            payload = json.loads(settings_path.read_text(encoding="utf-8"))

        self.assertEqual(result.touched_files, 1)
        self.assertNotIn("UserPromptSubmit", payload["hooks"])
        self.assertEqual(payload["hooks"]["Stop"][0]["hooks"][0]["command"], "echo keep-me")


if __name__ == "__main__":
    unittest.main()
