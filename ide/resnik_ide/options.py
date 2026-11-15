# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Steven Doran
"""
User-editable compiler arguments for the IDE.

:class:`IdeCompileSettings` is the *source-independent* part of a
``resnik compile`` invocation (everything except the input file), as the
user edits it in the Options dialog and as it is persisted in ``ide.json``.

Turning it into something runnable goes through the real CLI parser, so the
IDE can never drift from the shell: ``cli_args(source)`` builds the argument
list (including the free-form *extra arguments*), and
:meth:`to_compile_options` hands that list to
:func:`resnik.cli.parse_compile_args`.  Invalid input therefore fails with
the same message the shell would print.

Relative paths are anchored at the source file's directory (a GUI has no
meaningful cwd); the equivalent command shown to the user says so.
"""

from __future__ import annotations

import shlex
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Union

from resnik.backend.target_spec import TARGET_NAMES
from resnik.driver import CompileOptions

OPT_LEVELS = ("0", "1", "2", "3", "s", "z")


class OptionsError(ValueError):
    """The current settings cannot be turned into a valid compile command."""


@dataclass
class IdeCompileSettings:
    """Compiler arguments as edited in the IDE (all paths as typed)."""

    target: str = "host"
    emit_exe: bool = False
    opt_level: str = "2"
    emit_cargo: str = ""
    build: bool = False
    rust: str = ""
    tokens: str = ""
    ast: str = ""
    link_search: List[str] = field(default_factory=list)
    link_lib: List[str] = field(default_factory=list)
    link_obj: List[str] = field(default_factory=list)
    gen_tpl: str = ""
    templib: List[str] = field(default_factory=list)
    extra_args: str = ""  # appended verbatim (shell-split) — power-user escape hatch

    # -- (de)serialisation ---------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IdeCompileSettings":
        """Tolerant: unknown keys are ignored, missing keys keep defaults,
        wrong-typed values fall back to the default for that field."""
        defaults = cls()
        kwargs: Dict[str, Any] = {}
        for f in fields(cls):
            if f.name not in data:
                continue
            val = data[f.name]
            default = getattr(defaults, f.name)
            if isinstance(default, bool):
                kwargs[f.name] = bool(val)
            elif isinstance(default, list):
                if isinstance(val, (list, tuple)):
                    kwargs[f.name] = [str(v) for v in val if str(v).strip()]
            elif isinstance(val, (str, int, float)):
                kwargs[f.name] = str(val)
        obj = cls(**kwargs)
        if obj.target not in TARGET_NAMES:
            obj.target = defaults.target
        if obj.opt_level not in OPT_LEVELS:
            obj.opt_level = defaults.opt_level
        return obj

    def copy(self) -> "IdeCompileSettings":
        return IdeCompileSettings.from_dict(self.to_dict())

    # -- command line ----------------------------------------------------------

    def extra_argv(self) -> List[str]:
        """The extra-arguments field, shell-split.  Raises OptionsError."""
        try:
            return shlex.split(self.extra_args or "")
        except ValueError as exc:
            raise OptionsError(f"Extra arguments: {exc}") from None

    def cli_args(self, source: Union[str, Path]) -> List[str]:
        """``resnik compile`` arguments (without the two leading words)."""
        args: List[str] = [str(source)]
        if self.tokens:
            args += ["--tokens", self.tokens]
        if self.ast:
            args += ["--ast", self.ast]
        if self.rust:
            args += ["--rust", self.rust]
        if self.emit_exe:
            args.append("--emit-exe")
        if self.opt_level and self.opt_level != "2":
            args += ["--opt-level", self.opt_level]
        for d in self.link_search:
            args += ["--link-search", d]
        for lib in self.link_lib:
            args += ["--link-lib", lib]
        for obj in self.link_obj:
            args += ["--link-obj", obj]
        if self.emit_cargo:
            args += ["--emit-cargo", self.emit_cargo]
        if self.build:
            args.append("--build")
        if self.target and self.target != "host":
            args += ["--target", self.target]
        if self.gen_tpl:
            args += ["--gen-tpl", self.gen_tpl]
        for t in self.templib:
            args += ["--templib", t]
        args += self.extra_argv()
        return args

    def command_line(self, source: Union[str, Path, None] = None) -> str:
        """Shell-quoted equivalent command, for the preview line / log."""
        src = str(source) if source else "<file>.hal"
        try:
            args = self.cli_args(src)
        except OptionsError as exc:
            return f"resnik compile {shlex.quote(src)} … ({exc})"
        return "resnik compile " + " ".join(shlex.quote(a) for a in args)

    def to_compile_options(self, source: Union[str, Path]) -> CompileOptions:
        """Validate through the real CLI parser and anchor relative paths at
        the source file's directory.  Raises :class:`OptionsError`."""
        import click

        from resnik.cli import parse_compile_args

        source = Path(source)
        args = self.cli_args(source)
        try:
            opts = parse_compile_args(args)
        except click.exceptions.Exit:
            raise OptionsError(
                "Extra arguments: --help/--version are not valid here.") from None
        except click.ClickException as exc:
            raise OptionsError(exc.format_message()) from None
        return opts.resolved_relative_to(source.parent)
