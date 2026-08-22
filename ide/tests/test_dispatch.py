"""Dispatcher / Job — no display needed."""

import threading

from resnik_ide.dispatch import Dispatcher, Job


class TestDispatcher:
    def test_post_then_drain_runs_in_order(self):
        d = Dispatcher(root=None)
        seen = []
        for i in range(3):
            d.post(lambda i=i: seen.append(i))
        assert d.pending == 3
        assert d.drain() == 3
        assert seen == [0, 1, 2]
        assert d.pending == 0
        assert d.drain() == 0

    def test_post_is_thread_safe(self):
        d = Dispatcher(root=None)
        seen = []

        def worker(k):
            for i in range(100):
                d.post(lambda k=k, i=i: seen.append((k, i)))

        threads = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert d.drain() == 400
        assert len(seen) == 400


class TestJob:
    def test_result_is_delivered_on_done(self):
        d = Dispatcher(root=None)
        got = {}
        job = Job(d, lambda: 42, lambda r: got.setdefault("ok", r),
                  lambda e: got.setdefault("err", e)).start()
        job._thread.join(5)
        assert not job.alive
        d.drain()
        assert got == {"ok": 42}

    def test_exception_is_delivered_on_error(self):
        d = Dispatcher(root=None)
        got = {}

        def boom():
            raise ValueError("kaboom")

        job = Job(d, boom, lambda r: got.setdefault("ok", r),
                  lambda e: got.setdefault("err", e)).start()
        job._thread.join(5)
        d.drain()
        assert "ok" not in got
        assert isinstance(got["err"], ValueError)
        assert str(got["err"]) == "kaboom"
