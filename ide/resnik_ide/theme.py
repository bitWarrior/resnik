"""
Light / dark themes.

A *palette* is a flat dict of named colours.  ``apply(root, name)`` restyles
ttk (the ``clam`` theme is the only built-in one whose colours are fully
configurable, so dark mode uses it; light mode restores the platform
default theme) and records the current palette so widgets created later
(dialogs, viewers) can ask for it with ``palette()``.  Plain-Tk widgets that
already exist (editor text, gutter, log, menus) are restyled by their owners
via ``apply_palette`` methods.

The Tk file dialog (Open / Save As) draws its file list as a canvas whose
background and item colour come from the ``Treeview`` ttk style; clam's
defaults are a white window with (after we restyle ``.``) light text, so
dark mode must restyle Treeview too.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Dict, List, Optional

Palette = Dict[str, str]

LIGHT: Palette = {
    "name": "light",
    "bg": "#f0f0f0", "fg": "#000000", "panel_bg": "#e4e4e4", "border": "#9e9e9e",
    "field_bg": "#ffffff", "field_fg": "#000000", "select_bg": "#3875d7", "select_fg": "#ffffff",
    "button_bg": "#e6e6e6", "button_hover": "#d9d9d9", "muted": "#666666", "accent": "#5a32a3",
    "ok": "#1a7f37", "warn": "#9a6700", "error": "#c42b1c", "info": "#1f4e79", "cmd": "#5a32a3",
    "raw": "#444444", "ts": "#888888", "link": "#c42b1c",
    "editor_bg": "#ffffff", "editor_fg": "#000000", "caret": "#000000",
    "viewer_bg": "#f7f7f2",  # read-only generated-code viewer: a shade off the editor
    "gutter_bg": "#f0f0f0", "gutter_fg": "#777777", "gutter_err": "#c42b1c",
    "current_line": "#f5f7ff", "error_line": "#ffd6d6",
    "log_bg": "#ffffff", "log_fg": "#000000",
    "hl_keyword": "#0b3d91", "hl_type": "#7a2d8c", "hl_string": "#a31515", "hl_number": "#098658",
    "hl_comment": "#6a737d", "hl_label": "#795e26", "hl_directive": "#6a737d", "hl_macro": "#0550ae",
}

DARK: Palette = {
    "name": "dark",
    "bg": "#1f1f1f", "fg": "#d4d4d4", "panel_bg": "#2a2a2a", "border": "#3c3c3c",
    "field_bg": "#2b2b2b", "field_fg": "#e0e0e0", "select_bg": "#264f78", "select_fg": "#ffffff",
    "button_bg": "#333333", "button_hover": "#3f3f3f", "muted": "#9a9a9a", "accent": "#c4a5ff",
    "ok": "#4ec9b0", "warn": "#dcdcaa", "error": "#f48771", "info": "#9cdcfe", "cmd": "#c586c0",
    "raw": "#bdbdbd", "ts": "#7a7a7a", "link": "#f48771",
    "editor_bg": "#1e1e1e", "editor_fg": "#d4d4d4", "caret": "#ffffff",
    "viewer_bg": "#232327",
    "gutter_bg": "#252526", "gutter_fg": "#858585", "gutter_err": "#f48771",
    "current_line": "#2a2d2e", "error_line": "#5a1d1d",
    "log_bg": "#181818", "log_fg": "#d4d4d4",
    "hl_keyword": "#569cd6", "hl_type": "#c586c0", "hl_string": "#ce9178", "hl_number": "#b5cea8",
    "hl_comment": "#6a9955", "hl_label": "#dcdcaa", "hl_directive": "#808080", "hl_macro": "#4fc1ff",
}

PALETTES: Dict[str, Palette] = {"light": LIGHT, "dark": DARK}
THEME_NAMES = tuple(PALETTES)

_current: Palette = LIGHT
_original_ttk_theme: Optional[str] = None


def palette(name: Optional[str] = None) -> Palette:
    """The palette for ``name``, or the currently applied one."""
    if name is None:
        return _current
    return PALETTES.get(name, LIGHT)


def current_name() -> str:
    return _current["name"]


def is_dark() -> bool:
    return _current["name"] == "dark"


def apply(root: tk.Misc, name: str) -> Palette:
    """Restyle ttk + root for theme ``name`` and make it current."""
    global _current, _original_ttk_theme
    p = palette(name)
    _current = p
    style = ttk.Style(root)
    if _original_ttk_theme is None:
        _original_ttk_theme = style.theme_use()
    if p["name"] == "dark":
        style.theme_use("clam")
        style.configure(".", background=p["bg"], foreground=p["fg"],
                        fieldbackground=p["field_bg"], bordercolor=p["border"],
                        lightcolor=p["bg"], darkcolor=p["bg"], troughcolor=p["panel_bg"],
                        selectbackground=p["select_bg"], selectforeground=p["select_fg"],
                        insertcolor=p["fg"], focuscolor=p["select_bg"])
        # clam pads buttons more generously than the default theme; keep the
        # same vertical footprint so the Target panel fits as in light mode.
        style.configure("TButton", background=p["button_bg"], foreground=p["fg"],
                        bordercolor=p["border"], lightcolor=p["button_bg"], darkcolor=p["button_bg"],
                        padding=(6, 1))
        style.map("TButton",
                  background=[("disabled", p["panel_bg"]), ("pressed", p["select_bg"]),
                              ("active", p["button_hover"])],
                  foreground=[("disabled", p["muted"])])
        style.configure("TCheckbutton", background=p["bg"], foreground=p["fg"],
                        indicatorbackground=p["field_bg"], indicatorforeground=p["fg"])
        style.map("TCheckbutton", background=[("active", p["bg"])],
                  indicatorbackground=[("selected", p["select_bg"])])
        style.configure("TLabelframe", background=p["bg"], bordercolor=p["border"],
                        lightcolor=p["bg"], darkcolor=p["bg"], padding=2)
        style.configure("TLabelframe.Label", background=p["bg"], foreground=p["fg"])
        style.configure("TNotebook", background=p["bg"], bordercolor=p["border"],
                        lightcolor=p["bg"], darkcolor=p["bg"])
        style.configure("TNotebook.Tab", background=p["panel_bg"], foreground=p["fg"],
                        bordercolor=p["border"], lightcolor=p["panel_bg"], padding=(8, 2))
        style.map("TNotebook.Tab", background=[("selected", p["bg"])],
                  foreground=[("selected", p["fg"])])
        style.configure("TCombobox", fieldbackground=p["field_bg"], background=p["button_bg"],
                        foreground=p["field_fg"], arrowcolor=p["fg"], bordercolor=p["border"],
                        lightcolor=p["field_bg"], darkcolor=p["field_bg"])
        style.map("TCombobox",
                  fieldbackground=[("readonly", p["field_bg"]), ("disabled", p["panel_bg"])],
                  foreground=[("readonly", p["field_fg"]), ("disabled", p["muted"])],
                  selectbackground=[("readonly", p["field_bg"])],
                  selectforeground=[("readonly", p["field_fg"])],
                  background=[("active", p["button_hover"])])
        style.configure("TEntry", fieldbackground=p["field_bg"], foreground=p["field_fg"],
                        insertcolor=p["fg"], bordercolor=p["border"],
                        lightcolor=p["field_bg"], darkcolor=p["field_bg"])
        style.map("TEntry", fieldbackground=[("readonly", p["panel_bg"]), ("disabled", p["panel_bg"])],
                  foreground=[("readonly", p["fg"])])
        for sb in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
            style.configure(sb, background=p["button_bg"], troughcolor=p["panel_bg"],
                            arrowcolor=p["fg"], bordercolor=p["border"],
                            lightcolor=p["button_bg"], darkcolor=p["button_bg"])
            style.map(sb, background=[("active", p["button_hover"])])
        style.configure("TPanedwindow", background=p["bg"])
        style.configure("Sash", sashthickness=6, background=p["border"], gripcount=0)
        style.configure("TSeparator", background=p["border"])
        # Directory menubutton in the Tk file dialog.
        style.configure("TMenubutton", background=p["button_bg"], foreground=p["fg"],
                        arrowcolor=p["fg"], bordercolor=p["border"],
                        lightcolor=p["button_bg"], darkcolor=p["button_bg"],
                        padding=(6, 1))
        style.map("TMenubutton",
                  background=[("disabled", p["panel_bg"]), ("pressed", p["select_bg"]),
                              ("active", p["button_hover"])],
                  foreground=[("disabled", p["muted"])])
        # File-list canvas in tk::IconList looks up Treeview colours.
        style.configure("Treeview", background=p["field_bg"], foreground=p["field_fg"],
                        fieldbackground=p["field_bg"], bordercolor=p["border"],
                        lightcolor=p["field_bg"], darkcolor=p["field_bg"])
        style.map("Treeview",
                  background=[("selected", p["select_bg"]), ("disabled", p["panel_bg"])],
                  foreground=[("selected", p["select_fg"]), ("disabled", p["muted"])])
        style.configure("Heading", background=p["button_bg"], foreground=p["fg"],
                        bordercolor=p["border"], relief="raised")
        style.configure("Treeview.Heading", background=p["button_bg"],
                        foreground=p["fg"], bordercolor=p["border"])
    else:
        style.theme_use(_original_ttk_theme)
    # Named label styles used throughout (work in both themes).
    style.configure("Muted.TLabel", foreground=p["muted"])
    style.configure("Ok.TLabel", foreground=p["ok"])
    style.configure("Err.TLabel", foreground=p["error"])
    style.configure("Accent.TLabel", foreground=p["accent"])
    style.configure("Title.TLabel", font="TkHeadingFont")  # bold, follows View → Fonts…
    # Combobox drop-down list and other plain-Tk children created later.
    root.option_add("*TCombobox*Listbox.background", p["field_bg"])
    root.option_add("*TCombobox*Listbox.foreground", p["field_fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", p["select_bg"])
    root.option_add("*TCombobox*Listbox.selectForeground", p["select_fg"])
    try:
        root.configure(background=p["bg"])
    except tk.TclError:  # pragma: no cover
        pass
    return p


def style_menu(menu: tk.Menu, p: Optional[Palette] = None) -> None:
    """Colour a tk.Menu (and nothing else) for the palette."""
    p = p or _current
    try:
        menu.configure(background=p["bg"], foreground=p["fg"],
                       activebackground=p["select_bg"], activeforeground=p["select_fg"],
                       disabledforeground=p["muted"])
    except tk.TclError:  # pragma: no cover - platform menu
        pass


def style_text(widget: tk.Text, p: Optional[Palette] = None, *, kind: str = "editor") -> None:
    """Colour a plain tk.Text (editor / log / viewer / list field)."""
    p = p or _current
    bg = {"editor": p["editor_bg"], "viewer": p["viewer_bg"], "log": p["log_bg"]}.get(kind, p["field_bg"])
    fg = {"editor": p["editor_fg"], "viewer": p["editor_fg"], "log": p["log_fg"]}.get(kind, p["field_fg"])
    widget.configure(background=bg, foreground=fg, insertbackground=p["caret"],
                     selectbackground=p["select_bg"], selectforeground=p["select_fg"],
                     highlightbackground=p["border"], highlightcolor=p["select_bg"])


# ---------------------------------------------------------------------------
# Tk's built-in file dialogs (tk_getOpenFile / tk_getSaveFile / chooseDirectory)
# are classic-Tk widgets whose icon list hardcodes a white canvas and black
# text (iconlist.tcl), so the option database cannot darken them.  We restyle
# the live widget tree whenever such a dialog maps, and keep re-applying while
# it is open (navigating to another directory redraws the black text).
# ---------------------------------------------------------------------------

_DIALOG_CLASSES = ("TkFDialog", "TkChooseDir", "TkMotifFDialog")


def enable_dark_file_dialogs(root: tk.Misc) -> None:
    """Bind once on ``root``: dark-restyle Tk file dialogs as they open."""
    root.bind_all("<Map>", lambda e: _on_map(root, e), add="+")


def _on_map(root: tk.Misc, event) -> None:
    if not is_dark():
        return
    try:
        path = str(event.widget)
        top = root.tk.call("winfo", "toplevel", path)
        if root.tk.call("winfo", "class", top) in _DIALOG_CLASSES:
            _restyle_dialog(root, str(top))
    except tk.TclError:  # pragma: no cover - widget vanished mid-event
        pass


def _restyle_dialog(root: tk.Misc, top: str, repeats: int = 40) -> None:
    """Recolour one dialog toplevel now, then keep watching it (~250 ms)
    while it stays mapped, so newly drawn items are caught too."""
    if not _apply_dialog_colors(root, top):
        return
    if repeats > 0:
        root.after(250, lambda: _still_open(root, top) and
                   _restyle_dialog(root, top, repeats - 1))


def _still_open(root: tk.Misc, top: str) -> bool:
    try:
        return bool(int(root.tk.call("winfo", "exists", top))) and             bool(int(root.tk.call("winfo", "ismapped", top))) and is_dark()
    except tk.TclError:  # pragma: no cover
        return False


def _apply_dialog_colors(root: tk.Misc, top: str) -> bool:
    """Walk the dialog's widget tree with raw Tcl calls (its widgets are not
    tkinter objects) and recolour the classic-Tk pieces."""
    p = _current
    call = root.tk.call
    try:
        stack = [top]
        while stack:
            w = stack.pop()
            stack.extend(str(c) for c in root.tk.splitlist(call("winfo", "children", w)))
            cls = call("winfo", "class", w)
            if cls == "Canvas":
                call(w, "configure", "-background", p["field_bg"],
                     "-highlightbackground", p["bg"], "-highlightcolor", p["select_bg"])
                for item in root.tk.splitlist(call(w, "find", "all")):
                    if call(w, "type", item) == "text":
                        fill = str(call(w, "itemcget", item, "-fill"))
                        if fill in ("black", "#000000", ""):
                            call(w, "itemconfigure", item, "-fill", p["field_fg"])
            elif cls in ("Listbox",):
                call(w, "configure", "-background", p["field_bg"], "-foreground",
                     p["field_fg"], "-selectbackground", p["select_bg"],
                     "-selectforeground", p["select_fg"])
            elif cls in ("Menu",):
                call(w, "configure", "-background", p["bg"], "-foreground", p["fg"],
                     "-activebackground", p["select_bg"],
                     "-activeforeground", p["select_fg"])
            elif cls in ("Toplevel", "Frame", "Label", "Message"):
                call(w, "configure", "-background", p["bg"])
                if cls in ("Label", "Message"):
                    call(w, "configure", "-foreground", p["fg"])
        return True
    except tk.TclError:  # pragma: no cover - dialog torn down mid-walk
        return False


def all_menus(menubar: tk.Menu) -> List[tk.Menu]:
    """The menubar plus every cascade reachable from it."""
    out: List[tk.Menu] = []

    def walk(m: tk.Menu) -> None:
        out.append(m)
        try:
            last = m.index("end")
        except tk.TclError:
            return
        if last is None:
            return
        for i in range(last + 1):
            if m.type(i) == "cascade":
                sub = m.nametowidget(m.entrycget(i, "menu"))
                if sub not in out:
                    walk(sub)

    walk(menubar)
    return out
