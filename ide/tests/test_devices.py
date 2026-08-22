"""devices.py — serial ports / probes with fakes."""

import subprocess

from resnik_ide.devices import SerialPort, list_probes, list_serial_ports


class TestSerialPorts:
    def test_glob_fallback_sorted_and_deduped(self):
        seen = {
            "/dev/ttyACM*": ["/dev/ttyACM1", "/dev/ttyACM0"],
            "/dev/ttyUSB*": ["/dev/ttyUSB0", "/dev/ttyACM0"],
        }
        ports = list_serial_ports(globber=lambda p: seen.get(p, []), use_pyserial=False,
                                  platform="linux")
        assert [p.device for p in ports] == ["/dev/ttyACM0", "/dev/ttyACM1", "/dev/ttyUSB0"]
        assert str(ports[0]) == "/dev/ttyACM0"

    def test_no_ports(self):
        assert list_serial_ports(globber=lambda p: [], use_pyserial=False, platform="linux") == []

    def test_windows_offers_com_candidates(self):
        ports = list_serial_ports(globber=lambda p: [], use_pyserial=False, platform="win32")
        assert ports[0] == SerialPort("COM1") and len(ports) == 16

    def test_pyserial_path_when_available(self, monkeypatch):
        import sys
        import types

        class P:
            def __init__(self, d, desc):
                self.device, self.description = d, desc

        lp = types.SimpleNamespace(comports=lambda: [P("/dev/ttyACM0", "USB JTAG/serial debug unit")])
        tools = types.ModuleType("serial.tools")
        tools.list_ports = lp
        serial = types.ModuleType("serial")
        serial.tools = tools
        monkeypatch.setitem(sys.modules, "serial", serial)
        monkeypatch.setitem(sys.modules, "serial.tools", tools)
        monkeypatch.setitem(sys.modules, "serial.tools.list_ports", lp)
        ports = list_serial_ports(globber=lambda p: ["/dev/ttyUSB9"], platform="linux")
        assert ports == [SerialPort("/dev/ttyACM0", "USB JTAG/serial debug unit")]
        assert "USB JTAG" in str(ports[0])


class TestProbes:
    def test_missing_probe_rs(self):
        assert list_probes(which=lambda n: None) == []

    def test_parses_probe_list(self):
        def run(argv, **kw):
            return subprocess.CompletedProcess(argv, 0, "The following debug probes were found:\n"
                                               "[0]: J-Link -- 1366:0101:123 (J-Link)\n", "")
        out = list_probes(which=lambda n: "/x/probe-rs", run=run)
        assert out == ["The following debug probes were found:", "[0]: J-Link -- 1366:0101:123 (J-Link)"]

    def test_run_error_tolerated(self):
        def run(argv, **kw):
            raise OSError("nope")
        assert list_probes(which=lambda n: "/x/probe-rs", run=run) == []
