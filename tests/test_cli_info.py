# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Steven Doran
"""``resnik info`` describes the compiler as it actually ships."""

from click.testing import CliRunner

from resnik import __version__
from resnik.cli import main


def test_info_lists_all_three_stages_as_shipped():
    result = CliRunner().invoke(main, ["info"])
    assert result.exit_code == 0
    out = result.output
    assert __version__ in out
    for stage in ("Lexer", "Parser", "Backend"):
        assert f"✓ {stage}" in out
    # The back end shipped in 1.0.0; the panel must not call it future work.
    assert "Planned" not in out
    assert "Foundation" not in out
