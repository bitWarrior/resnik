/* ============================================================
 * i2c.c -- foreign implementation for the EXTERNAL symbols in
 *          hw_interface.hal.
 *
 * Resnik lowered the HAL/S EXTERNAL declarations to an extern "C"
 * interface, so this module must export matching symbols with C
 * linkage and matching types:
 *
 *   HAL/S                              generated Rust
 *   ---------------------------------  --------------------------------
 *   READ_I2C_BUS: PROCEDURE(BUS_ID)    extern "C" { fn read_i2c_bus(
 *       ... EXTERNAL;  (BUS_ID INTEGER)              bus_id: i32); }
 *   DECLARE RAW_TEMP SCALAR EXTERNAL;  extern "C" { static raw_temp: f32; }
 *
 * Type mapping:  INTEGER -> i32 -> int32_t ,  SCALAR -> f32 -> float.
 *
 * Build & link:
 *     cc -c i2c.c -o i2c.o
 *     resnik compile hw_interface.hal --emit-exe --link-obj i2c.o
 * ============================================================ */

#include <stdint.h>
#include <stdio.h>

/* The external sensor reading shared with the HAL/S program.
 *
 * This is a plain global (NOT the C keyword `static`, which would make it
 * file-local) so it has external linkage and the linker can resolve the Rust
 * `extern "C" { static raw_temp: f32; }` reference. It is defined (with an
 * initializer), not merely declared, so storage is allocated here. */
float raw_temp = 0.0f;

/* Simulated bus temperature, in degrees C. In real firmware read_i2c_bus would
 * clock the bus and read the sensor register; here we stub a plausible value.
 * Set this above 85.0 to exercise the KILROY overheat branch in the HAL/S. */
#define SIM_TEMP_C 72.5f   /* nominal */

/* void read_i2c_bus(int32_t) -- matches `fn read_i2c_bus(bus_id: i32)`.
 * The HAL/S program CALLs this, then reads RAW_TEMP, so we populate the shared
 * global before returning. */
void read_i2c_bus(int32_t bus_id)
{
    /* pretend to address and clock I2C bus `bus_id` and read its sensor */
    printf("[i2c.c] polling I2C bus %d\n", (int)bus_id);
    raw_temp = SIM_TEMP_C;
}
