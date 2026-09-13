"""ProcessRunner — real subprocesses (python -c …), no display needed."""

import sys
import time

import pytest

from resnik_ide.dispatch import Dispatcher
from resnik_ide.runner import ProcessRunner, strip_ansi

PY = sys.executable


def _drive(d: Dispatcher, until, timeout=15.0):
    deadline = time.monotonic() + timeout
    while not until():
        d.drain()
        if time.monotonic() > deadline:
            raise TimeoutError("runner did not finish")
        time.sleep(0.02)
    d.drain()


class TestStrip:
    def test_strip_ansi_and_cr(self):
        assert strip_ansi("\x1b[1;32m   Compiling\x1b[0m foo\r") == "   Compiling foo"
        assert strip_ansi("plain") == "plain"


class TestRunner:
    def test_streams_lines_in_order_and_reports_exit_code(self):
        d = Dispatcher(root=None)
        r = ProcessRunner(d)
        lines, done = [], []
        ok = r.start(
            [PY, "-u", "-c", "import sys; print('one'); print('err', file=sys.stderr); "
                       "sys.stdout.flush(); print('two'); sys.exit(3)"],
            on_line=lines.append, on_exit=done.append)
        assert ok is True
        assert r.running or done  # may already have finished
        _drive(d, lambda: bool(done))
        assert lines == ["one", "err", "two"]
        res = done[0]
        assert res.returncode == 3 and res.ok is False and res.cancelled is False
        assert res.error is None and res.duration >= 0
        assert r.running is False and r.history[-1] is res

    def test_success_is_ok(self):
        d = Dispatcher(root=None)
        r = ProcessRunner(d)
        done = []
        r.start([PY, "-c", "print('hi')"], on_line=lambda _l: None, on_exit=done.append)
        _drive(d, lambda: bool(done))
        assert done[0].ok is True and done[0].returncode == 0

    def test_quiet_env_and_cwd_and_extra_env(self, tmp_path):
        d = Dispatcher(root=None)
        r = ProcessRunner(d)
        lines, done = [], []
        r.start([PY, "-c", "import os; print(os.getcwd()); print(os.environ['NO_COLOR'], "
                           "os.environ['CARGO_TERM_COLOR'], os.environ.get('MYVAR'))"],
                cwd=tmp_path, env={"MYVAR": "42"},
                on_line=lines.append, on_exit=done.append)
        _drive(d, lambda: bool(done))
        assert lines[0] == str(tmp_path.resolve()) or lines[0] == str(tmp_path)
        assert lines[1] == "1 never 42"

    def test_missing_executable_reports_error(self):
        d = Dispatcher(root=None)
        r = ProcessRunner(d)
        done = []
        ok = r.start(["/definitely/not/a/program"], on_line=lambda _l: None,
                     on_exit=done.append)
        assert ok is False
        assert done and done[0].error and "not/a/program" in done[0].error
        assert done[0].ok is False and r.running is False

    def test_only_one_process_at_a_time(self):
        d = Dispatcher(root=None)
        r = ProcessRunner(d)
        done1, done2 = [], []
        r.start([PY, "-c", "import time; time.sleep(2)"], on_line=lambda _l: None,
                on_exit=done1.append)
        ok = r.start([PY, "-c", "print(1)"], on_line=lambda _l: None, on_exit=done2.append)
        assert ok is False and "still running" in done2[0].error
        assert r.cancel() is True
        _drive(d, lambda: bool(done1))

    @pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX process groups")
    def test_cancel_kills_process_group_and_reports_cancelled(self):
        d = Dispatcher(root=None)
        r = ProcessRunner(d)
        lines, done = [], []
        # Parent spawns a child that sleeps; both must die on cancel.
        script = (
            "import subprocess, sys, time\n"
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "print('child', c.pid, flush=True)\n"
            "time.sleep(60)\n"
        )
        r.start([PY, "-c", script], on_line=lines.append, on_exit=done.append)
        _drive(d, lambda: bool(lines))  # wait for the child pid line
        child_pid = int(lines[0].split()[1])
        t0 = time.monotonic()
        assert r.cancel() is True
        _drive(d, lambda: bool(done))
        assert time.monotonic() - t0 < 10
        res = done[0]
        assert res.cancelled is True and res.ok is False and res.returncode != 0
        # child is gone too (killed with the group)
        import os
        deadline = time.monotonic() + 5
        alive = True
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
                # may be a zombie until reaped by init; check /proc state if available
                try:
                    with open(f"/proc/{child_pid}/stat") as fh:
                        state = fh.read().split(")")[-1].split()[0]
                    alive = state not in ("Z", "X")
                except OSError:
                    alive = False
            except ProcessLookupError:
                alive = False
            if not alive:
                break
            time.sleep(0.1)
        assert alive is False
        assert r.cancel() is False  # nothing running any more

    def test_ansi_is_stripped_from_output(self):
        d = Dispatcher(root=None)
        r = ProcessRunner(d)
        lines, done = [], []
        r.start([PY, "-c", r"print('\x1b[32mgreen\x1b[0m done')"],
                on_line=lines.append, on_exit=done.append)
        _drive(d, lambda: bool(done))
        assert lines == ["green done"]


class TestCargoBinOnPath:
    def test_cargo_bin_appended_to_child_path(self, tmp_path, monkeypatch):
        import os
        from resnik_ide import runner as runner_mod
        monkeypatch.setattr(runner_mod, "CARGO_BIN", str(tmp_path))
        d = Dispatcher(root=None)
        r = ProcessRunner(d)
        lines, done = [], []
        r.start([PY, "-c", "import os; print(os.environ['PATH'].split(os.pathsep)[-1])"],
                on_line=lines.append, on_exit=done.append)
        _drive(d, lambda: bool(done))
        assert lines == [str(tmp_path)]
        assert done[0].ok


class TestRunningUntilDelivered:
    def test_running_stays_true_until_exit_delivered(self):
        d = Dispatcher(root=None)
        r = ProcessRunner(d)
        lines, done = [], []
        r.start([PY, "-u", "-c", "print('tail line')"], on_line=lines.append,
                on_exit=done.append)
        # wait for the OS process to die without draining the dispatcher
        deadline = time.monotonic() + 10
        while r._active.proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert r._active.proc.poll() is not None
        # dead process, undelivered callbacks → still "running"
        while not done:
            assert r.running is True
            d.drain()
            time.sleep(0.01)
        assert lines == ["tail line"]  # lines always precede the exit result
        assert r.running is False
