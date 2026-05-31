from __future__ import annotations

import platform
import subprocess
import textwrap


def ask_hook_install_confirmation(
    *,
    title: str,
    body: str,
    allow_label: str = "允许写入",
    cancel_label: str = "取消",
) -> bool:
    system = platform.system().lower()
    if system == "darwin":
        result = _ask_macos(title, body, allow_label, cancel_label)
        if result is not None:
            return result
    if system == "windows":
        result = _ask_windows(title, body)
        if result is not None:
            return result
    return _ask_tk(title, body)


def _ask_macos(
    title: str,
    body: str,
    allow_label: str,
    cancel_label: str,
) -> bool | None:
    script = textwrap.dedent(
        """
        on run argv
        set dialogBody to item 1 of argv
        set dialogTitle to item 2 of argv
        set allowButton to item 3 of argv
        set cancelButton to item 4 of argv
        set dialogResult to display dialog dialogBody buttons {cancelButton, allowButton} default button allowButton cancel button cancelButton with title dialogTitle
        return button returned of dialogResult
        end run
        """
    ).strip()
    try:
        result = subprocess.run(
            ["osascript", "-e", script, body, title, allow_label, cancel_label],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return False
    return allow_label in result.stdout


def _ask_windows(title: str, body: str) -> bool | None:
    escaped_title = title.replace("'", "''")
    escaped_body = body.replace("'", "''")
    script = (
        "Add-Type -AssemblyName PresentationFramework; "
        f"$r=[System.Windows.MessageBox]::Show('{escaped_body}','{escaped_title}',"
        "'YesNo','Warning'); "
        "if ($r -eq 'Yes') { exit 0 } else { exit 1 }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.returncode == 0


def _ask_tk(title: str, body: str) -> bool:
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        return False

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass
    try:
        return bool(messagebox.askyesno(title, body, parent=root))
    finally:
        root.destroy()
