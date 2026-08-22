# Cross-Compilation (Embedded Targets)

Resnik can emit either hosted Rust or bare-metal firmware, selected with the
`--target` flag on `resnik compile`:

| Profile            | Description                                                          |
| ------------------ | ------------------------------------------------------------------- |
| `host` (default)   | Hosted `std` Rust — runs on your local computer.                     |
| `cortex-m`         | `no_std` **stub** for ARM Cortex-M (semihosting `WRITE`; not a finished MCU port). |
| `esp32-c3`         | `no_std` firmware for **ESP32-C3** (RISC-V) via `esp-hal`. USB-serial `WRITE`. |

This is **not** classic Xtensa ESP32 (`ESP32` / S2 / S3). Those chips need the
`espup` toolchain; they are a later profile. ESP32-C3 uses stock `rustc`.
Do not treat ESP-IDF / `std` as this target.

**Feature support on `no_std` targets.** Core control flow, arithmetic, `BIT`
operations, `IF`/`DO`, procedures, and the `ON ERROR` fault model all lower
cleanly to bare metal — the fault model unwinds via `Result` and panics on an
unhandled fault (`panic-halt` on Cortex-M, `esp-backtrace` on ESP32-C3 so the
fault is visible on the serial console). Features whose lowering still depends
on `std` — `VECTOR`/`MATRIX` and math intrinsics, `SCHEDULE`/`TASK`, and
`COMPOOL` — emit a `compile_error!` rather than failing silently. These are on
the embedded roadmap.

## Cortex-M stub

The `cortex-m` profile emits a `#![no_std]` / `#![no_main]` crate with a
`#[cortex_m_rt::entry]` reset entry point, a halting panic handler, and routes
HAL/S `WRITE` output to semihosting (swappable for RTT or UART downstream).
It is a crate *shape*, not a complete board support package.

```bash
# rustup / rustc: see Requirements if these commands are missing
rustup target add thumbv7em-none-eabihf
resnik compile program.hal --target cortex-m --emit-cargo ./firmware
cd firmware
# 1. Edit memory.x to match your MCU's flash/RAM map.
# 2. Uncomment a runner in .cargo/config.toml
#    (probe-rs for hardware, QEMU for emulation).
cargo build --release
cargo run   --release
```

With `--emit-cargo`, the `cortex-m` profile scaffolds:

```
firmware/
├── Cargo.toml            # cortex-m, cortex-m-rt, panic-halt, cortex-m-semihosting
├── .cargo/config.toml    # target triple, linker args, probe-rs/QEMU runner
├── memory.x              # MCU flash/RAM layout — edit for your chip
├── build.rs              # puts memory.x on the linker search path
└── src/main.rs           # generated firmware
```

## Hello World on ESP32-C3

This is the path from a HAL/S source file to a running chip. The example
program is `docs/HAL-S-Source-Code-Examples/hello_word/hello_world.hal`.

**1. Write the HAL/S program.** A complete hello world is three lines:

```
HELLO: PROGRAM;
  WRITE(6) 'HELLO, WORLD [HAL/S LIVES!]';
CLOSE HELLO;
```

`WRITE(6)` is standard output. On `esp32-c3` it becomes `esp_println::println!`
on the DevKit USB serial / JTAG console. Stay inside the `no_std` subset
(no `VECTOR` / `MATRIX`, `SCHEDULE` / `TASK`, or `COMPOOL`).

You can copy that file or save the same text as `hello_world.hal` in the
current directory.

**2. Install the RISC-V target and flasher** (once per machine). Install
Rust with rustup first if `rustc` is missing ([Installation](../README.md#installation)).
You need a recent stable toolchain (esp-hal 1.1 wants **1.88+**), stock
`rustc` — not `espup`:

```bash
rustup update stable
rustup target add riscv32imc-unknown-none-elf
rustup component add rust-src
cargo install espflash
```

Plug an **ESP32-C3-DevKit** (or any C3 board with USB-Serial/JTAG) into USB.
It usually appears as `/dev/ttyACM0` on Linux.

**3. Compile HAL/S to a Cargo firmware project:**

```bash
# from this repository:
resnik compile docs/HAL-S-Source-Code-Examples/hello_word/hello_world.hal \
  --target esp32-c3 --emit-cargo ./firmware

# or from a copy named hello_world.hal in the current directory:
resnik compile hello_world.hal --target esp32-c3 --emit-cargo ./firmware
```

`--emit-cargo` writes a buildable crate (no STM32 `memory.x`; flash layout is
esp-hal's `linkall.x`):

```
firmware/
├── Cargo.toml            # esp-hal 1.1 (esp32c3), esp-backtrace, esp-println, esp-bootloader-esp-idf
├── rust-toolchain.toml   # stable + rust-src + riscv32imc-unknown-none-elf
├── .cargo/config.toml    # RISC-V triple, linkall.x, runner = espflash flash --monitor
└── src/main.rs           # generated firmware (#[esp_hal::main], USB-serial WRITE)
```

**4. Build, load onto the chip, and run.** From the scaffolded project:

```bash
cd firmware
cargo run --release
```

`cargo run` builds for `riscv32imc-unknown-none-elf`, then the configured
runner flashes and opens a serial monitor:

```text
espflash flash --monitor
```

Equivalent, if you prefer to flash the ELF yourself:

```bash
cargo build --release
espflash flash --monitor --chip esp32c3 \
  target/riscv32imc-unknown-none-elf/release/firmware
```

**5. What you should see.** After reset, the USB serial console prints:

```text
HELLO, WORLD [HAL/S LIVES!]
```

The program then parks in `loop {}` (bare-metal `main` never returns). Reset
the board to print again.

If `espflash` cannot open the port: unplug USB, hold **BOOT**, plug back in,
release **BOOT**. Some boards also need a tap on **RESET** while **BOOT** is
held. Pass `--port /dev/ttyACM0` if several serial devices are present.

Host check of the same source (no board required):

```bash
resnik compile hello_world.hal --emit-exe
./hello_world
```
