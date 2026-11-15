# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Steven Doran
"""Bridge ``resnik.driver.Reporter`` events into the IDE log."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Optional

from resnik.driver import Diagnostic, Reporter

from resnik_ide.logview import LogSink


class LogReporter(Reporter):
    """Writes driver progress to a :class:`LogSink`.

    ``on_diagnostic`` (optional) is invoked on the main thread when the user
    clicks a diagnostic line — the app uses it to jump the editor.
    """

    def __init__(
        self,
        sink: LogSink,
        on_diagnostic: Optional[Callable[[Diagnostic], None]] = None,
    ) -> None:
        self._sink = sink
        self._on_diagnostic = on_diagnostic
        self.diagnostics: list = []

    @contextmanager
    def phase(self, title: str):
        self._sink.log(title + " …", "info")
        yield

    def success(self, message: str) -> None:
        self._sink.log("✓ " + message, "ok")

    def info(self, message: str) -> None:
        self._sink.log("ℹ " + message, "info")

    def error(self, message: str) -> None:
        self._sink.log("✗ " + message, "error")

    def diagnostic(self, diag: Diagnostic) -> None:
        self.diagnostics.append(diag)
        cb = None
        if self._on_diagnostic is not None and diag.line > 0:
            cb = lambda d=diag: self._on_diagnostic(d)  # noqa: E731
        self._sink.log("  " + str(diag), "diag", on_click=cb)

    def raw(self, text: str) -> None:
        for line in text.rstrip("\n").splitlines():
            self._sink.log(line, "raw")
