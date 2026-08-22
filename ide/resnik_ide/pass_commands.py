"""
PASS-style keyboard commands for the log pane's command line.

The Shuttle crew drove the Primary Avionics Software System with a small
keyboard vocabulary — ``OPS nnn PRO`` (change major mode), ``SPEC nn PRO``
(display a specialist page), ``ITEM n EXEC`` (execute an item on that page)
— and the GPCs answered an unknown entry with ``ILLEGAL ENTRY``.  The IDE
borrows that vocabulary for a keyboard-only way to drive itself
(see ``docs/Space_Shuttle_Flight_Software_History/PASS-Crew-Input-Processing.md``).

``parse(text)`` is pure; ``Dispatcher``-free so it can be unit-tested.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

ILLEGAL_ENTRY = "ILLEGAL ENTRY"

# action keys the app implements
SPEC_PAGES = {
    1: ("toolchain", "SPEC 1  — toolchain status (GPC / rustc / cargo / targets)"),
    2: ("options", "SPEC 2  — current compile options (equivalent command)"),
    3: ("devices", "SPEC 3  — connected devices (serial ports)"),
    67: ("sts1", "SPEC 67 — STS-1: the bug heard 'round the world"),
}
ITEMS = {
    1: ("semantic_check", "ITEM 1 EXEC — Semantic Check"),
    2: ("compile", "ITEM 2 EXEC — Compile"),
    3: ("build_and_run", "ITEM 3 EXEC — Build and Run"),
    4: ("stop", "ITEM 4 EXEC — Stop the running process"),
    5: ("clear_log", "ITEM 5 EXEC — Clear the log"),
}
OPS_MODES = {
    9: ("about", "OPS 9 PRO — About Resnik"),
    0: ("help", "OPS 0 PRO — this list"),
}

STS1_TEXT = (
    "STS-1, 10 April 1981: the Backup Flight System refused to synchronize with "
    "the four PASS GPCs — a 1-in-67 timing window at power-up. Jack Garman called "
    "it \"the bug heard 'round the world\". Columbia launched two days later. "
    "docs/Space_Shuttle_Flight_Software_History/STS-1-The-Bug-Heard-Round-The-World.md"
)


@dataclass(frozen=True)
class Command:
    kind: str          # "spec" | "item" | "ops" | "help" | "illegal"
    number: Optional[int]
    action: Optional[str]
    echo: str          # normalized text to echo in the log


_PAT = re.compile(
    r"^\s*(?:(?P<kw>SPEC|ITEM|OPS)\s+(?P<num>\d{1,3})\s*(?P<suffix>PRO|EXEC)?|(?P<help>HELP|\?))\s*$",
    re.IGNORECASE,
)


def parse(text: str) -> Command:
    m = _PAT.match(text or "")
    if not m:
        return Command("illegal", None, None, ILLEGAL_ENTRY)
    if m.group("help"):
        return Command("help", None, "help", "HELP")
    kw = m.group("kw").upper()
    num = int(m.group("num"))
    suffix = (m.group("suffix") or "").upper()
    if kw == "SPEC" and num in SPEC_PAGES and suffix in ("", "PRO"):
        return Command("spec", num, SPEC_PAGES[num][0], f"SPEC {num} PRO")
    if kw == "ITEM" and num in ITEMS and suffix in ("", "EXEC"):
        return Command("item", num, ITEMS[num][0], f"ITEM {num} EXEC")
    if kw == "OPS" and num in OPS_MODES and suffix in ("", "PRO"):
        return Command("ops", num, OPS_MODES[num][0], f"OPS {num} PRO")
    return Command("illegal", num, None, ILLEGAL_ENTRY)


def help_lines() -> list:
    out = ["PASS keyboard — OPS nnn PRO · SPEC nn PRO · ITEM n EXEC  (HELP or ? for this list)"]
    out += [v[1] for v in OPS_MODES.values()]
    out += [v[1] for v in SPEC_PAGES.values()]
    out += [v[1] for v in ITEMS.values()]
    return out
