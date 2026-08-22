"""
Desktop integration (freedesktop ``.desktop`` entry).

GNOME Shell (and most Wayland desktops) pick the dash / Alt-Tab icon by
matching a window's ``WM_CLASS`` to an installed ``.desktop`` entry's
``StartupWMClass``; the X11 ``_NET_WM_ICON`` the IDE also sets is ignored
there.  Installing this entry gives the IDE its logo as the application
icon and a launcher in the applications menu.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

WM_CLASS = "resnik-ide"           # Tk className/baseName (the WM_CLASS *instance*)
# Tk capitalises the class part of WM_CLASS ("resnik-ide", "Resnik-ide").
# GNOME matches StartupWMClass against both, and also lowercases the class
# to look for "<class>.desktop" — so "resnik-ide.desktop" matches either way.
STARTUP_WM_CLASS = WM_CLASS[:1].upper() + WM_CLASS[1:]
ENTRY_NAME = "resnik-ide.desktop"

IMAGES_DIR = Path(__file__).resolve().parent.parent / "images"
ICON_PNG = IMAGES_DIR / "resnik_logo.png"


def applications_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "applications"


def entry_path(directory: Optional[Path] = None) -> Path:
    return (Path(directory) if directory else applications_dir()) / ENTRY_NAME


def default_exec() -> str:
    """Command GNOME runs for the launcher: the venv's resnik-ide script if
    it exists, else ``<python> -m resnik_ide``."""
    script = Path(sys.executable).parent / "resnik-ide"
    if script.exists():
        return f'"{script}" %F'
    return f'"{sys.executable}" -m resnik_ide %F'


def desktop_entry_text(exec_cmd: Optional[str] = None, icon: Optional[Path] = None) -> str:
    exec_cmd = exec_cmd or default_exec()
    icon = Path(icon) if icon else ICON_PNG
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        "Name=Resnik IDE\n"
        "GenericName=HAL/S IDE\n"
        "Comment=Edit, check, compile and run HAL/S programs with the Resnik compiler\n"
        f"Exec={exec_cmd}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=Development;IDE;\n"
        "Keywords=HAL/S;HAL;Shuttle;compiler;Rust;\n"
        f"StartupWMClass={STARTUP_WM_CLASS}\n"
        "MimeType=text/plain;\n"
    )


def install_desktop_entry(directory: Optional[Path] = None, *,
                          exec_cmd: Optional[str] = None, icon: Optional[Path] = None,
                          refresh: bool = True) -> Path:
    """Write the entry (creating the directory) and refresh the desktop
    database if the tool exists.  Returns the path written."""
    path = entry_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(desktop_entry_text(exec_cmd, icon), encoding="utf-8")
    if refresh:
        _refresh(path.parent)
    return path


def uninstall_desktop_entry(directory: Optional[Path] = None, *, refresh: bool = True) -> bool:
    path = entry_path(directory)
    if not path.exists():
        return False
    path.unlink()
    if refresh:
        _refresh(path.parent)
    return True


def is_installed(directory: Optional[Path] = None) -> bool:
    return entry_path(directory).exists()


def _refresh(directory: Path) -> None:
    exe = shutil.which("update-desktop-database")
    if exe:
        try:
            subprocess.run([exe, str(directory)], capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            pass
