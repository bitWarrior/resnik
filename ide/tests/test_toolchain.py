"""toolchain.py — probing with fake subprocesses; requirement lists."""

import subprocess

from resnik_ide.toolchain import (
    Requirement,
    ToolchainStatus,
    Tool,
    missing,
    parse_default_toolchain,
    parse_installed_components,
    parse_installed_targets,
    probe,
    requirements,
)


class TestParsing:
    def test_targets(self):
        text = "thumbv7em-none-eabihf\nx86_64-unknown-linux-gnu (installed)\n\n"
        assert parse_installed_targets(text) == ["thumbv7em-none-eabihf", "x86_64-unknown-linux-gnu"]
        assert parse_installed_targets("") == []
        assert parse_installed_targets("error: rustup could not choose a version") == []

    def test_components_and_toolchain(self):
        assert parse_installed_components("rust-src\nrust-std-x86_64-unknown-linux-gnu\n") == [
            "rust-src", "rust-std-x86_64-unknown-linux-gnu"]
        assert parse_default_toolchain("stable-x86_64-unknown-linux-gnu (default)\n") == \
            "stable-x86_64-unknown-linux-gnu"
        assert parse_default_toolchain("") == ""


def _fake_env(tools, targets="", components="", active="stable-x86_64-unknown-linux-gnu"):
    """Return (which, run) fakes for a machine that has the given tools."""

    def which(name):
        return f"/usr/bin/{name}" if name in tools else None

    def run(argv, **kw):
        exe = argv[0].rsplit("/", 1)[-1]
        if argv[1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, f"{exe} 9.9.9 (fake)\n", "")
        if exe == "rustup" and argv[1:] == ["target", "list", "--installed"]:
            return subprocess.CompletedProcess(argv, 0, targets, "")
        if exe == "rustup" and argv[1:] == ["component", "list", "--installed"]:
            return subprocess.CompletedProcess(argv, 0, components, "")
        if exe == "rustup" and argv[1:] == ["show", "active-toolchain"]:
            return subprocess.CompletedProcess(argv, 0, active + "\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "unexpected")

    return which, run


class TestProbe:
    def test_full_machine(self):
        which, run = _fake_env({"rustup", "cargo", "rustc", "espflash", "probe-rs"},
                               targets="riscv32imc-unknown-none-elf\nx86_64-unknown-linux-gnu\n",
                               components="rust-src\ncargo-x86_64-unknown-linux-gnu\n")
        st = probe(which=which, run=run)
        assert st.rustc.present and st.rustc.version == "rustc 9.9.9 (fake)"
        assert st.espflash.present and st.probe_rs.present
        assert st.installed_targets == ["riscv32imc-unknown-none-elf", "x86_64-unknown-linux-gnu"]
        assert st.has_target("riscv32imc-unknown-none-elf")
        assert st.has_component("rust-src") and st.has_component("cargo")
        assert not st.has_component("rust-analyzer")
        assert st.default_toolchain == "stable-x86_64-unknown-linux-gnu"
        assert any("installed targets:" in ln for ln in st.summary_lines())

    def test_bare_machine(self):
        which, run = _fake_env(set())
        st = probe(which=which, run=run)
        assert not st.rustup.present and not st.cargo.present
        assert st.installed_targets == [] and st.default_toolchain == ""
        assert str(st.rustc) == "rustc: not found"

    def test_run_failure_is_tolerated(self):
        def which(name):
            return "/usr/bin/" + name

        def run(argv, **kw):
            raise OSError("boom")

        st = probe(which=which, run=run)
        assert st.rustup.present and st.rustup.version is None
        assert st.installed_targets == []


class TestRequirements:
    def _status(self, **kw):
        st = ToolchainStatus(
            rustup=Tool("rustup", "/usr/bin/rustup", "rustup 1.27"),
            cargo=Tool("cargo", "/usr/bin/cargo", "cargo 1.96"),
            rustc=Tool("rustc", "/usr/bin/rustc", "rustc 1.96"),
        )
        for k, v in kw.items():
            setattr(st, k, v)
        return st

    def test_host_only_needs_rustc_and_cargo(self):
        reqs = requirements("host", self._status())
        assert [r.key for r in reqs] == ["rustc", "cargo"]
        assert all(r.satisfied for r in reqs)
        assert missing(reqs) == []

    def test_esp32_c3_checklist_with_install_commands(self):
        reqs = requirements("esp32-c3", self._status())
        keys = [r.key for r in reqs]
        assert keys == ["rustc", "cargo", "rustup", "target:riscv32imc-unknown-none-elf",
                        "component:rust-src", "tool:espflash"]
        by = {r.key: r for r in reqs}
        assert by["target:riscv32imc-unknown-none-elf"].install_argv == \
            ["/usr/bin/rustup", "target", "add", "riscv32imc-unknown-none-elf"]
        assert by["component:rust-src"].install_argv == ["/usr/bin/rustup", "component", "add", "rust-src"]
        assert by["tool:espflash"].install_argv == ["/usr/bin/cargo", "install", "espflash", "--locked"]
        assert [r.key for r in missing(reqs)] == keys[3:]
        assert all(r.installable for r in missing(reqs))

    def test_esp32_c3_all_satisfied(self):
        st = self._status(installed_targets=["riscv32imc-unknown-none-elf"],
                          installed_components=["rust-src"],
                          espflash=Tool("espflash", "/x/espflash", "espflash 3.1.0"))
        reqs = requirements("esp32-c3", st)
        assert missing(reqs) == []
        assert {r.key: r.detail for r in reqs}["tool:espflash"] == "espflash 3.1.0"

    def test_cortex_m_checklist(self):
        st = self._status(installed_targets=["thumbv7em-none-eabihf"])
        reqs = requirements("cortex-m", st)
        keys = [r.key for r in reqs]
        assert "target:thumbv7em-none-eabihf" in keys and "tool:probe-rs" in keys
        assert "component:rust-src" not in keys and "tool:espflash" not in keys
        by = {r.key: r for r in reqs}
        assert by["target:thumbv7em-none-eabihf"].satisfied
        assert by["tool:probe-rs"].install_argv == ["/usr/bin/cargo", "install", "probe-rs-tools", "--locked"]

    def test_without_rustup_nothing_is_auto_installable(self):
        st = ToolchainStatus()  # bare machine
        reqs = requirements("esp32-c3", st)
        assert not any(r.installable for r in reqs)
        assert all(not r.satisfied for r in reqs)
        assert "rustup.rs" in reqs[0].detail

    def test_unknown_target_falls_back_to_host_rows(self):
        reqs = requirements("nope", self._status())
        assert [r.key for r in reqs] == ["rustc", "cargo"]
        assert isinstance(reqs[0], Requirement)
