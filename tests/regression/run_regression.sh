#!/usr/bin/env bash

# -e: Exit immediately if a command exits with a non-zero status.
# -E: Ensure 'trap' inherits the ERR signal (works with -e).
# -u: Treat unset variables as an error and exit immediately.
# -o pipefail: Set exit status of a pipeline to the last non-zero exit code.
set -Eeuo pipefail

# ==============================================================================
# 2. ENVIRONMENT SANITIZATION
# ==============================================================================
# Hardcode the PATH to prevent PATH-injection attacks. Do not trust the 
# environment the script was launched from.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Clear the internal field separator to prevent strange word-splitting issues
# with user input.
IFS=$'\n\t'

# Determine Git Root
if ! GIT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null); then
    echo "Error: This script must be run from within a Git repository." >&2
    exit 1
fi

# Determine Resnik path
readonly _RESNIK="$GIT_ROOT/resnik-venv/bin/resnik"

# Verify Resnik exists and is executable
if [[ ! -x "$_RESNIK" ]]; then
    echo "Error: Resnik executable not found or not executable." >&2
    echo "Path checked: $_RESNIK" >&2
    echo "Did you forget to build the virtual environment?" >&2
    exit 1
fi

# ==============================================================================
# 3. CLEANUP 
# ==============================================================================
cleanup() {
    # Suppress errors during cleanup
    trap - SIGINT SIGTERM ERR EXIT

    # Find all generated rust files
    # Check if GIT_ROOT is set and the directory exists to prevent catastrophic deletions
    if [[ -n "${GIT_ROOT:-}" ]] && [[ -d "$GIT_ROOT/docs/HAL-S-Source-Code-Examples" ]]; then
        
        # 1. Remove the generated intermediate Rust (*.rs) files
        find "$GIT_ROOT/docs/HAL-S-Source-Code-Examples" -type f -name "*.rs" -delete
        
        # 2. Dynamically find and remove the compiled executables
        # We find every .hal file, null-terminate the output (-print0) to handle spaces,
        # and feed it into a while loop.
        while IFS= read -r -d '' hal_file; do
            
            # Use Bash parameter expansion to strip '.hal' from the end of the file path.
            # Example: /path/to/hello_world.hal becomes /path/to/hello_world
            exe_file="${hal_file%.hal}"
            
            # Check if that exact file exists, and if so, delete it
            if [[ -f "$exe_file" ]]; then
                rm -f "$exe_file"
            fi
            
        done < <(find "$GIT_ROOT/docs/HAL-S-Source-Code-Examples" -type f -name "*.hal" -print0)        
    fi

    # Remove generated template libraries from --gen-tpl regression cases
    if [[ -n "${SCRIPT_DIR:-}" ]]; then
        [[ -d "$SCRIPT_DIR/TEMPLIB" ]] && rm -rf "$SCRIPT_DIR/TEMPLIB"
        [[ -d "$SCRIPT_DIR/TEMPLATE_REGRESSION_TEMPLIB" ]] && \
            rm -rf "$SCRIPT_DIR/TEMPLATE_REGRESSION_TEMPLIB"
        [[ -d "$SCRIPT_DIR/HW_INTERFACE_BUILD" ]] && \
            rm -rf "$SCRIPT_DIR/HW_INTERFACE_BUILD"
    fi
}
trap cleanup SIGINT SIGTERM ERR EXIT

# ==============================================================================
# 4. PRIVILEGE CHECK 
# ==============================================================================
if [[ $EUID -eq 0 ]]; then
    echo "Warning: Running as root. Dropping privileges..."
    exec su -s /bin/bash nobody "$0" "$@"
    # The script restarts here as the unprivileged user
fi

# ==============================================================================
# 5. IMMUTABLE VARIABLES
# ==============================================================================
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
readonly SCRIPT_NAME="$(basename "$0")"

# ==============================================================================
# 6. Run Regression Test
# ==============================================================================

# Initialize Test Tracking Variables
PASSED=0
FAILED=0
TEST_CASE_NUM=0
declare -a FAILED_TESTS=()

# Helper function to run a numbered regression case
run_regression_case() {
    local title="$1"
    local description="$2"
    shift 2

    TEST_CASE_NUM=$((TEST_CASE_NUM + 1))

    echo "--------------------------------------------------------------------"
    echo "Test Case #${TEST_CASE_NUM} - ${title}"
    echo "--------------------------------------------------------------------"
    echo "[+] ${description}"
    run_test "TC${TEST_CASE_NUM} - ${title}" "$@"
}

# Helper function for multi-step HAL/S template pipeline regression
run_template_pipeline_case() {
    local title="$1"
    local description="$2"
    local test_name=""
    local tpl_dir=""
    local tpl_file=""
    local step_failed=""
    local template_root=""

    TEST_CASE_NUM=$((TEST_CASE_NUM + 1))
    test_name="TC${TEST_CASE_NUM} - ${title}"
    tpl_dir="$SCRIPT_DIR/TEMPLATE_REGRESSION_TEMPLIB"
    template_root="$GIT_ROOT/docs/HAL-S-Source-Code-Examples/template_regression_test"

    echo "----------------------------------"
    echo "Test Case #${TEST_CASE_NUM} - ${title}"
    echo "----------------------------------"
    echo "[+] ${description}"

    rm -rf "$tpl_dir"
    mkdir -p "$tpl_dir"

    if ! "$_RESNIK" compile "$template_root/nav_update.hal" --gen-tpl "$tpl_dir"; then
        step_failed="nav_update.hal --gen-tpl"
    elif ! "$_RESNIK" compile "$template_root/sensor_pool.hal" --gen-tpl "$tpl_dir"; then
        step_failed="sensor_pool.hal --gen-tpl"
    else
        for tpl_file in "$tpl_dir"/NAV_UPDATE.tpl "$tpl_dir"/SENSOR_POOL.tpl; do
            if [[ ! -f "$tpl_file" ]]; then
                step_failed="missing $(basename "$tpl_file")"
                break
            fi
            echo "  Generated: $tpl_file"
        done
    fi

    if [[ -z "$step_failed" ]]; then
        if ! "$_RESNIK" compile "$template_root/guidance_main.hal" --templib "$tpl_dir"; then
            step_failed="guidance_main.hal --templib"
        fi
    fi

    if [[ -n "$step_failed" ]]; then
        echo "[✗ FAIL] $test_name ($step_failed)"
        FAILED=$((FAILED + 1))
        FAILED_TESTS+=("$test_name ($step_failed)")
    else
        echo "[✓ PASS] $test_name (Template pipeline)"
        PASSED=$((PASSED + 1))
    fi
    echo ""
}

# Helper function for C object linking pipeline regression (--link-obj)
run_link_obj_pipeline_case() {
    local title="$1"
    local description="$2"
    local c_source="$3"
    local hal_file="$4"
    local test_name=""
    local build_dir=""
    local obj_file=""
    local exe_file=""
    local step_failed=""

    TEST_CASE_NUM=$((TEST_CASE_NUM + 1))
    test_name="TC${TEST_CASE_NUM} - ${title}"
    build_dir="$SCRIPT_DIR/HW_INTERFACE_BUILD"
    obj_file="$build_dir/i2c.o"
    exe_file="${hal_file%.hal}"

    echo "----------------------------------"
    echo "Test Case #${TEST_CASE_NUM} - ${title}"
    echo "----------------------------------"
    echo "[+] ${description}"

    rm -rf "$build_dir"
    mkdir -p "$build_dir"

    if ! cc -c "$c_source" -o "$obj_file"; then
        step_failed="cc -c $(basename "$c_source") -o i2c.o"
    elif ! "$_RESNIK" compile "$hal_file" --emit-exe --link-obj "$obj_file"; then
        step_failed="resnik compile $(basename "$hal_file") --emit-exe --link-obj i2c.o"
    elif [[ ! -x "$exe_file" ]]; then
        step_failed="executable not found or not runnable ($(basename "$exe_file"))"
    else
        echo "  Linked object: $obj_file"
        echo "  Built executable: $exe_file"
        echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
        if ! timeout 120 "$exe_file"; then
            step_failed="execution failed or timed out"
        fi
        echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
    fi

    if [[ -n "$step_failed" ]]; then
        echo "[✗ FAIL] $test_name ($step_failed)"
        FAILED=$((FAILED + 1))
        FAILED_TESTS+=("$test_name ($step_failed)")
    else
        echo "[✓ PASS] $test_name (C link-obj pipeline)"
        PASSED=$((PASSED + 1))
    fi
    echo ""
}

# Helper function to run tests, catch failures, and prevent script exit
run_test() {
    local test_name="$1"
    shift # The rest of the arguments are the compilation command

    local hal_file=""
    local exe_file=""
    local gen_tpl_mode=false
    local gen_tpl_dir=""
    local arg=""
    local next_is_tpl_dir=false

    # Parse compile arguments for .hal input, --emit-exe, and --gen-tpl modes
    for arg in "$@"; do
        if [[ "$next_is_tpl_dir" == true ]]; then
            gen_tpl_dir="$arg"
            next_is_tpl_dir=false
            continue
        fi

        if [[ "$arg" == *.hal ]]; then
            hal_file="$arg"
            # Strip the '.hal' extension to get the executable path
            exe_file="${hal_file%.hal}"
        elif [[ "$arg" == "--gen-tpl" ]]; then
            gen_tpl_mode=true
            next_is_tpl_dir=true
        fi
    done

    # Step 1: Run the compilation command
    if "$@"; then
        if [[ "$gen_tpl_mode" == true ]]; then
            # --gen-tpl writes TEMPLATE prototypes only; no executable is produced
            local tpl_file=""
            local tpl_found=false

            if [[ -n "$gen_tpl_dir" ]] && [[ -d "$gen_tpl_dir" ]]; then
                for tpl_file in "${gen_tpl_dir}"/*.tpl; do
                    if [[ -f "$tpl_file" ]]; then
                        tpl_found=true
                        echo "  Generated: $tpl_file"
                    fi
                done
            fi

            if [[ "$tpl_found" == true ]]; then
                echo "[✓ PASS] $test_name (Template generation)"
                PASSED=$((PASSED + 1))
            else
                echo "[✗ FAIL] $test_name (No .tpl files found in ${gen_tpl_dir:-<unset>})"
                FAILED=$((FAILED + 1))
                FAILED_TESTS+=("$test_name (Missing Template)")
            fi
        # Step 2: Verify the executable was created and is runnable
        elif [[ -n "$exe_file" ]] && [[ -x "$exe_file" ]]; then

            # Step 3: Execute the compiled binary. The timeout keeps one
            # wedged program (e.g. a WAIT UNTIL whose predicate never
            # becomes true) from hanging the whole suite.
            echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
            if timeout 120 "$exe_file"; then
                echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
                echo "[✓ PASS] $test_name"
                PASSED=$((PASSED + 1))
            else
                rc=$?
                echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
                if [[ "$rc" -eq 124 ]]; then
                    echo "[✗ FAIL] $test_name (Timed out after 120s)"
                    FAILED_TESTS+=("$test_name (Timed Out)")
                else
                    echo "[✗ FAIL] $test_name (Execution Failed)"
                    FAILED_TESTS+=("$test_name (Execution Failed)")
                fi
                FAILED=$((FAILED + 1))
            fi
        else
            echo "[✗ FAIL] $test_name (Executable not found or not executable)"
            FAILED=$((FAILED + 1))
            FAILED_TESTS+=("$test_name (Missing Exe)")
        fi
    else
        echo "[✗ FAIL] $test_name (Compilation Failed)"
        FAILED=$((FAILED + 1))
        FAILED_TESTS+=("$test_name (Compilation Failed)")
    fi
    echo ""
}

# Banner
cat << "EOF"
 _____  ______  _____  _   _  _____  _    _ 
|  __ \|  ____|/ ____|| \ | ||_   _|| |  / /
| |__) | |__  | (___  |  \| |  | |  | | / / 
|  _  /|  __|  \___ \ | . ` |  | |  | |/ /  
| | \ \| |____ ____) || |\  | _| |_ | |\ \  
|_|  \_\______|_____/ |_| \_||_____||_| \_\ 
                                            
   [ R E G R E S S I O N   T E S T S ]      
                                            
EOF

run_regression_case "Hello World" \
    "Verify Resnik can compile a very simple hal source code example." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/hello_word/hello_world.hal --emit-exe

# -----------------------

run_regression_case "Abort Monitor" \
    "Verify Resnik parses HAL/S GOTO Statements (yuck!)." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/abort_monitor/abort_monitor.hal --emit-exe

run_regression_case "Array Math" \
    "Verify Resnik's AST parses type overloading and generates proper Rust backend code." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/array_math/array_math.hal --emit-exe

run_regression_case "Attitude Control" \
    "Verify Resnik correctly handles assignments between calculated linear algebra nodes and statically declared dummy vectors." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/attitude_control/attitude_control.hal --emit-exe

run_regression_case "Attitude Error" \
    "Verify Resnik can handle single expression to multiple variables simultaneously - combined intrinsic geometric functions into expression evaluation tree." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/attitude_err/attitude_err.hal --emit-exe

run_regression_case "Attitude Math" \
    "Verify Resnik parser's ability to build and resolve a deeply nested expression tree of geometric intrinsics." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/attitude_math/attitude_math.hal --emit-exe

run_regression_case "Bit Assembler" \
    "Verify Resnik's ability to handle the || (concatenate) operator on raw bit arrays, generating dynamically sized integer types on the fly." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/bit_assembler/bit_assembler.hal --emit-exe

run_regression_case "Bit Manipulation" \
    "Verify Resnik's parsing of SET / RESET Keywords." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/bit_manipulation/bit_manipulation.hal --emit-exe

run_regression_case "Basic Math Test" \
    "Verify Resnik's compatability with HAL/S Adjacency Multiplication 'feature'. Resnik must recognize that two adjacent operand tokens (with no explicit operator like + or / between them) form a multiplication expression." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/basic_math_test/basic_math_test.hal --emit-exe

run_regression_case "Branch Inference" \
    "Verify Resnik's ability to process multi-branch control flow using HAL/S DO CASE for state machine logic." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/branch_inference/branch_inference.hal --emit-exe

run_regression_case "Buffer Search" \
    "Verify Resnik's parses DOWNTO and EXIT control logic keywords." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/buffer_search/buffer_search.hal --emit-exe

run_regression_case "Command Decoder" \
    "Verify Resnik handles HAL/S's unique casting syntax and intrinsic conversions that is stressing strongly-typed semantic analyzer." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/cmd_decoder/cmd_decoder.hal --emit-exe

run_regression_case "Command Parser" \
    "Verify Resnik's ability to extract dynamic substrings using ranges." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/cmd_parser/cmd_parser.hal --emit-exe

run_regression_case "Compile Time Math" \
    "Verify Resnik evaluate constant mathematical expressions at compile time vs. runtime (HAL/S requirement)." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/compile_time_math/compile_time_math.hal --emit-exe

run_regression_case "CVE Monitor" \
    "Verify Resnik's compatability to HAL/S EQUATE keyword aliasing across entirely different primitive types, simulating a memory corruption check." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/cve_monitor/cve_monitor.hal --emit-exe

run_regression_case "Dangling Else Trap" \
    "Verify Resnik prioritizes IF-THEN-ELSE statement over an open IF-THEN statement." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/dangling_else_trap/dangling_else_trap.hal --emit-exe

run_regression_case "Data Pipeline" \
    "Verify Resnik's ability to pass complex, multi-dimensional structures by reference using the ASSIGN blocks." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/data_pipeline/data_pipeline.hal --emit-exe

run_regression_case "DMA Telemetry" \
    "Verify Resnik's parsing of ALIGNED, AUTOMATIC and DENSE keywords." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/dma_telemetry/dma_telemetry.hal --emit-exe

run_regression_case "Dynamic Slicing" \
    "Verify Resnik automatically injects runtime bounds-checking logic immediately prior to the slice operation." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/dynamic_slicing/dynamic_slicing.hal --emit-exe

run_regression_case "Escape Velocity" \
    "Verify Resnik's ability to handle standard mathematical built-in functions (like SQRT), extremely large scientific notation literals, and complex adjacency multiplication grouped by parentheses." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/escape_vel/escape_vel.hal --emit-exe

run_regression_case "Euler" \
    "Verify Resnik's conversion of vectors as primitive types." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/euler/euler.hal --emit-exe

run_regression_case "Event Gateway" \
    "Verify Resnik parses EVENT types to seamlessly coerce to boolean values during conditional checks, representing whether the event is currently in a \"signaled\" state." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/event_gateway/event_gateway.hal --emit-exe

run_regression_case "First Contact" \
    "Verify Resnik's conversion of vectors as primitive types." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/first_contact/first_contact.hal --emit-exe

run_regression_case "GN and C" \
    "Verify Resnik's ability to lex and parse COMPOOL and '|' operator for bitwise OR." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/gn_and_c/gn_and_c.hal --emit-exe

run_regression_case "GNC Correction" \
    "Verify Resnik's ability to process asynchronous tasks." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/gnc_correction/gnc_correction.hal --emit-exe

run_regression_case "JSTAR Telemetry" \
    "Verify Resnik ability to process HAL/S includes native error-trapping constructs (ON ERROR block) on divide-by-zero error." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/jstar_telemetry/jstar_telemetry.hal --emit-exe

run_regression_case "Kalman Filter" \
    "Verify Resnik ability to generated rust code uses fixed-size matrices so the inversion happens entirely on the stack, preventing dynamic heap allocations." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/kalman_filter/kalman_filter.hal --emit-exe

run_regression_case "Linear Algebra Test" \
    "Verify Resnik's ability to apply adjacency multiplication across complex data structures, rather than just scalars." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/linear_algebra_test/linear_algebra_test.hal --emit-exe

run_regression_case "Macro Check Bit" \
    "Verify Resnik's ability to parse multi-line MACRO blocks that accept parameters." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/macro_check_bit/macro_check_bit.hal --emit-exe

run_regression_case "Macro Hygiene" \
    "Verify Resnik's ability to handler local MACRO declaration whose name collides with the caller's." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/macro_hygiene/macro_hygiene.hal --emit-exe

run_regression_case "Matrix Builder" \
    "Verify Resnik strictly enforce that the input vectors match the dimensions of the target matrix." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/matrix_builder/matrix_builder.hal --emit-exe

run_regression_case "Matrix Gen" \
    "Verify Resnik lexer properly manages HAL/S repetition token parsing." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/matrix_gen/matrix_gen.hal --emit-exe

run_regression_case "NAV Filter" \
    "Verify Resnik's compatability of HAL/S unique indexing semantics for geometric types using advanced matrix partitioning and slicing." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/nav_filter/nav_filter.hal --emit-exe

run_regression_case "NAV Update Template" \
    "Verify Resnik executes lexer and parser on HAL/S template blocks and generates a tpl file instead of an execuatable." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/nav_update_template/nav_update_template.hal --gen-tpl "$SCRIPT_DIR/TEMPLIB"

run_regression_case "Orbital Kinematics" \
    "Verify Resnik's ability to handle mixed-type arithmetic—specifically, scalar-to-vector broadcasting and exponentiation." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/orbital_kinematics/orbital_kinematics.hal --emit-exe

run_regression_case "Percision Math" \
    "Verify Resnik's ability to handle mixed-precision math and automatic type promotion." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/percision_math/percision_math.hal --emit-exe

run_regression_case "Pitch DAP" \
    "Verify Resnik's lowerering of nested tasks to separate top-level Rust functions. " \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/pitch_dap/pitch_dap.hal --emit-exe

run_regression_case "PTR Resolution" \
    "Verify Resnik parses pass references to pre-allocated memory using the NAME keyword. This acts as a strictly controlled pointer. " \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/ptr_resolution/ptr_resolution.hal --emit-exe

run_regression_case "Ring Buffer" \
    "Verify Resnik parses the MOD keyword. " \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/ring_buffer/ring_buffer.hal --emit-exe

run_regression_case "SAT Mon" \
    "Verify Resnik's parsing with the REPLACE keyword before or during AST generation and Bit Slicing." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/sat_mon/sat_mon.hal --emit-exe

run_regression_case "Scope Test" \
    "Verify Resnik's Abstract Syntax Tree (AST) handles localized scopes. It defines a function with its own local variables before the main program logic begins." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/scope_test/scope_test.hal --emit-exe

run_regression_case "Shuttle HUD Driver" \
    "Verify Resnik's support for HAL/S dat buffer packing" \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/shuttle_hud_driver/shuttle_hud_driver.hal --emit-exe

run_regression_case "Shuttle State" \
    "Verify Resnik does not perform dyanmic memory allocation - all global data shared between different tasks and programs must be statically allocated in a COMPOOL." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/shuttle_state/shuttle_state.hal --emit-exe

run_regression_case "Same Scope Fault" \
    "Verify Resnik ability to trap fault within the scope of the handler." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/same_scope_fault/same_scope_fault.hal --emit-exe

run_regression_case "Sensor Init" \
    "Verify Resnik's ability to process standard DO FOR loop and dynamic 1-dimensional subscripting." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/sensor_init/sensor_init.hal --emit-exe

run_regression_case "SpaceWire Mon" \
    "Verify REPLACE macros, BIT(16) with HEX literals, MSB-first bit slicing (\$), and bitwise XOR on a link-status register." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/spacewire_mon/spacewire_mon.hal --emit-exe

run_regression_case "Spw Security" \
    "Verify Resnik's compliance adherence to operator precedence parsing for complex boolean and bitwise expressions." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/spw_security/spw_security.hal --emit-exe

run_regression_case "Stack Bubbling" \
    "Verify Resnik's stack fault processing when an error occurs deeply nested in a procedure w/o ON ERROR block." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/stack_bubbling/stack_bubbling.hal --emit-exe

run_regression_case "Star Tracker" \
    "Verify Resnik's ability to handle deeply nested data types—specifically, arrays where each element is a complex structure." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/star_tracker/star_tracker.hal --emit-exe

run_regression_case "String Truncation" \
    "Verify Resnik's ability to verify declared length of string on both sides (no dynamic string resizing)" \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/string_trunc/string_trunc.hal --emit-exe

run_regression_case "Telemetry App" \
    "Verify Resnik parser's ability to process structure templates, instantiate structure variables, and handle multi-element assignments." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/telemetry_app/telemetry_app.hal --emit-exe

run_regression_case "Telemetry Formatter" \
    "Verify Resnik's string declarations, literal assignments, and the concatenation operator." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/telemetry_formatter/telemetry_formatter.hal --emit-exe

run_regression_case "Telemetry Shift" \
    "Verify Resnik's successfully parses arget language's left-shift (<<) or right-shift (>>) operators depending on the sign of the literal." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/telemetry_shift/telemetry_shift.hal --emit-exe

run_regression_case "Tensor Init" \
    "Verify Resnik's ability to parse tests nested iteration and the memory stride calculations required for arrays containing complex intrinsic types." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/tensor_init/tensor_init.hal --emit-exe

run_regression_case "Tensor Math" \
    "Verify Resnik's ability to distinguish between a PROCEDURE (which performs actions, cannot return a value directly, and is invoked via CALL) and a FUNCTION (which strictly returns a value and can be embedded directly within mathematical expressions)." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/tensor_math/tensor_math.hal --emit-exe

run_regression_case "Tsiolkovsky Delta V Calc" \
    "Verify Resnik's abiliy to parse combined arrays, loops, logarithms (LN), and conditional margin checks." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/tsiolkovsky_deltav_calc/tsiolkovsky_deltav_calc.hal --emit-exe

run_regression_case "Voyager Signal" \
    "Verify Resnik's ability to parse scientific notation." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/voyager_signal/voyager_signal.hal --emit-exe

run_regression_case "Warp Drive Init" \
    "Verify Resnik's ability to parse ability to handle matrix-vector multiplication, universal constants with scientific notation, array iteration, and compound logical branching." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/warp_drive_init/warp_drive_init.hal --emit-exe

run_regression_case "Yaw Rotate" \
    "Verify Resnik's ability to parse 2D matrix element assignments and trigonometric built-in functions." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/yaw_rotate/yaw_rotate.hal --emit-exe

run_regression_case "Lexer Edges" \
    "Verify block comments are token boundaries (not identifier glue), suffix '1010'B/'FF'H/'17'O and OCT literals, mixed-case hex, and a 32-character identifier." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/lexer_edges/lexer_edges.hal --emit-exe

run_regression_case "Goto Token" \
    "Verify the one-token GOTO spelling (not just GO TO) and case-insensitive label dispatch." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/goto_token/goto_token.hal --emit-exe

run_regression_case "Group Multiply" \
    "Verify juxtaposition multiply for NUMBER+( and )+( forms: 2.0(X+1.0) and (X+1.0)(X-1.0)." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/group_multiply/group_multiply.hal --emit-exe

run_regression_case "Replace Parametric" \
    "Verify parameterized REPLACE name(params) BY substitution, including expansion into NUMBER+( juxtaposition." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/replace_parametric/replace_parametric.hal --emit-exe

run_regression_case "Char Pad Length" \
    "Verify CHARACTER INITIAL blank-padding, LENGTH trailing-blank trim, blank-extended compare, and exact-fit concatenation." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/char_pad_length/char_pad_length.hal --emit-exe

run_regression_case "Bit Layout" \
    "Verify unsized BIT \$1 as MSB of 32, BIT(16)->BIT(8) MSB keep, BIT(8)->BIT(16) MSB justify, and ARRAY OF BIT nested subscripts." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/bit_layout/bit_layout.hal --emit-exe

run_regression_case "For Semantics" \
    "Verify DO FOR mutates the declared index (value after EXIT and after completion), BY step, and EXIT <label> from a nested loop." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/for_semantics/for_semantics.hal --emit-exe

run_regression_case "Integer Arith" \
    "Verify INTEGER constant 7/2 folds to 3, wrapping overflow, Euclidean MOD of a negative, INTEGER ** INTEGER, and SCALAR ** 0.5." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/integer_arith/integer_arith.hal --emit-exe

run_regression_case "Compool Fault" \
    "Verify ON ERROR in a PROGRAM that is a sibling of a COMPOOL, and COMPOOL read-modify-write taking a single lock." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/compool_fault/compool_fault.hal --emit-exe

run_regression_case "Aldrin Sys Exec" \
    "Verify cyclic SCHEDULE ... REPEAT EVERY, WAIT UNTIL on a COMPOOL flag, and CANCEL of the cyclic task." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/aldrin_sys_exec/aldrin_sys_exec.hal --emit-exe

run_regression_case "Exec Two Rates" \
    "Verify two SCHEDULE ... PRIORITY REPEAT EVERY tasks sharing a COMPOOL counter (host: one representative cycle each; embedded: the hal_exec cyclic executive)." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/exec_two_rates/exec_two_rates.hal --emit-exe

run_regression_case "Exec Priority Chain" \
    "Verify one-shot SCHEDULE ... PRIORITY at four priorities including a declaration-order tie, all bumping a shared COMPOOL sequence counter." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/exec_priority_chain/exec_priority_chain.hal --emit-exe

run_regression_case "Exec Event Latch" \
    "Verify the EVENT lifecycle: event-form SET and RESET, SIGNAL, EVENT read as a boolean in IF, and a task observing the latched state." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/exec_event_latch/exec_event_latch.hal --emit-exe

run_regression_case "Exec Terminate Forms" \
    "Verify bare TERMINATE self-termination inside a task (statements after it never run) and named TERMINATE of another task." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/exec_terminate_forms/exec_terminate_forms.hal --emit-exe

run_regression_case "Exec Oneshot Delay" \
    "Verify one-shot SCHEDULE ... IN d rendezvousing with the program via a COMPOOL flag and WAIT UNTIL." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/exec_oneshot_delay/exec_oneshot_delay.hal --emit-exe

run_regression_case "Exec Cancel Cycle" \
    "Verify periodic SCHEDULE ... IN d REPEAT EVERY stopped by CANCEL after bounded foreground work (the mandatory stop/join pair for the delay form)." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/exec_cancel_cycle/exec_cancel_cycle.hal --emit-exe

run_regression_case "Until Loops" \
    "Verify DO UNTIL (first cycle unguarded, stop when TRUE) and DO FOR ... UNTIL (guard after bound check, empty range never enters)." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/until_loops/until_loops.hal --emit-exe

run_regression_case "Cat and Dec" \
    "Verify CAT as the linear-form spelling of || (bit and character catenation) and DEC 'digits' decimal bit literals." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/cat_dec/cat_dec.hal --emit-exe

run_regression_case "Name Null" \
    "Verify NULL / NAME(NULL) as a NAME value, ASSIGN P TO NULL and NAME(P) = NULL unbind, and IF NAME(P) = NULL pointer compare (not pointee deref)." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/name_null/name_null.hal --emit-exe

run_template_pipeline_case "HAL/S Template Processing" \
    "Verify Resnik template workflow including supporting the OG 'D INCLUDE TEMPLATE' pre-processor directive (Link multiple templates into main)."

run_link_obj_pipeline_case "HW_Interface" \
    "Verify Resnik links HAL/S EXTERNAL symbols against a foreign C object file via --link-obj." \
    "$GIT_ROOT/docs/HAL-S-Source-Code-Examples/hw_interface/i2c.c" \
    "$GIT_ROOT/docs/HAL-S-Source-Code-Examples/hw_interface/hw_interface.hal"

# ==============================================================================
# 7. SUMMARY
# ==============================================================================
echo ""
echo "=================================="
echo "    REGRESSION TEST SUMMARY       "
echo "=================================="
echo "Passed: $PASSED"
echo "Failed: $FAILED"

if [[ $FAILED -gt 0 ]]; then
    echo ""
    echo "Failing Tests:"
    for test in "${FAILED_TESTS[@]}"; do
        echo "  - $test"
    done
    # Exit with an error code since regression failed
    exit 1 
else
    echo ""
    echo "All executed tests passed successfully!"
    exit 0
fi