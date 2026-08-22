"""
Target panel: toolchain checklist, connected device, and the Build /
Flash & Run / Monitor / Run / Stop actions.

Pure view: every button calls back into the app (``actions``), which owns
the ProcessRunner and the pre-flight logic.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional, Sequence

from resnik.backend.target_spec import TARGET_NAMES

from resnik_ide.devices import SerialPort
from resnik_ide.toolchain import Requirement, ToolchainStatus


class TargetPanel(ttk.Frame):
    """Right-hand panel.  ``actions`` maps names → callables:
    refresh_toolchain, refresh_devices, install(req), build, flash_run,
    monitor, run, stop."""

    def __init__(self, master: tk.Misc, *, var_target: tk.StringVar,
                 var_port: tk.StringVar, var_chip: tk.StringVar,
                 actions: Dict[str, Callable], **kwargs) -> None:
        super().__init__(master, padding=(8, 6), **kwargs)
        self._actions = actions
        self.var_target, self.var_port, self.var_chip = var_target, var_port, var_chip
        self._req_rows: List[tk.Widget] = []
        self.requirements: List[Requirement] = []

        ttk.Label(self, text="Target", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(2, 8))
        ttk.Label(row, text="Profile:").pack(side="left")
        self.cb_target = ttk.Combobox(row, textvariable=var_target, values=list(TARGET_NAMES),
                                      state="readonly", width=10)
        self.cb_target.pack(side="left", padx=(4, 0))

        # -- toolchain checklist ------------------------------------------------------------
        tc = ttk.LabelFrame(self, text="Toolchain", padding=6)
        tc.pack(fill="x")
        self.req_frame = ttk.Frame(tc)
        self.req_frame.pack(fill="x")
        self.req_frame.columnconfigure(1, weight=1)
        self.lbl_toolchain = ttk.Label(tc, text="not probed yet", foreground="#666666",
                                       wraplength=280, justify="left")
        self.lbl_toolchain.pack(anchor="w", pady=(4, 0))
        self.btn_refresh_tc = ttk.Button(tc, text="Refresh", command=actions["refresh_toolchain"])
        self.btn_refresh_tc.pack(anchor="e", pady=(4, 0))

        # -- device ---------------------------------------------------------------------------
        dv = ttk.LabelFrame(self, text="Device", padding=6)
        dv.pack(fill="x", pady=(8, 0))
        ttk.Label(dv, text="Serial port (espflash --port):").grid(row=0, column=0, sticky="w")
        self.cb_port = ttk.Combobox(dv, textvariable=var_port, values=[], width=22)
        self.cb_port.grid(row=1, column=0, sticky="ew")
        self.btn_refresh_dev = ttk.Button(dv, text="Refresh", command=actions["refresh_devices"])
        self.btn_refresh_dev.grid(row=1, column=1, padx=(4, 0))
        ttk.Label(dv, text="Chip (probe-rs --chip):").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.ent_chip = ttk.Entry(dv, textvariable=var_chip, width=24)
        self.ent_chip.grid(row=3, column=0, sticky="ew")
        self.lbl_device = ttk.Label(dv, text="", foreground="#666666", wraplength=280, justify="left")
        self.lbl_device.grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))
        dv.columnconfigure(0, weight=1)

        # -- actions ----------------------------------------------------------------------------
        ac = ttk.LabelFrame(self, text="Actions", padding=6)
        ac.pack(fill="x", pady=(8, 0))
        self.btn_build = ttk.Button(ac, text="Build  (cargo build)", command=actions["build"])
        self.btn_flash = ttk.Button(ac, text="Flash & Run", command=actions["flash_run"])
        self.btn_monitor = ttk.Button(ac, text="Monitor (serial console)", command=actions["monitor"])
        self.btn_run = ttk.Button(ac, text="Run on host", command=actions["run"])
        self.btn_stop = ttk.Button(ac, text="■ Stop", command=actions["stop"], state="disabled")
        for b in (self.btn_build, self.btn_flash, self.btn_monitor, self.btn_run, self.btn_stop):
            b.pack(fill="x", pady=2)
        self.lbl_hint = ttk.Label(self, text="", foreground="#666666", wraplength=280, justify="left")
        self.lbl_hint.pack(anchor="w", pady=(8, 0))

        var_target.trace_add("write", lambda *_a: self._update_hint())
        self._update_hint()

    # -- updates -------------------------------------------------------------------------

    def show_toolchain(self, status: Optional[ToolchainStatus],
                       reqs: Sequence[Requirement]) -> None:
        for w in self._req_rows:
            w.destroy()
        self._req_rows = []
        self.requirements = list(reqs)
        row = 0
        for r in reqs:
            head, _, tail = r.label.partition("\n")
            mark = ttk.Label(self.req_frame, text="✓" if r.satisfied else "✗",
                             foreground="#1a7f37" if r.satisfied else "#c42b1c", width=2)
            mark.grid(row=row, column=0, sticky="w")
            lab = ttk.Label(self.req_frame, text=head, wraplength=180, justify="left")
            lab.grid(row=row, column=1, sticky="w")
            self._req_rows += [mark, lab]
            if r.installable:
                btn = ttk.Button(self.req_frame, text="Install",
                                 command=lambda rq=r: self._actions["install"](rq))
                btn.grid(row=row, column=2, sticky="e", padx=(4, 0))
                self._req_rows.append(btn)
            row += 1
            if tail:
                # e.g. the target triple: its own line spanning under the button,
                # so long unbreakable names are not clipped.
                sub = ttk.Label(self.req_frame, text=tail, font="TkSmallCaptionFont",
                                foreground="#444444")
                sub.grid(row=row, column=1, columnspan=2, sticky="w", pady=(0, 3))
                self._req_rows.append(sub)
                row += 1
        if status is None:
            self.lbl_toolchain.configure(text="probing…")
        else:
            lines = [str(status.rustc), str(status.cargo)]
            if status.default_toolchain:
                lines.append(status.default_toolchain)
            self.lbl_toolchain.configure(text="\n".join(lines))

    def show_ports(self, ports: Sequence[SerialPort], probes: Sequence[str] = ()) -> None:
        devs = [p.device for p in ports]
        self.cb_port["values"] = devs
        if devs and not self.var_port.get():
            self.var_port.set(devs[0])
        msg = f"{len(devs)} serial port(s)" if devs else "no serial ports found"
        if probes:
            msg += "\n" + "\n".join(probes[:4])
        self.lbl_device.configure(text=msg)

    def set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for b in (self.btn_build, self.btn_flash, self.btn_monitor, self.btn_run,
                  self.btn_refresh_tc, self.btn_refresh_dev):
            b.configure(state=state)
        for w in self._req_rows:
            if isinstance(w, ttk.Button):
                w.configure(state=state)
        self.btn_stop.configure(state="normal" if running else "disabled")

    def _update_hint(self) -> None:
        t = self.var_target.get()
        hints = {
            "host": "Build = cargo build in the --emit-cargo project; Run on host executes the "
                    "compiled program (from --emit-exe or the Cargo build) and streams its output.",
            "esp32-c3": "Compile with --emit-cargo set, then Flash & Run = 'cargo run --release' "
                        "(espflash flash --monitor on the selected port). Monitor opens the serial "
                        "console. Stop ends the monitor.",
            "cortex-m": "Compile with --emit-cargo set, edit memory.x for your MCU, then Build and "
                        "Flash & Run = 'probe-rs run --chip <chip> <elf>'. Monitor = probe-rs attach.",
        }
        self.lbl_hint.configure(text=hints.get(t, ""))
