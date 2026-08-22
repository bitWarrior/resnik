"""
Target-aware check: which HAL/S features of a parsed unit would make the
generated firmware fail on a ``no_std`` target?

The backend already emits ``compile_error!("resnik: …")`` for those
(VECTOR/MATRIX & math intrinsics, SCHEDULE/TASK, COMPOOL — see
``RustCodeGenerator._emit_embedded_guard``).  Generating the code in memory
and collecting those messages surfaces them at Semantic Check time instead
of at ``cargo build`` time.
"""

from __future__ import annotations

import re
from typing import List

from resnik.backend.target_spec import SPECS
from resnik.driver import CheckResult

_ERR = re.compile(r'compile_error!\("resnik: (.*?)"\);')


def target_issues(res: CheckResult, target: str) -> List[str]:
    """Messages for features unsupported on ``target``; [] for host / errors."""
    spec = SPECS.get(target)
    if spec is None or not spec.embedded or not res.ok or res.ast is None:
        return []
    from resnik.backend.codegen import RustCodeGenerator

    try:
        code = RustCodeGenerator(target=target).generate(res.ast)
    except Exception as exc:  # codegen crash must not hide the semantic result
        return [f"code generation failed for target {target}: {exc}"]
    return [m.group(1) for m in _ERR.finditer(code)]
