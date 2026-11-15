# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Steven Doran
"""
Resnik IDE main window.

Layout::

    ┌ menu ───────────────────────────────────────────────┐
    │ [Semantic Check] [Compile]  ☐ Build executable       │  toolbar
    ├─────────────────────────────────────────────────────┤
    │ editor (line numbers + text)                         │
    ├─────────────────────────────────────────────────────┤
    │ log                                                  │
    ├─────────────────────────────────────────────────────┤
    │ file  *              Ln 1, Col 1     status   target │  status bar
    └─────────────────────────────────────────────────────┘

Compiler work runs on a worker thread through ``resnik.driver``; all widget
updates go through the :class:`Dispatcher`.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Optional

from resnik import __version__ as compiler_version
from resnik.backend.target_spec import TARGET_NAMES
from resnik.driver import (
    CheckResult,
    CompileOptions,
    Diagnostic,
    GenerateResult,
    check,
    generate,
)

from resnik_ide import __version__
from resnik_ide.dispatch import Dispatcher, Job
from resnik_ide.editor import Editor
from resnik_ide.logview import LogSink, LogView
from resnik_ide.options import IdeCompileSettings, OptionsError
import time

from resnik_ide import desktop, devices, fonts, pass_commands, shuttle, theme, toolchain
from resnik_ide.font_dialog import FontDialog
from resnik_ide.options_dialog import OptionsDialog
from resnik_ide import rust_highlight
from resnik_ide.reporter import LogReporter
from resnik_ide.runner import ProcessResult, ProcessRunner
from resnik_ide.settings import load_settings, save_settings, settings_path
from resnik_ide.target_panel import TargetPanel
from resnik_ide.targetcheck import target_issues
from resnik_ide.toolchain import Requirement, ToolchainStatus

HAL_FILETYPES = [("HAL/S source", "*.hal *.hs"), ("All files", "*")]
UNTITLED = "Untitled"


class ResnikIDE(tk.Tk):
    """The IDE window."""

    def __init__(self) -> None:
        # WM_CLASS = (resnik-ide, resnik-ide): what a .desktop entry's
        # StartupWMClass matches, so GNOME & co. show our icon/launcher.
        super().__init__(className=desktop.WM_CLASS, baseName=desktop.WM_CLASS)
        self.title(f"Resnik IDE — {UNTITLED}")
        self.geometry("1100x780")
        self.minsize(700, 480)
        self._set_window_icon()

        self.current_path: Optional[Path] = None
        self._job: Optional[Job] = None
        self._tmpdir: Optional[str] = None
        self.last_result: Any = None
        self.last_rust_path: Optional[Path] = None

        # Persistent settings (compiler arguments, window geometry).
        self.settings, settings_warning = load_settings()
        self.var_target = tk.StringVar(value=self.settings.compile.target)
        self.var_port = tk.StringVar(value=self.settings.port)
        self.var_chip = tk.StringVar(value=self.settings.chip)
        self._syncing = False  # guard: toolbar vars <-> settings

        theme.apply(self, self.settings.theme)
        theme.enable_dark_file_dialogs(self)
        fonts.apply_ui_font(self, self.settings.ui_font_family, self.settings.ui_font_size)
        self.dispatcher = Dispatcher(self)
        self.runner = ProcessRunner(self.dispatcher)
        self.toolchain_status: Optional[ToolchainStatus] = None
        self._probe_job: Optional[Job] = None
        self._build_menu()
        self._build_toolbar()
        self._build_panes()
        self._build_statusbar()
        self._bind_keys()

        self.log_sink = LogSink(self.log, self.dispatcher)
        self.dispatcher.start()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.log.log(
            f"Resnik IDE {__version__} — compiler {compiler_version} — "
            f"Python {sys.version.split()[0]}",
            "info",
        )
        self.log.log(shuttle.startup_banner(), "ok")
        self._remember_after: Optional[str] = None
        self.refresh_remembrance()
        if settings_warning:
            self.log.log(settings_warning, "warn")
        self.log.log(f"Settings: {settings_path()}", "info")
        if self.settings.geometry:
            try:
                self.geometry(self.settings.geometry)
            except tk.TclError:
                pass
        self.var_target.trace_add("write", lambda *_a: self._toolbar_changed())
        self.var_port.trace_add("write", lambda *_a: self._device_changed())
        self.var_chip.trace_add("write", lambda *_a: self._device_changed())
        self.editor.set_font_size(self.settings.font_size)
        if self.settings.mono_font_family:
            self.editor.set_font_family(self.settings.mono_font_family)
            self.log.set_font_family(self.settings.mono_font_family)
        self.editor.set_highlighting(self.settings.highlighting)
        if not self.settings.show_target_panel:
            self.set_target_panel_visible(False, save=False)
        self._fault = False  # "GPC FAULT" latched until the next clean check/compile
        self._paned.bind("<ButtonRelease-1>", self._on_sash_release)
        self._outer.bind("<ButtonRelease-1>", self._on_sash_release)
        # Keep re-applying the saved split while the window manager is still
        # delivering the initial geometry; stop once the user grabs a sash or
        # the startup window has passed.
        self._sash_restore_active = True
        self._paned.bind("<Configure>", lambda e: self._restore_sash_if_active())
        self._outer.bind("<Configure>", lambda e: self._restore_sash_if_active())
        self._sash_restore_after: Optional[str] = self.after(2000, self._end_sash_restore)
        if self.settings.log_height > 0 or self.settings.sash_target_panel > 0:
            self.after(50, self._restore_sash)
        self._rebuild_recent_menu()
        self.log.log("Ready. File → Open… to load a .hal file, or start typing.", "info")
        self._update_title()
        self.refresh_devices()
        self.refresh_toolchain()

    # ------------------------------------------------------------------ UI --

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        m_file = tk.Menu(menubar, tearoff=False)
        m_file.add_command(label="New", accelerator="Ctrl+N", command=self.new_file)
        m_file.add_command(label="Open…", accelerator="Ctrl+O", command=self.open_file)
        m_file.add_command(label="Save", accelerator="Ctrl+S", command=self.save_file)
        m_file.add_command(label="Save As…", accelerator="Ctrl+Shift+S",
                           command=self.save_file_as)
        self._menu_recent = tk.Menu(m_file, tearoff=False)
        m_file.add_cascade(label="Open Recent", menu=self._menu_recent)
        m_file.add_separator()
        m_file.add_command(label="Open Containing Folder", command=self.open_folder)
        m_file.add_command(label="Open Cargo Project Folder", command=self.open_project_folder)
        m_file.add_separator()
        m_file.add_command(label="Exit", accelerator="Ctrl+Q", command=self.on_close)
        menubar.add_cascade(label="File", menu=m_file)
        self._menu_file = m_file

        m_edit = tk.Menu(menubar, tearoff=False)
        for item in (
            ("Undo", "Ctrl+Z", "<<Undo>>"),
            ("Redo", "Ctrl+Y", "<<Redo>>"),
            None,
            ("Cut", "Ctrl+X", "<<Cut>>"),
            ("Copy", "Ctrl+C", "<<Copy>>"),
            ("Paste", "Ctrl+V", "<<Paste>>"),
            None,
            ("Select All", "Ctrl+A", "<<SelectAll>>"),
        ):
            if item is None:
                m_edit.add_separator()
            else:
                label, accel, ev = item
                m_edit.add_command(
                    label=label, accelerator=accel,
                    command=lambda e=ev: self.editor.text.event_generate(e))
        menubar.add_cascade(label="Edit", menu=m_edit)

        m_view = tk.Menu(menubar, tearoff=False)
        self.var_highlight = tk.BooleanVar(value=self.settings.highlighting)
        m_view.add_checkbutton(label="Syntax Highlighting", variable=self.var_highlight,
                               command=self._toggle_highlight)
        self.var_dark = tk.BooleanVar(value=self.settings.theme == "dark")
        m_view.add_checkbutton(label="Dark Mode", accelerator="Ctrl+Shift+D",
                               variable=self.var_dark, command=self._toggle_dark)
        self.var_show_target = tk.BooleanVar(value=self.settings.show_target_panel)
        m_view.add_checkbutton(label="Target Panel", accelerator="Ctrl+Shift+T",
                               variable=self.var_show_target, command=self._toggle_target_panel)
        m_view.add_command(label="Mission Elapsed Time", command=self.log_met)
        m_view.add_separator()
        m_view.add_command(label="Fonts…", command=self.open_fonts)
        menubar.add_cascade(label="View", menu=m_view)

        m_run = tk.Menu(menubar, tearoff=False)
        m_run.add_command(label="Semantic Check", accelerator="F7",
                          command=self.semantic_check)
        m_run.add_command(label="Compile", accelerator="F5", command=self.compile)
        m_run.add_command(label="Build and Run", accelerator="F9", command=self.build_and_run)
        m_run.add_separator()
        m_run.add_command(label="Compiler Options…", accelerator="Ctrl+,",
                          command=self.open_options)
        m_run.add_separator()
        m_run.add_command(label="Build (cargo build)", accelerator="F6", command=self.build)
        m_run.add_command(label="Flash & Run", accelerator="F8", command=self.flash_run)
        m_run.add_command(label="Monitor (serial console)", command=self.monitor)
        m_run.add_command(label="Run on host", command=self.run_host)
        m_run.add_command(label="Stop", accelerator="Esc", command=self.stop)
        m_run.add_separator()
        m_run.add_command(label="View Generated Rust", command=self.view_rust)
        menubar.add_cascade(label="Run", menu=m_run)
        self._menu_run = m_run

        m_log = tk.Menu(menubar, tearoff=False)
        m_log.add_command(label="Clear Log", command=lambda: self.log.clear())
        m_log.add_command(label="Save Log…", command=lambda: self.log.save_dialog())
        menubar.add_cascade(label="Log", menu=m_log)

        m_help = tk.Menu(menubar, tearoff=False)
        m_help.add_command(label="About", command=self.about)
        menubar.add_cascade(label="Help", menu=m_help)

        self.config(menu=menubar)
        self._menubar = menubar
        self._style_menus()

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(6, 4))
        bar.pack(side="top", fill="x")
        self.btn_check = ttk.Button(bar, text="Semantic Check", command=self.semantic_check)
        self.btn_check.pack(side="left")
        self.btn_compile = ttk.Button(bar, text="Compile", command=self.compile)
        self.btn_compile.pack(side="left", padx=(6, 0))
        self.btn_build_run = ttk.Button(bar, text="Build and Run", command=self.build_and_run)
        self.btn_build_run.pack(side="left", padx=(6, 0))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(bar, text="Target:").pack(side="left", padx=(0, 4))
        self.cb_target = ttk.Combobox(bar, textvariable=self.var_target,
                                      values=list(TARGET_NAMES), state="readonly", width=10)
        self.cb_target.pack(side="left")
        self.btn_options = ttk.Button(bar, text="Options…", command=self.open_options)
        self.btn_options.pack(side="left", padx=(10, 0))
        self.busy_label = ttk.Label(bar, text="", style="Accent.TLabel")
        self.busy_label.pack(side="right")

    def _build_panes(self) -> None:
        outer = ttk.PanedWindow(self, orient="horizontal")
        outer.pack(side="top", fill="both", expand=True)
        paned = ttk.PanedWindow(outer, orient="vertical")
        self.editor = Editor(paned, on_dirty=self._on_dirty, on_cursor=self._on_cursor)
        self.log_frame = ttk.Frame(paned)
        self.log = LogView(self.log_frame, font_size=self.settings.log_font_size,
                           on_font_size=self._log_font_changed)
        self.log.pack(side="top", fill="both", expand=True)
        cmd_row = ttk.Frame(self.log_frame)
        cmd_row.pack(side="bottom", fill="x")
        ttk.Label(cmd_row, text="PASS>", style="Muted.TLabel").pack(side="left", padx=(4, 2))
        self.var_pass = tk.StringVar()
        self.pass_entry = ttk.Entry(cmd_row, textvariable=self.var_pass, font="TkFixedFont")
        self.pass_entry.pack(side="left", fill="x", expand=True, padx=(0, 4), pady=2)
        self.pass_entry.bind("<Return>", lambda e: self.run_pass_command())
        self.pass_entry.bind("<Escape>", lambda e: (self.var_pass.set(""), self.editor.text.focus_set()))
        self.pass_entry.bind("<Up>", lambda e: self._pass_history(-1))
        self.pass_entry.bind("<Down>", lambda e: self._pass_history(+1))
        self._pass_hist: list = []
        self._pass_hist_pos = 0
        paned.add(self.editor, weight=3)
        paned.add(self.log_frame, weight=1)
        self.target_panel = TargetPanel(
            outer, var_target=self.var_target, var_port=self.var_port, var_chip=self.var_chip,
            actions={
                "refresh_toolchain": self.refresh_toolchain,
                "refresh_devices": self.refresh_devices,
                "install": self.install_requirement,
                "build": self.build,
                "flash_run": self.flash_run,
                "monitor": self.monitor,
                "run": self.run_host,
                "stop": self.stop,
            })
        outer.add(paned, weight=4)
        outer.add(self.target_panel, weight=0)
        self._paned = paned
        self._outer = outer

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self, relief="sunken", padding=(6, 2))
        bar.pack(side="bottom", fill="x")
        self.status_file = ttk.Label(bar, text=UNTITLED, anchor="w")
        self.status_file.pack(side="left", fill="x", expand=True)
        self.status_pos = ttk.Label(bar, text="Ln 1, Col 1", width=16, anchor="center")
        self.status_pos.pack(side="left")
        self._met_launch: Optional[float] = None  # T-0: firmware started on the board
        self.status_remember = ttk.Label(bar, text="", anchor="center", style="Muted.TLabel")
        self.status_remember.pack(side="left", fill="x", expand=True)
        self.status_msg = ttk.Label(bar, text="", width=36, anchor="e")
        self.status_msg.pack(side="right")

    def _bind_keys(self) -> None:
        self.bind_all("<Control-n>", lambda e: self.new_file())
        self.bind_all("<Control-o>", lambda e: self.open_file())
        self.bind_all("<Control-s>", lambda e: self.save_file())
        self.bind_all("<Control-S>", lambda e: self.save_file_as())
        self.bind_all("<Control-q>", lambda e: self.on_close())
        self.bind_all("<F7>", lambda e: self.semantic_check())
        self.bind_all("<F5>", lambda e: self.compile())
        self.bind_all("<Control-comma>", lambda e: self.open_options())
        self.bind_all("<F6>", lambda e: self.build())
        self.bind_all("<F8>", lambda e: self.flash_run())
        self.bind_all("<F9>", lambda e: self.build_and_run())
        self.bind_all("<Escape>", lambda e: self.stop())
        for seq in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            self.bind_all(seq, lambda e: self.zoom(+1))
        for seq in ("<Control-minus>", "<Control-KP_Subtract>"):
            self.bind_all(seq, lambda e: self.zoom(-1))
        self.bind_all("<Control-0>", lambda e: self.zoom(0))
        self.bind_all("<Control-D>", lambda e: self.toggle_dark())
        self.bind_all("<Control-T>", lambda e: self.toggle_target_panel())
        self.bind_all("<Control-l>", lambda e: self.pass_entry.focus_set())

    # -------------------------------------------------------------- state --

    @property
    def busy(self) -> bool:
        return (self._job is not None and self._job.alive) or self.runner.running

    def _set_busy(self, on: bool, what: str = "") -> None:
        state = "disabled" if on else "normal"
        self.btn_check.configure(state=state)
        self.btn_compile.configure(state=state)
        self.btn_build_run.configure(state=state)
        self.btn_options.configure(state=state)
        self.busy_label.configure(text=(f"⏳ {what}…" if on else ""))
        self.configure(cursor="watch" if on else "")
        self.target_panel.set_running(on)

    def _update_title(self) -> None:
        name = self.current_path.name if self.current_path else UNTITLED
        star = "*" if self.editor.dirty else ""
        self.title(f"Resnik IDE — {name}{star}")
        path = str(self.current_path) if self.current_path else UNTITLED
        self.status_file.configure(text=f"{path}{star}")

    def _on_dirty(self, _dirty: bool) -> None:
        self._update_title()

    def _on_cursor(self, line: int, col: int) -> None:
        self.status_pos.configure(text=f"Ln {line}, Col {col}")

    def set_status(self, text: str) -> None:
        self.status_msg.configure(text=text)

    # --------------------------------------------------------------- file --

    def _confirm_discard(self) -> bool:
        """Return True if it is OK to drop the current buffer."""
        if not self.editor.dirty:
            return True
        name = self.current_path.name if self.current_path else UNTITLED
        ans = messagebox.askyesnocancel(
            "Unsaved changes", f"Save changes to {name}?", parent=self)
        if ans is None:
            return False
        if ans:
            return self.save_file()
        return True

    def new_file(self) -> None:
        if not self._confirm_discard():
            return
        self.current_path = None
        self.editor.set_text("")
        self.last_rust_path = None
        self.log.log("New file.", "info")
        self._update_title()

    def open_file(self, path: Optional[str] = None) -> bool:
        if not self._confirm_discard():
            return False
        if path is None:
            path = filedialog.askopenfilename(title="Open HAL/S source",
                                              filetypes=HAL_FILETYPES, parent=self)
            if not path:
                return False
        try:
            self.editor.load(path)
        except OSError as exc:
            self.log.log(f"Could not open {path}: {exc}", "error")
            messagebox.showerror("Open failed", str(exc), parent=self)
            return False
        self.current_path = Path(path).resolve()
        self.last_rust_path = None
        self.log.log(f"Opened {self.current_path}", "info")
        self._update_title()
        self.set_status("")
        self._touch_recent(self.current_path)
        return True

    def save_file(self) -> bool:
        if self.current_path is None:
            return self.save_file_as()
        return self._save_to(self.current_path)

    def save_file_as(self) -> bool:
        path = filedialog.asksaveasfilename(
            title="Save HAL/S source", defaultextension=".hal",
            filetypes=HAL_FILETYPES, parent=self)
        if not path:
            return False
        self.current_path = Path(path).resolve()
        ok = self._save_to(self.current_path)
        if ok:
            self._touch_recent(self.current_path)
        return ok

    def _save_to(self, path: Path) -> bool:
        try:
            self.editor.save(str(path))
        except OSError as exc:
            self.log.log(f"Could not save {path}: {exc}", "error")
            messagebox.showerror("Save failed", str(exc), parent=self)
            return False
        self.log.log(f"Saved {path}", "info")
        self._update_title()
        return True

    def _source_for_run(self) -> Optional[Path]:
        """Make sure the buffer is on disk; return the path to compile.

        A named file is saved in place if dirty.  An untitled buffer is
        written to a private temp directory as ``untitled.hal``.
        """
        if self.current_path is not None:
            if self.editor.needs_save and not self._save_to(self.current_path):
                return None
            return self.current_path
        if self._tmpdir is None:
            self._tmpdir = tempfile.mkdtemp(prefix="resnik-ide-")
        tmp = Path(self._tmpdir) / "untitled.hal"
        try:
            self.editor.save(str(tmp))
            self.editor.set_dirty(True)  # untitled stays "unsaved" for the user
        except OSError as exc:
            self.log.log(f"Could not write scratch file: {exc}", "error")
            return None
        return tmp

    # ------------------------------------------------------ recent / view --

    def _touch_recent(self, path: Path) -> None:
        self.settings.touch_recent(str(path))
        self._save_settings()
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self) -> None:
        m = self._menu_recent
        m.delete(0, "end")
        if not self.settings.recent:
            m.add_command(label="(empty)", state="disabled")
            return
        for p in self.settings.recent:
            m.add_command(label=p, command=lambda q=p: self.open_recent(q))
        m.add_separator()
        m.add_command(label="Clear Recent", command=self._clear_recent)

    def _clear_recent(self) -> None:
        self.settings.recent = []
        self._save_settings()
        self._rebuild_recent_menu()

    def open_recent(self, path: str) -> bool:
        if not os.path.exists(path):
            self.log.log(f"Recent file no longer exists: {path}", "warn")
            self.settings.recent = [p for p in self.settings.recent if p != path]
            self._save_settings()
            self._rebuild_recent_menu()
            return False
        return self.open_file(path)

    def zoom(self, delta: int) -> int:
        """Change the editor font size (+1 / -1 / 0 = reset to 11)."""
        size = 11 if delta == 0 else self.editor.font_size + delta
        size = self.editor.set_font_size(size)
        if size != self.settings.font_size:
            self.settings.font_size = size
            self._save_settings()
        self.set_status(f"Font size {size}")
        return size

    # -- theme --------------------------------------------------------------------------

    def _style_menus(self) -> None:
        for m in theme.all_menus(self._menubar):
            theme.style_menu(m)
        theme.style_menu(self._menu_recent)

    def set_theme(self, name: str) -> None:
        """Switch light/dark: ttk styles, editor, log, menus; persisted."""
        if name not in theme.THEME_NAMES:
            name = "light"
        p = theme.apply(self, name)
        self.editor.apply_palette(p)
        self.log.apply_palette(p)
        self._style_menus()
        self.var_dark.set(name == "dark")
        if self.settings.theme != name:
            self.settings.theme = name
            self._save_settings()
        self.log.log(f"Theme: {name}", "info")

    # -- editor/log sash ------------------------------------------------------------

    @property
    def log_height(self) -> int:
        """Current height of the log pane in pixels (0 if not laid out yet)."""
        try:
            height = self._paned.winfo_height()
            if height <= 1:
                return 0
            return max(0, height - int(self._paned.sashpos(0)))
        except tk.TclError:
            return 0

    def _sash_positions(self) -> tuple:
        """(log height, target-panel sash) in pixels; 0 if unknown/hidden."""
        h = 0
        if self.target_panel_visible:
            try:
                if self._outer.winfo_width() > 1:
                    h = int(self._outer.sashpos(0))
            except tk.TclError:
                pass
        return self.log_height, h

    def _on_sash_release(self, _event=None) -> None:
        self._end_sash_restore()
        self._remember_sash()

    def _end_sash_restore(self) -> None:
        self._sash_restore_active = False
        self._sash_restore_after = None

    def _restore_sash_if_active(self) -> None:
        if getattr(self, "_sash_restore_active", False):
            self._restore_sash(attempts=0)

    def _remember_sash(self) -> None:
        """Persist the log height and the Target panel width (called on sash
        release and on close)."""
        lh, h = self._sash_positions()
        changed = False
        if lh > 0 and lh != self.settings.log_height:
            self.settings.log_height = lh
            changed = True
        if h > 0 and h != self.settings.sash_target_panel:
            self.settings.sash_target_panel = h
            changed = True
        if changed:
            self._save_settings()

    def _restore_sash(self, attempts: int = 20) -> None:
        """Apply the saved log height / panel width once the paned windows
        have real sizes (geometry arrives asynchronously from the WM)."""
        want_log, want_h = self.settings.log_height, self.settings.sash_target_panel
        height, width = self._paned.winfo_height(), self._outer.winfo_width()
        if (height <= 1 or width <= 1) and attempts > 0:
            self.after(50, lambda: self._restore_sash(attempts - 1))
            return
        if want_log > 0 and height > 1:
            pos = height - want_log
            try:
                self._paned.sashpos(0, max(60, min(pos, max(60, height - 60))))
            except tk.TclError:
                pass
        if want_h > 0 and self.target_panel_visible and width > 1:
            try:
                self._outer.sashpos(0, max(200, min(want_h, max(200, width - 150))))
            except tk.TclError:
                pass

    # -- Shuttle flavour: MET clock, GPC fault, PASS keyboard ----------------------

    @property
    def launched(self) -> bool:
        return self._met_launch is not None

    def met(self) -> str:
        """Mission Elapsed Time, ``ddd:hh:mm:ss``.  T-0 is the moment firmware
        last started on the board (flash completed); zero before that."""
        s = 0 if self._met_launch is None else int(time.monotonic() - self._met_launch)
        d, s = divmod(s, 86400)
        h, s = divmod(s, 3600)
        m, s = divmod(s, 60)
        return f"MET {d:03d}:{h:02d}:{m:02d}:{s:02d}"

    def met_launch(self) -> None:
        """T-0: called when a flash completes and the board starts running.
        A re-flash restarts the clock."""
        self._met_launch = time.monotonic()
        self.log.log("LIFTOFF — MET 000:00:00:00", "ok")

    def _met_line_hook(self, line: str) -> None:
        """Watches Flash & Run output for the moment the firmware starts."""
        if "Flashing has completed" in line or "Finished in" in line:
            self.met_launch()

    def log_met(self) -> str:
        """View → Mission Elapsed Time: one MET line into the log."""
        text = self.met()
        if not self.launched:
            text += " (pre-launch — no firmware started on the board yet)"
        self.log.log(text, "cmd")
        return text

    def refresh_remembrance(self, today=None) -> Optional[str]:
        """Date-gated quiet remembrance in the status bar (re-checked hourly
        so a long-running IDE notices midnight).  Returns the text shown."""
        entry = shuttle.remembrance_for(today)
        text = entry[0] if entry else ""
        if text and self.status_remember.cget("text") != text:
            self.log.log(entry[1], "info")
        self.status_remember.configure(text=text)
        if self._remember_after is not None:
            try:
                self.after_cancel(self._remember_after)
            except tk.TclError:  # pragma: no cover
                pass
        self._remember_after = self.after(3600 * 1000, self.refresh_remembrance)
        return text or None

    def _fault_status(self, what: str, n: int) -> None:
        """Failure wording in the GPC idiom; diagnostics themselves are untouched."""
        self._fault = True
        self.set_status(f"FAULT — {n} ERROR{'S' if n != 1 else ''} · {what}")

    def _fault_cleared(self) -> None:
        if self._fault:
            self._fault = False
            self.log.log("GPC FAULT CLEARED", "ok")

    def run_pass_command(self, text: Optional[str] = None) -> str:
        """Execute a PASS-style keyboard entry; returns the action key or
        ``ILLEGAL ENTRY``."""
        raw = (self.var_pass.get() if text is None else text).strip()
        if not raw:
            return ""
        cmd = pass_commands.parse(raw)
        self.var_pass.set("")
        if raw and (not self._pass_hist or self._pass_hist[-1] != raw):
            self._pass_hist.append(raw)
        self._pass_hist_pos = len(self._pass_hist)
        self.log.log(f"PASS> {raw}", "cmd")
        if cmd.kind == "illegal":
            self.log.log(pass_commands.ILLEGAL_ENTRY, "error")
            return pass_commands.ILLEGAL_ENTRY
        action = cmd.action or ""
        if action == "help":
            for ln in pass_commands.help_lines():
                self.log.log(ln, "info", timestamp=False)
        elif action == "toolchain":
            st = self.toolchain_status
            lines = st.summary_lines() if st else ["toolchain not probed yet"]
            for ln in lines:
                self.log.log("SPEC 1  " + ln, "info")
        elif action == "options":
            self.log.log("SPEC 2  " + self.settings.compile.command_line(
                self.current_path.name if self.current_path else None), "info")
        elif action == "devices":
            self.refresh_devices()
        elif action == "sts1":
            self.log.log("SPEC 67 " + pass_commands.STS1_TEXT, "info")
        elif action == "semantic_check":
            self.semantic_check()
        elif action == "compile":
            self.compile()
        elif action == "build_and_run":
            self.build_and_run()
        elif action == "stop":
            self.stop()
        elif action == "clear_log":
            self.log.clear()
        elif action == "met":
            self.log_met()
        elif action == "about":
            self.about()
        return action

    def _pass_history(self, step: int) -> str:
        if not self._pass_hist:
            return "break"
        self._pass_hist_pos = max(0, min(len(self._pass_hist), self._pass_hist_pos + step))
        self.var_pass.set(self._pass_hist[self._pass_hist_pos]
                          if self._pass_hist_pos < len(self._pass_hist) else "")
        self.pass_entry.icursor("end")
        return "break"

    # -- target panel visibility ---------------------------------------------------

    @property
    def target_panel_visible(self) -> bool:
        return str(self.target_panel) in self._outer.panes()

    def set_target_panel_visible(self, visible: bool, *, save: bool = True) -> None:
        """Show/hide the right-hand Target panel (actions stay available via
        the Run menu and shortcuts)."""
        visible = bool(visible)
        if visible and not self.target_panel_visible:
            self._outer.add(self.target_panel, weight=0)
            if self.settings.sash_target_panel > 0:
                self.after(50, self._restore_sash)
        elif not visible and self.target_panel_visible:
            self._outer.forget(self.target_panel)
        self.var_show_target.set(visible)
        if save and self.settings.show_target_panel != visible:
            self.settings.show_target_panel = visible
            self._save_settings()

    def toggle_target_panel(self) -> None:
        self.set_target_panel_visible(not self.target_panel_visible)

    def _toggle_target_panel(self) -> None:
        self.set_target_panel_visible(bool(self.var_show_target.get()))

    def toggle_dark(self) -> None:
        self.set_theme("light" if theme.is_dark() else "dark")

    def _toggle_dark(self) -> None:
        self.set_theme("dark" if self.var_dark.get() else "light")

    @property
    def theme_name(self) -> str:
        return theme.current_name()

    def _log_font_changed(self, size: int) -> None:
        if size != self.settings.log_font_size:
            self.settings.log_font_size = size
            self._save_settings()
        self.set_status(f"Log font size {size}")

    # -- fonts ----------------------------------------------------------------------

    def set_fonts(self, ui_family: str = "", ui_size: int = 0, mono_family: str = "",
                  editor_size: Optional[int] = None, log_size: Optional[int] = None) -> None:
        """Apply + persist every font setting ("" / 0 = platform default for
        the UI font; editor/log sizes default to 11 / 10 when None)."""
        def_fam, def_size = fonts.default_ui_font(self)
        fam, size = fonts.apply_ui_font(self, ui_family, ui_size)
        ui_family = "" if (fam == def_fam and not ui_family) else fam
        ui_size = 0 if (size == def_size and not ui_size) else size
        mono_family = mono_family or ""
        if mono_family == fonts.default_mono_family(self):
            mono_family = ""
        self.editor.set_font_family(mono_family)
        self.log.set_font_family(mono_family)
        if editor_size is not None:
            self.settings.font_size = self.editor.set_font_size(editor_size)
        if log_size is not None:
            self.settings.log_font_size = self.log.set_font_size(log_size, notify=False)
        self.settings.ui_font_family = ui_family
        self.settings.ui_font_size = ui_size
        self.settings.mono_font_family = mono_family
        self._save_settings()  # sizes may have changed too; always persist
        self.editor._redraw_gutter()
        self.log.log(f"Fonts: UI {fam} {size}; editor/log {self.editor.font_family} "
                     f"{self.editor.font_size}/{self.log.font_size}", "info")

    def open_fonts(self) -> None:
        dlg = FontDialog(self, ui_family=self.settings.ui_font_family,
                         ui_size=self.settings.ui_font_size,
                         mono_family=self.settings.mono_font_family,
                         editor_size=self.editor.font_size, log_size=self.log.font_size,
                         on_apply=self.set_fonts)
        dlg.show()

    def _toggle_highlight(self) -> None:
        on = bool(self.var_highlight.get())
        self.editor.set_highlighting(on)
        self.settings.highlighting = on
        self._save_settings()

    @staticmethod
    def _reveal(path: Path) -> Optional[str]:
        """Open a folder in the desktop file manager; returns the command used."""
        if sys.platform.startswith("win"):  # pragma: no cover - windows
            os.startfile(str(path))  # type: ignore[attr-defined]
            return "explorer"
        cmd = "open" if sys.platform == "darwin" else "xdg-open"
        exe = shutil.which(cmd)
        if exe is None:
            return None
        subprocess.Popen([exe, str(path)], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return cmd

    def _open_dir(self, path: Optional[Path], what: str) -> bool:
        if path is None or not path.is_dir():
            self.log.log(f"✗ {what}: no such folder" + (f" ({path})" if path else ""), "error")
            return False
        cmd = self._reveal(path)
        if cmd is None:
            self.log.log(f"✗ Cannot open a file manager (xdg-open/open not found): {path}", "error")
            return False
        self.log.log(f"Opened folder {path} ({cmd})", "info")
        return True

    def open_folder(self) -> bool:
        if self.current_path is None:
            self.log.log("No file is open — save it first to have a folder.", "warn")
            return False
        return self._open_dir(self.current_path.parent, "Containing folder")

    def open_project_folder(self) -> bool:
        proj = self._project_dir()
        if proj is None:
            self.log.log("No Cargo project dir configured (Options… → --emit-cargo).", "warn")
            return False
        return self._open_dir(proj, "Cargo project folder")

    # ---------------------------------------------------------------- run --

    # ------------------------------------------------------------ options --

    def current_options(self, source: Path) -> CompileOptions:
        """Compiler arguments for a Compile run, validated by the real CLI
        parser.  Raises :class:`OptionsError` on bad settings."""
        return self.settings.compile.to_compile_options(source)

    def _toolbar_changed(self) -> None:
        """Toolbar emit-exe / target → settings (+ save)."""
        if self._syncing:
            return
        comp = self.settings.compile
        changed = False
        tgt = self.var_target.get()
        if tgt in TARGET_NAMES and tgt != comp.target:
            comp.target = tgt
            changed = True
        if changed:
            self._save_settings()
            self.log.log("Options: " + comp.command_line(
                self.current_path.name if self.current_path else None), "cmd")
        if self.toolchain_status is not None:
            self._show_requirements()

    def _device_changed(self) -> None:
        if self._syncing:
            return
        port, chip = self.var_port.get().strip(), self.var_chip.get().strip()
        if port != self.settings.port or chip != self.settings.chip:
            self.settings.port, self.settings.chip = port, chip
            self._save_settings()

    def _sync_toolbar(self) -> None:
        """Settings → toolbar vars (without triggering a save)."""
        self._syncing = True
        try:
            self.var_target.set(self.settings.compile.target)
        finally:
            self._syncing = False

    def _save_settings(self) -> None:
        try:
            save_settings(self.settings)
        except OSError as exc:
            self.log.log(f"Could not save settings: {exc}", "warn")

    def apply_compile_settings(self, new: IdeCompileSettings) -> None:
        """Adopt ``new`` compiler settings: persist, sync toolbar, log."""
        self.settings.compile = new
        self._sync_toolbar()
        self._save_settings()
        self.log.log("Options: " + new.command_line(
            self.current_path.name if self.current_path else None), "cmd")

    def open_options(self) -> None:
        if self.busy:
            self.log.log("Options cannot change while a job is running.", "warn")
            return
        # Preview with the bare file name: relative paths are resolved against
        # the source's directory, so that is the exact shell equivalent from there.
        dlg = OptionsDialog(self, self.settings.compile,
                            source=self.current_path.name if self.current_path else None,
                            on_apply=self.apply_compile_settings,
                            desktop_install=self.install_desktop_entry,
                            desktop_remove=self.uninstall_desktop_entry)
        new = dlg.show()
        # Apply / OK already went through on_apply; this only catches a
        # dialog implementation that returns a result without calling it.
        if new is not None and new != self.settings.compile:
            self.apply_compile_settings(new)

    def semantic_check(self) -> None:
        if self.busy:
            return
        source = self._source_for_run()
        if source is None:
            return
        self.editor.clear_error_marks()
        target = self.var_target.get() or "host"
        self.log.log(f"── Semantic Check: {source.name} ──"
                     + (f" (target {target})" if target != "host" else ""), "cmd")
        reporter = LogReporter(self.log_sink, on_diagnostic=self._jump_to)
        templib = [Path(t) for t in self.settings.compile.templib]

        def job():
            res = check(source, templib, reporter=reporter)
            issues = target_issues(res, target) if res.ok else []
            return res, issues

        self._start_job(
            "Semantic Check",
            job,
            lambda pair: self._check_done(pair[0], reporter, pair[1]),
        )

    def compile(self) -> None:
        if self.busy:
            return
        source = self._source_for_run()
        if source is None:
            return
        try:
            opts = self.current_options(source)
        except OptionsError as exc:
            self.log.log(f"✗ Invalid compiler options: {exc}", "error")
            self.log.log("Fix them under Run → Compiler Options… (Ctrl+,).", "warn")
            self.set_status("Compile: invalid options")
            return
        self.editor.clear_error_marks()
        self.log.log(f"── Compile: {source.name} ──", "cmd")
        self.log.log("$ " + opts.command_line(), "cmd")
        reporter = LogReporter(self.log_sink, on_diagnostic=self._jump_to)
        self._start_job(
            "Compile",
            lambda: generate(opts, reporter),
            lambda res: self._compile_done(res, reporter),
        )

    def build_and_run(self) -> None:
        """Compile, then run the result and stream its output into the log.

        host: ``--emit-exe`` is forced for this run (the saved options are
        not changed) and the executable is run through the process runner.
        Embedded targets: compile (``--emit-cargo`` must be set), then the
        Flash & Run flow for the target.
        """
        if self.busy:
            return
        source = self._source_for_run()
        if source is None:
            return
        try:
            opts = self.current_options(source)
        except OptionsError as exc:
            self.log.log(f"✗ Invalid compiler options: {exc}", "error")
            self.set_status("Build and Run: invalid options")
            return
        if opts.target == "host":
            from dataclasses import replace
            opts = replace(opts, emit_exe=True)
        elif not opts.emit_cargo:
            self.log.log("✗ Build and Run on an embedded target needs a Cargo project: set "
                         "'Cargo project dir (--emit-cargo)' in Options….", "error")
            return
        self.editor.clear_error_marks()
        self.log.log(f"── Build and Run: {source.name} ──", "cmd")
        self.log.log("$ " + opts.command_line(), "cmd")
        reporter = LogReporter(self.log_sink, on_diagnostic=self._jump_to)
        self._start_job(
            "Build and Run",
            lambda: generate(opts, reporter),
            lambda res: self._build_and_run_done(res, reporter),
        )

    def _build_and_run_done(self, res: GenerateResult, reporter: LogReporter) -> None:
        self._compile_done(res, reporter)
        if not res.ok:
            self._fault_status("Build and Run", len(res.diagnostics))
            return
        if (res.check and res.check.ast is not None and
                self.var_target.get() in ("", "host")):
            if res.executable is None:
                self.log.log("No executable produced (no PROGRAM entry point) — nothing to run.",
                             "warn")
                return
            exe = Path(res.executable)
            self.run_process([str(exe)], cwd=exe.parent, what=f"Run {exe.name}")
        else:
            self.flash_run()

    def _start_job(self, what: str, fn: Callable[[], Any],
                   on_done: Callable[[Any], None]) -> None:
        self._set_busy(True, what)
        self.set_status(f"{what} running…")
        sink = self.log_sink

        def run() -> Any:
            # Anything the compiler prints lands in the log, not the console.
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                try:
                    return fn()
                finally:
                    sink.flush()

        def done(result: Any) -> None:
            self._set_busy(False)
            on_done(result)

        def failed(exc: BaseException) -> None:
            self._set_busy(False)
            self.log.log(f"✗ {what} crashed: {type(exc).__name__}: {exc}", "error")
            import traceback
            for line in traceback.format_exception(type(exc), exc, exc.__traceback__):
                for sub in line.rstrip("\n").splitlines():
                    self.log.log(sub, "raw", timestamp=False)
            self.set_status(f"{what}: internal error")

        self._job = Job(self.dispatcher, run, done, failed, name=what).start()

    def _apply_marks(self, diagnostics) -> None:
        lines = [d.line for d in diagnostics if d.line > 0 and self._is_current(d)]
        self.editor.mark_error_lines(lines)

    def _is_current(self, diag: Diagnostic) -> bool:
        """Does this diagnostic refer to the buffer in the editor?"""
        if not diag.filename:
            return True
        try:
            return Path(diag.filename).resolve() == self._run_path()
        except OSError:
            return False

    def _run_path(self) -> Optional[Path]:
        if self.current_path is not None:
            return self.current_path
        if self._tmpdir:
            return (Path(self._tmpdir) / "untitled.hal").resolve()
        return None

    def _jump_to(self, diag: Diagnostic) -> None:
        if not self._is_current(diag):
            self.log.log(f"(diagnostic is in {diag.filename}, not the open file)", "warn")
            return
        self.editor.goto(diag.line, diag.column)

    def _check_done(self, res: CheckResult, reporter: LogReporter,
                    issues: Optional[list] = None) -> None:
        self.last_result = res
        self._apply_marks(res.diagnostics)
        n = len(res.errors)
        if res.ok and issues:
            target = self.var_target.get() or "host"
            self.log.log(f"✓ HAL/S semantics OK, but {len(issues)} feature(s) are not yet "
                         f"supported on the '{target}' (no_std) target:", "warn")
            for msg in issues:
                self.log.log("  ⚠ " + msg, "warn")
            self.log.log("  The firmware build would fail with compile_error!; switch the "
                         "target to host or avoid these features.", "warn")
            self.set_status(f"Semantic Check: OK; {len(issues)} target issue(s)")
        elif res.ok:
            self.log.log("✓ Semantic Check passed — no errors.", "ok")
            self.set_status("Semantic Check: OK")
        else:
            self.log.log(f"✗ Semantic Check failed — {n} error(s). "
                         "Click a diagnostic to jump to it.", "error")
            self._fault_status("Semantic Check", n)
        if res.ok:
            self._fault_cleared()

    def _compile_done(self, res: GenerateResult, reporter: LogReporter) -> None:
        self.last_result = res
        self._apply_marks(res.diagnostics)
        if res.rust_path is not None:
            self.last_rust_path = Path(res.rust_path)
            self.log.log(f"View generated Rust: {res.rust_path}", "info",
                         on_click=self.view_rust)
        if res.ok:
            extra = f" → {res.executable}" if res.executable else ""
            self.log.log(f"✓ Compile succeeded{extra}", "ok")
            self.set_status("Compile: OK")
            self._fault_cleared()
        else:
            n = len(res.diagnostics)
            self.log.log(f"✗ Compile failed — {n} error(s).", "error")
            self._fault_status("Compile", n)

    # ------------------------------------------------------ toolchain/devices --

    def refresh_toolchain(self) -> None:
        """Probe rustup/cargo/… on a background thread and fill the checklist.

        Read-only, so it does not block the editor or Compile; only the
        panel's Refresh button is disabled while it runs."""
        if self._probe_job is not None and self._probe_job.alive:
            return
        self.target_panel.show_toolchain(None, [])
        self.target_panel.btn_refresh_tc.configure(state="disabled")

        def finish() -> None:
            try:
                self.target_panel.btn_refresh_tc.configure(state="normal")
            except tk.TclError:  # pragma: no cover - window gone
                pass

        def done(status: ToolchainStatus) -> None:
            finish()
            self.toolchain_status = status
            self._show_requirements()
            for ln in status.summary_lines():
                self.log.log("toolchain: " + ln, "info")
            if not status.rustc.present:
                self.log.log(toolchain.rustc_missing_warning(), "warn")

        def failed(exc: BaseException) -> None:
            finish()
            self.log.log(f"toolchain probe failed: {exc}", "error")

        self._probe_job = Job(self.dispatcher, toolchain.probe, done, failed,
                              name="toolchain-probe").start()

    @property
    def probing(self) -> bool:
        return self._probe_job is not None and self._probe_job.alive

    def _show_requirements(self) -> None:
        st = self.toolchain_status
        reqs = toolchain.requirements(self.var_target.get() or "host", st) if st else []
        self.target_panel.show_toolchain(st, reqs)
        if st:
            miss = toolchain.missing(reqs)
            self.set_status("Toolchain: OK for " + self.var_target.get()
                            if not miss else f"Toolchain: {len(miss)} missing")

    def refresh_devices(self) -> None:
        ports = devices.list_serial_ports()
        probes = devices.list_probes() if (self.toolchain_status and
                                           self.toolchain_status.probe_rs.present) else []
        self.target_panel.show_ports(ports, probes)
        self.log.log("devices: " + (", ".join(p.device for p in ports) or "no serial ports"), "info")
        for ln in probes[:4]:
            self.log.log("probe-rs: " + ln, "info")

    def install_requirement(self, req: Requirement) -> None:
        if not req.install_argv:
            self.log.log(f"{req.label}: cannot install automatically — {req.detail}", "warn")
            return
        self.run_process(req.install_argv, what=f"Install {req.label}",
                         on_done=lambda _r: self.refresh_toolchain())

    # ------------------------------------------------------------ processes --

    def run_process(self, argv, *, cwd=None, env=None, what: str = "Process",
                    on_done: Optional[Callable[[ProcessResult], None]] = None,
                    line_hook: Optional[Callable[[str], None]] = None,
                    header_suffix: str = "") -> bool:
        """Stream an external command into the log.  One at a time; Stop cancels."""
        if self.busy:
            self.log.log("Another job is running — Stop it first.", "warn")
            return False
        import shlex
        shown = " ".join(shlex.quote(str(a)) for a in argv)
        self.log.log(f"── {what} ──{header_suffix}", "cmd")
        self.log.log(("$ " if not cwd else f"$ (cd {cwd}) ") + shown, "cmd")

        def on_line(line: str) -> None:
            self.log.log(line, "raw", timestamp=False)
            if line_hook is not None:
                line_hook(line)

        def on_exit(res: ProcessResult) -> None:
            self._set_busy(False)
            if res.error:
                self.log.log(f"✗ {what}: {res.error}", "error")
                self.set_status(f"{what}: failed")
            elif res.cancelled:
                self.log.log(f"■ {what} stopped (exit {res.returncode}, {res.duration:.1f}s)", "warn")
                self.set_status(f"{what}: stopped")
            elif res.ok:
                self.log.log(f"✓ {what} finished (exit 0, {res.duration:.1f}s)", "ok")
                self.set_status(f"{what}: OK")
            else:
                self.log.log(f"✗ {what} failed (exit {res.returncode}, {res.duration:.1f}s)", "error")
                self.set_status(f"{what}: exit {res.returncode}")
            if on_done is not None and res.error is None:
                on_done(res)

        self._set_busy(True, what)
        self.set_status(f"{what} running…")
        started = self.runner.start(argv, cwd=cwd, env=env, on_line=on_line, on_exit=on_exit)
        if not started:
            self._set_busy(False)
        return started

    def stop(self) -> None:
        if self.runner.running:
            self.log.log("Stopping…", "warn")
            self.runner.cancel()

    # -------------------------------------------------------------- actions --

    def _project_dir(self) -> Optional[Path]:
        """The --emit-cargo directory for the current source (None if unset)."""
        comp = self.settings.compile
        if not comp.emit_cargo:
            return None
        base = self.current_path.parent if self.current_path else Path.cwd()
        p = Path(comp.emit_cargo)
        return p if p.is_absolute() else base / p

    def _crate_name(self, proj: Path) -> str:
        from resnik.backend.codegen import RustCodeGenerator
        return RustCodeGenerator._sanitize_crate_name(proj.name)

    def _elf_path(self, proj: Path) -> Path:
        from resnik.backend.target_spec import SPECS
        spec = SPECS.get(self.var_target.get() or "host")
        crate = self._crate_name(proj)
        if spec is not None and spec.triple:
            return proj / "target" / spec.triple / "release" / crate
        return proj / "target" / "release" / crate

    def _need_project(self) -> Optional[Path]:
        proj = self._project_dir()
        if proj is None:
            self.log.log("✗ No Cargo project: set 'Cargo project dir (--emit-cargo)' in "
                         "Options… and Compile first.", "error")
            return None
        if not (proj / "Cargo.toml").exists():
            self.log.log(f"✗ {proj}/Cargo.toml not found — run Compile first (it scaffolds "
                         "the project).", "error")
            return None
        return proj

    @staticmethod
    def _espflash_extra(st: Optional[ToolchainStatus]) -> list:
        """espflash's interactive monitor needs a real stdin; the IDE runs it
        with stdin=/dev/null, so ask for --non-interactive (espflash >= 3;
        output still streams, only the CTRL+R/CTRL+C key reader is skipped)."""
        import re
        version = (st.espflash.version or "") if st else ""
        m = re.search(r"(\d+)\.", version)
        if m and int(m.group(1)) < 3:
            return []
        return ["--non-interactive"]

    def _preflight(self) -> bool:
        """Toolchain requirements for the current target; log what is missing."""
        st = self.toolchain_status
        if st is None:
            self.log.log("Toolchain not probed yet — press Refresh in the Target panel.", "warn")
            return False
        miss = toolchain.missing(toolchain.requirements(self.var_target.get() or "host", st))
        for r in miss:
            hint = " — click Install in the Target panel" if r.installable else f" — {r.detail}"
            self.log.log(f"✗ missing: {r.label}{hint}", "error")
        return not miss

    def build(self) -> None:
        if self.busy:
            return
        proj = self._need_project()
        if proj is None or not self._preflight():
            return
        cargo = self.toolchain_status.cargo.path or "cargo"
        self.run_process([cargo, "build", "--release"], cwd=proj, what="Build")

    def flash_run(self) -> None:
        if self.busy:
            return
        target = self.var_target.get() or "host"
        if target == "host":
            self.run_host()
            return
        proj = self._need_project()
        if proj is None or not self._preflight():
            return
        st = self.toolchain_status
        if target == "esp32-c3":
            argv = [st.cargo.path or "cargo", "run", "--release"]
            port = self.var_port.get().strip()
            extra = []
            if port:
                extra += ["--port", port]
            else:
                self.log.log("No serial port selected — espflash will auto-detect / ask.", "warn")
            extra += self._espflash_extra(st)
            if extra:
                argv += ["--", *extra]
            self.run_process(argv, cwd=proj, what="Flash & Run (espflash)",
                             line_hook=self._met_line_hook,
                             header_suffix=f"  {self.met()}")
        elif target == "cortex-m":
            chip = self.var_chip.get().strip()
            if not chip:
                self.log.log("✗ Enter the MCU name for probe-rs (e.g. STM32F411CEUx) in the "
                             "Target panel.", "error")
                return
            elf = self._elf_path(proj)
            probe = st.probe_rs.path or "probe-rs"

            def after_build(res: ProcessResult) -> None:
                if res.ok:
                    self.run_process([probe, "run", "--chip", chip, str(elf)], cwd=proj,
                                     what="Flash & Run (probe-rs)",
                                     line_hook=self._met_line_hook,
                                     header_suffix=f"  {self.met()}")

            self.run_process([st.cargo.path or "cargo", "build", "--release"], cwd=proj,
                             what="Build", on_done=after_build)
        else:
            self.log.log(f"No flash flow for target '{target}'.", "warn")

    def monitor(self) -> None:
        if self.busy:
            return
        target = self.var_target.get() or "host"
        st = self.toolchain_status
        if target == "esp32-c3":
            if st is None or not st.espflash.present:
                self.log.log("✗ espflash not found — install it from the Target panel.", "error")
                return
            argv = [st.espflash.path, "monitor"]
            port = self.var_port.get().strip()
            if port:
                argv += ["--port", port]
            argv += self._espflash_extra(st)
            self.run_process(argv, what="Monitor (espflash)")
        elif target == "cortex-m":
            if st is None or not st.probe_rs.present:
                self.log.log("✗ probe-rs not found — install it from the Target panel.", "error")
                return
            proj = self._need_project()
            chip = self.var_chip.get().strip()
            if proj is None or not chip:
                self.log.log("✗ Need a Cargo project and a chip name for probe-rs attach.", "error")
                return
            self.run_process([st.probe_rs.path, "attach", "--chip", chip, str(self._elf_path(proj))],
                             cwd=proj, what="Monitor (probe-rs attach)")
        else:
            self.log.log("Monitor applies to embedded targets; use Run on host instead.", "warn")

    def run_host(self) -> None:
        """Execute the most recent host build and stream its output."""
        if self.busy:
            return
        exe: Optional[Path] = None
        res = self.last_result
        if isinstance(res, GenerateResult) and res.executable:
            exe = Path(res.executable)
        if exe is None or not exe.exists():
            proj = self._project_dir()
            if proj is not None:
                cand = proj / "target" / "release" / self._crate_name(proj)
                if cand.exists():
                    exe = cand
        if exe is None or not exe.exists():
            self.log.log("✗ Nothing to run: Compile with 'Build executable (--emit-exe)' ticked, "
                         "or Build the Cargo project first.", "error")
            return
        self.run_process([str(exe)], cwd=exe.parent, what=f"Run {exe.name}")

    # -------------------------------------------------------------- extras --

    def view_rust(self) -> None:
        if not self.last_rust_path or not self.last_rust_path.exists():
            self.log.log("No generated Rust yet — run Compile first.", "warn")
            return
        win = tk.Toplevel(self)
        win.title(f"Generated Rust — {self.last_rust_path.name}")
        win.geometry("900x650")
        win.configure(background=theme.palette()["bg"])
        path = self.last_rust_path
        # Header strip: what this is, where it lives, and the two things you
        # can do with read-only output.
        head = ttk.Frame(win, padding=(8, 4))
        head.pack(side="top", fill="x")
        ttk.Label(head, text="Generated Rust", style="Title.TLabel").pack(side="left")
        ttk.Label(head, text=f" · read-only · {path}", style="Muted.TLabel").pack(
            side="left", fill="x", expand=True)
        ttk.Button(head, text="Open folder",
                   command=lambda: self._open_dir(path.parent, "Rust output folder")).pack(
            side="right")
        ttk.Button(head, text="Copy all", command=lambda: self._copy_viewer()).pack(
            side="right", padx=(0, 6))
        ttk.Separator(win, orient="horizontal").pack(side="top", fill="x")
        # Reuse the editor widget: line-number gutter, theme, fonts — in viewer
        # mode (tinted background) with the Rust highlighter; read-only, but
        # selection/copy still work.
        viewer = Editor(win)
        viewer.pack(fill="both", expand=True)
        viewer.set_viewer_mode(True, highlighter=rust_highlight.compute_spans)
        viewer.set_font_size(self.editor.font_size)
        viewer.set_font_family(self.editor.font_family)
        viewer.set_text(path.read_text(encoding="utf-8"))
        viewer.text.configure(state="disabled")
        win.bind("<Escape>", lambda _e: win.destroy())
        self._rust_viewer = viewer
        self._rust_window = win

    def _copy_viewer(self) -> None:
        viewer = getattr(self, "_rust_viewer", None)
        if viewer is None:
            return
        self.clipboard_clear()
        self.clipboard_append(viewer.get_text())
        self.log.log("Generated Rust copied to the clipboard.", "info")

    IMAGES_DIR = Path(__file__).resolve().parent.parent / "images"

    def _set_window_icon(self) -> None:
        """Use the Resnik logo as the window/taskbar icon (inherited by every
        Toplevel: dialogs, About, Rust viewer).  Silently skipped if the PNGs
        are missing or Tk refuses."""
        self._icon_images = []
        for name in ("resnik_logo.png", "resnik_icon_64.png", "resnik_icon_32.png"):
            path = self.IMAGES_DIR / name
            if path.exists():
                try:
                    self._icon_images.append(tk.PhotoImage(file=str(path)))
                except tk.TclError:
                    pass
        if self._icon_images:
            try:
                self.iconphoto(True, *self._icon_images)
            except tk.TclError:
                self._icon_images = []

    def install_desktop_entry(self) -> Optional[Path]:
        """Write ~/.local/share/applications/resnik-ide.desktop so the desktop
        shows the Resnik logo for this window and offers a launcher
        (Options… → Desktop tab, or ``resnik-ide --install-desktop``)."""
        try:
            path = desktop.install_desktop_entry()
        except OSError as exc:
            self.log.log(f"✗ Could not install desktop entry: {exc}", "error")
            return None
        self.log.log(f"✓ Desktop entry installed: {path}", "ok")
        self.log.log("  GNOME picks the icon up for new windows; log out/in if the "
                     "dash still shows the generic one.", "info")
        return path

    def uninstall_desktop_entry(self) -> bool:
        try:
            removed = desktop.uninstall_desktop_entry()
        except OSError as exc:
            self.log.log(f"✗ Could not remove desktop entry: {exc}", "error")
            return False
        self.log.log("✓ Desktop entry removed." if removed else "No desktop entry to remove.",
                     "ok" if removed else "info")
        return removed

    ABOUT_TEXT = (
        "Resnik is an open-source compiler for HAL/S — the real-time aerospace "
        "language written for the Space Shuttle's flight software — that "
        "translates legacy HAL/S source into safe, modern Rust. Programs can be "
        "built as native host executables or as bare-metal firmware for "
        "embedded targets such as ARM Cortex-M and ESP32-C3.\n\n"
        "This IDE wraps the compiler: edit HAL/S, run a Semantic Check, compile "
        "to Rust, build and run, and flash hardware — with everything logged."
    )

    def about(self) -> None:
        """About window: logo, short description, versions, license."""
        win = tk.Toplevel(self)
        win.title("About Resnik")
        win.resizable(False, False)
        win.configure(background=theme.palette()["bg"])
        win.transient(self)
        body = ttk.Frame(win, padding=14)
        body.pack(fill="both", expand=True)

        logo_path = self.IMAGES_DIR / "resnik_logo.png"
        self._about_logo = None
        if logo_path.exists():
            try:
                self._about_logo = tk.PhotoImage(file=str(logo_path))
            except tk.TclError:
                self._about_logo = None
        if self._about_logo is not None:
            ttk.Label(body, image=self._about_logo).grid(row=0, column=0, rowspan=4,
                                                          padx=(0, 16), sticky="n")
        ttk.Label(body, text="Resnik HAL/S Compiler", style="Title.TLabel").grid(
            row=0, column=1, sticky="w")
        ttk.Label(body, text=f"Compiler {compiler_version} · IDE {__version__} · "
                             f"Python {sys.version.split()[0]}",
                  style="Muted.TLabel").grid(row=1, column=1, sticky="w", pady=(0, 8))
        ttk.Label(body, text=self.ABOUT_TEXT, wraplength=420, justify="left").grid(
            row=2, column=1, sticky="w")
        ttk.Label(body, text="License: GPL-3.0-or-later · github.com/bitWarrior/resnik",
                  style="Muted.TLabel").grid(row=3, column=1, sticky="sw", pady=(10, 0))
        ttk.Button(body, text="OK", command=win.destroy, default="active").grid(
            row=4, column=0, columnspan=2, sticky="e", pady=(12, 0))
        win.bind("<Escape>", lambda _e: win.destroy())
        win.bind("<Return>", lambda _e: win.destroy())
        self._about_window = win
        return None

    def on_close(self) -> None:
        if self.busy:
            if not messagebox.askyesno(
                "Job running", "A job is still running. Quit anyway?", parent=self
            ):
                return
            self.runner.cancel()
        if not self._confirm_discard():
            return
        self.shutdown()

    def shutdown(self) -> None:
        """Tear down without prompting (tests and on_close)."""
        try:
            if self.state() == "normal":
                self.settings.geometry = self.geometry()
                lh, h = self._sash_positions()
                if lh > 0:
                    self.settings.log_height = lh
                if h > 0:
                    self.settings.sash_target_panel = h
                self._save_settings()
        except tk.TclError:  # pragma: no cover
            pass
        self.dispatcher.stop()
        for aid in (getattr(self, "_remember_after", None),
                    getattr(self, "_sash_restore_after", None)):
            if aid is not None:
                try:
                    self.after_cancel(aid)
                except tk.TclError:  # pragma: no cover
                    pass
        if self.runner.running:
            self.runner.cancel()
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None
        try:
            self.destroy()
        except tk.TclError:  # pragma: no cover - already destroyed
            pass


def main(argv: Optional[list] = None) -> int:
    """Console-script entry point: ``resnik-ide [file.hal]``.

    ``--install-desktop`` / ``--uninstall-desktop`` manage the freedesktop
    launcher entry (application icon in GNOME & co.) and exit without
    opening a window.
    """
    argv = sys.argv[1:] if argv is None else list(argv)
    if "--install-desktop" in argv:
        path = desktop.install_desktop_entry()
        print(f"Installed {path}")
        return 0
    if "--uninstall-desktop" in argv:
        removed = desktop.uninstall_desktop_entry()
        print("Removed desktop entry." if removed else "No desktop entry installed.")
        return 0
    try:
        app = ResnikIDE()
    except tk.TclError as exc:
        print(
            "resnik-ide: could not open a display / initialise Tk "
            f"({exc}).\nOn Debian/Ubuntu install the 'python3-tk' package and "
            "make sure DISPLAY is set.",
            file=sys.stderr,
        )
        return 2
    if argv:
        target = argv[0]
        if os.path.exists(target):
            app.open_file(target)
        else:
            app.log.log(f"File not found: {target}", "error")
    app.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
