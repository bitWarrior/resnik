# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Steven Doran
"""
Space Shuttle flavour: the startup banner and date-gated remembrances.

Pure functions (dates injected) so they are unit-testable; the app owns the
widgets.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional, Tuple

# (month, day) -> (status-bar text, one-line log text)
REMEMBRANCES = {
    (1, 28): ("In memory of the crew of STS-51-L",
              "28 January 1986 — Challenger, STS-51-L. In memory of Dick Scobee, Michael Smith, "
              "Judith Resnik, Ellison Onizuka, Ronald McNair, Gregory Jarvis and Christa McAuliffe."),
    (2, 1): ("In memory of the crew of STS-107",
             "1 February 2003 — Columbia, STS-107. In memory of Rick Husband, William McCool, "
             "Michael Anderson, Kalpana Chawla, David Brown, Laurel Clark and Ilan Ramon."),
    (4, 12): ("HAIL Columbia!",
              "12 April 1981 — STS-1: Columbia's first flight, the first Shuttle launch, flown on "
              "HAL/S. HAIL Columbia!"),
    (4, 4): ("HAIL Challenger!",
             "4 April 1983 — STS-6: Challenger's first flight. HAIL Challenger!"),
    (8, 30): ("HAIL Discovery!",
              "30 August 1984 — STS-41-D: Discovery's first flight, with mission specialist "
              "Judith Resnik aboard. HAIL Discovery!"),
    (10, 3): ("HAIL Atlantis!",
              "3 October 1985 — STS-51-J: Atlantis's first flight. HAIL Atlantis!"),
    (5, 7): ("HAIL Endeavour!",
             "7 May 1992 — STS-49: Endeavour's first flight. HAIL Endeavour!"),
    (10, 18): ("GO Galileo!",
               "18 October 1989 — Galileo launched to Jupiter aboard Atlantis (STS-34); its "
               "Attitude and Articulation Control Subsystem flight software was written in "
               "HAL/S. GO Galileo!"),
}


def remembrance_for(day: Optional[_dt.date] = None) -> Optional[Tuple[str, str]]:
    """``(status text, log text)`` for today's date, or None on any other day."""
    d = day or _dt.date.today()
    return REMEMBRANCES.get((d.month, d.day))


def startup_banner(gpc_count: int = 1) -> str:
    """The GPC-style startup line: PASS came up in OPS 0 on a voting set of
    General Purpose Computers; we have rather fewer."""
    return (f"PASS STARTUP ✓  GPC 1 ONLINE · REDUNDANT SET {gpc_count} OF {gpc_count} "
            f"· OPS 0 PRO · MET 000:00:00:00")
