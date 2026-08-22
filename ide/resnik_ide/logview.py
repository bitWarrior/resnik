"""
Log pane: a read-only, tagged, timestamped transcript of everything the IDE
does — user actions, compiler phases, diagnostics, tool output.

:class:`LogView` is a widget (main thread only).  :class:`LogSink` is the
thread-safe handle worker code writes to; it posts through a
:class:`~resnik_ide.dispatch.Dispatcher`.
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import filedialog, ttk
from tkinter import font as tkfont
from tkinter.scrolledtext import ScrolledText
from typing import Callable, Optional

from resnik_ide import theme
from resnik_ide.dispatch import Dispatcher

LEVELS = ("info", "ok", "warn", "error", "cmd", "raw", "diag")


class LogView(ttk.Frame):
    """Scrolling, read-only log with per-level colours and clickable lines."""

    DEFAULT_FONT_SIZE = 10

    def __init__(self, master: tk.Misc, *, font_size: int = DEFAULT_FONT_SIZE,
                 on_font_size: Optional[Callable[[int], None]] = None, **kwargs) -> None:
        super().__init__(master, **kwargs)
        self.font = tkfont.nametofont("TkFixedFont").copy()
        self.bold_font = self.font.copy()
        self.bold_font.configure(weight="bold")
        self._on_font_size = on_font_size
        self.text = ScrolledText(
            self, wrap="word", state="disabled", height=10, undo=False, font=self.font
        )
        self.text.pack(fill="both", expand=True)
        self._link_seq = 0
        self._links = {}  # link tag -> callback (for click_at / tests)
        self._configure_tags()
        self.set_font_size(font_size, notify=False)

        self._menu = tk.Menu(self, tearoff=False)
        self._menu.add_command(label="Clear", command=self.clear)
        self._menu.add_command(label="Copy all", command=self.copy_all)
        self._menu.add_command(label="Save log…", command=self.save_dialog)
        self.text.bind("<Button-3>", self._popup)
        self.text.bind("<Button-2>", self._popup)  # macOS two-finger / middle
        self.text.bind("<Control-MouseWheel>", self._on_ctrl_wheel)
        self.text.bind("<Control-Button-4>", lambda e: self.zoom(+1) or "break")
        self.text.bind("<Control-Button-5>", lambda e: self.zoom(-1) or "break")

    # -- appearance ---------------------------------------------------------

    def _configure_tags(self) -> None:
        self.apply_palette(theme.palette())

    def apply_palette(self, p: dict) -> None:
        """Recolour the log text and its level tags for palette ``p``."""
        t = self.text
        theme.style_text(t, p, kind="log")
        t.tag_configure("ts", foreground=p["ts"])
        t.tag_configure("info", foreground=p["info"])
        t.tag_configure("ok", foreground=p["ok"])
        t.tag_configure("warn", foreground=p["warn"])
        t.tag_configure("error", foreground=p["error"])
        t.tag_configure("cmd", foreground=p["cmd"], font=self.bold_font)
        t.tag_configure("raw", foreground=p["raw"], font=self.font)
        t.tag_configure("diag", foreground=p["link"], underline=True)
        theme.style_menu(self._menu, p) if hasattr(self, "_menu") else None

    # -- font -----------------------------------------------------------------

    @property
    def font_size(self) -> int:
        return int(self.font.cget("size"))

    def set_font_size(self, size: int, *, notify: bool = True) -> int:
        size = max(6, min(40, int(size)))
        self.font.configure(size=size)
        self.bold_font.configure(size=size)
        if notify and self._on_font_size is not None:
            self._on_font_size(size)
        return size

    def set_font_family(self, family: str) -> None:
        family = family or tkfont.nametofont("TkFixedFont").cget("family")
        self.font.configure(family=family)
        self.bold_font.configure(family=family)

    @property
    def font_family(self) -> str:
        return self.font.cget("family")

    def zoom(self, delta: int) -> int:
        """+1 / -1 / 0 (reset to the default size)."""
        size = self.DEFAULT_FONT_SIZE if delta == 0 else self.font_size + delta
        return self.set_font_size(size)

    def _on_ctrl_wheel(self, event: tk.Event) -> str:
        self.zoom(+1 if event.delta > 0 else -1)
        return "break"

    # -- API ----------------------------------------------------------------

    def log(
        self,
        message: str,
        level: str = "info",
        *,
        on_click: Optional[Callable[[], None]] = None,
        timestamp: bool = True,
    ) -> None:
        """Append one entry.  ``on_click`` makes the line a hyperlink."""
        if level not in LEVELS:
            level = "info"
        t = self.text
        t.configure(state="normal")
        if timestamp:
            t.insert("end", time.strftime("[%H:%M:%S] "), ("ts",))
        tags = [level]
        if on_click is not None:
            self._link_seq += 1
            link = f"link-{self._link_seq}"
            tags.append(link)
            self._links[link] = on_click
            t.tag_bind(link, "<Button-1>", lambda _e, cb=on_click: cb())
            t.tag_bind(link, "<Enter>", lambda _e: t.configure(cursor="hand2"))
            t.tag_bind(link, "<Leave>", lambda _e: t.configure(cursor=""))
        t.insert("end", message.rstrip("\n") + "\n", tuple(tags))
        t.configure(state="disabled")
        t.see("end")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        for link in self._links:
            self.text.tag_delete(link)
        self._links.clear()

    def get_text(self) -> str:
        """The transcript, without the trailing newline."""
        return self.text.get("1.0", "end-1c").rstrip("\n")

    def click_at(self, index: str) -> bool:
        """Activate the link (if any) at a Text index. Returns True if one fired."""
        for tag in self.text.tag_names(index):
            cb = self._links.get(tag)
            if cb is not None:
                cb()
                return True
        return False

    def copy_all(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.get_text())

    def save_to(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.get_text())
            fh.write("\n")

    def save_dialog(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save log",
            defaultextension=".log",
            filetypes=[("Log files", "*.log *.txt"), ("All files", "*")],
        )
        if path:
            self.save_to(path)
            self.log(f"Log saved to {path}", "ok")

    def _popup(self, event: tk.Event) -> None:
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()


class LogSink:
    """Thread-safe writer onto a :class:`LogView` via a Dispatcher.

    Also usable as a file-like object (``write``/``flush``) so worker-thread
    ``print`` output can be redirected into the log.
    """

    def __init__(self, view: LogView, dispatcher: Dispatcher, level: str = "raw") -> None:
        self._view = view
        self._dispatcher = dispatcher
        self._default_level = level
        self._partial = ""

    def log(
        self,
        message: str,
        level: str = "info",
        *,
        on_click: Optional[Callable[[], None]] = None,
    ) -> None:
        self._dispatcher.post(lambda: self._view.log(message, level, on_click=on_click))

    # file-like --------------------------------------------------------------

    def write(self, s: str) -> int:
        self._partial += s
        while "\n" in self._partial:
            line, self._partial = self._partial.split("\n", 1)
            if line.strip():
                self.log(line, self._default_level)
        return len(s)

    def flush(self) -> None:
        if self._partial.strip():
            self.log(self._partial, self._default_level)
        self._partial = ""

    def isatty(self) -> bool:
        return False
