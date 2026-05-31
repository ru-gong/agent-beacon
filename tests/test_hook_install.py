import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_light.hook_install import HookInstaller
from agent_light.hook_registry import HookRegistry
from agent_light.models import AgentDefinition, AgentSessionCandidate, ProcessInfo
from agent_light.runtime_log import RuntimeLogger


class HookInstallTests(unittest.TestCase):
    def test_installs_codex_hook_json_for_project_session(self):
        definition = AgentDefinition(
            agent_id="codex_desktop",
            display_name="Codex Desktop",
            process_name_keywords=("codex",),
            cmdline_keywords=("codex",),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = HookRegistry(
                manifest_path=Path(tmpdir) / "state" / "manifest.json",
                logger=RuntimeLogger(log_dir=Path(tmpdir) / "logs", runtime_id="test"),
            )
            installer = HookInstaller(registry=registry)
            session = AgentSessionCandidate(
                session_id="codex_desktop:100",
                definition=definition,
                root_pid=100,
                processes=(
                    ProcessInfo(
                        pid=100,
                        name="codex",
                        cmdline=("codex", "app-server"),
                        cwd=tmpdir,
                    ),
                ),
                matched_by=("cmdline~=codex",),
                confidence=100,
                project_root=tmpdir,
            )

            with patch.dict(os.environ, {"AGENT_BEACON_STATE_DIR": str(Path(tmpdir) / "app-state")}):
                plan = installer.plan(session, "monitor-1")
                result = installer.install(plan)
            hook_path = Path(tmpdir) / ".codex" / "hooks.json"
            payload = json.loads(hook_path.read_text(encoding="utf-8"))

        self.assertTrue(result.installed)
        self.assertIn("UserPromptSubmit", payload["hooks"])
        self.assertIn("PreToolUse", payload["hooks"])
        command = payload["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        self.assertIn("--hook-event", command)
        self.assertIn("--monitor-id monitor-1", command)
        self.assertIn("agent_beacon_hook.py", command)

    def test_installs_claude_local_settings_for_cloud_code_session(self):
        definition = AgentDefinition(
            agent_id="cloud_code_cli",
            display_name="Cloud Code CLI",
            process_name_keywords=("claude",),
            cmdline_keywords=("claude",),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = HookRegistry(
                manifest_path=Path(tmpdir) / "state" / "manifest.json",
                logger=RuntimeLogger(log_dir=Path(tmpdir) / "logs", runtime_id="test"),
            )
            installer = HookInstaller(registry=registry)
            session = AgentSessionCandidate(
                session_id="cloud_code_cli:200",
                definition=definition,
                root_pid=200,
                processes=(
                    ProcessInfo(
                        pid=200,
                        name="claude",
                        cmdline=("claude",),
                        cwd=tmpdir,
                    ),
                ),
                matched_by=("cmdline~=claude",),
                confidence=80,
                project_root=tmpdir,
            )

            with patch.dict(os.environ, {"AGENT_BEACON_STATE_DIR": str(Path(tmpdir) / "app-state")}):
                plan = installer.plan(session, "monitor-2")
                result = installer.install(plan)
            hook_path = Path(tmpdir) / ".claude" / "settings.local.json"
            payload = json.loads(hook_path.read_text(encoding="utf-8"))
            wrapper_path = plan.wrapper_path
            self.assertIsNotNone(wrapper_path)
            wrapper_exists = wrapper_path.exists()
            wrapper_text = wrapper_path.read_text(encoding="utf-8")
            wrapper_executable = os.access(wrapper_path, os.X_OK)

        self.assertTrue(result.installed)
        self.assertIn("UserPromptSubmit", payload["hooks"])
        self.assertIn("StopFailure", payload["hooks"])
        handler = payload["hooks"]["UserPromptSubmit"][0]["hooks"][0]
        self.assertEqual(handler["type"], "command")
        self.assertIn("agent-beacon-managed", handler["command"])
        self.assertNotIn("args", handler)
        self.assertNotIn("agent_beacon_managed", handler)
        self.assertTrue(wrapper_exists)
        self.assertIn("--agent cloud_code_cli", wrapper_text)
        self.assertIn("--provider claude-code", wrapper_text)
        self.assertIn("agent_beacon_hook.py", wrapper_text)
        if os.name != "nt":
            self.assertTrue(wrapper_executable)

    def test_reinstall_removes_previous_claude_wrapper_hook(self):
        definition = AgentDefinition(
            agent_id="cloud_code_cli",
            display_name="Cloud Code CLI",
            process_name_keywords=("claude",),
            cmdline_keywords=("claude",),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = HookRegistry(
                manifest_path=Path(tmpdir) / "state" / "manifest.json",
                logger=RuntimeLogger(log_dir=Path(tmpdir) / "logs", runtime_id="test"),
            )
            installer = HookInstaller(registry=registry)
            session = AgentSessionCandidate(
                session_id="cloud_code_cli:200",
                definition=definition,
                root_pid=200,
                processes=(ProcessInfo(pid=200, name="claude", cmdline=("claude",), cwd=tmpdir),),
                matched_by=("cmdline~=claude",),
                confidence=80,
                project_root=tmpdir,
            )

            with patch.dict(os.environ, {"AGENT_BEACON_STATE_DIR": str(Path(tmpdir) / "app-state")}):
                first_plan = installer.plan(session, "monitor-1")
                second_plan = installer.plan(session, "monitor-2")
                installer.install(first_plan)
                installer.install(second_plan)
            hook_path = Path(tmpdir) / ".claude" / "settings.local.json"
            payload = json.loads(hook_path.read_text(encoding="utf-8"))
            self.assertIsNotNone(second_plan.wrapper_path)
            second_wrapper_exists = second_plan.wrapper_path.exists()
            second_wrapper_text = second_plan.wrapper_path.read_text(encoding="utf-8")

        handlers = payload["hooks"]["UserPromptSubmit"][0]["hooks"]
        self.assertEqual(len(handlers), 1)
        self.assertIn("monitor-2", handlers[0]["command"])
        self.assertNotIn("monitor-1", handlers[0]["command"])
        self.assertTrue(second_wrapper_exists)
        self.assertIn("monitor-2", second_wrapper_text)


if __name__ == "__main__":
    unittest.main()
