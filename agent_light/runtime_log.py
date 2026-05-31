from __future__ import annotations

import json
import platform
import threading
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from time import time
from typing import Any
from uuid import uuid4

from .paths import ensure_app_state_dir, safe_filename_component


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


@dataclass
class RuntimeLogger:
    """Append-only JSONL log for runtime status, hook, and cleanup events."""

    log_dir: Path | None = None
    runtime_id: str | None = None
    log_path: Path | None = None

    def __post_init__(self) -> None:
        self.runtime_id = self.runtime_id or uuid4().hex
        self.log_dir = self.log_dir or ensure_app_state_dir("logs")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if self.log_path is None:
            filename = f"agent-beacon-{_utc_stamp()}-{self.runtime_id[:8]}.jsonl"
            self.log_path = self.log_dir / filename
        self._lock = threading.Lock()
        self.record(
            "runtime_started",
            app="Agent Beacon",
            platform=platform.platform(),
            runtime_id=self.runtime_id,
        )

    def record(self, event_type: str, **payload: Any) -> None:
        entry = {
            "timestamp": time(),
            "event_type": event_type,
            "runtime_id": self.runtime_id,
            "payload": _jsonable(payload),
        }
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        try:
            with self._lock:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.log_path.open("a", encoding="utf-8") as file:
                    file.write(line + "\n")
        except OSError:
            # Logging must never break the tray indicator or hook command.
            return

    @property
    def display_path(self) -> str:
        return str(self.log_path)


_LOGGER: RuntimeLogger | None = None
_LOGGER_LOCK = threading.Lock()


def get_runtime_logger() -> RuntimeLogger:
    global _LOGGER
    with _LOGGER_LOCK:
        if _LOGGER is None:
            _LOGGER = RuntimeLogger()
        return _LOGGER


def log_event(event_type: str, **payload: Any) -> None:
    get_runtime_logger().record(event_type, **payload)


def log_file_basename(path: Path | str | None) -> str:
    if path is None:
        return "unknown"
    return safe_filename_component(Path(path).name, fallback="log")
