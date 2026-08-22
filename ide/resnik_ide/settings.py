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
from typing import Any, Dict, Optional, Tuple, Union

from resnik_ide.options import IdeCompileSettings

SETTINGS_VERSION = 1
ENV_VAR = "RESNIK_IDE_CONFIG"


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
    version: int = SETTINGS_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": SETTINGS_VERSION,
            "geometry": self.geometry,
            "port": self.port,
            "chip": self.chip,
            "compile": self.compile.to_dict(),
        }

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
