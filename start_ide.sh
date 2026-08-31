#!/usr/bin/env bash
# Launch the Resnik IDE.
#
#   ./start_ide.sh                 # empty editor
#   ./start_ide.sh program.hal     # open a file
#
# Creates the repo venv (resnik-venv) and pip-installs Resnik plus its
# required packages (dependency wheels, and any local dist/*.whl or
# wheels/*.whl) if the environment does not exist. On that first setup
# it also installs the freedesktop launcher (resnik-ide --install-desktop)
# so the applications menu / GNOME icon is in place. No need to activate
# the venv first.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/resnik-venv"
PY="$VENV/bin/python"

install_required() {
    echo "start_ide.sh: installing Resnik and required packages" >&2
    "$PY" -m pip install -U pip

    # Prefer any locally provided wheels (offline / pinned builds).
    local wheels=()
    local f
    for f in "$HERE"/dist/*.whl "$HERE"/wheels/*.whl; do
        [[ -f "$f" ]] && wheels+=("$f")
    done
    if (( ${#wheels[@]} > 0 )); then
        echo "start_ide.sh: installing local wheels" >&2
        "$PY" -m pip install "${wheels[@]}"
    fi

    # Editable install of this repo (pulls rply, click, rich as wheels).
    "$PY" -m pip install -e "$HERE"
}

run_ide() {
    if [[ -x "$VENV/bin/resnik-ide" ]]; then
        "$VENV/bin/resnik-ide" "$@"
    else
        "$PY" -m resnik_ide "$@"
    fi
}

install_desktop() {
    echo "start_ide.sh: installing desktop launcher" >&2
    if ! run_ide --install-desktop; then
        echo "start_ide.sh: desktop launcher install failed (IDE will still start)" >&2
    fi
}

if [[ ! -x "$PY" ]]; then
    if ! command -v python3 >/dev/null 2>&1; then
        echo "start_ide.sh: python3 not found. See README Installation." >&2
        exit 1
    fi
    echo "start_ide.sh: creating virtual environment at $VENV" >&2
    if ! python3 -m venv "$VENV"; then
        echo "start_ide.sh: failed to create venv. On Debian/Ubuntu: sudo apt install python3-venv python3-tk" >&2
        exit 1
    fi
    install_required
    install_desktop
fi

# Tk is a system package on some distros (python3-tk on Debian/Ubuntu).
if ! "$PY" -c 'import tkinter' 2>/dev/null; then
    echo "start_ide.sh: Python has no tkinter module. On Debian/Ubuntu: sudo apt install python3-tk" >&2
    echo "  Recreate the venv after installing Tk: rm -rf $VENV && ./start_ide.sh" >&2
    exit 1
fi

# The IDE package is installed with the compiler (pip install -e .).
if ! "$PY" -c 'from resnik_ide.app import main' 2>/dev/null; then
    echo "start_ide.sh: resnik_ide is not installed in $PY; installing..." >&2
    install_required
    if ! "$PY" -c 'from resnik_ide.app import main' 2>/dev/null; then
        echo "start_ide.sh: resnik_ide is not installed in $PY." >&2
        echo "  Fix: . $VENV/bin/activate && pip install -e ." >&2
        exit 1
    fi
fi

if [[ -x "$VENV/bin/resnik-ide" ]]; then
    exec "$VENV/bin/resnik-ide" "$@"
fi
exec "$PY" -m resnik_ide "$@"
