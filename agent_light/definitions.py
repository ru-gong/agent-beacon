from __future__ import annotations

from .models import AgentDefinition


# Agent feature definitions live here on purpose. Adding a fourth agent should
# normally mean adding one AgentDefinition and, if needed, one status provider.
AGENT_DEFINITIONS: tuple[AgentDefinition, ...] = (
    AgentDefinition(
        agent_id="codex_desktop",
        display_name="Codex Desktop",
        process_name_keywords=(
            "codex",
            "codex desktop",
            "codex-desktop",
        ),
        cmdline_keywords=(
            "codex.app",
            "codex desktop",
            "codex-desktop",
            "openai codex",
        ),
        exclude_keywords=(
            "@openai/codex",
            "codex-cli",
            "codex computer use",
            "skycomputeruseclient",
            "extension-host",
            "chrome-extension://",
        ),
        ipc_hints=(
            "~/Library/Application Support/Codex",
            "%APPDATA%\\Codex",
        ),
        status_file_globs=(
            "~/.agent-traffic-light/codex-desktop*.json",
        ),
        notes="Desktop app detection intentionally prefers app bundle / desktop markers.",
    ),
    AgentDefinition(
        agent_id="codex_cli",
        display_name="Codex CLI",
        process_name_keywords=(
            "codex",
            "codex.exe",
            "node",
            "npm",
            "npx",
        ),
        cmdline_keywords=(
            "@openai/codex",
            "openai/codex",
            "codex-cli",
            "codex",
        ),
        exclude_keywords=(
            "codex.app",
            "codex desktop",
            "codex-desktop",
            "codex computer use",
            "skycomputeruseclient",
            "extension-host",
            "chrome-extension://",
            "electron",
        ),
        ipc_hints=(
            "~/.codex",
        ),
        status_file_globs=(
            "~/.agent-traffic-light/codex-cli*.json",
        ),
        notes="CLI detection accepts node/npm/npx launchers when the command line identifies Codex.",
    ),
    AgentDefinition(
        agent_id="cloud_code_cli",
        display_name="Cloud Code CLI",
        process_name_keywords=(
            "cloud-code",
            "cloudcode",
            "cloud",
            "claude",
            "node",
            "npm",
            "npx",
        ),
        cmdline_keywords=(
            "cloud-code",
            "cloud code",
            "cloudcode",
            "@cloud-code",
            "claude-code",
            "@anthropic-ai/claude-code",
            "claude",
        ),
        exclude_keywords=(
            "google-cloud-sdk",
            "gcloud",
            "codex.app",
            "codex desktop",
            "codex computer use",
            "skycomputeruseclient",
            "extension-host",
            "chrome-extension://",
        ),
        ipc_hints=(
            "~/.cloud-code",
            "~/.claude",
        ),
        status_file_globs=(
            "~/.agent-traffic-light/cloud-code*.json",
            "~/.agent-traffic-light/claude-code*.json",
        ),
        notes="Includes Claude Code aliases because teams often use the two names interchangeably.",
    ),
)


def get_definition(agent_id: str) -> AgentDefinition | None:
    for definition in AGENT_DEFINITIONS:
        if definition.agent_id == agent_id:
            return definition
    return None
