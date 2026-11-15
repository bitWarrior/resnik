# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Steven Doran
"""
Resnik Backend Package
=====================

Code generator for translating HAL/S AST to Rust source code.

Modules:
    - codegen: Main AST to Rust code generator
    - rust_emitter: Rust code formatting and emission
    - type_mapper: HAL/S to Rust type mapping
"""

from resnik.backend.codegen import RustCodeGenerator
from resnik.backend.rust_emitter import RustEmitter
from resnik.backend.target_spec import (
    TARGET_NAMES,
    TARGET_TRIPLES,
    TargetSpec,
    lookup_target,
)
from resnik.backend.type_mapper import TypeMapper

__all__ = [
    "RustCodeGenerator",
    "RustEmitter",
    "TypeMapper",
    "TargetSpec",
    "TARGET_NAMES",
    "TARGET_TRIPLES",
    "lookup_target",
]
