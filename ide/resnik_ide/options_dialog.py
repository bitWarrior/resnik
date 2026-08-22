"""
Compiler Options dialog — every ``resnik compile`` flag, plus a free-form
extra-arguments field, with a live "equivalent command" preview.

Usage::

    dlg = OptionsDialog(parent, settings, source=current_file)
    new_settings = dlg.show()        # None if cancelled

``show()`` is modal.  Tests build the dialog, poke the widgets/vars, and
call ``collect()`` / ``_on_ok()`` directly without ``show()``.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable, List, Optional, Union

from resnik.backend.target_spec import TARGET_NAMES

from resnik_ide.options import OPT_LEVELS, IdeCompileSettings

_PAD = {"padx": 6, "pady": 3}


class _ListField(ttk.Frame):
    """One-entry-per-line text box with Add…/Remove buttons."""

    def __init__(self, master, *, browse: Optional[str] = None,
                 on_change: Optional[Callable[[], None]] = None, height: int = 4):
        super().__init__(master)
        self.text = tk.Text(self, height=height, width=48, wrap="none", undo=True)
        self.text.grid(row=0, column=0, sticky="nsew", rowspan=3)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        sb.grid(row=0, column=1, sticky="ns", rowspan=3)
        self.text.configure(yscrollcommand=sb.set)
        self.columnconfigure(0, weight=1)
        if browse == "dir":
            ttk.Button(self, text="Add dir…", command=self._add_dir).grid(
                row=0, column=2, sticky="ew", padx=(6, 0))
        elif browse == "file":
            ttk.Button(self, text="Add file…", command=self._add_file).grid(
                row=0, column=2, sticky="ew", padx=(6, 0))
        ttk.Button(self, text="Clear", command=self.clear).grid(
            row=1, column=2, sticky="ew", padx=(6, 0))
        self._on_change = on_change
        self.text.bind("<KeyRelease>", lambda _e: self._changed())

    def _changed(self) -> None:
        if self._on_change:
            self._on_change()

    def get(self) -> List[str]:
        return [ln.strip() for ln in self.text.get("1.0", "end-1c").splitlines()
                if ln.strip()]

    def set(self, items: List[str]) -> None:
        self.text.delete("1.0", "end")
        self.text.insert("1.0", "\n".join(items))
        self._changed()

    def append(self, item: str) -> None:
        items = self.get()
        items.append(item)
        self.set(items)

    def clear(self) -> None:
        self.set([])

    def _add_dir(self) -> None:
        d = filedialog.askdirectory(parent=self)
        if d:
            self.append(d)

    def _add_file(self) -> None:
        f = filedialog.askopenfilename(parent=self)
        if f:
            self.append(f)


class OptionsDialog(tk.Toplevel):
    """Modal editor for :class:`IdeCompileSettings`."""

    def __init__(self, master: tk.Misc, settings: IdeCompileSettings,
                 source: Union[str, Path, None] = None) -> None:
        super().__init__(master)
        self.title("Compiler Options")
        self.resizable(True, True)
        self.result: Optional[IdeCompileSettings] = None
        self._source = str(source) if source else None
        self._initial = settings.copy()

        # -- variables -----------------------------------------------------------
        self.v_target = tk.StringVar(value=settings.target)
        self.v_emit_exe = tk.BooleanVar(value=settings.emit_exe)
        self.v_opt_level = tk.StringVar(value=settings.opt_level)
        self.v_emit_cargo = tk.StringVar(value=settings.emit_cargo)
        self.v_build = tk.BooleanVar(value=settings.build)
        self.v_rust = tk.StringVar(value=settings.rust)
        self.v_tokens = tk.StringVar(value=settings.tokens)
        self.v_ast = tk.StringVar(value=settings.ast)
        self.v_gen_tpl = tk.StringVar(value=settings.gen_tpl)
        self.v_extra = tk.StringVar(value=settings.extra_args)
        self.v_preview = tk.StringVar()

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        self._build_tab_target(nb)
        self._build_tab_output(nb)
        self._build_tab_linking(nb)
        self._build_tab_templates(nb)
        self._build_tab_extra(nb)
        self._build_preview_and_buttons()

        for var in (self.v_target, self.v_emit_exe, self.v_opt_level, self.v_emit_cargo,
                    self.v_build, self.v_rust, self.v_tokens, self.v_ast,
                    self.v_gen_tpl, self.v_extra):
            var.trace_add("write", lambda *_a: self.refresh_preview())
        self.refresh_preview()

        self.bind("<Escape>", lambda _e: self._on_cancel())
        self.bind("<Return>", self._on_return)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    # -- tabs -------------------------------------------------------------------

    def _entry_row(self, parent, row: int, label: str, var: tk.StringVar,
                   browse: Optional[str] = None, hint: str = "") -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", **_PAD)
        ttk.Entry(parent, textvariable=var, width=48).grid(
            row=row, column=1, sticky="ew", **_PAD)
        if browse:
            ttk.Button(parent, text="Browse…",
                       command=lambda: self._browse(var, browse)).grid(
                row=row, column=2, sticky="ew", **_PAD)
        if hint:
            ttk.Label(parent, text=hint, foreground="#666666").grid(
                row=row + 1, column=1, sticky="w", padx=6, pady=(0, 4))
        parent.columnconfigure(1, weight=1)

    def _build_tab_target(self, nb: ttk.Notebook) -> None:
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="Target & Build")
        ttk.Label(f, text="Target profile (--target)").grid(row=0, column=0, sticky="w", **_PAD)
        self.cb_target = ttk.Combobox(f, textvariable=self.v_target, values=list(TARGET_NAMES),
                                      state="readonly", width=14)
        self.cb_target.grid(row=0, column=1, sticky="w", **_PAD)
        ttk.Label(f, text="host = std Rust on this machine; cortex-m / esp32-c3 = no_std "
                          "firmware (use with --emit-cargo)",
                  foreground="#666666", wraplength=420, justify="left").grid(
            row=1, column=1, sticky="w", padx=6, pady=(0, 8))

        ttk.Checkbutton(f, text="Build a native executable with rustc (--emit-exe)",
                        variable=self.v_emit_exe).grid(row=2, column=0, columnspan=3, sticky="w", **_PAD)
        ttk.Label(f, text="rustc optimization level (--opt-level)").grid(row=3, column=0, sticky="w", **_PAD)
        ttk.Combobox(f, textvariable=self.v_opt_level, values=list(OPT_LEVELS),
                     state="readonly", width=6).grid(row=3, column=1, sticky="w", **_PAD)

        ttk.Separator(f, orient="horizontal").grid(row=4, column=0, columnspan=3, sticky="ew", pady=8)
        self._entry_row(f, 5, "Cargo project dir (--emit-cargo)", self.v_emit_cargo, browse="dir",
                        hint="Scaffold a Cargo crate here (required for embedded targets). "
                             "Relative paths are relative to the source file.")
        ttk.Checkbutton(f, text="Run 'cargo build --release' after scaffolding (--build)",
                        variable=self.v_build).grid(row=7, column=0, columnspan=3, sticky="w", **_PAD)

    def _build_tab_output(self, nb: ttk.Notebook) -> None:
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="Output")
        self._entry_row(f, 0, "Rust output file (--rust)", self.v_rust, browse="save",
                        hint="Default: <source>.rs next to the source file.")
        self._entry_row(f, 2, "Export tokens JSON (--tokens)", self.v_tokens, browse="save")
        self._entry_row(f, 3, "Export AST JSON (--ast)", self.v_ast, browse="save")

    def _build_tab_linking(self, nb: ttk.Notebook) -> None:
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="Linking")
        ttk.Label(f, text="For EXTERNAL symbols with --emit-exe (one entry per line).",
                  foreground="#666666").grid(row=0, column=0, columnspan=2, sticky="w", **_PAD)
        ttk.Label(f, text="Linker search dirs (-L / --link-search)").grid(row=1, column=0, sticky="nw", **_PAD)
        self.lf_link_search = _ListField(f, browse="dir", on_change=self.refresh_preview)
        self.lf_link_search.grid(row=1, column=1, sticky="ew", **_PAD)
        ttk.Label(f, text="Native libraries (-l / --link-lib)").grid(row=2, column=0, sticky="nw", **_PAD)
        self.lf_link_lib = _ListField(f, on_change=self.refresh_preview, height=3)
        self.lf_link_lib.grid(row=2, column=1, sticky="ew", **_PAD)
        ttk.Label(f, text="Objects / archives (--link-obj)").grid(row=3, column=0, sticky="nw", **_PAD)
        self.lf_link_obj = _ListField(f, browse="file", on_change=self.refresh_preview)
        self.lf_link_obj.grid(row=3, column=1, sticky="ew", **_PAD)
        f.columnconfigure(1, weight=1)

    def _build_tab_templates(self, nb: ttk.Notebook) -> None:
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="Templates")
        self._entry_row(f, 0, "Generate templates into (--gen-tpl)", self.v_gen_tpl, browse="dir",
                        hint="Writes a .tpl prototype for each PROCEDURE/FUNCTION/COMPOOL (TEMPLIB).")
        ttk.Label(f, text="Template library dirs (--templib)").grid(row=2, column=0, sticky="nw", **_PAD)
        self.lf_templib = _ListField(f, browse="dir", on_change=self.refresh_preview)
        self.lf_templib.grid(row=2, column=1, sticky="ew", **_PAD)
        ttk.Label(f, text="Searched by 'D INCLUDE TEMPLATE <name>'; the source's own directory "
                          "is always searched.", foreground="#666666", wraplength=420,
                  justify="left").grid(row=3, column=1, sticky="w", padx=6)
        f.columnconfigure(1, weight=1)

    def _build_tab_extra(self, nb: ttk.Notebook) -> None:
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="Extra")
        self._entry_row(f, 0, "Extra arguments", self.v_extra,
                        hint="Appended verbatim (shell-split) and validated by the real "
                             "'resnik compile' parser when you run.")

    def _build_preview_and_buttons(self) -> None:
        pf = ttk.LabelFrame(self, text="Equivalent command", padding=6)
        pf.pack(fill="x", padx=8, pady=4)
        self.preview = ttk.Entry(pf, textvariable=self.v_preview, state="readonly")
        self.preview.pack(side="left", fill="x", expand=True)
        ttk.Button(pf, text="Copy", command=self._copy_preview).pack(side="left", padx=(6, 0))
        ttk.Label(self, text="Relative paths are resolved against the source file's directory.",
                  foreground="#666666").pack(anchor="w", padx=14)

        bf = ttk.Frame(self, padding=(8, 6))
        bf.pack(fill="x")
        ttk.Button(bf, text="Reset to defaults", command=self.reset_defaults).pack(side="left")
        ttk.Button(bf, text="Cancel", command=self._on_cancel).pack(side="right")
        self.btn_ok = ttk.Button(bf, text="OK", command=self._on_ok, default="active")
        self.btn_ok.pack(side="right", padx=(0, 6))

        # list fields are created in tabs; populate now that they exist
        self.lf_link_search.set(self._initial.link_search)
        self.lf_link_lib.set(self._initial.link_lib)
        self.lf_link_obj.set(self._initial.link_obj)
        self.lf_templib.set(self._initial.templib)

    # -- behaviour --------------------------------------------------------------

    def collect(self) -> IdeCompileSettings:
        """Current widget state as settings."""
        return IdeCompileSettings(
            target=self.v_target.get() or "host",
            emit_exe=bool(self.v_emit_exe.get()),
            opt_level=self.v_opt_level.get() or "2",
            emit_cargo=self.v_emit_cargo.get().strip(),
            build=bool(self.v_build.get()),
            rust=self.v_rust.get().strip(),
            tokens=self.v_tokens.get().strip(),
            ast=self.v_ast.get().strip(),
            link_search=self.lf_link_search.get(),
            link_lib=self.lf_link_lib.get(),
            link_obj=self.lf_link_obj.get(),
            gen_tpl=self.v_gen_tpl.get().strip(),
            templib=self.lf_templib.get(),
            extra_args=self.v_extra.get().strip(),
        )

    def apply(self, settings: IdeCompileSettings) -> None:
        """Push settings into the widgets."""
        self.v_target.set(settings.target)
        self.v_emit_exe.set(settings.emit_exe)
        self.v_opt_level.set(settings.opt_level)
        self.v_emit_cargo.set(settings.emit_cargo)
        self.v_build.set(settings.build)
        self.v_rust.set(settings.rust)
        self.v_tokens.set(settings.tokens)
        self.v_ast.set(settings.ast)
        self.v_gen_tpl.set(settings.gen_tpl)
        self.v_extra.set(settings.extra_args)
        self.lf_link_search.set(settings.link_search)
        self.lf_link_lib.set(settings.link_lib)
        self.lf_link_obj.set(settings.link_obj)
        self.lf_templib.set(settings.templib)
        self.refresh_preview()

    def reset_defaults(self) -> None:
        self.apply(IdeCompileSettings())

    def refresh_preview(self) -> None:
        try:
            self.v_preview.set(self.collect().command_line(self._source))
        except AttributeError:
            pass  # list fields not built yet during construction

    def _copy_preview(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.v_preview.get())

    def _browse(self, var: tk.StringVar, kind: str) -> None:
        if kind == "dir":
            val = filedialog.askdirectory(parent=self)
        elif kind == "save":
            val = filedialog.asksaveasfilename(parent=self)
        else:
            val = filedialog.askopenfilename(parent=self)
        if val:
            var.set(val)

    def _on_return(self, event) -> None:
        # Return inside a multi-line list field is a newline, not OK.
        if isinstance(event.widget, tk.Text):
            return
        self._on_ok()

    def _on_ok(self) -> None:
        self.result = self.collect()
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()

    def show(self) -> Optional[IdeCompileSettings]:
        """Run modally; return the new settings or None if cancelled."""
        self.transient(self.master)
        self.update_idletasks()
        try:
            self.grab_set()
        except tk.TclError:  # pragma: no cover - not viewable (tests)
            pass
        self.focus_set()
        self.wait_window(self)
        return self.result
