from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass
from typing import Protocol


class Notifier(Protocol):
    def notify(self, title: str, body: str) -> None:
        """Show a native OS notification."""


@dataclass
class NativeNotifier:
    app_id: str = "Agent Traffic Light"
    timeout_seconds: float = 2.0

    def notify(self, title: str, body: str) -> None:
        system = platform.system().lower()
        if system == "darwin":
            self._notify_macos(title, body)
        elif system == "windows":
            self._notify_windows(title, body)
        else:
            self._notify_linux(title, body)

    def _notify_macos(self, title: str, body: str) -> None:
        script = (
            "on run argv\n"
            "display notification (item 2 of argv) with title (item 1 of argv)\n"
            "end run"
        )
        subprocess.run(
            ["osascript", "-e", script, title, body],
            timeout=self.timeout_seconds,
            check=False,
        )

    def _notify_windows(self, title: str, body: str) -> None:
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            return
        script = r"""
param([string]$Title, [string]$Body, [string]$AppId)
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$escapedTitle = [System.Security.SecurityElement]::Escape($Title)
$escapedBody = [System.Security.SecurityElement]::Escape($Body)
$template = "<toast><visual><binding template=`"ToastGeneric`"><text>$escapedTitle</text><text>$escapedBody</text></binding></visual></toast>"
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($AppId).Show($toast)
"""
        subprocess.run(
            [
                shell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
                title,
                body,
                self.app_id,
            ],
            timeout=self.timeout_seconds,
            check=False,
        )

    def _notify_linux(self, title: str, body: str) -> None:
        notify_send = shutil.which("notify-send")
        if not notify_send:
            return
        subprocess.run(
            [notify_send, title, body],
            timeout=self.timeout_seconds,
            check=False,
        )


@dataclass
class NullNotifier:
    def notify(self, title: str, body: str) -> None:
        return None
