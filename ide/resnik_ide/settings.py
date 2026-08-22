"""
Persistent IDE settings (``ide.json``).

Location: ``$RESNIK_IDE_CONFIG`` if set (tests, portable installs), else
``$XDG_CONFIG_HOME/resnik/ide.json`` / ``~/.config/resnik/ide.json``
(``%APPDATA%\\resnik\\ide.json`` on Windows).

Loading is tolerant — a missing or corrupt file yields defaults plus a
warning string the app logs — and saving is atomic (write-then-replace).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from resnik_ide.options import IdeCompileSettings

SETTINGS_VERSION = 1
ENV_VAR = "RESNIK_IDE_CONFIG"
MAX_RECENT = 10


def _int_or(val: Any, default: int, lo: int, hi: int) -> int:
    try:
        v = int(val)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def settings_path() -> Path:
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env).expanduser()
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "resnik" / "ide.json"


@dataclass
class IdeSettings:
    compile: IdeCompileSettings = field(default_factory=IdeCompileSettings)
    geometry: str = ""
    port: str = ""   # serial port for espflash
    chip: str = ""   # probe-rs --chip
    recent: List[str] = field(default_factory=list)  # most recent first
    font_size: int = 11
    log_font_size: int = 10
    ui_font_family: str = ""   # "" = platform default
    ui_font_size: int = 0      # 0 = platform default
    mono_font_family: str = "" # editor/log family; "" = TkFixedFont default
    highlighting: bool = True
    theme: str = "light"
    show_target_panel: bool = True
    log_height: int = 0  # log pane height in pixels; 0 = default split
    sash_target_panel: int = 0  # pixels from the left edge; 0 = default width
    version: int = SETTINGS_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": SETTINGS_VERSION,
            "geometry": self.geometry,
            "port": self.port,
            "chip": self.chip,
            "recent": list(self.recent),
            "font_size": self.font_size,
            "log_font_size": self.log_font_size,
            "ui_font_family": self.ui_font_family,
            "ui_font_size": self.ui_font_size,
            "mono_font_family": self.mono_font_family,
            "highlighting": self.highlighting,
            "theme": self.theme,
            "show_target_panel": self.show_target_panel,
            "log_height": self.log_height,
            "sash_target_panel": self.sash_target_panel,
            "compile": self.compile.to_dict(),
        }

    def touch_recent(self, path: str, limit: int = MAX_RECENT) -> None:
        """Move ``path`` to the front of the recent list (bounded)."""
        self.recent = [path] + [p for p in self.recent if p != path]
        del self.recent[limit:]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IdeSettings":
        if not isinstance(data, dict):
            raise ValueError("settings root must be a JSON object")
        comp = data.get("compile", {})
        return cls(
            compile=IdeCompileSettings.from_dict(comp if isinstance(comp, dict) else {}),
            geometry=str(data.get("geometry", "") or ""),
            port=str(data.get("port", "") or ""),
            chip=str(data.get("chip", "") or ""),
            recent=[str(p) for p in (data.get("recent") if isinstance(data.get("recent"), list) else [])
                    if isinstance(p, str)][:MAX_RECENT],
            font_size=_int_or(data.get("font_size"), 11, 6, 40),
            log_font_size=_int_or(data.get("log_font_size"), 10, 6, 40),
            ui_font_family=str(data.get("ui_font_family") or ""),
            ui_font_size=_int_or(data.get("ui_font_size"), 0, 0, 24),
            mono_font_family=str(data.get("mono_font_family") or ""),
            highlighting=bool(data.get("highlighting", True)),
            theme=data.get("theme") if data.get("theme") in ("light", "dark") else "light",
            show_target_panel=bool(data.get("show_target_panel", True)),
            log_height=_int_or(data.get("log_height"), 0, 0, 100000),
            sash_target_panel=_int_or(data.get("sash_target_panel"), 0, 0, 100000),
            version=int(data.get("version", SETTINGS_VERSION) or SETTINGS_VERSION),
        )


def load_settings(path: Union[str, Path, None] = None) -> Tuple[IdeSettings, Optional[str]]:
    """Return ``(settings, warning)``; ``warning`` is None when all is well."""
    p = Path(path) if path else settings_path()
    if not p.exists():
        return IdeSettings(), None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return IdeSettings.from_dict(data), None
    except (OSError, ValueError) as exc:
        return IdeSettings(), f"Could not read settings {p}: {exc} — using defaults."


def save_settings(settings: IdeSettings, path: Union[str, Path, None] = None) -> Path:
    """Atomically write ``settings``; returns the path written."""
    p = Path(path) if path else settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(settings.to_dict(), indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)
    return p
