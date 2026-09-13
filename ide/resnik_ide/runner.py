"""
Streamed, cancellable subprocess runner for external tools (rustc, cargo,
rustup, espflash, probe-rs, the built program …).

One process at a time.  Output lines are delivered on the Tk main thread via
the :class:`~resnik_ide.dispatch.Dispatcher`; ``cancel()`` kills the whole
process group (a serial monitor never exits on its own).
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Union

from resnik_ide.dispatch import Dispatcher

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\r")

# Keep tool output plain: no colours, no progress-bar redraws.
QUIET_ENV: Dict[str, str] = {
    "NO_COLOR": "1",
    "CARGO_TERM_COLOR": "never",
    "CARGO_TERM_PROGRESS_WHEN": "never",
    "RUST_BACKTRACE": "1",
    "TERM": "dumb",
}

GRACE_SECONDS = 3.0  # SIGTERM → SIGKILL

CARGO_BIN = os.path.join(os.path.expanduser("~"), ".cargo", "bin")


def strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


@dataclass
class ProcessResult:
    argv: List[str]
    returncode: Optional[int]
    cancelled: bool = False
    duration: float = 0.0
    error: Optional[str] = None  # e.g. executable not found

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.cancelled and self.error is None


@dataclass
class _Active:
    proc: subprocess.Popen
    argv: List[str]
    started: float
    on_line: Callable[[str], None]
    on_exit: Callable[[ProcessResult], None]
    cancelled: bool = False
    killer: Optional[threading.Timer] = None
    delivered: bool = False  # on_exit has run on the main thread


class ProcessRunner:
    """Run one external command at a time, streaming its output."""

    def __init__(self, dispatcher: Dispatcher) -> None:
        self._dispatcher = dispatcher
        self._active: Optional[_Active] = None
        self._lock = threading.Lock()
        self.history: List[ProcessResult] = []

    # -- state -----------------------------------------------------------------

    @property
    def running(self) -> bool:
        """True until the exit result has been DELIVERED on the main thread —
        the process may already be dead while its final output lines are
        still queued; callers polling ``running`` must not observe an empty
        log in that window."""
        a = self._active
        return a is not None and (a.proc.poll() is None or not a.delivered)

    @property
    def current_argv(self) -> Optional[List[str]]:
        a = self._active
        return list(a.argv) if a and a.proc.poll() is None else None

    # -- start / cancel ------------------------------------------------------------

    def start(
        self,
        argv: Sequence[str],
        *,
        cwd: Union[str, os.PathLike, None] = None,
        env: Optional[Dict[str, str]] = None,
        on_line: Callable[[str], None],
        on_exit: Callable[[ProcessResult], None],
    ) -> bool:
        """Launch ``argv``.  Returns False (and calls ``on_exit`` with an
        ``error``) if another process is running or the executable is missing.
        ``on_line``/``on_exit`` are invoked on the main thread."""
        argv = [str(a) for a in argv]
        with self._lock:
            if self.running:
                on_exit(ProcessResult(argv, None, error="another process is still running"))
                return False
            full_env = dict(os.environ)
            full_env.update(QUIET_ENV)
            # cargo-installed tools (espflash, probe-rs) live in ~/.cargo/bin,
            # which is often missing from PATH; the scaffold's `cargo run`
            # runner must be able to find them.
            if os.path.isdir(CARGO_BIN) and CARGO_BIN not in                     full_env.get("PATH", "").split(os.pathsep):
                full_env["PATH"] = full_env.get("PATH", "") + os.pathsep + CARGO_BIN
            if env:
                full_env.update(env)
            kwargs: Dict[str, object] = {}
            if sys.platform.startswith("win"):  # pragma: no cover - windows
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            else:
                kwargs["start_new_session"] = True
            try:
                proc = subprocess.Popen(
                    argv,
                    cwd=str(cwd) if cwd else None,
                    env=full_env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    **kwargs,
                )
            except OSError as exc:
                on_exit(ProcessResult(argv, None, error=f"{argv[0]}: {exc}"))
                return False
            self._active = _Active(proc, argv, time.monotonic(), on_line, on_exit)
        threading.Thread(target=self._pump, args=(self._active,),
                         name="resnik-runner", daemon=True).start()
        return True

    def cancel(self) -> bool:
        """Terminate the running process group; SIGKILL after a grace period."""
        a = self._active
        if a is None or a.proc.poll() is not None:
            return False
        a.cancelled = True
        self._signal(a.proc, signal.SIGTERM)
        a.killer = threading.Timer(GRACE_SECONDS, lambda: self._signal(a.proc, signal.SIGKILL))
        a.killer.daemon = True
        a.killer.start()
        return True

    @staticmethod
    def _signal(proc: subprocess.Popen, sig: int) -> None:
        if proc.poll() is not None:
            return
        try:
            if sys.platform.startswith("win"):  # pragma: no cover - windows
                if sig == signal.SIGKILL:
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                                   capture_output=True)
                else:
                    proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    # -- worker ----------------------------------------------------------------------

    def _pump(self, a: _Active) -> None:
        assert a.proc.stdout is not None
        try:
            for raw in a.proc.stdout:
                line = strip_ansi(raw.rstrip("\n"))
                self._dispatcher.post(lambda ln=line: a.on_line(ln))
        finally:
            rc = a.proc.wait()
            if a.killer is not None:
                a.killer.cancel()
            result = ProcessResult(a.argv, rc, cancelled=a.cancelled,
                                   duration=time.monotonic() - a.started)
            self.history.append(result)

            def deliver() -> None:
                a.delivered = True
                a.on_exit(result)

            self._dispatcher.post(deliver)
