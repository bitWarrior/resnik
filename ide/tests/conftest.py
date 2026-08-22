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
def settings_file(tmp_path, monkeypatch):
    """Point the IDE at a private ide.json so tests never touch ~/.config."""
    p = tmp_path / "cfg" / "ide.json"
    monkeypatch.setenv("RESNIK_IDE_CONFIG", str(p))
    return p


@pytest.fixture
def fake_toolchain(monkeypatch):
    """Hermetic toolchain/device discovery: no rustup/cargo subprocesses.

    Returns a mutable holder; tests set ``holder.status`` before the app
    probes (or call ``app.refresh_toolchain()`` afterwards)."""
    import types

    from resnik_ide import devices, toolchain
    from resnik_ide.toolchain import Tool, ToolchainStatus

    holder = types.SimpleNamespace(status=ToolchainStatus(
        rustup=Tool("rustup", "/usr/bin/rustup", "rustup 1.27.1"),
        cargo=Tool("cargo", "/usr/bin/cargo", "cargo 1.96.1"),
        rustc=Tool("rustc", "/usr/bin/rustc", "rustc 1.96.1"),
        installed_targets=["x86_64-unknown-linux-gnu", "thumbv7em-none-eabihf"],
        installed_components=["rust-std-x86_64-unknown-linux-gnu"],
        default_toolchain="stable-x86_64-unknown-linux-gnu",
    ), ports=[])
    monkeypatch.setattr(toolchain, "probe", lambda **kw: holder.status)
    monkeypatch.setattr(devices, "list_serial_ports", lambda **kw: list(holder.ports))
    monkeypatch.setattr(devices, "list_probes", lambda **kw: [])
    return holder


@pytest.fixture
def app(settings_file, fake_toolchain):
    """A withdrawn ResnikIDE window (private settings, fake toolchain),
    torn down after the test.  The startup toolchain probe has completed."""
    if not have_tk():
        pytest.skip("Tk display not available")
    from resnik_ide.app import ResnikIDE

    ide = ResnikIDE()
    ide.withdraw()
    ide.update()
    pump(ide, lambda: not ide.probing and ide.dispatcher.pending == 0)
    yield ide
    try:
        if ide.theme_name != "light":
            ide.set_theme("light")  # theme state is process-global; leave it clean
    except Exception:
        pass
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
