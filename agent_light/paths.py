from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator


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
