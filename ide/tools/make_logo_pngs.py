#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Steven Doran
"""Regenerate the IDE's PNG logo/icon renditions from images/resnik_logo.jpg.

Tk cannot decode JPEG, so the IDE ships PNGs (About window logo, window
icon, desktop-entry icon). Re-run this whenever images/resnik_logo.jpg
changes:

    python3 ide/tools/make_logo_pngs.py        # needs Pillow (pip install pillow)

Outputs (overwritten): ide/images/resnik_logo.png (200 px),
resnik_icon_64.png, resnik_icon_32.png.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "images" / "resnik_logo.jpg"
OUT = ROOT / "ide" / "images"
SIZES = {"resnik_logo.png": 200, "resnik_icon_64.png": 64, "resnik_icon_32.png": 32}


def main() -> int:
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is required: pip install pillow  (or use the system python3 that has it)",
              file=sys.stderr)
        return 2
    if not SRC.exists():
        print(f"source not found: {SRC}", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    im = Image.open(SRC).convert("RGBA")
    for name, size in SIZES.items():
        t = im.copy()
        t.thumbnail((size, size), Image.LANCZOS)
        t.save(OUT / name, optimize=True)
        print(f"wrote {OUT / name} ({t.size[0]}x{t.size[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
