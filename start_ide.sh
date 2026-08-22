#!/usr/bin/env bash
# Launch the Resnik IDE.
#
#   ./start_ide.sh                 # empty editor
#   ./start_ide.sh program.hal     # open a file
#
# Uses the repo venv (resnik-venv) when present, otherwise whatever python3
# is on PATH. No need to activate the venv first.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -x "$HERE/resnik-venv/bin/python" ]]; then
    PY="$HERE/resnik-venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="$(command -v python3)"
else
    echo "start_ide.sh: python3 not found (and no resnik-venv/). See README Installation." >&2
    exit 1
fi

# Tk is a system package on some distros (python3-tk on Debian/Ubuntu).
if ! "$PY" -c 'import tkinter' 2>/dev/null; then
    echo "start_ide.sh: Python has no tkinter module. On Debian/Ubuntu: sudo apt install python3-tk" >&2
    exit 1
fi

# The IDE package is installed with the compiler (pip install -e .).
if ! "$PY" -c 'import resnik_ide' 2>/dev/null; then
    echo "start_ide.sh: resnik_ide is not installed in $PY." >&2
    echo "  Fix: . resnik-venv/bin/activate && pip install -e ." >&2
    exit 1
fi

exec "$PY" -m resnik_ide "$@"
