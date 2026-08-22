"""Code-generation target profiles.

A profile is the *shape* of the emitted crate (std vs no_std, entry point,
WRITE sink, panic handler, cargo scaffold), not a chip part number.
``cortex-m`` is a no_std stub (semihosting WRITE, compile_error! for
VECTOR/TASK/COMPOOL). ``esp32-c3`` is the first silicon profile (esp-hal,
USB-serial WRITE). Additional MCU profiles register here and are selected
with ``--target``.
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class TargetSpec:
    """One ``--target`` profile.

    Attributes:
        name: CLI / ``RustCodeGenerator(target=...)`` key.
        embedded: True for bare-metal (no_std, no_main, diverging main).
        triple: Default rustc/cargo target triple, or None for the host.
        entry: Runtime that supplies the reset entry
            (``cortex-m-rt`` → ``#[entry]``, ``esp-hal`` → ``#[main]``).
        write_sink: How ``WRITE`` lowers: ``println`` (std), ``hprintln``
            (Cortex-M semihosting), or ``esp-println`` (ESP USB-serial/JTAG).
        panic: Panic strategy: ``std``, ``panic-halt``, or ``esp-backtrace``.
    """

    name: str
    embedded: bool
    triple: Optional[str] = None
    entry: Optional[str] = None
    write_sink: str = "println"
    panic: str = "std"


SPECS: Dict[str, TargetSpec] = {
    "host": TargetSpec(name="host", embedded=False),
    "cortex-m": TargetSpec(
        name="cortex-m",
        embedded=True,
        triple="thumbv7em-none-eabihf",
        entry="cortex-m-rt",
        write_sink="hprintln",
        panic="panic-halt",
    ),
    "esp32-c3": TargetSpec(
        name="esp32-c3",
        embedded=True,
        triple="riscv32imc-unknown-none-elf",
        entry="esp-hal",
        write_sink="esp-println",
        panic="esp-backtrace",
    ),
}

TARGET_NAMES: Tuple[str, ...] = tuple(SPECS)
TARGET_TRIPLES: Dict[str, str] = {
    name: spec.triple for name, spec in SPECS.items() if spec.triple
}


def lookup_target(name: str) -> TargetSpec:
    """Return the spec for ``name``, or raise ValueError listing known names."""
    spec = SPECS.get(name)
    if spec is None:
        raise ValueError(
            f"Unknown target '{name}'. Expected one of {TARGET_NAMES}."
        )
    return spec
