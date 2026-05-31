from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from time import time
from typing import Any, Literal
from uuid import uuid4

from .paths import ensure_app_state_dir
from .runtime_log import RuntimeLogger, get_runtime_logger, log_file_basename


AGENT_BEACON_MARKER = "agent-beacon-managed"
MARKER_BLOCK_RE = re.compile(
    r"(?m)^[^\n]*agent-beacon-managed:start[^\n]*\n"
    r"(?:.*\n)*?"
    r"^[^\n]*agent-beacon-managed:end[^\n]*(?:\n|$)"
)
CleanupStrategy = Literal["auto", "json_managed_entries", "marker_block", "delete_file"]


@dataclass(frozen=True)
class HookFileRecord:
    path: str
    created_by_agent_beacon: bool = False
    cleanup_strategy: CleanupStrategy = "auto"


@dataclass(frozen=True)
class HookRegistration:
    agent_id: str
    project_root: str
    files: tuple[HookFileRecord, ...]
    session_id: str | None = None
    monitor_id: str | None = None
    registration_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: float = field(default_factory=time)
    note: str = ""


@dataclass(frozen=True)
class HookCleanupResult:
    registrations: int
    touched_files: int
    removed_files: int
    skipped_files: int
    messages: tuple[str, ...] = ()


class HookRegistry:
    """Tracks Agent Beacon-managed hook edits so cleanup never guesses."""

    def __init__(
        self,
        manifest_path: Path | None = None,
        logger: RuntimeLogger | None = None,
    ) -> None:
        self.manifest_path = manifest_path or ensure_app_state_dir("hooks") / "manifest.json"
        self.logger = logger or get_runtime_logger()
        self._lock = threading.Lock()

    def register(self, registration: HookRegistration) -> None:
        with self._lock:
            registrations = self._read_manifest()
            registrations.append(_registration_to_json(registration))
            self._write_manifest(registrations)
        self.logger.record(
            "hook_registered",
            registration_id=registration.registration_id,
            agent_id=registration.agent_id,
            session_id=registration.session_id,
            monitor_id=registration.monitor_id,
            has_project_root=bool(registration.project_root),
            files=_file_records_log_payload(registration.files),
            note=registration.note,
        )

    def registration_count(self) -> int:
        return len(self._read_manifest())

    def cleanup_all(self) -> HookCleanupResult:
        with self._lock:
            registrations = self._read_manifest()
            touched_files = 0
            removed_files = 0
            skipped_files = 0
            messages: list[str] = []

            for registration in registrations:
                for file_record in _files_from_registration(registration):
                    result = self._cleanup_file(file_record)
                    touched_files += int(result == "touched")
                    removed_files += int(result == "removed")
                    skipped_files += int(result == "skipped")
                    messages.append(f"{result}: {file_record.path}")

            self._write_manifest([])

        cleanup_result = HookCleanupResult(
            registrations=len(registrations),
            touched_files=touched_files,
            removed_files=removed_files,
            skipped_files=skipped_files,
            messages=tuple(messages),
        )
        self.logger.record(
            "hook_cleanup_all",
            registrations=cleanup_result.registrations,
            touched_files=cleanup_result.touched_files,
            removed_files=cleanup_result.removed_files,
            skipped_files=cleanup_result.skipped_files,
        )
        return cleanup_result

    def _cleanup_file(self, file_record: HookFileRecord) -> str:
        path = Path(file_record.path).expanduser()
        if not path.exists():
            return "skipped"
        if file_record.cleanup_strategy == "delete_file":
            if file_record.created_by_agent_beacon:
                try:
                    path.unlink()
                    _remove_empty_parents(path.parent)
                    return "removed"
                except OSError:
                    return "skipped"
            return "skipped"

        try:
            original_text = path.read_text(encoding="utf-8")
        except OSError:
            return "skipped"

        updated_text: str | None = None
        strategy = file_record.cleanup_strategy
        if strategy in {"auto", "marker_block"} and AGENT_BEACON_MARKER in original_text:
            updated_text = MARKER_BLOCK_RE.sub("", original_text)

        if strategy in {"auto", "json_managed_entries"} and (
            updated_text is None or updated_text == original_text
        ):
            updated_text = _remove_json_managed_entries(original_text)

        if updated_text is None or updated_text == original_text:
            return "skipped"

        if file_record.created_by_agent_beacon and _json_or_text_is_empty(updated_text):
            try:
                path.unlink()
                _remove_empty_parents(path.parent)
                return "removed"
            except OSError:
                return "skipped"

        try:
            path.write_text(updated_text, encoding="utf-8")
        except OSError:
            return "skipped"
        return "touched"

    def _read_manifest(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if isinstance(payload, dict) and isinstance(payload.get("registrations"), list):
            return [item for item in payload["registrations"] if isinstance(item, dict)]
        return []

    def _write_manifest(self, registrations: list[dict[str, Any]]) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "managed_by": "Agent Beacon",
            "registrations": registrations,
        }
        self.manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _registration_to_json(registration: HookRegistration) -> dict[str, Any]:
    return {
        "registration_id": registration.registration_id,
        "agent_id": registration.agent_id,
        "session_id": registration.session_id,
        "monitor_id": registration.monitor_id,
        "project_root": registration.project_root,
        "created_at": registration.created_at,
        "note": registration.note,
        "files": [
            {
                "path": file_record.path,
                "created_by_agent_beacon": file_record.created_by_agent_beacon,
                "cleanup_strategy": file_record.cleanup_strategy,
            }
            for file_record in registration.files
        ],
    }


def _file_records_log_payload(files: tuple[HookFileRecord, ...]) -> list[dict[str, Any]]:
    return [
        {
            "file": log_file_basename(file_record.path),
            "created_by_agent_beacon": file_record.created_by_agent_beacon,
            "cleanup_strategy": file_record.cleanup_strategy,
        }
        for file_record in files
    ]


def _files_from_registration(registration: dict[str, Any]) -> tuple[HookFileRecord, ...]:
    records: list[HookFileRecord] = []
    for item in registration.get("files") or ():
        if not isinstance(item, dict) or not item.get("path"):
            continue
        strategy = item.get("cleanup_strategy") or "auto"
        if strategy not in {"auto", "json_managed_entries", "marker_block", "delete_file"}:
            strategy = "auto"
        records.append(
            HookFileRecord(
                path=str(item["path"]),
                created_by_agent_beacon=bool(item.get("created_by_agent_beacon")),
                cleanup_strategy=strategy,
            )
        )
    return tuple(records)


def _is_managed_json_item(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("agent_beacon_managed") is True:
        return True
    if value.get("managed_by") == "Agent Beacon":
        return True
    for key in ("description", "statusMessage", "command", "commandWindows", "command_windows"):
        item = value.get(key)
        if isinstance(item, str) and AGENT_BEACON_MARKER in item:
            return True
    args = value.get("args")
    if isinstance(args, list) and any(AGENT_BEACON_MARKER in str(arg) for arg in args):
        return True
    return False


def _clean_json(value: Any) -> tuple[Any, bool]:
    if isinstance(value, list):
        changed = False
        cleaned: list[Any] = []
        for item in value:
            if _is_managed_json_item(item):
                changed = True
                continue
            new_item, item_changed = _clean_json(item)
            changed = changed or item_changed
            if item_changed and new_item is None:
                continue
            cleaned.append(new_item)
        return cleaned, changed
    if isinstance(value, dict):
        if _is_managed_json_item(value):
            return None, True
        changed = False
        cleaned_dict: dict[str, Any] = {}
        for key, item in value.items():
            new_item, item_changed = _clean_json(item)
            changed = changed or item_changed
            if new_item in ({}, []) and key != "hooks":
                changed = True
                continue
            cleaned_dict[key] = new_item
        if (
            isinstance(cleaned_dict.get("hooks"), list)
            and not cleaned_dict["hooks"]
            and set(cleaned_dict).issubset({"hooks", "matcher"})
        ):
            return None, True
        return cleaned_dict, changed
    return value, False


def _remove_json_managed_entries(text: str) -> str | None:
    try:
        payload = json.loads(text or "{}")
    except json.JSONDecodeError:
        return None
    cleaned, changed = _clean_json(payload)
    if not changed:
        return None
    return json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n"


def _json_or_text_is_empty(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return False
    if payload in ({}, [], None):
        return True
    if isinstance(payload, dict):
        hooks = payload.get("hooks")
        return set(payload).issubset({"hooks"}) and hooks in ({}, [], None)
    return False


def _remove_empty_parents(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        return
