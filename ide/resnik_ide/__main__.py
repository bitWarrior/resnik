# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Steven Doran
"""Allow ``python -m resnik_ide``."""

from resnik_ide.app import main

if __name__ == "__main__":
    raise SystemExit(main())
