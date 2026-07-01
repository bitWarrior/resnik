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

    echo "----------------------------------"
    echo "Test Case #${TEST_CASE_NUM} - ${title}"
    echo "----------------------------------"
    echo "[+] ${description}"
    run_test "TC${TEST_CASE_NUM} - ${title}" "$@"
}

# Helper function to run tests, catch failures, and prevent script exit
run_test() {
    local test_name="$1"
    shift # The rest of the arguments are the compilation command

    local hal_file=""
    local exe_file=""

    # Dynamically find the .hal file in the argument list
    for arg in "$@"; do
        if [[ "$arg" == *.hal ]]; then
            hal_file="$arg"
            # Strip the '.hal' extension to get the executable path
            exe_file="${hal_file%.hal}"
            break
        fi
    done

    # Step 1: Run the compilation command
    if "$@"; then
        # Step 2: Verify the executable was created and is runnable
        if [[ -n "$exe_file" ]] && [[ -x "$exe_file" ]]; then
            
            # Step 3: Execute the compiled binary
            echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
            if "$exe_file"; then
                echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
                echo "[✓ PASS] $test_name"
                PASSED=$((PASSED + 1))
            else
                echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
                echo "[✗ FAIL] $test_name (Execution Failed)"
                FAILED=$((FAILED + 1))
                FAILED_TESTS+=("$test_name (Execution Failed)")
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

run_regression_case "Abort Monitor" \
    "Verify Resnik parses HAL/S GOTO Statements (yuck!)." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/abort_monitor/abort_monitor.hal --emit-exe

run_regression_case "Attitude Control" \
    "Verify Resnik correctly handles assignments between calculated linear algebra nodes and statically declared dummy vectors." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/attitude_control/attitude_control.hal --emit-exe

run_regression_case "Attitude Error" \
    "Verify Resnik can handle single expression to multiple variables simultaneously - combined intrinsic geometric functions into expression evaluation tree." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/attitude_err/attitude_err.hal --emit-exe

run_regression_case "Attitude Math" \
    "Verify Resnik parser's ability to build and resolve a deeply nested expression tree of geometric intrinsics." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/attitude_math/attitude_math.hal --emit-exe

run_regression_case "Basic Math Test" \
    "Verify Resnik's compatability with HAL/S Adjacency Multiplication 'feature'. Resnik must recognize that two adjacent operand tokens (with no explicit operator like + or / between them) form a multiplication expression." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/basic_math_test/basic_math_test.hal --emit-exe

run_regression_case "Branch Inference" \
    "Verify Resnik's ability to process multi-branch control flow using HAL/S DO CASE for state machine logic." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/branch_inference/branch_inference.hal --emit-exe

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
    "Verify Resnik compatability to HAL/S EQUATE keyword aliasing across entirely different primitive types, simulating a memory corruption check." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/cve_monitor/cve_monitor.hal --emit-exe

run_regression_case "Data Pipeline" \
    "Verify Resnik's ability to pass complex, multi-dimensional structures by reference using the ASSIGN blocks." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/data_pipeline/data_pipeline.hal --emit-exe

run_regression_case "Escape Velocity" \
    "Verify Resnik's ability to handle standard mathematical built-in functions (like SQRT), extremely large scientific notation literals, and complex adjacency multiplication grouped by parentheses." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/escape_vel/escape_vel.hal --emit-exe

run_regression_case "EULER" \
    "Verify Resnik's conversion of vectors as primitive types." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/euler/euler.hal --emit-exe

run_regression_case "Escape Velocity" \
    "Verify Resnik parses EVENT types to seamlessly coerce to boolean values during conditional checks, representing whether the event is currently in a "signaled" state." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/escape_vel/escape_vel.hal --emit-exe

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

run_regression_case "Matrix Builder" \
    "Verify Resnik strictly enforce that the input vectors match the dimensions of the target matrix." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/matrix_builder/matrix_builder.hal --emit-exe

run_regression_case "Matrix Gen" \
    "Verify Resnik lexer properly manages HAL/S repetition token parsing." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/matrix_gen/matrix_gen.hal --emit-exe

run_regression_case "NAV Filter" \
    "Verify Resnik's compatability of HAL/S unique indexing semantics for geometric types using advanced matrix partitioning and slicing." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/nav_filter/nav_filter.hal --emit-exe

run_regression_case "ORBITAL KINEMATICS" \
    "Verify Resnik's ability to handle mixed-type arithmetic—specifically, scalar-to-vector broadcasting and exponentiation." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/orbital_kinematics/orbital_kinematics.hal --emit-exe

run_regression_case "Pitch DAP" \
    "Verify Resnik's lowerering of nested tasks to separate top-level Rust functions. " \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/pitch_dap/pitch_dap.hal --emit-exe

run_regression_case "PTR Resolution" \
    "Verify Resnik parses pass references to pre-allocated memory using the NAME keyword. This acts as a strictly controlled pointer. " \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/ptr_resolution/ptr_resolution.hal --emit-exe

run_regression_case "Sat Mon" \
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

run_regression_case "Sensor Init" \
    "Verify Resnik's ability to process standard DO FOR loop and dynamic 1-dimensional subscripting." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/sensor_init/sensor_init.hal --emit-exe

run_regression_case "SpaceWire Mon" \
    "Verify Resnik's processing of HAL/S process synchronization using EVENT variables and WAIT commands." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/spacewire_mon/spacewire_mon.hal --emit-exe

run_regression_case "Spw Security" \
    "Verify Resnik's compliance adherence to operator precedence parsing for complex boolean and bitwise expressions." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/spw_security/spw_security.hal --emit-exe

run_regression_case "Star Tracker" \
    "Verify Resnik's ability to to handle deeply nested data types—specifically, arrays where each element is a complex structure." \
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

run_regression_case "Warp Drive Init" \
    "Verify Resnik's ability to parse ability to handle matrix-vector multiplication, universal constants with scientific notation, array iteration, and compound logical branching." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/warp_drive_init/warp_drive_init.hal --emit-exe

run_regression_case "Yaw Rotate" \
    "Verify Resnik's ability to parse 2D matrix element assignments and trigonometric built-in functions." \
    "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/yaw_rotate/yaw_rotate.hal --emit-exe

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