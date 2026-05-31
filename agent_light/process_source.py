from __future__ import annotations

import csv
import platform
import shutil
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from .models import ProcessInfo


class ProcessSource(Protocol):
    def snapshot(self) -> Sequence[ProcessInfo]:
        """Return a point-in-time process snapshot."""


@dataclass
class PsutilProcessSource:
    """psutil-backed source with cross-platform process metadata."""

    def snapshot(self) -> Sequence[ProcessInfo]:
        import psutil

        processes: list[ProcessInfo] = []
        attrs = [
            "pid",
            "ppid",
            "name",
            "cmdline",
            "status",
            "cpu_percent",
            "create_time",
        ]
        try:
            iterator = psutil.process_iter(attrs=attrs, ad_value=None)
            for proc in iterator:
                info = proc.info
                try:
                    cmdline = tuple(str(part) for part in (info.get("cmdline") or ()))
                    processes.append(
                        ProcessInfo(
                            pid=int(info["pid"]),
                            name=str(info.get("name") or ""),
                            ppid=(
                                int(info["ppid"])
                                if info.get("ppid") is not None
                                else None
                            ),
                            cmdline=cmdline,
                            status=str(info.get("status") or "") or None,
                            cpu_percent=(
                                float(info["cpu_percent"])
                                if info.get("cpu_percent") is not None
                                else None
                            ),
                            create_time=(
                                float(info["create_time"])
                                if info.get("create_time") is not None
                                else None
                            ),
                        )
                    )
                except (psutil.Error, ValueError, TypeError, KeyError):
                    continue
        except (psutil.Error, PermissionError, OSError):
            return SubprocessProcessSource(timeout_seconds=0.75).snapshot()
        return processes


@dataclass
class SubprocessProcessSource:
    """Dependency-free fallback for systems without psutil."""

    timeout_seconds: float = 1.0

    def snapshot(self) -> Sequence[ProcessInfo]:
        if platform.system().lower() == "windows":
            return self._snapshot_windows()
        return self._snapshot_posix()

    def _snapshot_posix(self) -> Sequence[ProcessInfo]:
        try:
            result = subprocess.run(
                ["ps", "-axww", "-o", "pid=,ppid=,command="],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        processes: list[ProcessInfo] = []
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            try:
                pid = int(parts[0])
                ppid = int(parts[1])
            except ValueError:
                continue
            command = parts[2]
            try:
                command_parts = shlex.split(command)
            except ValueError:
                command_parts = command.split()
            executable = command_parts[0] if command_parts else command
            name = Path(executable).name
            processes.append(ProcessInfo(pid=pid, name=name, ppid=ppid, cmdline=(command,)))
        return processes

    def _snapshot_windows(self) -> Sequence[ProcessInfo]:
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            return []
        script = (
            "Get-CimInstance Win32_Process | "
            "Select-Object ProcessId,ParentProcessId,Name,CommandLine | "
            "ConvertTo-Csv -NoTypeInformation"
        )
        try:
            result = subprocess.run(
                [shell, "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        processes: list[ProcessInfo] = []
        reader = csv.DictReader(result.stdout.splitlines())
        for row in reader:
            try:
                pid = int(row.get("ProcessId") or "")
            except ValueError:
                continue
            try:
                ppid = int(row.get("ParentProcessId") or "")
            except ValueError:
                ppid = None
            processes.append(
                ProcessInfo(
                    pid=pid,
                    name=row.get("Name") or "",
                    ppid=ppid,
                    cmdline=(row.get("CommandLine") or "",),
                )
            )
        return processes


def build_default_process_source() -> ProcessSource:
    try:
        import psutil  # noqa: F401
    except ImportError:
        return SubprocessProcessSource()
    return PsutilProcessSource()
