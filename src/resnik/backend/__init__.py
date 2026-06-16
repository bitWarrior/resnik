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
from resnik.backend.type_mapper import TypeMapper

__all__ = ["RustCodeGenerator", "RustEmitter", "TypeMapper"]
