# Separate compilation (templates) and foreign code (`EXTERNAL`)

## Templates & Separate Compilation

HAL/S supports separate compilation through *templates* — signature-only
prototypes, analogous to C headers. A block marked `TEMPLATE` declares a unit's
name, parameters, and data layout for type checking, but generates no executable
code and allocates no memory:

```
NAV_UPDATE: PROCEDURE(POS, VEL) ASSIGN(STATE_VEC) TEMPLATE;
  DECLARE POS VECTOR(3);
  DECLARE VEL VECTOR(3);
  DECLARE STATE_VEC VECTOR(6);
CLOSE NAV_UPDATE;
```

Resnik mirrors the original HAL/S compiler's **template library** (TEMPLIB)
workflow, so you never hand-write a prototype.

**Generate templates.** On a successful compile, `--gen-tpl` auto-generates a
prototype for each reusable unit (`PROCEDURE`, `FUNCTION`, `COMPOOL`) into a
template-library directory:

```bash
resnik compile nav_update.hal --gen-tpl ./TEMPLIB
# writes ./TEMPLIB/NAV_UPDATE.tpl
```

**Include templates.** A `D INCLUDE TEMPLATE <name>` directive — with `D` in
column 1, following the HAL/S directive-card convention — pulls a stored
template from the library and injects its signature *before the rest of the file
is parsed*. Point the preprocessor at the library with `--templib` (repeatable;
the source file's own directory is always searched):

```bash
resnik compile guidance.hal --templib ./TEMPLIB
```

```
D INCLUDE TEMPLATE NAV_UPDATE
GUIDANCE: PROGRAM;
  DECLARE POSITION VECTOR(3);
  DECLARE VELOCITY VECTOR(3);
  DECLARE STATE VECTOR(6);
  CALL NAV_UPDATE(POSITION, VELOCITY) ASSIGN(STATE);
CLOSE GUIDANCE;
```

**Cross-unit type checking.** Once a template's signature is registered, `CALL`
sites are validated against it — input arity, output (`ASSIGN`) arity, and type
*dimensions*. Passing a `VECTOR(4)` where the prototype requires a `VECTOR(3)`
is rejected at semantic analysis, before any code is generated, even though the
called unit's body lives in another file.

**Linkage.** A `TEMPLATE` that is actually called is forward-declared as an
`extern "C"` binding in the generated Rust, and the call is wrapped in `unsafe`,
leaving the separately compiled body to be resolved by the linker:

```rust
extern "C" { fn nav_update(pos: Vector<3>, vel: Vector<3>, state_vec: &mut Vector<6>); }
...
unsafe { nav_update(position, velocity, &mut state); }
```

## Foreign Code: `EXTERNAL`

Where a `TEMPLATE` is a prototype for another HAL/S unit, `EXTERNAL` declares a
symbol whose definition is genuinely foreign — hand-written assembly, a C
routine, a BSP entry point, or a linker-provided value. It is Resnik's
straightforward Foreign Function Interface: `EXTERNAL` procedures and variables
lower directly to `extern "C"` and — unlike `TEMPLATE` — are **not** type-checked
across the boundary or banked in the template library. You are trusting the
linker.

```
HW_INTERFACE: PROGRAM;
  /* external variable -- defined in another object file; no storage here */
  DECLARE RAW_TEMP SCALAR EXTERNAL;

  /* external routine -- a prototype for code compiled outside HAL/S */
  READ_I2C_BUS: PROCEDURE(BUS_ID) EXTERNAL;
    DECLARE BUS_ID INTEGER;
  CLOSE READ_I2C_BUS;

  CALL READ_I2C_BUS(2);
  IF RAW_TEMP > 85.0 THEN DO; ... END;
CLOSE HW_INTERFACE;
```

lowers to:

```rust
extern "C" { fn read_i2c_bus(bus_id: i32); }
extern "C" { static raw_temp: f32; }
...
unsafe { read_i2c_bus(2); }
if (unsafe { raw_temp }) > 85.0 { ... }
```

Every use is `unsafe`, and the external variable allocates no local storage.
Symbol names follow HAL/S case-insensitivity: `READ_I2C_BUS` links against the C
symbol `read_i2c_bus`.

**Linking.** rustc compiles the `extern "C"` declarations into *undefined*
symbols; the linker must be handed their definitions. When building an
executable with `--emit-exe`, pass the foreign objects/libraries through:

| Flag | rustc | Purpose |
| ---- | ----- | ------- |
| `-L, --link-search DIR` | `-L DIR`           | linker search directory |
| `-l, --link-lib NAME`   | `-l NAME`          | link a native library   |
| `--link-obj FILE`       | `-C link-arg=FILE` | add an object / archive |

```bash
cd docs/HAL-S-Source-Code-Examples/hw_interface
cc -c i2c.c -o i2c.o
resnik compile hw_interface.hal --emit-exe --link-obj i2c.o
```

For larger builds, prefer `--emit-cargo` and let a `build.rs` (with the `cc`
crate) compile and link the foreign sources — the idiomatic path for BSP objects
and linker scripts.

> **Note:** an external hardware register whose value changes underneath the
> program is currently read as a plain (non-volatile) `static`; volatile-correct
> reads for memory-mapped registers are on the roadmap.
