"""
IDE-wide font control.

Tk draws every ttk widget, menu and label with *named fonts*
(``TkDefaultFont``, ``TkMenuFont``, …).  Reconfiguring those in place
restyles the whole window live — no widget needs to know.  The editor and
log use private copies of ``TkFixedFont`` (so they can be zoomed
independently); their *family* is set through the owning widgets.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from typing import Dict, List, Tuple

UI_FONTS: Tuple[str, ...] = (
    "TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont",
    "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont",
)
MIN_SIZE, MAX_SIZE = 6, 24

_defaults: Dict[str, Tuple[str, int, str, str]] = {}  # name -> (family, size, weight, slant)
_family_cache: Dict[str, List[str]] = {}


def _remember_defaults(root: tk.Misc) -> None:
    if _defaults:
        return
    for name in UI_FONTS + ("TkFixedFont",):
        try:
            f = tkfont.nametofont(name, root=root)
        except tk.TclError:  # pragma: no cover - exotic Tk build
            continue
        _defaults[name] = (f.cget("family"), int(f.cget("size")),
                           f.cget("weight"), f.cget("slant"))


def default_ui_font(root: tk.Misc) -> Tuple[str, int]:
    _remember_defaults(root)
    fam, size, _w, _s = _defaults.get("TkDefaultFont", ("TkDefaultFont", 10, "normal", "roman"))
    return fam, abs(size)


def default_mono_family(root: tk.Misc) -> str:
    _remember_defaults(root)
    return _defaults.get("TkFixedFont", ("TkFixedFont", 10, "", ""))[0]


def current_ui_font(root: tk.Misc) -> Tuple[str, int]:
    f = tkfont.nametofont("TkDefaultFont", root=root)
    return f.cget("family"), abs(int(f.cget("size")))


def apply_ui_font(root: tk.Misc, family: str = "", size: int = 0) -> Tuple[str, int]:
    """Set the UI family/size on all named UI fonts.  Empty family / size 0
    mean "platform default".  Returns what was applied."""
    _remember_defaults(root)
    def_fam, def_size = default_ui_font(root)
    family = family or def_fam
    size = int(size) if size else def_size
    size = max(MIN_SIZE, min(MAX_SIZE, size))
    for name in UI_FONTS:
        if name not in _defaults:
            continue
        _f, _s, weight, slant = _defaults[name]
        kw = {"family": family, "size": size, "weight": weight, "slant": slant}
        if name == "TkSmallCaptionFont":
            kw["size"] = max(MIN_SIZE, size - 1)
        elif name == "TkHeadingFont":
            kw["weight"] = "bold"
        try:
            tkfont.nametofont(name, root=root).configure(**kw)
        except tk.TclError:  # pragma: no cover
            pass
    return family, size


def families(root: tk.Misc) -> List[str]:
    """Sorted, de-duplicated font families available on this display."""
    key = "all"
    if key not in _family_cache:
        fams = {f for f in tkfont.families(root) if f and not f.startswith("@")}
        _family_cache[key] = sorted(fams, key=str.lower)
    return list(_family_cache[key])


def monospace_families(root: tk.Misc) -> List[str]:
    """Families whose glyphs are fixed-width (Tk's ``metrics fixed``)."""
    key = "mono"
    if key not in _family_cache:
        out = []
        for fam in families(root):
            try:
                if tkfont.Font(root=root, family=fam, size=10).metrics("fixed"):
                    out.append(fam)
            except tk.TclError:  # pragma: no cover
                continue
        _family_cache[key] = out
    return list(_family_cache[key])


def clear_cache() -> None:
    _family_cache.clear()
