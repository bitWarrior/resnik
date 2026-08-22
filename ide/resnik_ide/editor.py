"""
HAL/S source editor: a ``tk.Text`` with undo, a line-number gutter, dirty
tracking, error-line marks and goto-line.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Callable, Iterable, List, Optional

TAB_SPACES = 2


class _ProxiedText(tk.Text):
    """``tk.Text`` that fires ``<<Change>>`` on any edit or scroll.

    Done by renaming the underlying Tcl command and interposing a proxy, the
    canonical idiom for keeping a line-number gutter in sync.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._orig = self._w + "_orig"
        self.tk.call("rename", self._w, self._orig)
        self.tk.createcommand(self._w, self._proxy)
        # Tk's Text bindings keep a per-widget selection-anchor mark that is
        # only created by a ButtonPress inside the widget; a <B1-Motion> that
        # starts elsewhere (dragging a sash / window edge over the editor)
        # then errors with 'bad text index "tk::anchorN"'.  Create it up
        # front so such drags behave like an ordinary selection drag.
        try:
            anchor = self.tk.call("::tk::TextAnchor", self._w)
            self.mark_set(anchor, "insert")
        except tk.TclError:  # pragma: no cover - non-standard Tk build
            pass

    # Errors Tk's own Text bindings produce in normal use (no selection, no
    # undo history, drag without a press …).  Plain Tk reports these as
    # background errors and carries on; so do we, silently.
    _BENIGN = (
        "nothing to undo",
        "nothing to redo",
        'bad text index "tk::anchor',
        'bad text index "sel.',
        'text doesn\'t contain any characters tagged with "sel"',
    )

    def _proxy(self, *args):
        cmd = (self._orig,) + args
        try:
            result = self.tk.call(cmd)
        except tk.TclError as exc:
            # NEVER let an exception escape: when Tcl invoked us (class
            # bindings such as tk::TextSelectTo), _tkinter turns an escaping
            # Python exception into a fatal error for mainloop().
            msg = str(exc)
            if not any(b in msg for b in self._BENIGN):
                self._report_tcl_error()
            return ""
        if (
            args and args[0] in ("insert", "delete", "replace")
            or args[0:3] == ("mark", "set", "insert")
            or args[0:2] in (("xview", "moveto"), ("xview", "scroll"),
                             ("yview", "moveto"), ("yview", "scroll"))
        ):
            self.event_generate("<<Change>>", when="tail")
        return result

    def _report_tcl_error(self) -> None:
        """Route an unexpected TclError through Tk's normal callback-error
        reporting (stderr traceback) instead of raising."""
        import sys

        root = self._root()
        try:
            root.report_callback_exception(*sys.exc_info())
        except Exception:  # pragma: no cover - reporting must never fail
            pass


class Editor(ttk.Frame):
    """Editor widget.

    Callbacks:
        on_dirty(dirty: bool)      — dirty flag changed / text edited
        on_cursor(line, col)       — caret moved
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        on_dirty: Optional[Callable[[bool], None]] = None,
        on_cursor: Optional[Callable[[int, int], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        self._on_dirty = on_dirty
        self._on_cursor = on_cursor
        self._dirty = False
        self._error_lines: List[int] = []

        self.font = tkfont.nametofont("TkFixedFont").copy()
        self.font.configure(size=11)

        self.gutter = tk.Canvas(self, width=48, highlightthickness=0, bd=0,
                                background="#f0f0f0")
        self.gutter.pack(side="left", fill="y")

        self.vbar = ttk.Scrollbar(self, orient="vertical")
        self.vbar.pack(side="right", fill="y")

        self.text = _ProxiedText(
            self,
            wrap="none",
            undo=True,
            autoseparators=True,
            maxundo=-1,
            font=self.font,
            yscrollcommand=self.vbar.set,
        )
        self.text.pack(side="left", fill="both", expand=True)
        self.vbar.configure(command=self.text.yview)

        self.text.tag_configure("error_line", background="#ffd6d6")
        self.text.tag_configure("current_line", background="#f5f7ff")

        self.text.bind("<<Change>>", self._on_change)
        self.text.bind("<Configure>", self._on_change)
        self.text.bind("<<Modified>>", self._on_modified)
        self.text.bind("<Tab>", self._on_tab)
        self.text.bind("<KeyRelease>", self._report_cursor)
        self.text.bind("<ButtonRelease-1>", self._report_cursor)

        self.text.edit_modified(False)

    # -- content --------------------------------------------------------------

    def get_text(self) -> str:
        return self.text.get("1.0", "end-1c")

    def set_text(self, content: str) -> None:
        """Replace the buffer, reset undo history and the dirty flag."""
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        self.text.edit_reset()
        self.clear_error_marks()
        self.text.mark_set("insert", "1.0")
        self.text.see("1.0")
        self.set_dirty(False)
        self._redraw_gutter()
        self._report_cursor()

    def load(self, path: str) -> None:
        with open(path, "r", encoding="utf-8") as fh:
            self.set_text(fh.read())

    def save(self, path: str) -> None:
        content = self.get_text()
        if content and not content.endswith("\n"):
            content += "\n"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        self.set_dirty(False)

    # -- dirty ---------------------------------------------------------------

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def needs_save(self) -> bool:
        """True if the buffer differs from disk, even if the ``<<Modified>>``
        virtual event has not been delivered yet (it is queued, not
        synchronous — an edit followed immediately by Run must still save)."""
        return self._dirty or bool(self.text.edit_modified())

    def set_dirty(self, value: bool) -> None:
        changed = value != self._dirty
        self._dirty = value
        self.text.edit_modified(False)
        if changed and self._on_dirty:
            self._on_dirty(value)

    def _on_modified(self, _event=None) -> None:
        if not self.text.edit_modified():
            return  # our own reset of the flag
        self.clear_error_marks()
        if not self._dirty:
            self.set_dirty(True)
        else:
            self.text.edit_modified(False)

    # -- navigation ------------------------------------------------------------

    def goto(self, line: int, column: int = 0) -> None:
        """Move the caret to ``line`` (1-based) / ``column`` (0-based) and show it."""
        line = max(1, int(line))
        col = max(0, int(column) - 1) if column > 0 else 0
        index = f"{line}.{col}"
        self.text.mark_set("insert", index)
        self.text.see(index)
        self.text.focus_set()
        self._highlight_current_line()
        self._report_cursor()

    def cursor(self) -> tuple:
        line, col = map(int, self.text.index("insert").split("."))
        return line, col + 1

    # -- error marks -------------------------------------------------------------

    def mark_error_lines(self, lines: Iterable[int]) -> None:
        self.clear_error_marks()
        for ln in lines:
            ln = int(ln)
            if ln <= 0:
                continue
            self.text.tag_add("error_line", f"{ln}.0", f"{ln}.0 lineend+1c")
            self._error_lines.append(ln)

    def clear_error_marks(self) -> None:
        if self._error_lines:
            self.text.tag_remove("error_line", "1.0", "end")
            self._error_lines = []

    @property
    def error_lines(self) -> List[int]:
        return list(self._error_lines)

    # -- internals -------------------------------------------------------------

    def _on_tab(self, _event) -> str:
        self.text.insert("insert", " " * TAB_SPACES)
        return "break"

    def _on_change(self, _event=None) -> None:
        self._redraw_gutter()
        self._highlight_current_line()

    def _highlight_current_line(self) -> None:
        self.text.tag_remove("current_line", "1.0", "end")
        self.text.tag_add("current_line", "insert linestart", "insert lineend+1c")

    def _report_cursor(self, _event=None) -> None:
        if self._on_cursor:
            line, col = self.cursor()
            self._on_cursor(line, col)
        self._highlight_current_line()

    def _redraw_gutter(self) -> None:
        g = self.gutter
        g.delete("all")
        index = self.text.index("@0,0")
        last_line = int(self.text.index("end-1c").split(".")[0])
        width_chars = max(3, len(str(last_line)))
        g.configure(width=self.font.measure("0" * width_chars) + 12)
        while True:
            dline = self.text.dlineinfo(index)
            if dline is None:
                break
            y = dline[1]
            ln = int(index.split(".")[0])
            fill = "#c42b1c" if ln in self._error_lines else "#777777"
            g.create_text(
                g.winfo_width() - 6, y, anchor="ne", text=str(ln),
                font=self.font, fill=fill,
            )
            index = self.text.index(f"{index}+1line")
            if int(index.split(".")[0]) > last_line:
                break
