# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Steven Doran
"""
Main-thread dispatch for Tk.

Tk widgets may only be touched from the thread that created them.  Worker
threads therefore never call into widgets; they ``post`` callables to a
:class:`Dispatcher`, which the Tk main loop drains every ``interval_ms``.
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Callable, Optional


class Dispatcher:
    """Thread-safe queue of callables executed on the Tk main thread."""

    def __init__(self, root: Any, interval_ms: int = 50) -> None:
        self._root = root
        self._queue: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._interval = interval_ms
        self._after_id: Optional[str] = None
        self._stopped = False

    # -- scheduling ---------------------------------------------------------

    def start(self) -> None:
        self._stopped = False
        self._schedule()

    def stop(self) -> None:
        self._stopped = True
        if self._after_id is not None:
            try:
                self._root.after_cancel(self._after_id)
            except Exception:  # pragma: no cover - root already gone
                pass
            self._after_id = None

    def _schedule(self) -> None:
        if not self._stopped:
            self._after_id = self._root.after(self._interval, self._tick)

    def _tick(self) -> None:
        self.drain()
        self._schedule()

    # -- API ----------------------------------------------------------------

    def post(self, fn: Callable[[], None]) -> None:
        """Queue ``fn`` to run on the main thread (callable from any thread)."""
        self._queue.put(fn)

    def drain(self) -> int:
        """Run everything queued so far (main thread only). Returns count."""
        n = 0
        while True:
            try:
                fn = self._queue.get_nowait()
            except queue.Empty:
                return n
            n += 1
            fn()

    @property
    def pending(self) -> int:
        return self._queue.qsize()


class Job:
    """Run ``fn`` on a worker thread; deliver the outcome via a Dispatcher.

    Exactly one of ``on_done(result)`` / ``on_error(exc)`` is posted to the
    main thread when ``fn`` returns or raises.
    """

    def __init__(
        self,
        dispatcher: Dispatcher,
        fn: Callable[[], Any],
        on_done: Callable[[Any], None],
        on_error: Callable[[BaseException], None],
        name: str = "resnik-job",
    ) -> None:
        self._dispatcher = dispatcher
        self._fn = fn
        self._on_done = on_done
        self._on_error = on_error
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    def start(self) -> "Job":
        self._thread.start()
        return self

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def _run(self) -> None:
        try:
            result = self._fn()
        except BaseException as exc:  # noqa: BLE001 - surfaced to the UI
            err = exc  # `exc` is unbound once the except block exits
            self._dispatcher.post(lambda: self._on_error(err))
        else:
            self._dispatcher.post(lambda: self._on_done(result))
