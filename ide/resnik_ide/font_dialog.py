"""View → Fonts… dialog: every font setting in one place — UI font family/size,
the editor/log monospace family, and the editor and log sizes."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from resnik_ide import fonts, theme


class FontDialog(tk.Toplevel):
    """Modal font chooser.  ``on_apply(ui_family, ui_size, mono_family,
    editor_size, log_size)`` is called for Apply / OK (empty family / UI size 0
    = platform default)."""

    EDITOR_DEFAULT, LOG_DEFAULT = 11, 10

    def __init__(self, master: tk.Misc, *, ui_family: str, ui_size: int, mono_family: str,
                 editor_size: int = EDITOR_DEFAULT, log_size: int = LOG_DEFAULT,
                 on_apply: Callable[[str, int, str, int, int], None]) -> None:
        super().__init__(master)
        self.title("Fonts")
        self.configure(background=theme.palette()["bg"])
        self.resizable(False, False)
        self._on_apply = on_apply
        self.result: Optional[tuple] = None
        self._applied: Optional[tuple] = None  # last values handed to on_apply
        def_fam, def_size = fonts.default_ui_font(master)

        self.v_ui_family = tk.StringVar(value=ui_family or def_fam)
        self.v_ui_size = tk.IntVar(value=ui_size or def_size)
        self.v_mono_family = tk.StringVar(value=mono_family or fonts.default_mono_family(master))
        self.v_all_mono = tk.BooleanVar(value=False)
        self.v_editor_size = tk.IntVar(value=editor_size or self.EDITOR_DEFAULT)
        self.v_log_size = tk.IntVar(value=log_size or self.LOG_DEFAULT)

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Interface font (menus, buttons, panels)", style="Title.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(body, text="Family:").grid(row=1, column=0, sticky="w", pady=(4, 2))
        self.cb_ui = ttk.Combobox(body, textvariable=self.v_ui_family, width=32,
                                  values=fonts.families(master))
        self.cb_ui.grid(row=1, column=1, sticky="ew", pady=(4, 2))
        ttk.Label(body, text="Size:").grid(row=2, column=0, sticky="w")
        self.sp_ui = ttk.Spinbox(body, from_=fonts.MIN_SIZE, to=fonts.MAX_SIZE, width=5,
                                 textvariable=self.v_ui_size)
        self.sp_ui.grid(row=2, column=1, sticky="w")

        ttk.Separator(body, orient="horizontal").grid(row=3, column=0, columnspan=3,
                                                      sticky="ew", pady=10)
        ttk.Label(body, text="Editor & log font (monospace)", style="Title.TLabel").grid(
            row=4, column=0, columnspan=3, sticky="w")
        ttk.Label(body, text="Family:").grid(row=5, column=0, sticky="w", pady=(4, 2))
        self.cb_mono = ttk.Combobox(body, textvariable=self.v_mono_family, width=32,
                                    values=fonts.monospace_families(master))
        self.cb_mono.grid(row=5, column=1, sticky="ew", pady=(4, 2))
        ttk.Checkbutton(body, text="list all families", variable=self.v_all_mono,
                        command=self._refill_mono).grid(row=5, column=2, sticky="w", padx=(6, 0))
        ttk.Label(body, text="Editor size:").grid(row=6, column=0, sticky="w")
        self.sp_editor = ttk.Spinbox(body, from_=6, to=40, width=5, textvariable=self.v_editor_size)
        self.sp_editor.grid(row=6, column=1, sticky="w")
        ttk.Label(body, text="Log size:").grid(row=7, column=0, sticky="w", pady=(2, 0))
        self.sp_log = ttk.Spinbox(body, from_=6, to=40, width=5, textvariable=self.v_log_size)
        self.sp_log.grid(row=7, column=1, sticky="w", pady=(2, 0))
        ttk.Label(body, text="Shortcuts: Ctrl+ / Ctrl- / Ctrl+0 resize the editor, "
                              "Ctrl+wheel over the log resizes the log.",
                  style="Muted.TLabel", wraplength=420, justify="left").grid(
            row=8, column=0, columnspan=3, sticky="w", pady=(4, 0))

        self.preview = ttk.Label(body, text="The quick brown fox — DECLARE X INTEGER;")
        self.preview.grid(row=9, column=0, columnspan=3, sticky="w", pady=(10, 0))
        body.columnconfigure(1, weight=1)

        bf = ttk.Frame(self, padding=(10, 6))
        bf.pack(fill="x")
        ttk.Button(bf, text="Reset to defaults", command=self.reset_defaults).pack(side="left")
        ttk.Button(bf, text="Cancel", command=self._on_cancel).pack(side="right")
        ttk.Button(bf, text="OK", command=self._on_ok, default="active").pack(side="right", padx=(0, 6))
        ttk.Button(bf, text="Apply", command=self.apply).pack(side="right", padx=(0, 6))
        self.bind("<Escape>", lambda _e: self._on_cancel())
        self.bind("<Return>", lambda _e: self._on_ok())
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    # -- behaviour ----------------------------------------------------------------------

    def _refill_mono(self) -> None:
        root = self.master
        self.cb_mono["values"] = (fonts.families(root) if self.v_all_mono.get()
                                  else fonts.monospace_families(root))

    @staticmethod
    def _int(var: tk.IntVar, default: int) -> int:
        try:
            return int(var.get())
        except (tk.TclError, ValueError):
            return default

    def collect(self) -> tuple:
        return (self.v_ui_family.get().strip(), self._int(self.v_ui_size, 0),
                self.v_mono_family.get().strip(),
                self._int(self.v_editor_size, self.EDITOR_DEFAULT),
                self._int(self.v_log_size, self.LOG_DEFAULT))

    def reset_defaults(self) -> None:
        def_fam, def_size = fonts.default_ui_font(self.master)
        self.v_ui_family.set(def_fam)
        self.v_ui_size.set(def_size)
        self.v_mono_family.set(fonts.default_mono_family(self.master))
        self.v_editor_size.set(self.EDITOR_DEFAULT)
        self.v_log_size.set(self.LOG_DEFAULT)

    def apply(self) -> tuple:
        """Apply without closing; only calls back if something changed since
        the last Apply (same semantics as the Options dialog)."""
        current = self.collect()
        if current != self._applied:
            self._on_apply(*current)
            self._applied = current
        return current

    def _on_ok(self) -> None:
        self.result = self.apply()
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()

    def show(self) -> Optional[tuple]:
        self.transient(self.master)
        self.update_idletasks()
        try:
            self.grab_set()
        except tk.TclError:  # pragma: no cover
            pass
        self.focus_set()
        self.wait_window(self)
        return self.result
