# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Steven Doran
"""shuttle.py — startup banner and date-gated remembrances (pure)."""

import datetime as dt

from resnik_ide import shuttle


class TestRemembrance:
    def test_dates(self):
        assert shuttle.remembrance_for(dt.date(2026, 1, 28))[0] == "In memory of the crew of STS-51-L"
        assert shuttle.remembrance_for(dt.date(2026, 2, 1))[0] == "In memory of the crew of STS-107"
        assert shuttle.remembrance_for(dt.date(2026, 4, 12))[0] == "HAIL Columbia!"
        assert shuttle.remembrance_for(dt.date(2026, 4, 4))[0] == "HAIL Challenger!"
        assert shuttle.remembrance_for(dt.date(2026, 8, 30))[0] == "HAIL Discovery!"
        assert shuttle.remembrance_for(dt.date(2026, 10, 3))[0] == "HAIL Atlantis!"
        assert shuttle.remembrance_for(dt.date(2026, 5, 7))[0] == "HAIL Endeavour!"
        assert shuttle.remembrance_for(dt.date(2026, 10, 18))[0] == "GO Galileo!"
        assert shuttle.remembrance_for(dt.date(2026, 8, 21)) is None
        assert shuttle.remembrance_for(dt.date(1999, 12, 31)) is None

    def test_log_text_names_the_missions(self):
        assert "Challenger" in shuttle.remembrance_for(dt.date(2000, 1, 28))[1]
        assert "STS-107" in shuttle.remembrance_for(dt.date(2000, 2, 1))[1]
        assert "STS-1" in shuttle.remembrance_for(dt.date(2000, 4, 12))[1]
        assert "STS-6" in shuttle.remembrance_for(dt.date(2000, 4, 4))[1]
        assert "Judith Resnik" in shuttle.remembrance_for(dt.date(2000, 8, 30))[1]
        assert "STS-51-J" in shuttle.remembrance_for(dt.date(2000, 10, 3))[1]
        assert "STS-49" in shuttle.remembrance_for(dt.date(2000, 5, 7))[1]
        assert "HAL/S" in shuttle.remembrance_for(dt.date(2000, 10, 18))[1]

    def test_banner(self):
        b = shuttle.startup_banner()
        assert b.startswith("PASS STARTUP") and "GPC 1 ONLINE" in b and "OPS 0 PRO" in b
        assert "REDUNDANT SET 1 OF 1" in b
