"""Shared fixtures for the IDE tests.

Tk tests need a display.  ``tk_root`` skips the test when one is not
available (CI without xvfb), so the suite stays green headless.
"""

import time
import tkinter as tk

import pytest


def _tk_works() -> bool:
    try:
        r = tk.Tk()
    except tk.TclError:
        return False
    r.withdraw()
    r.destroy()
    return True


_HAVE_TK = None


def have_tk() -> bool:
    global _HAVE_TK
    if _HAVE_TK is None:
        _HAVE_TK = _tk_works()
    return _HAVE_TK


@pytest.fixture
def tk_root():
    """A withdrawn Tk root; the test is skipped if Tk cannot start."""
    if not have_tk():
        pytest.skip("Tk display not available")
    root = tk.Tk()
    root.withdraw()
    yield root
    try:
        root.destroy()
    except tk.TclError:
        pass


@pytest.fixture
def app():
    """A withdrawn ResnikIDE window, torn down after the test."""
    if not have_tk():
        pytest.skip("Tk display not available")
    from resnik_ide.app import ResnikIDE

    ide = ResnikIDE()
    ide.withdraw()
    ide.update()
    yield ide
    ide.shutdown()


def pump(widget, until, timeout: float = 30.0, step: float = 0.02) -> None:
    """Drive the Tk event loop until ``until()`` is true (or time out)."""
    deadline = time.monotonic() + timeout
    while not until():
        if time.monotonic() > deadline:
            raise TimeoutError("condition not met in time")
        widget.update()
        time.sleep(step)
    widget.update()


VALID = "HELLO: PROGRAM;\n  WRITE(6) 'HELLO, WORLD';\nCLOSE HELLO;\n"
SEMANTIC_DUP = "T: PROGRAM;\n  DECLARE X INTEGER;\n  DECLARE X SCALAR;\nCLOSE T;\n"
LEX_ERROR = "T: PROGRAM;\n  DECLARE B BIT(8) INITIAL(BIN '10201010');\nCLOSE T;\n"
