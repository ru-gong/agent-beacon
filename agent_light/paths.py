from __future__ import annotations

import os
import platform
import re
from collections.abc import Iterable, Iterator
from pathlib import Path


_WINDOWS_ENV_RE = re.compile(r"%([^%]+)%")
_UNRESOLVED_ENV_RE = re.compile(
    r"(\$[A-Za-z_][A-Za-z0-9_]*|\$\{[^}]+\}|%[^%]+%)"
)


def expand_path_pattern(pattern: str) -> str | None:
    """Expand cross-platform path variables in a glob pattern.

    Supports POSIX/user syntax (`~`, `$HOME`, `${HOME}`) and Windows syntax
    (`%APPDATA%`) regardless of the platform running the test suite.
    """

    unresolved = False

    def replace_windows_env(match: re.Match[str]) -> str:
        nonlocal unresolved
        value = os.environ.get(match.group(1))
        if value is None:
            unresolved = True
            return match.group(0)
        return value

    expanded = _WINDOWS_ENV_RE.sub(replace_windows_env, pattern)
    expanded = os.path.expandvars(expanded)
    expanded = os.path.expanduser(expanded)

    if unresolved or _UNRESOLVED_ENV_RE.search(expanded):
        return None
    return expanded


def expand_path_patterns(patterns: Iterable[str]) -> Iterator[str]:
    seen: set[str] = set()
    for pattern in patterns:
        expanded = expand_path_pattern(pattern)
        if expanded is None or expanded in seen:
            continue
        seen.add(expanded)
        yield expanded


def app_state_dir(app_name: str = "Agent Beacon") -> Path:
    override = os.environ.get("AGENT_BEACON_STATE_DIR")
    if override:
        return Path(override).expanduser()

    system = platform.system().lower()
    if system == "windows":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / app_name
        return Path.home() / "AppData" / "Local" / app_name
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name

    base = os.environ.get("XDG_STATE_HOME")
    if base:
        return Path(base) / "agent-beacon"
    return Path.home() / ".local" / "state" / "agent-beacon"


def ensure_app_state_dir(*parts: str) -> Path:
    path = app_state_dir().joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename_component(value: str | None, fallback: str = "default") -> str:
    source = value or fallback
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", source).strip(".-")
    return cleaned[:96] or fallback
