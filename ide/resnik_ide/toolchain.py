"""
Rust toolchain discovery for the Target panel.

``probe()`` asks rustup / cargo / rustc / espflash / probe-rs what is
installed; ``requirements(target, status)`` turns that into the checklist
the panel shows for a Resnik target profile, each row carrying the command
that would satisfy it (run through the ProcessRunner so output streams).

Everything here is side-effect free; all subprocess calls are injectable
for tests.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

from resnik.backend.target_spec import SPECS

RunFn = Callable[..., "subprocess.CompletedProcess[str]"]
WhichFn = Callable[[str], Optional[str]]

RUSTUP_URL = "https://rustup.rs"


@dataclass
class Tool:
    name: str
    path: Optional[str] = None
    version: Optional[str] = None

    @property
    def present(self) -> bool:
        return self.path is not None

    def __str__(self) -> str:
        if not self.present:
            return f"{self.name}: not found"
        return f"{self.name}: {self.version or self.path}"


@dataclass
class ToolchainStatus:
    rustup: Tool = field(default_factory=lambda: Tool("rustup"))
    cargo: Tool = field(default_factory=lambda: Tool("cargo"))
    rustc: Tool = field(default_factory=lambda: Tool("rustc"))
    espflash: Tool = field(default_factory=lambda: Tool("espflash"))
    probe_rs: Tool = field(default_factory=lambda: Tool("probe-rs"))
    installed_targets: List[str] = field(default_factory=list)
    installed_components: List[str] = field(default_factory=list)
    default_toolchain: str = ""

    def has_target(self, triple: str) -> bool:
        return triple in self.installed_targets

    def has_component(self, name: str) -> bool:
        return any(c == name or c.startswith(name + "-") for c in self.installed_components)

    def summary_lines(self) -> List[str]:
        out = [str(t) for t in (self.rustup, self.cargo, self.rustc, self.espflash, self.probe_rs)]
        if self.default_toolchain:
            out.append(f"default toolchain: {self.default_toolchain}")
        if self.installed_targets:
            out.append("installed targets: " + ", ".join(self.installed_targets))
        return out


@dataclass
class Requirement:
    key: str
    label: str
    satisfied: bool
    install_argv: Optional[List[str]] = None  # None → cannot auto-install
    detail: str = ""

    @property
    def installable(self) -> bool:
        return not self.satisfied and self.install_argv is not None


# -- parsing --------------------------------------------------------------------


def first_line(text: str) -> str:
    for ln in (text or "").splitlines():
        if ln.strip():
            return ln.strip()
    return ""


def parse_installed_targets(text: str) -> List[str]:
    """``rustup target list --installed`` → triples (tolerates ``(installed)`` suffixes)."""
    out = []
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith(("error", "warning", "info")):
            continue
        out.append(ln.split()[0])
    return out


def parse_installed_components(text: str) -> List[str]:
    return parse_installed_targets(text)


def parse_default_toolchain(text: str) -> str:
    """``rustup show active-toolchain`` → ``stable-x86_64-unknown-linux-gnu``."""
    ln = first_line(text)
    return ln.split()[0] if ln else ""


# -- probing ----------------------------------------------------------------------


def _run(run: RunFn, argv: Sequence[str], timeout: float = 20.0) -> "subprocess.CompletedProcess[str]":
    try:
        return run(list(argv), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(list(argv), 127, "", str(exc))


def _tool(name: str, which: WhichFn, run: RunFn, version_args=("--version",)) -> Tool:
    path = which(name)
    if not path:
        return Tool(name)
    cp = _run(run, [path, *version_args])
    if cp.returncode != 0:
        return Tool(name, path, None)
    # rustup prints the version line on stdout and chatter on stderr.
    ver = first_line(cp.stdout) or first_line(cp.stderr)
    return Tool(name, path, ver or None)


def probe(*, which: WhichFn = shutil.which, run: RunFn = subprocess.run) -> ToolchainStatus:
    """Discover the installed toolchain.  Never raises."""
    st = ToolchainStatus(
        rustup=_tool("rustup", which, run),
        cargo=_tool("cargo", which, run),
        rustc=_tool("rustc", which, run),
        espflash=_tool("espflash", which, run),
        probe_rs=_tool("probe-rs", which, run),
    )
    if st.rustup.present:
        rp = st.rustup.path
        st.installed_targets = parse_installed_targets(
            _run(run, [rp, "target", "list", "--installed"]).stdout)
        st.installed_components = parse_installed_components(
            _run(run, [rp, "component", "list", "--installed"]).stdout)
        st.default_toolchain = parse_default_toolchain(
            _run(run, [rp, "show", "active-toolchain"]).stdout)
    return st


# -- requirements per target profile ---------------------------------------------------


def requirements(target: str, st: ToolchainStatus) -> List[Requirement]:
    """Checklist for ``target`` (a ``resnik --target`` profile name)."""
    spec = SPECS.get(target)
    reqs: List[Requirement] = []
    rustup = st.rustup.path or "rustup"
    cargo = st.cargo.path or "cargo"

    reqs.append(Requirement(
        "rustc", "Rust compiler (rustc)", st.rustc.present,
        None, st.rustc.version or f"install from {RUSTUP_URL}"))
    reqs.append(Requirement(
        "cargo", "Cargo", st.cargo.present,
        None, st.cargo.version or f"install from {RUSTUP_URL}"))

    if spec is None or not spec.embedded:
        return reqs

    reqs.append(Requirement(
        "rustup", "rustup", st.rustup.present,
        None, st.rustup.version or f"install from {RUSTUP_URL}"))

    triple = spec.triple or ""
    have_triple = st.has_target(triple)
    reqs.append(Requirement(
        f"target:{triple}", f"target\n{triple}", have_triple,
        [rustup, "target", "add", triple] if st.rustup.present else None,
        "installed" if have_triple else f"rustup target add {triple}"))

    if spec.entry == "esp-hal":
        have_src = st.has_component("rust-src")
        reqs.append(Requirement(
            "component:rust-src", "rust-src component", have_src,
            [rustup, "component", "add", "rust-src"] if st.rustup.present else None,
            "installed" if have_src else "rustup component add rust-src"))
        reqs.append(Requirement(
            "tool:espflash", "espflash (flash + monitor)", st.espflash.present,
            [cargo, "install", "espflash", "--locked"] if st.cargo.present else None,
            st.espflash.version or "cargo install espflash --locked"))
    elif spec.entry == "cortex-m-rt":
        reqs.append(Requirement(
            "tool:probe-rs", "probe-rs (debug probe)", st.probe_rs.present,
            [cargo, "install", "probe-rs-tools", "--locked"] if st.cargo.present else None,
            st.probe_rs.version or "cargo install probe-rs-tools --locked"))
    return reqs


def missing(reqs: Sequence[Requirement]) -> List[Requirement]:
    return [r for r in reqs if not r.satisfied]
