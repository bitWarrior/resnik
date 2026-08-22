"""
Connected-device discovery for the Target panel.

Serial ports: ``pyserial`` (``serial.tools.list_ports``) when it happens to
be installed, else a glob over the usual device names.  Debug probes:
``probe-rs list`` when probe-rs is on PATH.  Everything is injectable for
tests and never raises.
"""

from __future__ import annotations

import glob as _glob
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

POSIX_PATTERNS = ("/dev/ttyACM*", "/dev/ttyUSB*", "/dev/cu.usbmodem*", "/dev/cu.usbserial*",
                  "/dev/cu.SLAB_USBtoUART*", "/dev/cu.wchusbserial*")


@dataclass(frozen=True)
class SerialPort:
    device: str
    description: str = ""

    def __str__(self) -> str:
        return f"{self.device} — {self.description}" if self.description else self.device


def _pyserial_ports() -> Optional[List[SerialPort]]:
    try:
        from serial.tools import list_ports  # type: ignore
    except Exception:
        return None
    try:
        return [SerialPort(p.device, (p.description or "").strip()) for p in list_ports.comports()]
    except Exception:
        return None


def list_serial_ports(
    *,
    globber: Callable[[str], List[str]] = _glob.glob,
    patterns: Sequence[str] = POSIX_PATTERNS,
    use_pyserial: bool = True,
    platform: str = sys.platform,
) -> List[SerialPort]:
    """Best-effort list of serial ports, most-likely-dev-board first."""
    if use_pyserial:
        found = _pyserial_ports()
        if found:
            return sorted(found, key=lambda p: p.device)
    if platform.startswith("win"):
        # No registry walking in v1: offer COM1..COM16 candidates.
        return [SerialPort(f"COM{i}") for i in range(1, 17)]
    devs: List[str] = []
    for pat in patterns:
        devs.extend(globber(pat))
    return [SerialPort(d) for d in sorted(set(devs))]


def list_probes(
    *,
    which: Callable[[str], Optional[str]] = shutil.which,
    run=subprocess.run,
) -> List[str]:
    """Lines from ``probe-rs list`` (empty if probe-rs is missing / fails)."""
    exe = which("probe-rs")
    if not exe:
        return []
    try:
        cp = run([exe, "list"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln.strip() for ln in (cp.stdout or "").splitlines()
            if ln.strip() and not ln.lower().startswith("no debug probes")]
