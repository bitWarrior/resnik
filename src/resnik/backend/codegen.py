# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Steven Doran
"""
Rust code generator from HAL/S AST.
"""

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional, List
from pathlib import Path

from resnik.backend.target_spec import (
    TARGET_NAMES,
    TARGET_TRIPLES,
    TargetSpec,
    lookup_target,
)
from resnik.parser.ast_nodes import (
    NameAssignNode,
    NullNode,
    NameRefNode,
    GotoNode,
    LabelNode,
    ASTNode,
    ProgramNode,
    TaskNode,
    CompoolNode,
    CompilationNode,
    DeclareNode,
    ProcedureNode,
    AssignmentNode,
    BinaryOpNode,
    UnaryOpNode,
    SubscriptNode,
    ForNode,
    ExitNode,
    StarNode,
    IndexListNode,
    RangeNode,
    MatrixLiteralNode,
    MatrixSubscriptNode,
    VectorLiteralNode,
    StructureDefNode,
    FieldAccessNode,
    OnErrorNode,
    ResumeNode,
    ReturnNode,
    MultiAssignNode,
    LiteralNode,
    IdentifierNode,
    WriteNode,
    ReadNode,
    FunctionCallNode,
    CallNode,
    DoBlockNode,
    WhileNode,
    CaseNode,
    IfNode,
    ScheduleNode,
    TerminateNode,
    WaitNode,
    SignalNode,
    CancelNode,
)
from resnik.backend.rust_emitter import RustEmitter
from resnik.backend.type_mapper import TypeMapper
from resnik.exceptions import BackendError
from resnik.types import NodeType, DataType, Precision


@dataclass
class CompileResult:
    """Result of compiling generated Rust source to a native executable."""

    success: bool
    executable: Optional[str] = None
    source: Optional[str] = None
    stdout: str = ""
    stderr: str = ""
    library: bool = False  # True when built as a lib (no PROGRAM entry point)


@dataclass
class CargoProjectResult:
    """Result of scaffolding (and optionally building) a Cargo project."""

    project_dir: str
    cargo_toml: str
    main_rs: str
    kept_files: list = field(default_factory=list)  # existed; not overwritten
    built: bool = False
    success: Optional[bool] = None
    executable: Optional[str] = None
    stdout: str = ""
    stderr: str = ""


class ConstantFolder:
    """AST optimization pass: fold compile-time-constant expressions to
    literals, so the flight computer spends no cycles recomputing values fixed
    at build time. Runs after parsing and before code generation. A subtree
    folds when every leaf is a numeric literal or a CONSTANT whose value is
    itself foldable; intrinsic calls are evaluated with Python's math module.
    Anything referencing a runtime variable is left untouched.
    """

    import math as _math

    _UNARY_INTRINSICS = {
        "SQRT": _math.sqrt, "SIN": _math.sin, "COS": _math.cos,
        "TAN": _math.tan, "ARCSIN": _math.asin, "ARCCOS": _math.acos,
        "ARCTAN": _math.atan, "SINH": _math.sinh, "COSH": _math.cosh,
        "TANH": _math.tanh, "EXP": _math.exp, "LOG": _math.log,
        "LN": _math.log, "ABS": abs,
    }

    def __init__(self) -> None:
        self.constants: dict = {}
        self.folded_count = 0

    def run(self, ast):
        if ast is None:
            return ast
        # Pass 1: collect CONSTANT values in declaration order (a later constant
        # may be defined from an earlier one), folding each definition.
        for node in self._walk(ast):
            if (isinstance(node, DeclareNode) and node.is_constant
                    and node.initial_value is not None
                    and len(node.identifiers) == 1):
                val = self._eval(node.initial_value)
                if val is not None:
                    dt = node.type_spec.data_type if node.type_spec else None
                    if dt == DataType.SCALAR:
                        val = float(val)
                    elif dt == DataType.INTEGER:
                        val = int(val)
                    self.constants[node.identifiers[0].lower()] = val
                    node.initial_value = self._literal(val)
        # Pass 2: fold every other expression in the tree.
        self._transform(ast)
        return ast

    def _eval(self, node):
        if isinstance(node, LiteralNode):
            v = node.value
            if isinstance(v, bool):
                return None
            if isinstance(v, (int, float)):
                return v
            return None
        if isinstance(node, IdentifierNode):
            return self.constants.get(node.name.lower())
        if isinstance(node, UnaryOpNode):
            o = self._eval(node.operand)
            if o is None:
                return None
            if node.operator == "-":
                return -o
            if node.operator == "+":
                return o
            return None
        if isinstance(node, BinaryOpNode):
            lo = self._eval(node.left)
            ro = self._eval(node.right)
            if lo is None or ro is None:
                return None
            op = node.operator
            try:
                if op == "+":
                    return lo + ro
                if op == "-":
                    return lo - ro
                if op == "*":
                    return lo * ro
                if op == "/":
                    if ro == 0:
                        return None
                    # INTEGER / INTEGER is truncating (toward zero), matching
                    # the runtime `i32` `/`. Python 3 `/` is always float.
                    if (isinstance(lo, int) and isinstance(ro, int)
                            and not isinstance(lo, bool)
                            and not isinstance(ro, bool)):
                        return int(lo / ro)
                    return lo / ro
                if op == "**":
                    if (isinstance(lo, int) and isinstance(ro, int)
                            and not isinstance(lo, bool)
                            and not isinstance(ro, bool) and ro >= 0):
                        return lo ** ro
                    return float(lo) ** float(ro)
            except (ValueError, OverflowError, ZeroDivisionError):
                return None
            return None
        if isinstance(node, FunctionCallNode):
            args = [self._eval(a) for a in node.arguments]
            if any(a is None for a in args):
                return None
            nm = node.name.upper()
            try:
                if nm in self._UNARY_INTRINSICS and len(args) == 1:
                    return float(self._UNARY_INTRINSICS[nm](args[0]))
                if nm == "ARCTAN2" and len(args) == 2:
                    return self._math.atan2(args[0], args[1])
            except (ValueError, OverflowError):
                return None
        return None

    def _literal(self, value) -> LiteralNode:
        self.folded_count += 1
        if isinstance(value, int) and not isinstance(value, bool):
            return LiteralNode(value=value, literal_type="INTEGER")
        return LiteralNode(value=float(value), literal_type="SCALAR")

    def _transform(self, node) -> None:
        for fname, fval in list(vars(node).items()):
            if isinstance(fval, ASTNode):
                folded = self._eval(fval)
                if folded is not None and not isinstance(fval, LiteralNode):
                    setattr(node, fname, self._literal(folded))
                else:
                    self._transform(fval)
            elif isinstance(fval, list):
                new_list = []
                for item in fval:
                    if isinstance(item, ASTNode):
                        folded = self._eval(item)
                        if folded is not None and not isinstance(
                                item, LiteralNode):
                            new_list.append(self._literal(folded))
                        else:
                            self._transform(item)
                            new_list.append(item)
                    else:
                        new_list.append(item)
                setattr(node, fname, new_list)

    def _walk(self, node):
        yield node
        for fval in vars(node).values():
            children = (fval if isinstance(fval, list)
                        else [fval] if isinstance(fval, ASTNode) else [])
            for child in children:
                if isinstance(child, ASTNode):
                    yield from self._walk(child)


class RustCodeGenerator:
    """
    Generate Rust code from HAL/S AST.
    
    Translates HAL/S constructs to idiomatic Rust:
    - PROGRAM → fn main()
    - DECLARE → let statements
    - PROCEDURE → fn definitions
    - Assignments → variable assignments
    - Expressions → Rust expressions
    
    Example:
        >>> generator = RustCodeGenerator()
        >>> rust_code = generator.generate(ast)
        >>> generator.write_to_file("output.rs", rust_code)
    """

    # HAL/S runtime fault code for divide-by-zero, as used by ON ERROR(n).
    # NOTE: a full implementation would map each HAL/S runtime error to its
    # real (group:code); this is the single code the current lowering raises.
    _DIVIDE_FAULT_CODE = 5

    # Rust reserved keywords that can be escaped with the raw-identifier
    # syntax r#foo. HAL/S source may legally use these as identifiers (e.g.
    # a variable named TYPE or MATCH), and the lowered snake_case would be
    # a syntax error in Rust without escaping.
    _RUST_KEYWORDS_RAW = frozenset({
        "as", "break", "const", "continue", "else", "enum", "extern",
        "false", "fn", "for", "if", "impl", "in", "let", "loop", "match",
        "mod", "move", "mut", "pub", "ref", "return", "static", "struct",
        "trait", "true", "type", "unsafe", "use", "where", "while",
        "async", "await", "dyn",
        "abstract", "become", "box", "do", "final", "macro", "override",
        "priv", "typeof", "unsized", "virtual", "yield", "try",
    })
    # Rust keywords that Rust refuses to escape with r# (they have syntactic
    # roles the parser reserves). A trailing underscore suffix disambiguates.
    _RUST_KEYWORDS_NO_RAW = frozenset({"crate", "self", "super"})

    @staticmethod
    def _escape_rust_string(s: str) -> str:
        """Escape a Python string so it can be embedded between `"..."` as a
        Rust string literal without losing bytes or terminating early.

        Order matters: backslash first, then quote, then the named control
        escapes Rust recognises (\\n, \\r, \\t, \\0). Any remaining C0 or
        DEL byte becomes a `\\xNN` hex escape. Printable UTF-8 passes
        through unchanged -- Rust source is UTF-8, so no re-encoding needed.
        """
        out = []
        for ch in s:
            if ch == "\\":
                out.append("\\\\")
            elif ch == '"':
                out.append('\\"')
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            elif ch == "\0":
                out.append("\\0")
            elif ord(ch) < 0x20 or ord(ch) == 0x7F:
                out.append(f"\\x{ord(ch):02X}")
            else:
                out.append(ch)
        return "".join(out)

    @classmethod
    def _rust_ident(cls, name: str) -> str:
        """Lowercase a HAL/S source identifier and rewrite it if the result
        collides with a Rust keyword. Applied at every point a HAL/S name is
        written into emitted Rust text; internal dict keys keep the raw
        lowercase (they never reach Rust)."""
        lo = name.lower()
        if lo in cls._RUST_KEYWORDS_NO_RAW:
            return f"{lo}_"
        if lo in cls._RUST_KEYWORDS_RAW:
            return f"r#{lo}"
        return lo

    # Supported profiles (from target_spec.SPECS). "host" is hosted std;
    # "cortex-m" is the no_std stub; "esp32-c3" is the RISC-V esp-hal
    # profile. Kept as class attrs so the CLI can build click.Choice /
    # triples without constructing a generator.
    _TARGETS = TARGET_NAMES
    TARGET_TRIPLES = TARGET_TRIPLES

    def __init__(self, target: str = "host"):
        """Initialize code generator.

        Args:
            target: Code-generation profile name (see ``TargetSpec`` /
                ``target_spec.SPECS``). Default ``host``.
        """
        self.spec: TargetSpec = lookup_target(target)
        self.target = self.spec.name
        self.emitter = RustEmitter()
        self.type_mapper = TypeMapper()
        self.in_main = False
        self._dense_structs: set = set()
        self._extern_vars: set = set()  # DECLARE ... EXTERNAL variable names
        self._task_names: set = set()
        self._bit_widths: dict = {}
        self._var_info: dict = {}
        self._globals: dict = {}
        self._structs: dict = {}
        self._error_handlers: dict = {}
        self._panic_hook_emitted: bool = False
        self._equates: dict = {}
        # ON ERROR fault model (populated by _analyze_faults each generate()):
        self._uses_faults: bool = False
        self._unit_handlers: dict = {}     # unit key -> {code: handler body}
        self._proc_raises: dict = {}       # proc -> set of codes it may propagate
        self._faulting_procs: set = set()  # procs that get a Result signature
        self._cur_unit: str = "@main"      # "@main" or a procedure name
        self._cur_unit_is_result: bool = False
    
    def generate(self, ast: Optional[ASTNode]) -> str:
        """
        Generate Rust code from AST.
        
        Args:
            ast: Root AST node (usually ProgramNode)
            
        Returns:
            Generated Rust source code
        """
        if ast is None:
            return "// No AST to generate code from\n"
        
        self.emitter.clear()

        # Pre-pass: record declared BIT widths (for $ bit subscripting) and a
        # general variable -> TypeSpec map (so $ on a VECTOR does element
        # access rather than a bit extract).
        self._bit_widths = {}
        self._var_info = {}
        self._compool_guard = {}
        self._compool_locked = False
        for node in self._walk(ast):
            if isinstance(node, DeclareNode) and node.type_spec is not None:
                for ident in node.identifiers:
                    self._var_info[ident.lower()] = node.type_spec
                if node.type_spec.data_type == DataType.BIT:
                    # Unsized BIT maps to u32 (TypeMapper._bit_type(None));
                    # subscripting must use the same width so $1 is the MSB.
                    width = (
                        node.type_spec.dimensions[0]
                        if node.type_spec.dimensions else 32
                    )
                    for ident in node.identifiers:
                        self._bit_widths[ident.lower()] = width

        # Pre-pass: record COMPOOL globals so task/program bodies resolve their
        # references to the shared statics (const for CONSTANT, Mutex otherwise).
        self._globals = {}
        for node in self._walk(ast):
            if isinstance(node, CompoolNode):
                for decl in node.declarations:
                    rust_type = self.type_mapper.map_type(decl.type_spec)
                    kind = "const" if decl.is_constant else "mut"
                    for ident in decl.identifiers:
                        self._globals[ident.upper()] = (kind, rust_type)

        # Pre-pass: structure templates, by upper-cased name.
        self._structs = {}
        self._error_handlers = {}
        self._panic_hook_emitted = False
        self._equates = {}
        self._name_vars = {}
        for node in self._walk(ast):
            if isinstance(node, StructureDefNode):
                self._structs[node.name.upper()] = node
            if (isinstance(node, DeclareNode) and node.type_spec is not None
                    and getattr(node.type_spec, "is_name", False)):
                rust_type = self.type_mapper.map_type(node.type_spec)
                for ident in node.identifiers:
                    self._name_vars[ident.lower()] = rust_type

        # Pre-pass: ON ERROR scope analysis (which procedures propagate faults,
        # and which handlers are active in each scope). Drives the Result-based
        # stack-unwinding lowering.
        self._analyze_faults(ast)

        # Emit file header
        self._emit_file_header()

        # Portability guard: features whose lowering still depends on std are
        # not yet available on a no_std target. Emit a loud compile_error! so
        # the boundary is explicit rather than a confusing linker failure.
        if self._embedded:
            self._emit_embedded_guard(ast)

        # Structs used in a DENSE array get a #[repr(C, packed)] companion so
        # dense arrays can be laid out end-to-end; aligned arrays keep the
        # #[repr(C)] form. Field access on packed elements is lowered through
        # read_unaligned/write_unaligned (Rust forbids references into packed
        # fields), and cross-layout copies convert element-by-element.
        self._dense_structs = {
            (n.type_spec.element_type.type_name or "").upper()
            for n in self._walk(ast)
            if isinstance(n, DeclareNode) and n.type_spec is not None
            and getattr(n.type_spec, "layout", "aligned") == "dense"
            and n.type_spec.element_type is not None
            and n.type_spec.element_type.type_name
        }

        # Emit the runtime prelude if the program uses vectors/matrices,
        # runtime intrinsics, or real-time statements. Embedded targets also
        # need it for plain scalar float math (FloatExt via libm), which is
        # inherent-on-f32 (std) everywhere else.
        if self._uses_runtime(ast) or (
                self.spec.embedded and self._uses_float_math(ast)):
            self._emit_prelude()
        if self.spec.embedded and self._uses_exec(ast):
            self._emit_hal_exec()

        # COMPOOL shared data lowers to module-level statics so every generated
        # function (program + scheduled tasks) can reach it. Emit each COMPOOL
        # up front wherever it appears -- a top-level unit OR nested inside a
        # PROGRAM -- and record it so it is not re-emitted inside a function.
        self._emitted_compools: set = set()
        self._hal_cs_emitted = False
        # Optimization pass: fold compile-time-constant expressions to literals
        # before code generation, so no constant math runs on the flight CPU.
        ConstantFolder().run(ast)
        for node in self._walk(ast):
            if isinstance(node, CompoolNode):
                self._generate_compool(node)
                self._emitted_compools.add(id(node))

        # EVENT variables are shared synchronization flags: a task may SIGNAL an
        # event that main (or another task) WAITs on. Lower them to module-level
        # AtomicBool statics so they are visible across every generated function.
        self._event_vars: set = set()
        for node in self._walk(ast):
            if (isinstance(node, DeclareNode) and node.type_spec is not None
                    and node.type_spec.data_type == DataType.EVENT):
                for ident in node.identifiers:
                    self._event_vars.add(ident.lower())
                    # core:: paths so events work on no_std too (riscv32imc
                    # has atomic load/store, which is all an EVENT needs).
                    self.emitter.emit_line(
                        f"static {ident.upper()}: "
                        "core::sync::atomic::AtomicBool = "
                        "core::sync::atomic::AtomicBool::new(false);")
        if self._event_vars:
            self.emitter.emit_line()

        # NONHAL external functions -> module-level `extern "C"` FFI bindings;
        # the linker resolves the actual (assembly/C) implementation. Calls to
        # them are wrapped in `unsafe` below.
        self._extern_fns: set = set()
        for node in self._walk(ast):
            if isinstance(node, ProcedureNode) and getattr(
                    node, "is_external", False):
                self._extern_fns.add(node.name.lower())
                self._generate_extern(node)
        # A TEMPLATE procedure that is actually CALLed is a prototype whose body
        # lives elsewhere; emit an `extern "C"` forward declaration so the call
        # resolves at link time (calls to it are wrapped in `unsafe`).
        _called = {c.name.lower() for c in self._walk(ast)
                   if isinstance(c, CallNode)}
        for node in self._walk(ast):
            if (isinstance(node, ProcedureNode)
                    and getattr(node, "is_template", False)
                    and node.name.lower() in _called
                    and node.name.lower() not in self._extern_fns):
                self._extern_fns.add(node.name.lower())
                self._generate_extern(node)
        if self._extern_fns:
            self.emitter.emit_line()

        # EXTERNAL variables -> module-level `extern "C"` statics; no storage is
        # allocated here, and reads are wrapped in `unsafe` (see identifiers).
        self._extern_vars = set()
        for node in self._walk(ast):
            if (isinstance(node, DeclareNode)
                    and (node.attributes or {}).get("external")):
                for ident in node.identifiers:
                    self._extern_vars.add(ident.lower())
                    rty = self._full_rust_type(node.type_spec)
                    # `static mut`: the definition is foreign and mutable by
                    # contract; all access goes through volatile ptr ops.
                    self.emitter.emit_line(
                        f'extern "C" {{ static mut {self._rust_ident(ident)}: {rty}; }}')
        if self._extern_vars:
            self.emitter.emit_line()

        # Generate code from AST
        self._generate_node(ast)
        
        return self.emitter.get_code()

    # Operator precedence for minimal, correct parenthesization.
    # Precedence of the *Rust* operators we emit, used only to decide when a
    # sub-expression needs parentheses. NOTE these follow Rust, where & ^ |
    # bind TIGHTER than comparisons -- so `(a=b) AND (c=d)` must keep its parens.
    _PRECEDENCE = {
        "**": 12,
        "*": 10, "/": 10, "MOD": 10,
        "+": 9, "-": 9,
        "AND": 7,                      # &
        "XOR": 6,                      # ^
        "OR": 5,                       # |
        "<": 4, ">": 4, "<=": 4, ">=": 4, "¬=": 4, "~=": 4, "=": 4,
    }

    # Self-contained, no external crates. f32 element type; host (std) stubs
    # for the real-time statements. The element type and the SCHEDULE/TERMINATE
    # behavior are the pieces that would change for a no_std / embedded target.
    # The prelude is assembled per target: a shared, core-only math
    # section (Vector/Matrix/intrinsics), plus EITHER the hosted std
    # stubs (SCHEDULE/TERMINATE threads, stdin READ) OR the no_std
    # FloatExt trait that routes f32 math through libm.
    _PRELUDE_MATH = """mod hal_rt {
    //! Minimal dependency-free runtime for generated HAL/S code (shared math core).
    #![allow(dead_code)]
    use core::ops::{Add, Sub, Mul, Div, Neg};

    #[derive(Clone, Copy, Debug)]
    pub struct Vector<const N: usize>(pub [f32; N]);

    impl<const N: usize> Vector<N> {
        pub const fn zero() -> Self { Vector([0.0; N]) }
    }

    impl<const N: usize> Add for Vector<N> {
        type Output = Vector<N>;
        fn add(self, rhs: Vector<N>) -> Vector<N> {
            let mut out = [0.0; N];
            for i in 0..N { out[i] = self.0[i] + rhs.0[i]; }
            Vector(out)
        }
    }

    impl<const N: usize> Sub for Vector<N> {
        type Output = Vector<N>;
        fn sub(self, rhs: Vector<N>) -> Vector<N> {
            let mut out = [0.0; N];
            for i in 0..N { out[i] = self.0[i] - rhs.0[i]; }
            Vector(out)
        }
    }

    impl<const N: usize> Mul<f32> for Vector<N> {
        type Output = Vector<N>;
        fn mul(self, s: f32) -> Vector<N> {
            let mut out = [0.0; N];
            for i in 0..N { out[i] = self.0[i] * s; }
            Vector(out)
        }
    }

    impl<const N: usize> Mul<Vector<N>> for f32 {
        type Output = Vector<N>;
        fn mul(self, v: Vector<N>) -> Vector<N> {
            let mut out = [0.0; N];
            for i in 0..N { out[i] = self * v.0[i]; }
            Vector(out)
        }
    }

    impl<const N: usize> Div<f32> for Vector<N> {
        type Output = Vector<N>;
        fn div(self, s: f32) -> Vector<N> {
            let mut out = [0.0; N];
            for i in 0..N { out[i] = self.0[i] / s; }
            Vector(out)
        }
    }

    impl<const N: usize> core::fmt::Display for Vector<N> {
        fn fmt(&self, f: &mut core::fmt::Formatter) -> core::fmt::Result {
            write!(f, "[")?;
            for i in 0..N {
                if i > 0 { write!(f, ", ")?; }
                write!(f, "{}", self.0[i])?;
            }
            write!(f, "]")
        }
    }

    impl<const N: usize> Neg for Vector<N> {
        type Output = Vector<N>;
        fn neg(self) -> Vector<N> {
            let mut out = [0.0; N];
            for i in 0..N { out[i] = -self.0[i]; }
            Vector(out)
        }
    }

    #[derive(Clone, Copy, Debug)]
    pub struct Matrix<const R: usize, const C: usize>(pub [[f32; C]; R]);

    impl<const R: usize, const C: usize> Matrix<R, C> {
        pub const fn zero() -> Self { Matrix([[0.0; C]; R]) }
        /// 1-indexed row i as a Vector<C>.
        pub fn row(&self, i: usize) -> Vector<C> { Vector(self.0[i - 1]) }
        /// 1-indexed column j as a Vector<R>.
        pub fn col(&self, j: usize) -> Vector<R> {
            let mut v = [0.0; R];
            let mut i = 0;
            while i < R { v[i] = self.0[i][j - 1]; i += 1; }
            Vector(v)
        }
    }

    impl<const R: usize, const C: usize> Mul<Vector<C>> for Matrix<R, C> {
        type Output = Vector<R>;
        fn mul(self, rhs: Vector<C>) -> Vector<R> {
            let mut out = [0.0; R];
            for r in 0..R {
                let mut acc = 0.0f32;
                for c in 0..C { acc += self.0[r][c] * rhs.0[c]; }
                out[r] = acc;
            }
            Vector(out)
        }
    }

    impl<const R: usize, const C: usize, const K: usize> Mul<Matrix<C, K>> for Matrix<R, C> {
        type Output = Matrix<R, K>;
        fn mul(self, rhs: Matrix<C, K>) -> Matrix<R, K> {
            let mut out = [[0.0f32; K]; R];
            for i in 0..R {
                for j in 0..K {
                    let mut acc = 0.0f32;
                    for k in 0..C { acc += self.0[i][k] * rhs.0[k][j]; }
                    out[i][j] = acc;
                }
            }
            Matrix(out)
        }
    }

    impl<const R: usize, const C: usize> core::fmt::Display for Matrix<R, C> {
        fn fmt(&self, f: &mut core::fmt::Formatter) -> core::fmt::Result {
            write!(f, "[")?;
            for r in 0..R {
                if r > 0 { write!(f, "; ")?; }
                for c in 0..C {
                    if c > 0 { write!(f, ", ")?; }
                    write!(f, "{}", self.0[r][c])?;
                }
            }
            write!(f, "]")
        }
    }

    impl<const R: usize, const C: usize> Neg for Matrix<R, C> {
        type Output = Matrix<R, C>;
        fn neg(self) -> Matrix<R, C> {
            let mut out = [[0.0; C]; R];
            for r in 0..R {
                for c in 0..C { out[r][c] = -self.0[r][c]; }
            }
            Matrix(out)
        }
    }

    pub fn abval<const N: usize>(v: Vector<N>) -> f32 {
        let mut acc = 0.0f32;
        for i in 0..N { acc += v.0[i] * v.0[i]; }
        acc.sqrt()
    }

    pub fn cross(a: Vector<3>, b: Vector<3>) -> Vector<3> {
        Vector([
            a.0[1] * b.0[2] - a.0[2] * b.0[1],
            a.0[2] * b.0[0] - a.0[0] * b.0[2],
            a.0[0] * b.0[1] - a.0[1] * b.0[0],
        ])
    }

    pub fn dot<const N: usize>(a: Vector<N>, b: Vector<N>) -> f32 {
        let mut acc = 0.0f32;
        for i in 0..N { acc += a.0[i] * b.0[i]; }
        acc
    }

    // Unit vector: v scaled to length 1 (a zero vector is returned unchanged).
    pub fn unit<const N: usize>(v: Vector<N>) -> Vector<N> {
        let m = abval(v);
        if m == 0.0 { return v; }
        let mut out = [0.0f32; N];
        for i in 0..N { out[i] = v.0[i] / m; }
        Vector(out)
    }

    pub fn transpose<const R: usize, const C: usize>(m: Matrix<R, C>) -> Matrix<C, R> {
        let mut out = [[0.0f32; R]; C];
        for i in 0..R { for j in 0..C { out[j][i] = m.0[i][j]; } }
        Matrix(out)
    }

    // Trace: sum of the leading-diagonal elements of a square matrix.
    pub fn trace<const N: usize>(m: Matrix<N, N>) -> f32 {
        let mut s = 0.0f32;
        for i in 0..N { s += m.0[i][i]; }
        s
    }

    // Square-matrix determinant via Gaussian elimination with partial pivoting.
    pub fn determ<const N: usize>(m: Matrix<N, N>) -> f32 {
        let mut a = m.0;
        let mut det = 1.0f32;
        for i in 0..N {
            let mut piv = i;
            for r in (i + 1)..N { if a[r][i].abs() > a[piv][i].abs() { piv = r; } }
            if a[piv][i] == 0.0 { return 0.0; }
            if piv != i { a.swap(i, piv); det = -det; }
            det *= a[i][i];
            for r in (i + 1)..N {
                let f = a[r][i] / a[i][i];
                for c in i..N { a[r][c] -= f * a[i][c]; }
            }
        }
        det
    }

    // Square-matrix inverse via Gauss-Jordan elimination. A singular matrix
    // yields a partial result (the caller is expected to gate on determ != 0).
    pub fn inverse<const N: usize>(m: Matrix<N, N>) -> Matrix<N, N> {
        let mut a = m.0;
        let mut inv = [[0.0f32; N]; N];
        for i in 0..N { inv[i][i] = 1.0; }
        for i in 0..N {
            let mut piv = i;
            for r in (i + 1)..N { if a[r][i].abs() > a[piv][i].abs() { piv = r; } }
            if a[piv][i] == 0.0 { return Matrix(inv); }
            if piv != i { a.swap(i, piv); inv.swap(i, piv); }
            let d = a[i][i];
            for c in 0..N { a[i][c] /= d; inv[i][c] /= d; }
            for r in 0..N {
                if r != i {
                    let f = a[r][i];
                    for c in 0..N {
                        a[r][c] -= f * a[i][c];
                        inv[r][c] -= f * inv[i][c];
                    }
                }
            }
        }
        Matrix(inv)
    }"""

    _PRELUDE_HOST_RT = """
    pub fn schedule(task: &str, priority: i32, body: fn()) -> std::thread::JoinHandle<()> {
        // OS-thread model: spawn the task body. priority is advisory --
        // std::thread has no portable priority control.
        eprintln!("[hal_rt] SCHEDULE {} PRIORITY({}) -> spawn (priority advisory)", task, priority);
        std::thread::spawn(body)
    }

    pub fn schedule_pending(task: &str, priority: i32) {
        // SCHEDULE referencing a task not defined in this unit: nothing to spawn.
        eprintln!("[hal_rt] SCHEDULE {} PRIORITY({}) (task undefined here; no-op)", task, priority);
    }

    pub fn terminate(task: &str) {
        eprintln!("[hal_rt] TERMINATE {} (host stub)", task);
    }

    use std::cell::RefCell;
    use std::collections::VecDeque;
    use std::io::BufRead;
    thread_local! {
        // Leftover tokens from previously read lines, shared across all READs
        // so multiple statements draw from one continuous input cursor.
        static INPUT_TOKENS: RefCell<VecDeque<f32>> = RefCell::new(VecDeque::new());
    }

    /// Read exactly `n` f32 values from stdin, pulling additional lines only
    /// as needed and caching any surplus tokens for later READs. Reads
    /// line-by-line (never waits for EOF once `n` values are available); at
    /// EOF any shortfall is filled with 0.0.
    pub fn read_f32(n: usize) -> Vec<f32> {
        INPUT_TOKENS.with(|cell| {
            let mut buf = cell.borrow_mut();
            let mut out: Vec<f32> = Vec::with_capacity(n);
            while out.len() < n {
                if let Some(v) = buf.pop_front() {
                    out.push(v);
                    continue;
                }
                let mut line = String::new();
                let stdin = std::io::stdin();
                let read = stdin.lock().read_line(&mut line).unwrap_or(0);
                if read == 0 {
                    while out.len() < n { out.push(0.0); }
                    break;
                }
                for t in line.split_whitespace() {
                    if let Ok(v) = t.parse::<f32>() { buf.push_back(v); }
                }
            }
            out
        })
    }"""

    _PRELUDE_FLOATEXT = """
    // ---- no_std float math --------------------------------------------
    // core has no sqrt/sin/powf/... on f32, so on embedded targets these
    // route through libm (pure-Rust, no_std). Hosted builds do not emit
    // this trait -- the inherent std methods apply -- so generated call
    // sites like `(x).sqrt()` are identical on every target.
    pub trait FloatExt {
        fn sqrt(self) -> f32; fn sin(self) -> f32; fn cos(self) -> f32;
        fn tan(self) -> f32; fn asin(self) -> f32; fn acos(self) -> f32;
        fn atan(self) -> f32; fn sinh(self) -> f32; fn cosh(self) -> f32;
        fn tanh(self) -> f32; fn exp(self) -> f32; fn ln(self) -> f32;
        fn abs(self) -> f32; fn floor(self) -> f32; fn ceil(self) -> f32;
        fn round(self) -> f32; fn trunc(self) -> f32;
        fn powi(self, n: i32) -> f32; fn powf(self, e: f32) -> f32;
    }
    impl FloatExt for f32 {
        #[inline] fn sqrt(self) -> f32 { libm::sqrtf(self) }
        #[inline] fn sin(self) -> f32 { libm::sinf(self) }
        #[inline] fn cos(self) -> f32 { libm::cosf(self) }
        #[inline] fn tan(self) -> f32 { libm::tanf(self) }
        #[inline] fn asin(self) -> f32 { libm::asinf(self) }
        #[inline] fn acos(self) -> f32 { libm::acosf(self) }
        #[inline] fn atan(self) -> f32 { libm::atanf(self) }
        #[inline] fn sinh(self) -> f32 { libm::sinhf(self) }
        #[inline] fn cosh(self) -> f32 { libm::coshf(self) }
        #[inline] fn tanh(self) -> f32 { libm::tanhf(self) }
        #[inline] fn exp(self) -> f32 { libm::expf(self) }
        #[inline] fn ln(self) -> f32 { libm::logf(self) }
        #[inline] fn abs(self) -> f32 { libm::fabsf(self) }
        #[inline] fn floor(self) -> f32 { libm::floorf(self) }
        #[inline] fn ceil(self) -> f32 { libm::ceilf(self) }
        #[inline] fn round(self) -> f32 { libm::roundf(self) }
        #[inline] fn trunc(self) -> f32 { libm::truncf(self) }
        #[inline] fn powi(self, n: i32) -> f32 { libm::powf(self, n as f32) }
        #[inline] fn powf(self, e: f32) -> f32 { libm::powf(self, e) }
    }"""

    _PRELUDE_TAIL = "}\n\nuse hal_rt::*;"

    def _emit_prelude(self) -> None:
        """Emit the runtime support module for the current target."""
        extra = (self._PRELUDE_FLOATEXT if self.spec.embedded
                 else self._PRELUDE_HOST_RT)
        text = self._PRELUDE_MATH + "\n" + extra + "\n" + self._PRELUDE_TAIL
        for line in text.split("\n"):
            self.emitter.emit_line(line)
        self.emitter.emit_line()

    # Function-call names that lower to inline Rust (not the hal_rt prelude).
    _INTRINSICS_NO_RUNTIME = {"LENGTH", "INTEGER", "SCALAR"}

    # Intrinsics that lower to hal_rt prelude functions (so their use requires
    # the prelude). User-defined functions and inline casts are NOT here.
    _PRELUDE_INTRINSICS = {"ABVAL", "CROSS", "DOT", "UNIT",
                           "DETERM", "INVERSE", "TRANSPOSE",
                           "TRACE"}

    # HAL/S scalar math built-ins that lower to Rust f32 methods (no prelude
    # needed -- they're inherent on f32). Single-argument: SQRT(x) -> (x).sqrt().
    _MATH_METHODS = {
        "SQRT": "sqrt", "SIN": "sin", "COS": "cos", "TAN": "tan",
        "ARCSIN": "asin", "ARCCOS": "acos", "ARCTAN": "atan",
        "SINH": "sinh", "COSH": "cosh", "TANH": "tanh",
        "EXP": "exp", "LOG": "ln", "LN": "ln", "ABS": "abs",
        "FLOOR": "floor", "CEILING": "ceil", "ROUND": "round",
        "TRUNCATE": "trunc",
    }


    # Cyclic executive for SCHEDULE/TASK on bare metal
    # (docs/CYCLIC_EXECUTIVE_DESIGN.md): a static table of task slots run by
    # a cooperative superloop -- highest-priority due slot runs to
    # completion; declaration order breaks ties; periods accumulate from the
    # due time (no drift); overruns print a debug line. Single execution
    # context: Cells are sound because ISRs never touch slots.
    _HAL_EXEC = """mod hal_exec {
    //! Cooperative cyclic executive for HAL/S SCHEDULE/TASK (no_std).
    #![allow(dead_code)]
    use core::cell::Cell;

    #[derive(Clone, Copy, PartialEq)]
    pub enum State { Idle, Armed }

    pub struct Slot {
        pub name: &'static str,
        pub body: fn(),
        pub priority: Cell<i32>,
        pub period_us: Cell<u64>,   // 0 = one-shot
        pub next_due_us: Cell<u64>,
        pub state: Cell<State>,
    }
    // Single-context executive: slots are only ever touched from main.
    unsafe impl Sync for Slot {}

    impl Slot {
        pub const fn new(name: &'static str, body: fn()) -> Self {
            Slot { name, body, priority: Cell::new(0), period_us: Cell::new(0),
                   next_due_us: Cell::new(0), state: Cell::new(State::Idle) }
        }
    }

    pub fn arm(s: &Slot, now_us: u64, delay_us: u64, period_us: u64, priority: i32) {
        s.priority.set(priority);
        s.period_us.set(period_us);
        s.next_due_us.set(now_us + delay_us);
        s.state.set(State::Armed);
    }

    pub fn idle(s: &Slot) { s.state.set(State::Idle); }

    pub fn run(table: &[&Slot], now_us: fn() -> u64,
               overrun: fn(&'static str)) -> ! {
        loop {
            let now = now_us();
            let mut best: Option<&Slot> = None;
            for s in table {
                if s.state.get() == State::Armed && s.next_due_us.get() <= now {
                    // strict > keeps the earliest table index on ties
                    if best.map_or(true, |b| s.priority.get() > b.priority.get()) {
                        best = Some(s);
                    }
                }
            }
            if let Some(s) = best {
                (s.body)();
                if s.state.get() == State::Armed {   // body may self-TERMINATE
                    let p = s.period_us.get();
                    if p == 0 {
                        s.state.set(State::Idle);
                    } else {
                        let due = s.next_due_us.get() + p;  // no drift
                        if now_us() > due { overrun(s.name); }
                        s.next_due_us.set(due);
                    }
                }
            }
        }
    }
}
"""

    def _uses_exec(self, node: ASTNode) -> bool:
        """True if the tree needs the cyclic executive on an embedded target."""
        for n in self._walk(node):
            if isinstance(n, (ScheduleNode, TaskNode, TerminateNode, CancelNode)):
                return True
        return False

    def _emit_hal_exec(self) -> None:
        for line in self._HAL_EXEC.split("\n"):
            self.emitter.emit_line(line)
        # Target time base + overrun sink for hal_exec::run.
        if self.spec.entry == "esp-hal":
            self.emitter.emit_line("fn __exec_now_us() -> u64 {")
            self.emitter.emit_line(
                "    esp_hal::time::Instant::now()"
                ".duration_since_epoch().as_micros()")
            self.emitter.emit_line("}")
            self.emitter.emit_line(
                'fn __exec_overrun(n: &\'static str) '
                '{ println!("[hal_exec] OVERRUN {}", n); }')
        else:
            self.emitter.emit_line(
                "// Cortex-M has no portable clock: the shim provides it "
                "(see src/shim.rs).")
            self.emitter.emit_line(
                'extern "C" { fn hal_exec_now_us() -> u64; }')
            self.emitter.emit_line(
                "fn __exec_now_us() -> u64 { unsafe { hal_exec_now_us() } }")
            self.emitter.emit_line(
                'fn __exec_overrun(n: &\'static str) '
                '{ hprintln!("[hal_exec] OVERRUN {}", n); }')
        self.emitter.emit_line()

    def _uses_float_math(self, node: ASTNode) -> bool:
        """True if the tree calls a scalar math built-in or uses ** — the
        f32 methods behind them need FloatExt/libm on no_std targets."""
        for n in self._walk(node):
            if (isinstance(n, FunctionCallNode)
                    and n.name.upper() in self._MATH_METHODS):
                return True
            if isinstance(n, BinaryOpNode) and n.operator == "**":
                return True
        return False

    def _uses_runtime(self, node: ASTNode) -> bool:
        """True if any node in the tree needs the hal_rt prelude."""
        for n in self._walk(node):
            if isinstance(n, FunctionCallNode):
                if n.name.upper() in self._PRELUDE_INTRINSICS:
                    return True
            elif isinstance(n, ScheduleNode):
                # Priority schedules use the schedule() helper; timed/cyclic
                # ones are generated inline with std and need no prelude.
                if n.delay is None:
                    return True
            elif isinstance(n, TerminateNode):
                if n.task_name:   # named TERMINATE uses the terminate() helper
                    return True
            elif isinstance(n, (VectorLiteralNode, MatrixLiteralNode,
                                MatrixSubscriptNode)):
                return True
            elif isinstance(n, ReadNode):
                return True  # READ uses the hal_rt::read_f32 helper
            if (
                isinstance(n, DeclareNode)
                and n.type_spec is not None
                and n.type_spec.data_type in (DataType.VECTOR, DataType.MATRIX)
            ):
                return True
        return False

    def _walk(self, node: ASTNode):
        """Yield node and all descendant AST nodes.

        The attribute lists must cover every AST-node-valued field on every
        node type in ast_nodes.py; any omission silently hides that subtree
        from every pre-pass (fault analysis, prelude gating, bit-width /
        var-info collection), producing miscompilation, not a loud failure.
        """
        yield node
        list_attrs = (
            "statements", "declarations", "procedures", "tasks", "units",
            "items", "arguments", "values", "args", "fields", "targets",
            "indices", "assign_args",
            "branches",        # CaseNode
            "initial_values",  # DeclareNode array initializers
            "bits",            # SignalNode bit-list positions
        )
        for attr in list_attrs:
            for child in getattr(node, attr, None) or []:
                if hasattr(child, "node_type"):
                    yield from self._walk(child)
        single_attrs = (
            "value", "target", "left", "right",
            "condition", "then_block", "else_block",
            "priority", "initial_value",
            "operand",     # UnaryOpNode, SubscriptNode, FieldAccessNode
            "index",       # SubscriptNode (may be int; guarded below)
            "body",        # OnErrorNode handler
            "selector",    # CaseNode
            "start", "end", "step",   # ForNode bounds
            "until",                  # ForNode UNTIL guard (WhileNode.until is bool)
            "delay", "period",        # ScheduleNode timing
            "lo", "hi",               # RangeNode bounds
            "statement",              # LabelNode
            "record",                 # WriteNode / ReadNode direct-access
        )
        for attr in single_attrs:
            child = getattr(node, attr, None)
            if child is not None and hasattr(child, "node_type"):
                yield from self._walk(child)
    
    @property
    def _embedded(self) -> bool:
        """True for any bare-metal (no_std) target profile."""
        return self.spec.embedded

    def _emit_embedded_guard(self, ast: ASTNode) -> None:
        """For a no_std target, emit compile_error! for any feature whose
        lowering still relies on std (so the build fails with a clear message
        instead of a cryptic linker error). These are the follow-up steps of
        the embedded port."""
        reasons = []
        if any(isinstance(n, ReadNode) for n in self._walk(ast)):
            reasons.append(
                "READ needs a console (std stdin) -- no input channel on "
                "this target")
        if any(isinstance(n, DeclareNode)
               and n.type_spec is not None
               and n.type_spec.data_type == DataType.CHARACTER
               for n in self._walk(ast)):
            reasons.append(
                "CHARACTER variables need heap allocation (String) -- not "
                "yet lowered for no_std")
        if any(isinstance(n, BinaryOpNode) and n.operator == "||"
               for n in self._walk(ast)):
            reasons.append(
                "character concatenation (CAT / ||) allocates a String -- "
                "not yet lowered for no_std")
        if any(isinstance(n, WaitNode) for n in self._walk(ast)):
            reasons.append(
                "WAIT stalls only the issuing process (HAL/S spec 8.6); a "
                "cooperative executive cannot honor that yet -- design v2")
        for r in reasons:
            self.emitter.emit_line(f'compile_error!("resnik: {r}");')
        if reasons:
            self.emitter.emit_line()

    def _emit_file_header(self) -> None:
        """Emit Rust file header with metadata."""
        self.emitter.emit_comment("Generated by Resnik HAL/S Compiler")
        self.emitter.emit_comment("https://github.com/bitWarrior/resnik")
        if self._embedded:
            self.emitter.emit_comment(f"Target profile: {self.target} (no_std)")
        self.emitter.emit_line()
        if self._embedded:
            # Bare-metal crates opt out of std and the normal main entry.
            self.emitter.emit_line("#![no_std]")
            self.emitter.emit_line("#![no_main]")
        self.emitter.emit_line("#![allow(unused_variables)]")
        self.emitter.emit_line("#![allow(unused_mut)]")
        self.emitter.emit_line("#![allow(unused_assignments)]")
        self.emitter.emit_line("#![allow(unused_parens)]")
        self.emitter.emit_line("#![allow(unused_imports)]")
        self.emitter.emit_line("#![allow(improper_ctypes)]")
        self.emitter.emit_line("#![allow(unreachable_code)]")
        self.emitter.emit_line("#![allow(dead_code)]")
        self.emitter.emit_line()
        if self.spec.entry == "cortex-m-rt":
            # Reset vector / entry point, a halting panic handler, and a simple
            # semihosting output sink for WRITE (swap for RTT or UART later).
            self.emitter.emit_line("use cortex_m_rt::entry;")
        elif self.spec.entry == "esp-hal":
            self.emitter.emit_line("use esp_hal::main;")
        if self.spec.panic == "panic-halt":
            self.emitter.emit_line("use panic_halt as _;")
        elif self.spec.panic == "esp-backtrace":
            self.emitter.emit_line("use esp_backtrace as _;")
        if self.spec.write_sink == "hprintln":
            self.emitter.emit_line("use cortex_m_semihosting::hprintln;")
        elif self.spec.write_sink == "esp-println":
            self.emitter.emit_line("use esp_println::println;")
        if self.spec.entry or self.spec.panic != "std" or (
                self.spec.write_sink != "println"):
            self.emitter.emit_line()
        if self.spec.entry == "esp-hal":
            # ESP-IDF 2nd-stage bootloader requires an app descriptor in the
            # image; without it a flashed binary is rejected at boot.
            self.emitter.emit_line("esp_bootloader_esp_idf::esp_app_desc!();")
            self.emitter.emit_line()
    
    def _generate_node(self, node: ASTNode) -> None:
        """
        Generate code for an AST node.
        
        Args:
            node: AST node to generate code for
        """
        if isinstance(node, CompilationNode):
            self._generate_compilation(node)
        elif isinstance(node, CompoolNode):
            if id(node) not in getattr(self, "_emitted_compools", set()):
                self._generate_compool(node)
        elif isinstance(node, ProgramNode):
            self._generate_program(node)
        elif isinstance(node, TaskNode):
            self._generate_task(node)
        elif isinstance(node, DeclareNode):
            self._generate_declare(node)
        elif isinstance(node, ProcedureNode):
            self._generate_procedure(node)
        elif isinstance(node, NameAssignNode):
            self._generate_name_assign(node)
        elif isinstance(node, AssignmentNode):
            self._generate_assignment(node)
        elif isinstance(node, WriteNode):
            self._generate_write(node)
        elif isinstance(node, ReadNode):
            self._generate_read(node)
        elif isinstance(node, IfNode):
            self._generate_if(node)
        elif isinstance(node, DoBlockNode):
            self._generate_do_block(node)
        elif isinstance(node, WhileNode):
            self._generate_while(node)
        elif isinstance(node, CaseNode):
            self._generate_case(node)
        elif isinstance(node, OnErrorNode):
            self._generate_on_error(node)
        elif isinstance(node, ResumeNode):
            self.emitter.emit_comment(
                "RESUME: control returns to the statement after the fault"
            )
        elif isinstance(node, MultiAssignNode):
            self._generate_multi_assign(node)
        elif isinstance(node, ReturnNode):
            if node.value is not None:
                inner = self._generate_expression(node.value)
                if self._cur_unit_is_result:
                    self.emitter.emit_line(f"return Ok({inner});")
                else:
                    self.emitter.emit_line(f"return {inner};")
            else:
                if self._cur_unit_is_result:
                    self.emitter.emit_line("return Ok(());")
                else:
                    self.emitter.emit_line("return;")
        elif isinstance(node, ForNode):
            self._generate_for(node)
        elif isinstance(node, ExitNode):
            # EXIT breaks the innermost loop; EXIT <label> the named loop.
            if node.label:
                self.emitter.emit_line(
                    f"break '{self._rust_ident(node.label)};")
            else:
                self.emitter.emit_line("break;")
        elif isinstance(node, GotoNode):
            self._generate_goto(node)
        elif isinstance(node, LabelNode):
            # A label wrapping a loop becomes a Rust loop label so EXIT <label>
            # can target it. Otherwise it's an ordinary statement label.
            if isinstance(node.statement, ForNode):
                self._generate_for(node.statement, label=node.name)
            elif isinstance(node.statement, WhileNode):
                self._generate_while(node.statement, label=node.name)
            elif node.statement is not None:
                self._generate_node(node.statement)
        elif isinstance(node, CallNode):
            self._generate_call(node)
        elif isinstance(node, ScheduleNode):
            self._generate_schedule(node)
        elif isinstance(node, WaitNode):
            self._generate_wait(node)
        elif isinstance(node, SignalNode):
            self._generate_signal(node)
        elif isinstance(node, CancelNode):
            self._generate_cancel(node)
        elif isinstance(node, TerminateNode):
            self._generate_terminate(node)
        else:
            # Exhaustiveness backstop: an AST statement type that reaches here
            # without a handler is a codegen bug. Silently emitting a comment
            # would let whole HAL/S statements vanish -- the exact failure
            # mode a safety-critical pipeline cannot tolerate.
            raise BackendError(
                f"codegen: no handler for statement node "
                f"{type(node).__name__} (add a branch to _generate_node)",
                line=getattr(node, "line", 0),
                column=getattr(node, "column", 0),
            )
    
    def _generate_compilation(self, node: CompilationNode) -> None:
        """Generate each top-level unit. COMPOOLs (globals) are emitted first
        so the shared statics are defined before the code that uses them."""
        compools = [u for u in node.units if isinstance(u, CompoolNode)]
        others = [u for u in node.units if not isinstance(u, CompoolNode)]
        for unit in compools + others:
            self._generate_node(unit)

    def _generate_compool(self, node: CompoolNode) -> None:
        """Generate COMPOOL shared data as module-level globals.

        CONSTANT(...) -> `const NAME: T = v;`. Mutable shared data ->
        `static NAME: Mutex<T> = Mutex::new(v);` so it is safe to touch from
        multiple scheduled task threads. References are resolved in
        _generate_identifier / _generate_assignment.
        """
        if self.spec.embedded:
            self._ensure_hal_cs()
        self.emitter.emit_comment(
            f"HAL/S Compool (shared global memory): {node.name}"
        )
        mutex = "hal_cs::CsMutex" if self.spec.embedded else "std::sync::Mutex"
        for decl in node.declarations:
            ts = decl.type_spec
            # ARRAY-typed shared data -> Mutex<[T; n]> (nested for multi-dim).
            if (ts is not None and ts.array_dims is not None
                    and ts.element_type is not None):
                ets = ts.element_type
                if ets.data_type == DataType.STRUCTURE:
                    et = self._struct_name(ets.type_name or "")
                    edef = f"{et}::new()"
                else:
                    et = self.type_mapper.map_type(ets)
                    edef = self.type_mapper.get_default_value(ets)
                rust_type = et
                for d in reversed(ts.array_dims):
                    rust_type = f"[{rust_type}; {d}]"
                init_vals = (decl.attributes or {}).get("initial")
                if init_vals is not None and len(ts.array_dims) == 1:
                    init = "[" + ", ".join(
                        self._generate_expression(v) for v in init_vals) + "]"
                else:
                    init = edef
                    for d in reversed(ts.array_dims):
                        init = f"[{init}; {d}]"
                for ident in decl.identifiers:
                    name = ident.upper()
                    if decl.is_constant:
                        self.emitter.emit_line(
                            f"const {name}: {rust_type} = {init};")
                    else:
                        self.emitter.emit_line(
                            f"static {name}: {mutex}<{rust_type}> = "
                            f"{mutex}::new({init});")
                continue
            rust_type = self.type_mapper.map_type(decl.type_spec)
            if decl.initial_value is not None:
                init = self._generate_expression(decl.initial_value)
            elif decl.initial_values:
                init = self._generate_initial_values(decl)
            else:
                init = self.type_mapper.get_default_value(decl.type_spec)
            for ident in decl.identifiers:
                name = ident.upper()
                if decl.is_constant:
                    self.emitter.emit_line(
                        f"const {name}: {rust_type} = {init};"
                    )
                else:
                    self.emitter.emit_line(
                        f"static {name}: {mutex}<{rust_type}> = "
                        f"{mutex}::new({init});"
                    )
        self.emitter.emit_line()

    # COMPOOL on bare metal: a critical-section-backed cell with a
    # std::sync::Mutex-SHAPED api (lock() -> Result<guard, Infallible>,
    # Deref/DerefMut, release on Drop), so every generated access site --
    # `NAME.lock().unwrap()`, the per-statement guard bindings -- is
    # byte-identical across targets. Nested lock() is safe here (RestoreState
    # nests), unlike the host Mutex, so the existing single-guard-per-
    # statement discipline is sufficient on both.
    _HAL_CS = """mod hal_cs {
    //! COMPOOL shared data for no_std targets (critical-section backed).
    #![allow(dead_code)]
    pub struct CsMutex<T>(core::cell::UnsafeCell<T>);
    unsafe impl<T: Send> Sync for CsMutex<T> {}
    impl<T> CsMutex<T> {
        pub const fn new(v: T) -> Self { CsMutex(core::cell::UnsafeCell::new(v)) }
        pub fn lock(&self) -> Result<CsGuard<'_, T>, core::convert::Infallible> {
            let rs = unsafe { critical_section::acquire() };
            Ok(CsGuard { m: self, rs })
        }
    }
    pub struct CsGuard<'a, T> {
        m: &'a CsMutex<T>,
        rs: critical_section::RestoreState,
    }
    impl<T> core::ops::Deref for CsGuard<'_, T> {
        type Target = T;
        fn deref(&self) -> &T { unsafe { &*self.m.0.get() } }
    }
    impl<T> core::ops::DerefMut for CsGuard<'_, T> {
        fn deref_mut(&mut self) -> &mut T { unsafe { &mut *self.m.0.get() } }
    }
    impl<T> Drop for CsGuard<'_, T> {
        fn drop(&mut self) { unsafe { critical_section::release(self.rs) } }
    }
}
"""

    def _ensure_hal_cs(self) -> None:
        """Emit the hal_cs module once per file (embedded compools)."""
        if getattr(self, "_hal_cs_emitted", False):
            return
        self._hal_cs_emitted = True
        for line in self._HAL_CS.split("\n"):
            self.emitter.emit_line(line)


    def _generate_program(self, node: ProgramNode) -> None:
        """
        Generate Rust code for PROGRAM block.
        
        HAL/S:
            PROGRAM MyProgram;
                <declarations>
                <statements>
            CLOSE MyProgram;
        
        Rust:
            fn main() {
                <declarations>
                <statements>
            }
        
        Args:
            node: ProgramNode to generate
        """
        # Emit program comment
        self.emitter.emit_comment(f"HAL/S Program: {node.name}")
        self._task_names = {t.name.lower() for t in node.tasks}

        # A fault code carried out of a faulting procedure via Result<_, HalFault>.
        if self._uses_faults:
            self.emitter.emit_comment(
                "Fault code propagated out of faulting procedures (ON ERROR)")
            self.emitter.emit_line("#[derive(Clone, Copy)]")
            self.emitter.emit_line("struct HalFault { code: i32 }")
            self.emitter.emit_line()

        # Structure templates -> module-level Rust structs.
        structures = [s for s in node.statements
                      if isinstance(s, StructureDefNode)]
        body = [s for s in node.statements
                if not isinstance(s, StructureDefNode)]
        for st in structures:
            self._generate_structure_def(st)
            self.emitter.emit_line()

        # Nested TASK blocks -> standalone functions (host: thread bodies;
        # embedded: cyclic-executive slot bodies).
        for task in node.tasks:
            self._generate_task_function(task)
            self.emitter.emit_line()

        # Embedded: one static Slot per TASK plus the executive table
        # (declaration order == priority tiebreak order).
        self._exec_tasks = [t.name for t in node.tasks]
        if self.spec.embedded and node.tasks:
            for t in node.tasks:
                self.emitter.emit_line(
                    f'static __SLOT_{t.name.upper()}: hal_exec::Slot = '
                    f'hal_exec::Slot::new("{t.name.upper()}", '
                    f'{self._rust_ident(t.name)});')
            refs = ", ".join(f"&__SLOT_{t.name.upper()}" for t in node.tasks)
            self.emitter.emit_line(
                f"static __EXEC_TABLE: [&hal_exec::Slot; {len(node.tasks)}] "
                f"= [{refs}];")
            self.emitter.emit_line()

        # Generate procedures (before main)
        for proc in node.procedures:
            self._generate_node(proc)
            self.emitter.emit_line()

        # Generate main function. On a bare-metal target this is a diverging
        # `fn main() -> !` with the profile's entry attribute.
        if self.spec.embedded:
            if self.spec.entry == "esp-hal":
                self.emitter.emit_line("#[main]")
            else:
                self.emitter.emit_line("#[entry]")
            self.emitter.emit_line("fn main() -> ! {")
            self.emitter.indent()
            if self.spec.entry == "esp-hal":
                # Clocks / watchdog; USB-serial WRITE works after this.
                self.emitter.emit_line(
                    "let _peripherals = "
                    "esp_hal::init(esp_hal::Config::default());"
                )
        else:
            self.emitter.emit_function_header("main", [], None, is_public=False)
        self.in_main = True
        self._cur_unit = "@main"
        self._cur_unit_is_result = False
        self._generate_unit_body(node.declarations, body)
        self.in_main = False
        if self.spec.embedded:
            if node.tasks:
                # Program body done: hand the core to the cyclic executive.
                self.emitter.emit_line(
                    "hal_exec::run(&__EXEC_TABLE, __exec_now_us, "
                    "__exec_overrun)")
            else:
                # A no_std entry never returns; park the core when done.
                self.emitter.emit_line("loop {}")
        self.emitter.emit_block_end()

    def _generate_task_function(self, task: TaskNode) -> None:
        """Emit a nested TASK as a free function (the body run by a thread)."""
        self.emitter.emit_comment(
            f"HAL/S Task: {task.name} (runs on its own OS thread when scheduled)"
        )
        # Tasks nested inside this task become functions too.
        for sub in task.tasks:
            self._generate_task_function(sub)
            self.emitter.emit_line()
        self.emitter.emit_function_header(
            self._rust_ident(task.name), [], None, is_public=False
        )
        prev_task = getattr(self, "_cur_task_name", None)
        self._cur_task_name = task.name
        self._generate_unit_body(task.declarations, task.statements)
        self._cur_task_name = prev_task
        self.emitter.emit_block_end()

    def _contains_schedule(self, statements: list) -> bool:
        """True if any priority-style SCHEDULE appears (these use __tasks).
        Timed/cyclic schedules manage their own handles, so they don't count."""
        for stmt in statements:
            for n in self._walk(stmt):
                if isinstance(n, ScheduleNode) and n.delay is None:
                    return True
        return False

    def _generate_unit_body(self, declarations: list, statements: list) -> None:
        """Emit a function body: declarations, statements, and (if any SCHEDULE
        is present) a join handle vector plus a join loop before returning."""
        # Embedded targets run tasks on the cyclic executive, not threads:
        # no join-handle bookkeeping there.
        threaded = (not self.spec.embedded
                    and self._contains_schedule(statements))
        if threaded:
            self.emitter.emit_line(
                "let mut __tasks: Vec<std::thread::JoinHandle<()>> = Vec::new();"
            )

        for decl in declarations:
            self._generate_node(decl)
        if declarations:
            self.emitter.emit_line()

        # A body containing statement labels / GO TO lowers to a dispatch loop
        # (state machine): Rust has no goto, but `continue 'dispatch` from inside
        # nested loops reproduces a branch out to any label.
        if self._needs_goto_dispatch(statements):
            # Hoist declarations to function scope so every segment can see
            # them (HAL/S requires declarations first, so they lead the body).
            hoisted = [s for s in statements if isinstance(s, DeclareNode)]
            rest = [s for s in statements if not isinstance(s, DeclareNode)]
            for d in hoisted:
                self._generate_node(d)
            if hoisted:
                self.emitter.emit_line()
            self._generate_dispatch(rest)
        else:
            for stmt in statements:
                self._generate_node(stmt)

        if threaded:
            self.emitter.emit_line()
            self.emitter.emit_comment("join scheduled tasks before exit")
            self.emitter.emit_line("for __h in __tasks { let _ = __h.join(); }")

    def _needs_goto_dispatch(self, statements: list) -> bool:
        """A body needs the dispatch state machine only to emulate GO TO
        (including a GO TO nested inside loops/ifs). A plain label -- e.g. a
        labeled loop used as an EXIT <label> target -- does not, and lowers to
        an ordinary (labeled) Rust loop instead."""
        return any(
            isinstance(n, GotoNode)
            for s in statements
            for n in self._walk(s)
        )

    def _split_segments(self, statements: list):
        """Split a statement list into (label, [stmts]) segments at each label.
        Segment 0 (label None) holds everything before the first label."""
        segments = [(None, [])]
        for stmt in statements:
            if isinstance(stmt, LabelNode):
                inner = stmt.statement
                seg = [inner] if inner is not None else []
                # Unwrap a directly-nested label (A: B: stmt).
                while seg and isinstance(seg[0], LabelNode):
                    ln = seg[0]
                    segments.append((stmt.name, []))
                    stmt = ln
                    seg = [ln.statement] if ln.statement is not None else []
                segments.append((stmt.name, seg))
            else:
                segments[-1][1].append(stmt)
        return segments

    def _generate_dispatch(self, statements: list) -> None:
        """Emit a labelled body as `'dispatch: loop { match __pc { ... } }`."""
        segments = self._split_segments(statements)
        # Map each label to its segment (state) index.
        self._label_states = {
            name.upper(): i
            for i, (name, _) in enumerate(segments) if name is not None
        }
        last = len(segments) - 1
        self.emitter.emit_line("let mut __pc: usize = 0;")
        self.emitter.emit_line("'dispatch: loop {")
        self.emitter.indent()
        self.emitter.emit_line("match __pc {")
        self.emitter.indent()
        for i, (name, stmts) in enumerate(segments):
            label_note = f"  // {name}" if name else ""
            self.emitter.emit_line(f"{i} => {{{label_note}")
            self.emitter.indent()
            for stmt in stmts:
                self._generate_node(stmt)
            # Fall-through: skip if the segment ends in an unconditional GO TO
            # (its `continue 'dispatch` already transferred control).
            ends_in_goto = bool(stmts) and isinstance(stmts[-1], GotoNode)
            if not ends_in_goto:
                if i < last:
                    self.emitter.emit_line(f"__pc = {i + 1};")
                else:
                    self.emitter.emit_line("break 'dispatch;")
            self.emitter.dedent()
            self.emitter.emit_line("}")
        self.emitter.emit_line("_ => break 'dispatch,")
        self.emitter.dedent()
        self.emitter.emit_line("}")
        self.emitter.dedent()
        self.emitter.emit_line("}")
        self._label_states = {}

    def _generate_goto(self, node: GotoNode) -> None:
        """GO TO <label> -> set the dispatch state and branch out via the
        labelled loop, which unwinds any enclosing loops on the way."""
        state = getattr(self, "_label_states", {}).get(
            (node.label or "").upper())
        if state is None:
            self.emitter.emit_comment(
                f"GO TO {node.label} (unresolved label -- no matching target)")
            return
        self.emitter.emit_line(f"{{ __pc = {state}; continue 'dispatch; }}")

    def _generate_task(self, node: TaskNode) -> None:
        """
        Generate Rust for a TASK block.

        NOTE: This is a *sequential stand-in*, not real concurrency. A HAL/S
        TASK is a schedulable process; here we run its body once as `fn main`
        so a single-task file compiles and executes. Real concurrent dispatch
        (multiple tasks, SCHEDULE/priority, preemption) is a separate runtime
        model and is not implemented — SCHEDULE/TERMINATE remain host stubs.

        Args:
            node: TaskNode to generate
        """
        self.emitter.emit_comment(f"HAL/S Task: {node.name}")
        self.emitter.emit_comment(
            "sequential stand-in (not concurrent); body runs once as main"
        )
        self._task_names = {t.name.lower() for t in node.tasks}

        for task in node.tasks:
            self._generate_task_function(task)
            self.emitter.emit_line()

        for proc in node.procedures:
            self._generate_node(proc)
            self.emitter.emit_line()

        self.emitter.emit_function_header("main", [], None, is_public=False)
        self.in_main = True
        self._generate_unit_body(node.declarations, node.statements)
        self.in_main = False
        self.emitter.emit_block_end()
    
    def _generate_declare(self, node: DeclareNode) -> None:
        """
        Generate Rust code for DECLARE statement.
        
        HAL/S:
            DECLARE X INTEGER;
            DECLARE Y SCALAR INITIAL(3.14);
        
        Rust:
            let mut x: i32;
            let mut y: f32 = 3.14;
        
        Args:
            node: DeclareNode to generate
        """
        rust_type = self.type_mapper.map_type(node.type_spec)

        # EXTERNAL variables are foreign `extern "C"` statics (emitted in the
        # generate() pre-pass); no per-scope storage is allocated here.
        if (node.attributes or {}).get("external"):
            return

        # EVENT variables are module-level AtomicBool statics (emitted in the
        # generate() pre-pass); no per-scope local is declared.
        if node.type_spec is not None \
                and node.type_spec.data_type == DataType.EVENT:
            return

        # NAME (pointer) declaration -> a raw pointer (`*mut T`). HAL/S NAMEs are
        # addresses that can be re-bound; a raw pointer models that faithfully
        # and avoids the borrow checker's aliasing restrictions. It starts null
        # and is bound with ASSIGN X TO Y.
        if node.type_spec is not None and getattr(
                node.type_spec, "is_name", False):
            for ident in node.identifiers:
                self._name_vars[ident.lower()] = rust_type
                self.emitter.emit_line(
                    f"let mut {self._rust_ident(ident)}: *mut {rust_type} "
                    f"= std::ptr::null_mut();")
            return

        # EQUATE overlay: VIEW aliases TARGET's storage. No separate binding;
        # accesses are rewritten onto the target with bit-preserving casts.
        equate = node.attributes.get("equate") if node.attributes else None
        if equate is not None:
            view = node.identifiers[0].lower()
            target = equate.lower()
            target_ts = self._var_info.get(target)
            target_type = (self.type_mapper.map_type(target_ts)
                           if target_ts is not None else "u32")
            self._equates[view] = (target, rust_type, target_type)
            self.emitter.emit_comment(
                f"{node.identifiers[0]} EQUATE {equate} "
                f"(overlay: {rust_type} view of {target_type})"
            )
            return
        # General ARRAY(d1, d2, ...) OF <element> -> nested Rust array.
        if (node.type_spec is not None
                and node.type_spec.array_dims is not None
                and node.type_spec.element_type is not None):
            ets = node.type_spec.element_type
            if ets.data_type == DataType.STRUCTURE:
                et = self._elem_struct_name(
                    ets.type_name, getattr(node.type_spec, "layout", "aligned"))
                ei = f"{et}::new()"
            else:
                et = self.type_mapper.map_type(ets)
                ei = self.type_mapper.get_default_value(ets)
            rust_t = et
            for d in reversed(node.type_spec.array_dims):
                rust_t = f"[{rust_t}; {d}]"
            # INITIAL(v0, v1, ...) -> literal array (1-D, row-major).
            init_vals = (node.attributes or {}).get("initial")
            dims = node.type_spec.array_dims
            if init_vals is not None and len(dims) == 1:
                elems = ", ".join(
                    self._generate_expression(v) for v in init_vals)
                rust_i = f"[{elems}]"
            else:
                rust_i = ei
                for d in reversed(dims):
                    rust_i = f"[{rust_i}; {d}]"
            for identifier in node.identifiers:
                self.emitter.emit_variable_declaration(
                    self._rust_ident(identifier), rust_t, rust_i, True
                )
            return
        # STRUCTURE-typed variable: `let mut v: Struct = Struct::new();`
        if (node.type_spec is not None
                and node.type_spec.data_type == DataType.STRUCTURE):
            sname = self._struct_name(node.type_spec.type_name or "")
            # ARRAY(n) OF <template> STRUCTURE -> [Struct; n] of zero-inits.
            if node.type_spec.array_size is not None:
                n = node.type_spec.array_size
                for identifier in node.identifiers:
                    self.emitter.emit_variable_declaration(
                        self._rust_ident(identifier), f"[{sname}; {n}]",
                        f"[{sname}::new(); {n}]", True
                    )
                return
            for identifier in node.identifiers:
                self.emitter.emit_variable_declaration(
                    self._rust_ident(identifier), sname,
                    f"{sname}::new()", True
                )
            return

        for identifier in node.identifiers:
            # Convert HAL/S name to Rust ident (snake_case, keyword-escaped).
            rust_name = self._rust_ident(identifier)
            
            # Use the INITIAL value(s) if given, else a typed default so the
            # variable is always initialized (Rust rejects reads of an
            # uninitialized binding).
            if node.initial_value is not None:
                initial_value = self._generate_expression(node.initial_value)
                # CHARACTER variables are Rust String; a bare string literal is
                # &str, so convert it to an owned String. Fixed-length
                # CHARACTER(n) is blank-padded / truncated to n.
                if (
                    node.type_spec is not None
                    and node.type_spec.data_type == DataType.CHARACTER
                ):
                    slen = self._char_length(node.initial_value)
                    tlen = self._char_length_of_spec(node.type_spec)
                    if tlen is not None:
                        initial_value = self._char_fit(
                            initial_value, tlen, slen)
                    elif (
                        isinstance(node.initial_value, LiteralNode)
                        and node.initial_value.literal_type == "STRING"
                    ):
                        initial_value += ".to_string()"
            elif node.initial_values:
                initial_value = self._generate_initial_values(node)
            else:
                tlen = self._char_length_of_spec(node.type_spec)
                if (node.type_spec is not None
                        and node.type_spec.data_type == DataType.CHARACTER
                        and tlen is not None):
                    initial_value = f'format!("{{:<{tlen}}}", "")'
                else:
                    initial_value = self.type_mapper.get_default_value(
                        node.type_spec)
            
            # Emit declaration
            is_mut = not node.is_constant
            self.emitter.emit_variable_declaration(
                rust_name,
                rust_type,
                initial_value,
                is_mut
            )

    def _generate_initial_values(self, node: DeclareNode) -> str:
        """Build an initializer from an INITIAL(...) value list."""
        vals = [self._generate_expression(v) for v in node.initial_values]
        ts = node.type_spec
        if ts is not None and ts.data_type == DataType.VECTOR:
            return f"Vector([{', '.join(vals)}])"
        if ts is not None and ts.data_type == DataType.MATRIX:
            # Row-major fill if the count matches rows*cols, else fall back.
            dims = ts.dimensions or []
            if len(dims) >= 2 and len(vals) == dims[0] * dims[1]:
                rows = dims[0]
                cols = dims[1]
                rows_src = [
                    "[" + ", ".join(vals[r * cols:(r + 1) * cols]) + "]"
                    for r in range(rows)
                ]
                return f"Matrix([{', '.join(rows_src)}])"
            return self.type_mapper.get_default_value(ts)
        # Scalar/other: use the first value.
        return vals[0]
    
    def _generate_procedure(self, node: ProcedureNode) -> None:
        """
        Generate Rust code for PROCEDURE.
        
        HAL/S:
            PROCEDURE Calculate(X, Y);
                DECLARE RESULT SCALAR;
                RESULT = X + Y;
            CLOSE Calculate;
        
        Rust:
            fn calculate(x: i32, y: i32) {
                let mut result: f32;
                result = x + y;
            }
        
        Args:
            node: ProcedureNode to generate
        """
        # NONHAL external functions are emitted as `extern "C"` bindings in the
        # generate() pre-pass; there is no HAL/S body to lower here. A TEMPLATE
        # is a signature-only prototype -- parsed and type-checked, but it
        # generates no code and allocates no memory.
        if getattr(node, "is_external", False) or getattr(
                node, "is_template", False):
            return
        # Internal key stays raw lowercase so lookups into fault/scope maps
        # continue to match. Emission uses the Rust-safe form.
        callee_key = node.name.lower()
        rust_fn_name = self._rust_ident(node.name)
        all_param_names = list(node.parameters) + list(node.assign_params)
        param_set = {p.lower() for p in all_param_names}
        assign_set = {p.lower() for p in node.assign_params}

        # In HAL/S a parameter's type is given by a DECLARE in the body. Collect
        # those (from both the declarations list and the statement stream, since
        # the parser keeps body declares as statements) to type the signature.
        body_decls = list(node.declarations) + [
            s for s in node.statements if isinstance(s, DeclareNode)
        ]
        param_types = {}
        for d in body_decls:
            if len(d.identifiers) == 1 and d.identifiers[0].lower() in param_set:
                param_types[d.identifiers[0].lower()] = self._full_rust_type(
                    d.type_spec
                )

        # Input parameters by value; ASSIGN (output) parameters by &mut. The
        # emitted parameter name is Rust-safe; type lookup keys stay raw.
        params = [
            (self._rust_ident(p), param_types.get(p.lower(), "i32"))
            for p in node.parameters
        ]
        params += [
            (self._rust_ident(p),
             f"&mut {param_types.get(p.lower(), 'i32')}")
            for p in node.assign_params
        ]

        # Emit procedure (or function, when it carries a return type)
        base_ret = (self.type_mapper.map_type(node.return_type)
                    if node.return_type is not None else None)
        is_result = self._uses_faults and callee_key in self._faulting_procs
        if is_result:
            inner = base_ret if base_ret is not None else "()"
            ret = f"Result<{inner}, HalFault>"
        else:
            ret = base_ret
        kind = "Function" if base_ret is not None else "Procedure"
        self.emitter.emit_comment(f"HAL/S {kind}: {node.name}")
        self.emitter.emit_function_header(
            rust_fn_name, params, ret, is_public=False)

        prev_unit = self._cur_unit
        prev_is_result = self._cur_unit_is_result
        self._cur_unit = callee_key
        self._cur_unit_is_result = is_result


        def _is_param_decl(n: ASTNode) -> bool:
            return (
                isinstance(n, DeclareNode)
                and len(n.identifiers) == 1
                and n.identifiers[0].lower() in param_set
            )

        # Within the body, ASSIGN parameters are &mut references, so uses must
        # be dereferenced. _generate_identifier consults this set.
        prev_assign = getattr(self, "_assign_params", set())
        self._assign_params = assign_set

        # Local declarations (those not naming a parameter)
        for decl in node.declarations:
            if not _is_param_decl(decl):
                self._generate_node(decl)

        # Body statements; skip the parameter-typing declares (now in the
        # signature) so we don't shadow the incoming arguments.
        for stmt in node.statements:
            if _is_param_decl(stmt):
                continue
            self._generate_node(stmt)

        # A faulting procedure returns Result; its normal path ends in Ok(()).
        if is_result and base_ret is None:
            self.emitter.emit_line("Ok(())")

        self._cur_unit = prev_unit
        self._cur_unit_is_result = prev_is_result
        self._assign_params = prev_assign
        self.emitter.emit_block_end()

    def _generate_while(self, node: WhileNode, label: str = "") -> None:
        """Generate a DO WHILE or DO UNTIL loop.

        DO WHILE tests before the first cycle (`while cond { body }`).
        DO UNTIL always runs the body once, then breaks when cond is TRUE
        (`loop { body; if cond { break; } }`). A `while !cond` lowering
        would skip the first cycle when cond is already true, which is
        not HAL/S §7.6.3. A `label` becomes a Rust loop label for EXIT.
        """
        condition = self._generate_expression(node.condition)
        lbl = f"'{self._rust_ident(label)}: " if label else ""
        if node.until:
            self.emitter.emit_line(f"{lbl}loop {{")
            self.emitter.indent()
            for stmt in node.statements:
                self._generate_node(stmt)
            self.emitter.emit_line(f"if {condition} {{ break; }}")
            self.emitter.dedent()
            self.emitter.emit_line("}")
            return
        self.emitter.emit_line(f"{lbl}while {condition} {{")
        self.emitter.indent()
        for stmt in node.statements:
            self._generate_node(stmt)
        self.emitter.dedent()
        self.emitter.emit_line("}")

    def _generate_case(self, node: CaseNode) -> None:
        """Generate a DO CASE as a Rust match. Branches are 1-indexed: the
        Nth statement runs when the selector evaluates to N."""
        selector = self._generate_expression(node.selector)
        self.emitter.emit_line(f"match {selector} {{")
        self.emitter.indent()
        for i, branch in enumerate(node.branches, start=1):
            self.emitter.emit_line(f"{i} => {{")
            self.emitter.indent()
            if isinstance(branch, DoBlockNode):
                for stmt in branch.statements:
                    self._generate_node(stmt)
            else:
                self._generate_node(branch)
            self.emitter.dedent()
            self.emitter.emit_line("}")
        # HAL/S: an out-of-range selector falls through (no-op here).
        self.emitter.emit_line("_ => {}")
        self.emitter.dedent()
        self.emitter.emit_line("}")

    def _generate_multi_assign(self, node: MultiAssignNode) -> None:
        """A, B, ... = <expr>;  Evaluate the RHS once into a temp, then assign
        it to each target (aggregates like Vector are Copy, so this is a real
        independent copy to each)."""
        value = self._generate_expression(node.value)
        self.emitter.emit_line("{")
        self.emitter.indent()
        self.emitter.emit_line(f"let __t = {value};")
        for target in node.targets:
            self.emitter.emit_line(f"{self._generate_expression(target)} = __t;")
        self.emitter.dedent()
        self.emitter.emit_line("}")

    def _generate_on_error(self, node: OnErrorNode) -> None:
        """ON ERROR(code) is a declaration, not sequential code. Its handler
        body is emitted at the fault site (same-scope handler) or at the CALL
        that unwinds into this scope (cross-scope). Handler registration is
        done in the _analyze_faults pre-pass, so nothing is emitted inline."""
        self.emitter.emit_comment(
            f"ON ERROR({node.code}): handler active in this scope")

    def _generate_handler_body(self, body: ASTNode) -> None:
        """Emit a registered handler's statements (a DO; ... END; group),
        skipping RESUME (which is modeled by falling through the trap)."""
        stmts = body.statements if isinstance(body, DoBlockNode) else [body]
        for stmt in stmts:
            self._generate_node(stmt)

    def _generate_fault_division(self, target: ASTNode,
                                 binop: BinaryOpNode) -> None:
        """Lower `target = left / right` in a fault-aware program (one that uses
        ON ERROR). A zero divisor raises HAL/S fault code 5, which is either
        handled inline by a same-scope ON ERROR (RESUME falls through, skipping
        the assignment) or unwound to the caller via `return Err(..)` so an
        enclosing handler in the calling scope runs."""
        code = self._DIVIDE_FAULT_CODE
        ts = None
        if isinstance(target, IdentifierNode):
            ts = self._var_info.get(target.name.lower())
        rust_t = self.type_mapper.map_type(ts) if ts is not None else "f32"
        left = self._generate_expression(binop.left)
        right = self._generate_expression(binop.right)
        unit_handlers = self._unit_handlers.get(self._cur_unit, {})
        quot = f"{left} / __d"

        self.emitter.emit_line("{")
        self.emitter.indent()
        self.emitter.emit_line(f"let __d = ({right}) as {rust_t};")
        if code in unit_handlers:
            # Same-scope handler: run it inline; RESUME continues after this
            # statement, so the assignment is skipped on a fault.
            self.emitter.emit_line(f"if __d == 0 as {rust_t} {{")
            self.emitter.indent()
            self._generate_handler_body(unit_handlers[code])
            self.emitter.dedent()
            self.emitter.emit_line("} else {")
            self.emitter.indent()
            self._emit_store(target, quot, src_node=binop)
            self.emitter.dedent()
            self.emitter.emit_line("}")
        elif self._cur_unit_is_result:
            # No local handler: unwind this procedure so the caller's handler
            # runs. Statements after the fault are skipped by the early return.
            self.emitter.emit_line(
                f"if __d == 0 as {rust_t} {{ "
                f"return Err(HalFault {{ code: {code} }}); }}")
            self._emit_store(target, quot, src_node=binop)
        else:
            # Unhandled fault at top level -> HAL/S terminates the program.
            self.emitter.emit_line(f"if __d == 0 as {rust_t} {{")
            self.emitter.indent()
            self._emit_unhandled_fault(str(code))
            self.emitter.dedent()
            self.emitter.emit_line("}")
            self._emit_store(target, quot, src_node=binop)
        self.emitter.dedent()
        self.emitter.emit_line("}")

    def _generate_faulting_call(self, call_expr: str, callee: str) -> None:
        """Lower a CALL to a procedure that may raise a fault. If the current
        scope has a matching ON ERROR handler, dispatch to it (RESUME falls
        through past the call); otherwise propagate the fault outward."""
        codes = self._proc_raises.get(callee, set())
        unit_handlers = self._unit_handlers.get(self._cur_unit, {})
        handled = [c for c in sorted(codes) if c in unit_handlers]
        if handled:
            self.emitter.emit_line(f"match {call_expr} {{")
            self.emitter.indent()
            self.emitter.emit_line("Ok(()) => {}")
            for c in handled:
                self.emitter.emit_line(f"Err(__f) if __f.code == {c} => {{")
                self.emitter.indent()
                self._generate_handler_body(unit_handlers[c])
                self.emitter.dedent()
                self.emitter.emit_line("}")
            # Exhaustiveness arm: re-raise any code not handled in this scope.
            self.emitter.emit_line("Err(__f) => {")
            self.emitter.indent()
            self._emit_reraise("__f")
            self.emitter.dedent()
            self.emitter.emit_line("}")
            self.emitter.dedent()
            self.emitter.emit_line("}")
        elif self._cur_unit_is_result:
            # No handler here; propagate to our caller.
            self.emitter.emit_line(f"{call_expr}?;")
        else:
            # No handler anywhere up to main -> unhandled fault terminates.
            self.emitter.emit_line(f"if let Err(__f) = {call_expr} {{")
            self.emitter.indent()
            self._emit_unhandled_fault("__f.code")
            self.emitter.dedent()
            self.emitter.emit_line("}")

    def _emit_unhandled_fault(self, code_expr: str) -> None:
        """Terminate on a fault that reached the top with no handler. Hosted
        std reports to stderr and exits; a bare-metal target panics, which the
        profile panic handler (panic-halt or esp-backtrace) handles."""
        if self._embedded:
            self.emitter.emit_line(
                f'panic!("HAL/S fault {{}} (unhandled)", {code_expr});')
        else:
            self.emitter.emit_line(
                f'eprintln!("HAL/S fault {{}} (unhandled)", {code_expr});')
            self.emitter.emit_line(f"std::process::exit({code_expr});")

    def _emit_reraise(self, fvar: str) -> None:
        """Propagate a fault the current scope does not handle."""
        if self._cur_unit_is_result:
            self.emitter.emit_line(f"return Err({fvar});")
        else:
            self._emit_unhandled_fault(f"{fvar}.code")

    def _unit_body_nodes(self, unit):
        """Walk the nodes of a unit's own body (declarations + statements),
        without descending into nested PROCEDURE/TASK definitions."""
        body = (list(getattr(unit, "declarations", []))
                + list(getattr(unit, "statements", [])))
        for stmt in body:
            yield from self._walk(stmt)

    def _collect_unit_handlers(self, unit) -> dict:
        """{fault code: handler body} for ON ERROR handlers declared directly
        in this unit's scope."""
        handlers = {}
        for n in self._unit_body_nodes(unit):
            if isinstance(n, OnErrorNode):
                handlers[n.code] = n.body
        return handlers

    @staticmethod
    def _is_trappable_division(node) -> bool:
        return (isinstance(node, AssignmentNode)
                and isinstance(node.value, BinaryOpNode)
                and node.value.operator == "/")

    def _analyze_faults(self, ast) -> None:
        """Scope pre-pass for the ON ERROR fault model.

        Records each unit's active handlers and computes, via a fixpoint over
        the call graph, the set of fault codes each procedure may propagate to
        its caller (own faulting ops plus callees', minus anything it handles
        locally). Procedures with a non-empty set get a `Result<_, HalFault>`
        signature so a fault unwinds to the nearest enclosing handler."""
        self._uses_faults = any(
            isinstance(n, OnErrorNode) for n in self._walk(ast))
        self._unit_handlers = {}
        self._proc_raises = {}
        self._faulting_procs = set()
        if not self._uses_faults:
            return

        procs = {
            n.name.lower(): n
            for n in self._walk(ast)
            if isinstance(n, ProcedureNode) and not getattr(n, "is_external", False)
        }

        # A COMPOOL+PROGRAM file is a CompilationNode; still register the
        # PROGRAM (and any TASK) so same-scope ON ERROR in main fires.
        for n in self._walk(ast):
            if isinstance(n, ProgramNode):
                self._unit_handlers["@main"] = self._collect_unit_handlers(n)
            elif isinstance(n, TaskNode):
                self._unit_handlers[n.name.lower()] = (
                    self._collect_unit_handlers(n))
        for name, proc in procs.items():
            self._unit_handlers[name] = self._collect_unit_handlers(proc)

        own_codes = {}
        callees = {}
        for name, proc in procs.items():
            oc = set()
            cl = set()
            for n in self._unit_body_nodes(proc):
                if self._is_trappable_division(n):
                    oc.add(self._DIVIDE_FAULT_CODE)
                if isinstance(n, CallNode):
                    cl.add(n.name.lower())
            own_codes[name] = oc
            callees[name] = cl

        # Fixpoint: raised = (own U raised(callees)) - handled-locally.
        raises = {name: set() for name in procs}
        changed = True
        while changed:
            changed = False
            for name in procs:
                handled = set(self._unit_handlers.get(name, {}).keys())
                new = set(own_codes[name])
                for callee in callees[name]:
                    new |= raises.get(callee, set())
                new -= handled
                if new != raises[name]:
                    raises[name] = new
                    changed = True

        self._proc_raises = raises
        self._faulting_procs = {n for n, c in raises.items() if c}

    def _generate_call(self, node: CallNode) -> None:
        """Generate a CALL as a Rust function call. Input arguments pass by
        value; ASSIGN (output) arguments pass by &mut so the callee writes
        back to the caller's variables."""
        parts = [self._generate_expression(a) for a in node.arguments]
        parts += [
            f"&mut {self._generate_expression(a)}" for a in node.assign_args
        ]
        call_expr = f"{self._rust_ident(node.name)}({', '.join(parts)})"
        callee = node.name.lower()
        if self._uses_faults and callee in self._faulting_procs:
            self._generate_faulting_call(call_expr, callee)
        elif callee in getattr(self, "_extern_fns", set()):
            # extern "C" (NONHAL or CALLed TEMPLATE): calling it is unsafe.
            self.emitter.emit_line(f"unsafe {{ {call_expr}; }}")
        else:
            self.emitter.emit_line(f"{call_expr};")
    
    def _generate_name_assign(self, node: NameAssignNode) -> None:
        """ASSIGN <pointer> TO <target> -> bind the raw pointer to the target's
        address (addr_of_mut! avoids forming an intermediate &mut borrow).
        ASSIGN P TO NULL unbinds (`null_mut()`)."""
        ptr = self._rust_ident(node.pointer)
        if not node.target:
            self.emitter.emit_line(f"{ptr} = std::ptr::null_mut();")
            return
        tgt = self._rust_ident(node.target)
        self.emitter.emit_line(
            f"{ptr} = std::ptr::addr_of_mut!({tgt});")

    def _is_name_value(self, node) -> bool:
        return isinstance(node, (NullNode, NameRefNode))

    def _name_ptr_rust(self, node) -> str:
        """Emit a NAME value (the pointer), never a pointee deref."""
        if isinstance(node, NullNode):
            return "std::ptr::null_mut()"
        if isinstance(node, NameRefNode):
            nm = self._rust_ident(node.name)
            if node.name.lower() in getattr(self, "_name_vars", {}):
                return nm
            return f"std::ptr::addr_of_mut!({nm})"
        return self._generate_expression(node)

    def _expr_uses_name(self, node) -> bool:
        """True if the expression reads any NAME (pointer) variable."""
        for n in self._walk(node):
            if isinstance(n, IdentifierNode) \
                    and n.name.lower() in getattr(self, "_name_vars", {}):
                return True
        return False

    def _char_length_of_spec(self, ts) -> Optional[int]:
        if (ts is not None and ts.data_type == DataType.CHARACTER
                and ts.dimensions):
            return ts.dimensions[0]
        return None

    def _char_length(self, node):
        """Declared CHARACTER length of an expression: dimensions[0] for a
        CHARACTER variable, or the literal length for a string literal."""
        if isinstance(node, IdentifierNode):
            return self._char_length_of_spec(
                self._var_info.get(node.name.lower()))
        if isinstance(node, LiteralNode) and isinstance(node.value, str):
            return len(node.value)
        return None

    def _is_char_var(self, node) -> bool:
        if isinstance(node, IdentifierNode):
            ts = self._var_info.get(node.name.lower())
            return ts is not None and ts.data_type == DataType.CHARACTER
        return False

    def _is_char_source(self, node) -> bool:
        """True if an expression yields a CHARACTER (String) value that a
        fixed-length assignment should normalize."""
        if isinstance(node, LiteralNode) and isinstance(node.value, str):
            return True
        if self._is_char_var(node):
            return True
        if (isinstance(node, BinaryOpNode) and node.operator == "||"
                and self._bit_width_expr(node) is None):
            return True
        if (isinstance(node, SubscriptNode)
                and isinstance(node.operand, IdentifierNode)):
            ts = self._var_info.get(node.operand.name.lower())
            return ts is not None and ts.data_type == DataType.CHARACTER
        return False

    def _char_fit(self, src_rust: str, tlen: int, slen: Optional[int] = None) -> str:
        """Blank-pad or right-truncate a String/&str expr to exactly tlen."""
        if slen is not None:
            if slen > tlen:
                return (f"{src_rust}.chars().take({tlen})"
                        ".collect::<String>()")
            if slen < tlen:
                return f'format!("{{:<{tlen}}}", {src_rust})'
            # Equal length: a &str literal still needs an owned String.
            if src_rust.startswith('"'):
                return f"{src_rust}.to_string()"
            return src_rust
        return (
            f'{{ let __s: String = ({src_rust}).to_string(); '
            f'if __s.chars().count() > {tlen} '
            f'{{ __s.chars().take({tlen}).collect() }} '
            f'else {{ format!("{{:<{tlen}}}", __s) }} }}'
        )

    def _collect_compool_mut(self, node, acc=None):
        """Mutable COMPOOL names referenced by an AST subtree."""
        if acc is None:
            acc = set()
        if node is None or not isinstance(node, ASTNode):
            return acc
        if isinstance(node, IdentifierNode):
            up = node.name.upper()
            if up in self._globals and self._globals[up][0] == "mut":
                acc.add(up)
        for v in vars(node).values():
            for x in (v if isinstance(v, list) else [v]):
                if isinstance(x, ASTNode):
                    self._collect_compool_mut(x, acc)
        return acc

    def _bit_assign_cast(self, value_rust: str, src_node, dst_ts) -> str:
        """Justify BIT assignment to dst width: keep bits 1..n (MSB-first)."""
        dw = dst_ts.dimensions[0] if dst_ts.dimensions else 32
        dt = TypeMapper._bit_type(dw)
        sw = self._bit_width_expr(src_node)
        if sw is not None and sw > dw:
            return f"((({value_rust}) >> {sw - dw}) as {dt})"
        if sw is not None and sw < dw:
            return f"((({value_rust}) as {dt}) << {dw - sw})"
        return f"({value_rust}) as {dt}"

    def _emit_store(self, target, value_rust: str, src_node=None) -> None:
        """Write `value_rust` into `target` (COMPOOL / ASSIGN / NAME / place)."""
        if isinstance(target, IdentifierNode):
            lo = target.name.lower()
            up = target.name.upper()
            tts = self._var_info.get(lo)
            if (tts is not None and tts.data_type == DataType.BIT
                    and src_node is not None):
                value_rust = self._bit_assign_cast(value_rust, src_node, tts)
            elif (tts is not None and tts.data_type == DataType.BIT
                    and tts.dimensions):
                value_rust = (
                    f"({value_rust}) as {self.type_mapper.map_type(tts)}")
            if up in getattr(self, "_compool_guard", {}):
                self.emitter.emit_line(
                    f"(*{self._compool_guard[up]}) = {value_rust};")
                return
            if up in self._globals and self._globals[up][0] == "mut":
                self.emitter.emit_line(
                    f"*{up}.lock().unwrap() = {value_rust};")
                return
            if lo in getattr(self, "_assign_params", set()):
                self.emitter.emit_line(
                    f"(*{self._rust_ident(target.name)}) = {value_rust};")
                return
            if lo in getattr(self, "_name_vars", {}):
                self.emitter.emit_line(
                    f"unsafe {{ *{self._rust_ident(target.name)} "
                    f"= {value_rust}; }}")
                return
            self.emitter.emit_line(
                f"{self._rust_ident(target.name)} = {value_rust};")
            return
        place = self._generate_expression(target)
        self.emitter.emit_line(f"{place} = {value_rust};")

    def _generate_assignment(self, node: AssignmentNode) -> None:
        """
        Generate Rust code for assignment.
        
        HAL/S:
            X = Y + 5;
        
        Rust:
            x = y + 5;
        
        Args:
            node: AssignmentNode to generate
        """
        if node.target and node.value:
            # NAME(P) = NULL / NAME(G) — assign the pointer cell, not *P.
            if isinstance(node.target, NameRefNode):
                ptr = self._rust_ident(node.target.name)
                rhs = self._name_ptr_rust(node.value)
                self.emitter.emit_line(f"{ptr} = {rhs};")
                return
            # EXTERNAL variable target -> VOLATILE store through the foreign
            # `static mut` (a plain assignment would not even compile, and a
            # non-volatile store could be elided for a hardware register).
            if (isinstance(node.target, IdentifierNode)
                    and node.target.name.lower()
                    in getattr(self, "_extern_vars", set())):
                nm = self._rust_ident(node.target.name)
                rhs = self._generate_expression(node.value)
                self.emitter.emit_line(
                    "unsafe { ::core::ptr::write_volatile("
                    f"::core::ptr::addr_of_mut!({nm}), {rhs}) }};")
                return
            # One MutexGuard per COMPOOL name for the whole statement so
            # `G$I = G$I + 1` cannot deadlock on a non-reentrant Mutex.
            if not getattr(self, "_compool_locked", False):
                tnames = self._collect_compool_mut(node.target)
                vnames = self._collect_compool_mut(node.value)
                names = tnames & vnames
                if names:
                    self._compool_locked = True
                    self._compool_guard = {}
                    self.emitter.emit_line("{")
                    self.emitter.indent()
                    for n in sorted(names):
                        bind = f"__cp_{n.lower()}"
                        self.emitter.emit_line(
                            f"let mut {bind} = {n}.lock().unwrap();")
                        self._compool_guard[n] = bind
                    self._generate_assignment(node)
                    self._compool_guard = {}
                    self._compool_locked = False
                    self.emitter.dedent()
                    self.emitter.emit_line("}")
                    return
            # ARRAY OF STRUCTURE copy between differing memory layouts
            # (ALIGNED <-> DENSE): the two arrays are distinct Rust types
            # (padded vs packed), so convert element-by-element.
            if (isinstance(node.target, IdentifierNode)
                    and isinstance(node.value, IdentifierNode)):
                tts = self._var_info.get(node.target.name.lower())
                vts = self._var_info.get(node.value.name.lower())
                if (tts is not None and vts is not None
                        and tts.data_type == DataType.STRUCTURE
                        and vts.data_type == DataType.STRUCTURE
                        and tts.array_size is not None
                        and vts.array_size is not None
                        and getattr(tts, "layout", "aligned")
                            != getattr(vts, "layout", "aligned")):
                    self._generate_cross_layout_copy(
                        self._rust_ident(node.target.name),
                        self._rust_ident(node.value.name),
                        tts, vts)
                    return
            # Write to a field of a DENSE (packed) array element: use
            # write_unaligned (Rust forbids &mut into a packed field).
            if self._is_dense_field(node.target):
                place = (f"{self._generate_expression(node.target.operand)}"
                         f".{self._rust_ident(node.target.field_name)}")
                val = self._generate_expression(node.value)
                self.emitter.emit_line(
                    f"unsafe {{ core::ptr::addr_of_mut!({place})"
                    f".write_unaligned({val}); }}")
                return
            # HAL/S CHARACTER is fixed-length: assigning to CHARACTER(n) right-
            # truncates an oversized source and right-pads (with spaces) an
            # undersized one, decided from the declared lengths at compile time.
            if isinstance(node.target, IdentifierNode):
                tlen = self._char_length(node.target)
                if tlen is not None and self._is_char_source(node.value):
                    tgt = self._rust_ident(node.target.name)
                    src = self._generate_expression(node.value)
                    slen = self._char_length(node.value)
                    self.emitter.emit_line(
                        f"{tgt} = {self._char_fit(src, tlen, slen)};")
                    return
            # Context-sensitive IDENTITY: `M = IDENTITY` builds an identity
            # matrix sized to the (square) target's declared dimensions.
            if (isinstance(node.value, IdentifierNode)
                    and node.value.name.upper() == "IDENTITY"
                    and isinstance(node.target, IdentifierNode)):
                ts = self._var_info.get(node.target.name.lower())
                dims = ts.dimensions if ts is not None else None
                if (ts is not None and ts.data_type == DataType.MATRIX
                        and dims and len(dims) == 2):
                    r, c = dims[0], dims[1]
                    rows = ", ".join(
                        "[" + ", ".join(
                            "1.0" if i == j else "0.0" for j in range(c)
                        ) + "]" for i in range(r)
                    )
                    self.emitter.emit_line(
                        f"{self._rust_ident(node.target.name)} "
                        f"= Matrix([{rows}]);")
                    return
            # NAME (pointer) deref assignment. If the target is a NAME, write
            # through the pointer; if either side reads a NAME, the whole
            # statement runs in one `unsafe` block (so reads are bare derefs and
            # there is no nested/duplicate unsafe).
            target_is_name = (isinstance(node.target, IdentifierNode)
                              and node.target.name.lower() in self._name_vars)
            if target_is_name or self._expr_uses_name(node.value):
                self._unsafe_ctx = True
                rhs = self._generate_expression(node.value)
                if target_is_name:
                    lhs = f"*{self._rust_ident(node.target.name)}"
                else:
                    lhs = self._generate_expression(node.target)
                self._unsafe_ctx = False
                self.emitter.emit_line(f"unsafe {{ {lhs} = {rhs}; }}")
                return
            # EQUATE overlay write: VIEW = expr  ->  target = (expr) as target_type
            # (bit-preserving, so the alias and the target stay byte-identical).
            if (isinstance(node.target, IdentifierNode)
                    and node.target.name.lower() in self._equates):
                target, _, target_type = self._equates[node.target.name.lower()]
                rhs = self._generate_expression(node.value)
                self.emitter.emit_line(
                    f"{self._rust_ident(target)} = "
                    f"({rhs}) as {target_type};")
                return
            # In a fault-aware program (one that uses ON ERROR), a division is
            # lowered so a zero divisor raises HAL/S fault 5 -- handled inline
            # by a same-scope handler, or unwound to the caller.
            if (
                self._uses_faults
                and isinstance(node.value, BinaryOpNode)
                and node.value.operator == "/"
            ):
                self._generate_fault_division(node.target, node.value)
                return
            # A MATRIX(...) constructor needs the target's dimensions.
            if isinstance(node.value, MatrixLiteralNode) \
                    and isinstance(node.target, IdentifierNode):
                ts = self._var_info.get(node.target.name.lower())
                dims = ts.dimensions if ts is not None else None
                value = self._generate_matrix_literal(node.value, dims)
            else:
                value = self._generate_expression(node.value)
            # Assigning a mutable COMPOOL global: evaluate the RHS first (which
            # releases any read locks it takes) and only then acquire the write
            # lock, so a read-modify-write of the same global can't deadlock.
            if isinstance(node.target, IdentifierNode):
                up = node.target.name.upper()
                if (up in self._globals and self._globals[up][0] == "mut"
                        and up not in getattr(self, "_compool_guard", {})):
                    self.emitter.emit_line("{")
                    self.emitter.indent()
                    self.emitter.emit_line(f"let __v = {value};")
                    self.emitter.emit_line(f"*{up}.lock().unwrap() = __v;")
                    self.emitter.dedent()
                    self.emitter.emit_line("}")
                    return
            # BIT-target: keep bits 1..n (MSB-first), then store through the
            # COMPOOL / ASSIGN / NAME / local place.
            self._emit_store(node.target, value, src_node=node.value)
    
    def _generate_write(self, node: WriteNode) -> None:
        """
        Generate Rust code for a WRITE statement.

        HAL/S WRITE sends its output list to a channel (6 is the standard
        output device). Items are mapped to a println! call, one "{}"
        placeholder per item, space-separated like HAL/S output columns.

        HAL/S:
            WRITE(6) 'HELLO, WORLD!';
            WRITE(6) 'X =', X;
            WRITE(6);

        Rust:
            println!("{}", "HELLO, WORLD!");
            println!("{} {}", "X =", x);
            println!();

        Args:
            node: WriteNode to generate
        """
        # Note non-standard channels for traceability; 6 is standard output.
        if node.channel != 6:
            self.emitter.emit_comment(f"WRITE channel {node.channel}")
        # Direct-access (record-number) I/O parses today but has no runtime
        # lowering. Flag it explicitly so the emitted Rust doesn't silently
        # ignore the record and pretend to have done a sequential write.
        if node.record is not None:
            record_src = self._generate_expression(node.record)
            self.emitter.emit_comment(
                f"TODO: record I/O -- WRITE(unit={node.channel}, "
                f"record={record_src}) direct-access is not yet lowered; "
                "items below are emitted as a sequential WRITE")

        # Output sink is target-dependent: hosted std and esp-println use
        # println!; the cortex-m stub uses semihosting hprintln!.
        # cortex-m-semihosting 0.5 hprintln! returns () (0.4 returned a
        # Result), so no .ok() -- the macro panics internally on error.
        if self.spec.write_sink == "hprintln":
            if not node.items:
                self.emitter.emit_line('hprintln!("");')
                return
            placeholders = " ".join(["{}"] * len(node.items))
            args = [self._generate_expression(item) for item in node.items]
            args_str = ", ".join(args)
            self.emitter.emit_line(
                f'hprintln!("{placeholders}", {args_str});')
            return

        if not node.items:
            # WRITE with no output list emits a blank line
            self.emitter.emit_line("println!();")
            return

        placeholders = " ".join(["{}"] * len(node.items))
        # WRITE of an ARRAY(n) OF <elem> lists its elements space-separated;
        # arrays don't implement Display, so convert to a String at the arg
        # site so a mixed `WRITE(6) 'label', ARR, X;` still fits the plain
        # println! shape.
        args = []
        for item in node.items:
            src = self._generate_expression(item)
            if self._is_array_expr(item):
                src = (f"({src}).iter().map(|__x| __x.to_string())"
                       ".collect::<Vec<_>>().join(\" \")")
            args.append(src)
        args_str = ", ".join(args)
        self.emitter.emit_line(f'println!("{placeholders}", {args_str});')

    def _generate_read(self, node: ReadNode) -> None:
        """Generate a READ statement using the shared hal_rt::read_f32 cursor.
        All READs draw from one continuous input stream, line-by-line, without
        waiting for EOF. SCALAR -> 1 f32, INTEGER -> 1 i32, VECTOR(n) -> n f32;
        read_f32 always returns exactly the requested count (0.0-padded at EOF),
        so the distribution below needs no bounds checks."""
        if node.channel != 5:
            self.emitter.emit_comment(f"READ channel {node.channel}")
        # Direct-access (record-number) I/O parses today but has no runtime
        # lowering. Same rationale as _generate_write: flag it explicitly.
        if node.record is not None:
            record_src = self._generate_expression(node.record)
            self.emitter.emit_comment(
                f"TODO: record I/O -- READ(unit={node.channel}, "
                f"record={record_src}) direct-access is not yet lowered; "
                "targets below are filled from the sequential input stream")

        target_info = []
        total = 0
        for tgt in node.targets:
            ts = (self._var_info.get(tgt.name.lower())
                  if isinstance(tgt, IdentifierNode) else None)
            # An ARRAY(n) OF <elem> target is filled element-by-element with
            # a per-element cast to the array's declared Rust element type.
            if ts is not None and ts.array_size is not None:
                elem_type = (self.type_mapper.map_type(ts.element_type)
                             if ts.element_type is not None else "f32")
                target_info.append((
                    self._generate_expression(tgt), "array",
                    ts.array_size, elem_type))
                total += ts.array_size
                continue
            if ts is not None and ts.data_type == DataType.VECTOR:
                n, kind = (ts.dimensions[0] if ts.dimensions else 3), "vector"
            elif ts is not None and ts.data_type in (
                    DataType.INTEGER, DataType.BIT):
                n, kind = 1, "int"
            else:
                n, kind = 1, "scalar"
            target_info.append((self._generate_expression(tgt), kind, n, None))
            total += n

        self.emitter.emit_line("{")
        self.emitter.indent()
        self.emitter.emit_line(f"let __vals = hal_rt::read_f32({total});")
        self.emitter.emit_line("let mut __k = 0usize;")
        for lvalue, kind, n, elem_type in target_info:
            if kind == "vector":
                self.emitter.emit_line(
                    f"{lvalue} = {{ let mut __v = [0.0f32; {n}]; "
                    f"for __j in 0..{n} {{ __v[__j] = __vals[__k]; "
                    "__k += 1; } Vector(__v) };")
            elif kind == "array":
                self.emitter.emit_line(
                    f"for __j in 0..{n} {{ "
                    f"{lvalue}[__j] = __vals[__k] as {elem_type}; "
                    "__k += 1; }")
            elif kind == "int":
                self.emitter.emit_line(
                    f"{lvalue} = {{ let __t = __vals[__k] as i32; "
                    "__k += 1; __t };")
            else:
                self.emitter.emit_line(
                    f"{lvalue} = {{ let __t = __vals[__k]; __k += 1; __t }};")
        self.emitter.dedent()
        self.emitter.emit_line("}")

    def _generate_expression(self, node: ASTNode) -> str:
        """
        Generate Rust expression from AST node.
        
        Args:
            node: Expression node
            
        Returns:
            Rust expression string
        """
        if isinstance(node, LiteralNode):
            return self._generate_literal(node)
        elif isinstance(node, NullNode):
            return "std::ptr::null_mut()"
        elif isinstance(node, NameRefNode):
            return self._name_ptr_rust(node)
        elif isinstance(node, IdentifierNode):
            return self._generate_identifier(node)
        elif isinstance(node, BinaryOpNode):
            return self._generate_binary_op(node)
        elif isinstance(node, UnaryOpNode):
            return self._generate_unary_op(node)
        elif isinstance(node, SubscriptNode):
            return self._generate_subscript(node)
        elif isinstance(node, MatrixLiteralNode):
            return self._generate_matrix_literal(node)
        elif isinstance(node, MatrixSubscriptNode):
            return self._generate_matrix_subscript(node)
        elif isinstance(node, VectorLiteralNode):
            return self._generate_vector_literal(node)
        elif isinstance(node, FieldAccessNode):
            return self._generate_field_access(node)
        elif isinstance(node, FunctionCallNode):
            return self._generate_function_call(node)
        else:
            # Exhaustiveness backstop -- see _generate_node for rationale. An
            # unhandled expression node silently emitting a comment would let
            # a subexpression evaluate to nothing, corrupting the surrounding
            # statement.
            raise BackendError(
                f"codegen: no handler for expression node "
                f"{type(node).__name__} "
                f"(add a branch to _generate_expression)",
                line=getattr(node, "line", 0),
                column=getattr(node, "column", 0),
            )
    
    def _generate_literal(self, node: LiteralNode) -> str:
        """
        Generate Rust literal.
        
        Args:
            node: LiteralNode
            
        Returns:
            Rust literal string
        """
        value = node.value
        
        if node.literal_type == "INTEGER":
            return str(value)
        elif node.literal_type == "SCALAR":
            # Ensure float has decimal point
            val_str = str(value)
            if '.' not in val_str and 'e' not in val_str.lower():
                val_str += ".0"
            return val_str
        elif node.literal_type == "STRING":
            # HAL/S source strings are preserved verbatim: every char that
            # Rust's string-literal grammar would reinterpret (backslash,
            # double-quote, control chars) gets escaped so the emitted
            # literal represents the same bytes the HAL/S programmer wrote.
            # Previously only `"` was escaped, so a HAL/S value containing
            # `\` produced an unterminated Rust string.
            return f'"{self._escape_rust_string(str(value))}"'
        elif node.literal_type == "BIT":
            if isinstance(value, bool):
                return "true" if value else "false"
            return str(value)
        elif node.literal_type == "HEX":
            return f"0x{int(value):X}"
        elif node.literal_type == "OCT":
            return f"0o{int(value):o}"
        elif node.literal_type == "DEC":
            return str(value)
        else:
            return str(value)
    
    def _generate_identifier(self, node: IdentifierNode) -> str:
        """
        Generate Rust identifier.
        
        Args:
            node: IdentifierNode
            
        Returns:
            Rust identifier (snake_case)
        """
        # HAL/S boolean literals
        up = node.name.upper()
        if up == "TRUE":
            return "true"
        if up == "FALSE":
            return "false"
        if up in getattr(self, "_compool_guard", {}):
            return f"(*{self._compool_guard[up]})"
        # EVENT read -> atomic load of its shared flag.
        if node.name.lower() in getattr(self, "_event_vars", set()):
            return (f"{node.name.upper()}"
                    ".load(core::sync::atomic::Ordering::SeqCst)")
        # EXTERNAL variable -> VOLATILE read of the foreign `extern "C"`
        # static: something outside this program writes it (hardware, an ISR,
        # a shim), so the optimizer must not cache or elide the load.
        if node.name.lower() in getattr(self, "_extern_vars", set()):
            nm = self._rust_ident(node.name)
            read = (f"::core::ptr::read_volatile("
                    f"::core::ptr::addr_of!({nm}))")
            if getattr(self, "_unsafe_ctx", False):
                return read
            return f"(unsafe {{ {read} }})"
        # `unsafe` the bare deref suffices; elsewhere use an inline unsafe block
        # so the expression is valid on its own.
        if node.name.lower() in getattr(self, "_name_vars", {}):
            nm = self._rust_ident(node.name)
            if getattr(self, "_unsafe_ctx", False):
                return f"(*{nm})"
            return f"(unsafe {{ *{nm} }})"
        # COMPOOL globals: const -> NAME; mutable -> locked access
        if up in self._globals:
            kind, _ = self._globals[up]
            if kind == "const":
                return up
            return f"(*{up}.lock().unwrap())"
        # EQUATE overlay read: VIEW -> (target as view_type), bit-preserving.
        if node.name.lower() in self._equates:
            target, view_type, _ = self._equates[node.name.lower()]
            return f"({self._rust_ident(target)} as {view_type})"
        # ASSIGN (output) parameters are &mut references inside a procedure;
        # dereference on every use so reads and assignment targets both work.
        if node.name.lower() in getattr(self, "_assign_params", set()):
            return f"(*{self._rust_ident(node.name)})"
        # Convert HAL/S UPPER_CASE to Rust snake_case (raw-escaped if it
        # would collide with a Rust keyword).
        return self._rust_ident(node.name)
    
    def _shift_direction(self, node):
        """Classify a SHIFT count's compile-time sign.
        Returns ('right', mag) / ('left', mag) / (None, None) for runtime."""
        if isinstance(node, UnaryOpNode) and node.operator == "-":
            return ("right", self._generate_expression(node.operand))
        if isinstance(node, LiteralNode) and isinstance(node.value, int) \
                and not isinstance(node.value, bool):
            if node.value < 0:
                return ("right", str(-node.value))
            return ("left", str(node.value))
        return (None, None)

    def _is_boolean_expr(self, node) -> bool:
        """True if an expression is BOOLEAN-typed (so AND/OR are logical, not
        bitwise). Best-effort, mirroring _numeric_kind."""
        if isinstance(node, LiteralNode):
            lt = (node.literal_type or "").upper()
            if lt == "BOOLEAN":
                return True
            return isinstance(node.value, bool)
        if isinstance(node, IdentifierNode):
            if node.name.upper() in ("TRUE", "FALSE"):
                return True
            ts = self._var_info.get(node.name.lower())
            if node.name.lower() in getattr(self, "_event_vars", set()):
                return True
            return ts is not None and ts.data_type == DataType.BOOLEAN
        if isinstance(node, UnaryOpNode):
            if node.operator in ("NOT", "¬", "~"):
                return True
            return self._is_boolean_expr(node.operand)
        if isinstance(node, BinaryOpNode):
            o = node.operator
            if o in ("=", "==", "¬=", "~=", "<", ">", "<=", ">="):
                return True  # comparisons yield bool
            if o in ("AND", "OR"):
                return (self._is_boolean_expr(node.left)
                        and self._is_boolean_expr(node.right))
        return False

    def _bit_width_expr(self, node):
        """Declared BIT width of an expression (None if not a BIT). Used to
        lower `||` bit concatenation; a nested `||` sums its operands' widths."""
        if isinstance(node, IdentifierNode):
            w = self._bit_widths.get(node.name.lower())
            if w is not None:
                return w
            ts = self._var_info.get(node.name.lower())
            if (ts is not None and ts.data_type == DataType.BIT
                    and ts.dimensions):
                return ts.dimensions[0]
            return None
        if isinstance(node, LiteralNode) and node.literal_type in (
                "HEX", "OCT", "BIT", "DEC") and node.bit_width is not None:
            return node.bit_width
        if isinstance(node, BinaryOpNode) and node.operator == "||":
            lw = self._bit_width_expr(node.left)
            rw = self._bit_width_expr(node.right)
            if lw is not None and rw is not None:
                return lw + rw
        return None

    def _is_array_expr(self, node) -> bool:
        """True if an expression is a plain ARRAY value (not an element access).
        Used to unroll array +/-/* into element-wise loops."""
        if isinstance(node, IdentifierNode):
            ts = self._var_info.get(node.name.lower())
            return ts is not None and ts.array_size is not None
        return False

    def _generate_binary_op(self, node: BinaryOpNode) -> str:
        """
        Generate Rust binary operation with minimal, correct parentheses.
        
        Args:
            node: BinaryOpNode
            
        Returns:
            Rust binary expression
        """
        op = node.operator

        # Array arithmetic. Rust cannot `+`/`*` fixed arrays, so element-wise
        # operations (and scalar broadcasting) unroll to std::array::from_fn,
        # which builds a [T; N] sized to the assignment target.
        if op in ("+", "-", "*", "/"):
            l_arr = self._is_array_expr(node.left)
            r_arr = self._is_array_expr(node.right)
            if l_arr or r_arr:
                left = self._generate_expression(node.left)
                right = self._generate_expression(node.right)
                lt = f"{left}[i]" if l_arr else left
                rt = f"{right}[i]" if r_arr else right
                return f"std::array::from_fn(|i| {lt} {op} {rt})"
        # `||` is overloaded: bit concatenation for BIT operands (shift the
        # left operand up by the right operand's width and OR in the right), and
        # string concatenation otherwise. Both operands are widened to the
        # result's unsigned type first: after the P0 #5 fix each BIT(w) picks
        # its own narrow Rust type (u8/u16/...), so a plain `u8 << 8` would
        # overflow and `u8 | u8` would not fit an assignment to a wider u16.
        if op == "||":
            lw = self._bit_width_expr(node.left)
            rw = self._bit_width_expr(node.right)
            left = self._generate_expression(node.left)
            right = self._generate_expression(node.right)
            if lw is not None and rw is not None:
                result_type = TypeMapper._bit_type(lw + rw)
                # Inner parens shield {left}/{right} from `as`'s low precedence
                # (an unparenthesized `a | b as T` binds as `a | (b as T)`).
                return (f"((({left}) as {result_type}) << {rw}) "
                        f"| (({right}) as {result_type})")
            return f'format!("{{}}{{}}", {left}, {right})'
        # HAL/S MOD is mathematically non-negative (e.g. -7 MOD 3 == 2); Rust
        # `%` is truncated-toward-zero (-7 % 3 == -1). rem_euclid gives HAL/S
        # semantics and is defined on both integer and float types in std.
        if op.upper() == "MOD":
            left = self._generate_expression(node.left)
            right = self._generate_expression(node.right)
            return f"({left}).rem_euclid({right})"
        # INTEGER + - * : lower to wrapping_add / wrapping_sub / wrapping_mul so
        # behavior is deterministic across Rust debug (panic on overflow) and
        # release (silent two's-complement wrap) profiles. HAL/S integer
        # arithmetic is modular within the target width; wrapping was chosen
        # over checked_* + fault-raise because the fault model currently only
        # models divide-by-zero (extending it to OVERFLOW is a separate task).
        if op in ("+", "-", "*") \
                and self._numeric_kind(node.left) == "int" \
                and self._numeric_kind(node.right) == "int":
            method = {"+": "wrapping_add", "-": "wrapping_sub",
                      "*": "wrapping_mul"}[op]
            left = self._generate_expression(node.left)
            right = self._generate_expression(node.right)
            return f"({left}).{method}({right})"
        op_map = {
            "+": "+", "-": "-", "*": "*", "/": "/",
            "**": ".pow", "=": "==", "¬=": "!=", "~=": "!=",
            "<": "<", ">": ">", "<=": "<=", ">=": ">=",
            "AND": "&", "OR": "|", "XOR": "^",
        }
        # Word operators (AND/OR/XOR) are case-insensitive in HAL/S; fall back
        # to the upper-cased spelling so mixed-case source still maps. (MOD is
        # handled by its own branch above.)
        rust_op = op_map.get(op) or op_map.get(op.upper(), op)

        # NAME-value compare: NAME(P) = NULL is pointer equality, not *p.
        if rust_op in ("==", "!=") and (
                self._is_name_value(node.left)
                or self._is_name_value(node.right)):
            return (
                f"{self._name_ptr_rust(node.left)} {rust_op} "
                f"{self._name_ptr_rust(node.right)}")

        # AND/OR are bitwise on BIT/INTEGER. On BOOLEAN they stay `&`/`|`
        # (eager: HAL/S evaluates both operands, so ON ERROR still sees
        # `X / D` in `D ¬= 0 AND X / D > 1`). Comparisons are parenthesized
        # by _operand because AND binds tighter than `==` in Rust.

        # Power has no Rust infix operator. f32 uses .powi(i32); integer
        # bases use .pow(u32). Default to the float form (common in HAL/S math).
        if op == "**":
            base = self._generate_expression(node.left)
            exp = self._generate_expression(node.right)
            if (self._is_integer_operand(node.left)
                    and self._is_integer_operand(node.right)):
                return f"({base}).pow({exp} as u32)"
            if self._is_integer_operand(node.right):
                return f"({base}).powi({exp} as i32)"
            return f"(({base}) as f32).powf(({exp}) as f32)"

        my_prec = self._PRECEDENCE.get(op, 0)
        left = self._operand(node.left, my_prec, right_side=False)
        right = self._operand(node.right, my_prec, right_side=True)

        # HAL/S promotes mixed arithmetic to the wider operand. Rust has no
        # implicit numeric coercion, so cast: an integer operand to the float
        # type, and a single-precision (f32) operand to f64 when the other side
        # is double-precision (e.g. SINGLE * DOUBLE -> (s as f64) * d).
        if op in ("+", "-", "*", "/"):
            lk = self._numeric_kind(node.left)
            rk = self._numeric_kind(node.right)
            lp = self._float_precision(node.left)
            rp = self._float_precision(node.right)
            is_double = (lp == "double" or rp == "double")
            ftype = "f64" if is_double else "f32"
            if lk == "int" and rk == "float":
                left = f"({left} as {ftype})"
            elif rk == "int" and lk == "float":
                right = f"({right} as {ftype})"
            if is_double:
                if lp == "single" and rp == "double":
                    left = f"({left} as f64)"
                elif rp == "single" and lp == "double":
                    right = f"({right} as f64)"
        # CHARACTER compare: blank-extend the shorter operand to the longer.
        if rust_op in ("==", "!=") and (
                self._is_char_var(node.left) or self._is_char_var(node.right)):
            return (
                f'({{ let __a = ({left}).to_string(); '
                f'let __b = ({right}).to_string(); '
                f'let __n = __a.chars().count().max(__b.chars().count()); '
                f'format!("{{:<width$}}", __a, width = __n) {rust_op} '
                f'format!("{{:<width$}}", __b, width = __n) }})'
            )
        return f"{left} {rust_op} {right}"

    def _float_precision(self, node):
        """'double' / 'single' / None for a scalar expression's precision, used
        to promote mixed-precision arithmetic. Literals return None (they adapt
        to context in Rust)."""
        if isinstance(node, IdentifierNode):
            ts = self._var_info.get(node.name.lower())
            if ts is None:
                return None
            if ts.data_type == DataType.SCALAR:
                return "double" if ts.precision == Precision.DOUBLE else "single"
            if ts.data_type in (DataType.VECTOR, DataType.MATRIX):
                return "single"
            return None
        if isinstance(node, UnaryOpNode):
            return self._float_precision(node.operand)
        if isinstance(node, BinaryOpNode) and node.operator in (
                "+", "-", "*", "/"):
            lp = self._float_precision(node.left)
            rp = self._float_precision(node.right)
            if lp == "double" or rp == "double":
                return "double"
            if lp == "single" or rp == "single":
                return "single"
        return None

    def _numeric_kind(self, node) -> str:
        """Best-effort numeric classification of an expression: 'int', 'float',
        or 'unknown'. Drives mixed-type promotion casts. VECTOR/MATRIX count as
        'float' (their element type is f32)."""
        if isinstance(node, LiteralNode):
            lt = (node.literal_type or "").upper()
            if lt in ("INTEGER", "BIT"):
                return "int"
            if lt == "SCALAR":
                return "float"
            if isinstance(node.value, bool):
                return "unknown"
            if isinstance(node.value, int):
                return "int"
            if isinstance(node.value, float):
                return "float"
            return "unknown"
        if isinstance(node, IdentifierNode):
            ts = self._var_info.get(node.name.lower())
            if ts is None:
                return "unknown"
            if ts.data_type in (DataType.INTEGER, DataType.BIT):
                return "int"
            if ts.data_type in (DataType.SCALAR, DataType.VECTOR,
                                DataType.MATRIX):
                return "float"
            return "unknown"
        if isinstance(node, UnaryOpNode):
            return self._numeric_kind(node.operand)
        if isinstance(node, BinaryOpNode):
            op = (node.operator or "").upper()
            if op in ("+", "-", "*", "/", "**", "MOD"):
                lk = self._numeric_kind(node.left)
                rk = self._numeric_kind(node.right)
                if lk == "float" or rk == "float":
                    return "float"
                if lk == "int" and rk == "int":
                    return "int"
            return "unknown"
        if isinstance(node, FunctionCallNode):
            nm = node.name.upper()
            if nm in ("ABVAL", "DOT", "SCALAR", "DETERM", "TRACE") or nm in self._MATH_METHODS \
                    or nm == "ARCTAN2":
                return "float"
            if nm == "INTEGER":
                return "int"
        return "unknown"

    def _is_integer_operand(self, node) -> bool:
        """Best-effort: is this expression an integer (INTEGER/BIT) rather than
        a float? Used to pick .pow vs .powi for the ** operator."""
        if isinstance(node, LiteralNode):
            return (isinstance(node.value, int)
                    and (node.literal_type or "").upper() in ("INTEGER", "BIT"))
        if isinstance(node, IdentifierNode):
            ts = self._var_info.get(node.name.lower())
            return ts is not None and ts.data_type in (
                DataType.INTEGER, DataType.BIT)
        return False

    def _generate_unary_op(self, node: UnaryOpNode) -> str:
        """Generate a unary operation. NOT -> bitwise/logical ! in Rust."""
        operand = self._generate_expression(node.operand)
        op = (node.operator or "").upper()
        if op == "NOT":
            return f"!({operand})"
        if node.operator in ("-", "+"):
            return f"{node.operator}({operand})"
        return f"/* unary {node.operator} */({operand})"

    def _generate_subscript(self, node: SubscriptNode) -> str:
        """Generate a subscript X$k.

        On a VECTOR: element access (1-indexed in HAL/S -> 0-indexed array).
        On a BIT register: MSB-first, 1-indexed bit extract (x >> (w-k)) & 1.
        The index may be a literal or a runtime expression.
        """
        operand = self._generate_expression(node.operand)

        # N-D subscript X$(i, j[, ...]): nested 1-indexed array access.
        if isinstance(node.index, IndexListNode):
            out = operand
            # MATRIX/VECTOR are tuple structs -> index through .0; a multi-dim
            # ARRAY is a plain nested array and needs no .0.
            ts0 = None
            if isinstance(node.operand, IdentifierNode):
                ts0 = self._var_info.get(node.operand.name.lower())
            if (ts0 is not None and ts0.array_size is None
                    and ts0.data_type in (DataType.MATRIX, DataType.VECTOR)):
                out += ".0"
            for ix in node.index.indices:
                if isinstance(ix, LiteralNode) and isinstance(ix.value, int):
                    out += f"[{ix.value - 1}]"
                elif isinstance(ix, int):
                    out += f"[{ix - 1}]"
                else:
                    out += f"[({self._generate_expression(ix)} - 1) as usize]"
            return out

        data_type = None
        ts = None
        if isinstance(node.operand, IdentifierNode):
            ts = self._var_info.get(node.operand.name.lower())
            if ts is not None:
                data_type = ts.data_type

        # Range subscript X$(lo TO hi): CHARACTER -> substring; BIT -> bit slice.
        if isinstance(node.index, RangeNode):
            if data_type == DataType.CHARACTER:
                return self._generate_char_slice(operand, node.index)
            return self._generate_bit_slice(node.operand, operand, node.index)

        idx = node.index
        if isinstance(idx, int):
            idx_src, idx_const = str(idx), idx
        elif isinstance(idx, LiteralNode) and isinstance(idx.value, int):
            idx_src, idx_const = str(idx.value), idx.value
        else:
            idx_src, idx_const = self._generate_expression(idx), None

        # ARRAY(n) OF ... element access, 1-indexed -> 0-indexed.
        if ts is not None and ts.array_size is not None:
            if idx_const is not None:
                return f"{operand}[{idx_const - 1}]"
            return f"{operand}[({idx_src} - 1) as usize]"

        if data_type == DataType.VECTOR:
            # 1-indexed element access into the underlying [f32; N] array.
            if idx_const is not None:
                return f"{operand}.0[{idx_const - 1}]"
            return f"{operand}.0[({idx_src} - 1) as usize]"

        # Default: bit extract on a BIT-typed operand. Width is derived from
        # the operand itself so nested subscripts A$I$K use the array
        # element's BIT(w), not a 32-bit fallback.
        width = self._operand_bit_width(node.operand)
        if idx_const is not None:
            return f"(({operand} >> {width - idx_const}) & 1)"
        return f"(({operand} >> ({width} - {idx_src})) & 1)"

    def _operand_bit_width(self, node) -> int:
        """BIT width of a value expression, defaulting to 32 for unknowns.

        Handles bare BIT identifiers (via _bit_widths, populated at pre-pass)
        and element access of an ARRAY(N) OF BIT(w) (via _var_info + the
        array's declared element_type). Without the array-element branch a
        nested subscript like FLAGS$I$K would default to 32 and mis-compute
        MSB-first bit positions on a narrower element."""
        if isinstance(node, IdentifierNode):
            return self._bit_widths.get(node.name.lower(), 32)
        if isinstance(node, SubscriptNode) and isinstance(
                node.operand, IdentifierNode):
            ts = self._var_info.get(node.operand.name.lower())
            if (ts is not None and ts.array_size is not None
                    and ts.element_type is not None
                    and ts.element_type.data_type == DataType.BIT
                    and ts.element_type.dimensions):
                return ts.element_type.dimensions[0]
        return 32

    def _const_int(self, node) -> Optional[int]:
        """Return an int if node is an integer literal, else None."""
        if isinstance(node, int):
            return node
        if isinstance(node, LiteralNode) and isinstance(node.value, int):
            return node.value
        return None

    def _generate_char_slice(self, operand_src, rng) -> str:
        """X$(lo TO hi) on a CHARACTER value -> substring, 1-indexed inclusive.

        HAL/S chars 1..N map to Rust byte indices [lo-1 .. hi]. CHARACTER is
        ASCII-oriented, so byte slicing is appropriate here.
        """
        lo = self._const_int(rng.lo)
        hi = self._const_int(rng.hi)
        if lo is not None and hi is not None:
            return f"{operand_src}[{lo - 1}..{hi}].to_string()"
        lo_s = self._generate_expression(rng.lo)
        hi_s = self._generate_expression(rng.hi)
        return (f"{operand_src}[(({lo_s}) as usize - 1).."
                f"(({hi_s}) as usize)].to_string()")

    def _generate_bit_slice(self, operand_node, operand_src, rng) -> str:
        """X$(lo TO hi) — extract a contiguous bit field, MSB-first, 1-indexed.

        For a BIT(W) register, bit 1 is the MSB. The slice [lo..hi] occupies
        the bits right-shifted by (W - hi), masked to (hi - lo + 1) bits.
        """
        width = self._operand_bit_width(operand_node)
        lo = self._const_int(rng.lo)
        hi = self._const_int(rng.hi)
        if lo is not None and hi is not None:
            shift = width - hi
            mask = (1 << (hi - lo + 1)) - 1
            return f"(({operand_src} >> {shift}) & 0x{mask:X})"
        lo_s = self._generate_expression(rng.lo)
        hi_s = self._generate_expression(rng.hi)
        bt = TypeMapper._bit_type(width)
        return (
            f"(({operand_src} >> ({width} - ({hi_s}))) "
            f"& (((1 as {bt}) << (({hi_s}) - ({lo_s}) + 1)) - 1))"
        )

    def _struct_name(self, name: str) -> str:
        """HAL/S template name -> Rust PascalCase struct name (NODE -> Node)."""
        return "".join(w.capitalize() for w in name.split("_"))

    def _elem_struct_name(self, type_name: str, layout: str) -> str:
        """Rust struct name for an array element: the packed companion for a
        DENSE array of a dense-used template, else the aligned struct."""
        base = self._struct_name(type_name or "")
        if layout == "dense" and (type_name or "").upper() in self._dense_structs:
            return base + "Packed"
        return base

    def _generate_structure_def(self, node: StructureDefNode) -> None:
        """Emit a STRUCTURE template as a Rust struct plus a zero-init new().

        The aligned form is #[repr(C)] (HAL/S's default ALIGNED layout: fields
        padded to natural boundaries for CPU/DMA access). If the template is
        used in any DENSE array, also emit a #[repr(C, packed)] companion named
        <Name>Packed for end-to-end storage layout."""
        self._emit_struct(node, self._struct_name(node.name),
                          repr_attr="#[repr(C)]",
                          derives="Clone, Copy, Debug")
        if node.name.upper() in self._dense_structs:
            self._emit_struct(node, self._struct_name(node.name) + "Packed",
                              repr_attr="#[repr(C, packed)]",
                              # no Debug: deriving it on a packed struct needs
                              # unaligned reads and is rejected/lints on rustc.
                              derives="Clone, Copy")

    def _emit_struct(self, node: StructureDefNode, sname: str,
                     repr_attr: str, derives: str) -> None:
        """Emit one Rust struct (given name/repr/derives) with a zero-init new()."""
        self.emitter.emit_comment(f"HAL/S Structure template: {node.name}")
        self.emitter.emit_line(repr_attr)
        self.emitter.emit_line(f"#[derive({derives})]")
        self.emitter.emit_line(f"struct {sname} {{")
        self.emitter.indent()
        for decl in node.fields:
            rust_type = self.type_mapper.map_type(decl.type_spec)
            for ident in decl.identifiers:
                self.emitter.emit_line(
                    f"{self._rust_ident(ident)}: {rust_type},")
        self.emitter.dedent()
        self.emitter.emit_line("}")
        self.emitter.emit_line(f"impl {sname} {{")
        self.emitter.indent()
        self.emitter.emit_line("fn new() -> Self {")
        self.emitter.indent()
        inits = []
        for decl in node.fields:
            default = self.type_mapper.get_default_value(decl.type_spec)
            for ident in decl.identifiers:
                inits.append(f"{self._rust_ident(ident)}: {default}")
        self.emitter.emit_line(f"{sname} {{ {', '.join(inits)} }}")
        self.emitter.dedent()
        self.emitter.emit_line("}")
        self.emitter.dedent()
        self.emitter.emit_line("}")

    def _is_dense_field(self, node) -> bool:
        """True if `node` is a field access on an element of a DENSE (packed)
        array -- i.e. reading/writing it must go through un-aligned pointer ops
        rather than a normal reference."""
        if not isinstance(node, FieldAccessNode):
            return False
        base = node.operand
        if isinstance(base, SubscriptNode):
            base = base.operand
        if isinstance(base, IdentifierNode):
            ts = self._var_info.get(base.name.lower())
            return ts is not None and getattr(ts, "layout", "aligned") == "dense"
        return False

    def _struct_field_names(self, type_name: str) -> list:
        """Ordered Rust-safe field names of a STRUCTURE template. Used to
        emit field-by-field cross-layout copies, so each name lands in
        emitted Rust and must be raw-escaped if it collides with a keyword."""
        sdef = self._structs.get((type_name or "").upper())
        if sdef is None:
            return []
        return [self._rust_ident(ident)
                for decl in sdef.fields for ident in decl.identifiers]

    def _generate_cross_layout_copy(self, tgt: str, src: str,
                                    tgt_ts, src_ts) -> None:
        """Copy an ARRAY OF STRUCTURE between differing layouts (ALIGNED <->
        DENSE) element-by-element. The two arrays are distinct Rust types
        (padded vs packed), so a whole-array assignment won't type-check; and
        packed fields must be touched via un-aligned pointer ops."""
        n = tgt_ts.array_size
        tname = tgt_ts.element_type.type_name if tgt_ts.element_type else None
        fields = self._struct_field_names(tname)
        tgt_dense = getattr(tgt_ts, "layout", "aligned") == "dense"
        src_dense = getattr(src_ts, "layout", "aligned") == "dense"
        self.emitter.emit_line(f"for __i in 0..{n} {{")
        self.emitter.indent()
        self.emitter.emit_line(f"let __s = {src}[__i];")
        self.emitter.emit_line("unsafe {")
        self.emitter.indent()
        for f in fields:
            rhs = (f"core::ptr::addr_of!(__s.{f}).read_unaligned()"
                   if src_dense else f"__s.{f}")
            if tgt_dense:
                self.emitter.emit_line(
                    f"core::ptr::addr_of_mut!({tgt}[__i].{f})"
                    f".write_unaligned({rhs});")
            else:
                self.emitter.emit_line(f"{tgt}[__i].{f} = {rhs};")
        self.emitter.dedent()
        self.emitter.emit_line("}")
        self.emitter.dedent()
        self.emitter.emit_line("}")

    def _generate_field_access(self, node: FieldAccessNode) -> str:
        """Structure field access: operand.field. A field of a DENSE (packed)
        array element is read via read_unaligned (a plain reference into a
        packed field is rejected by the borrow checker)."""
        operand = self._generate_expression(node.operand)
        field = self._rust_ident(node.field_name)
        if self._is_dense_field(node):
            return (f"unsafe {{ core::ptr::addr_of!({operand}.{field})"
                    f".read_unaligned() }}")
        return f"{operand}.{field}"

    def _generate_vector_literal(self, node: VectorLiteralNode) -> str:
        """VECTOR(v1, v2, ...) -> Vector([v1, v2, ...])."""
        vals = ", ".join(self._generate_expression(v) for v in node.values)
        return f"Vector([{vals}])"

    def _is_vector_expr(self, node) -> bool:
        """True if an expression yields a Vector (used to build a MATRIX from
        vector rows rather than scalar cells)."""
        if isinstance(node, VectorLiteralNode):
            return True
        if isinstance(node, IdentifierNode):
            ts = self._var_info.get(node.name.lower())
            return ts is not None and ts.data_type == DataType.VECTOR
        if isinstance(node, FunctionCallNode):
            if node.name.upper() in ("CROSS", "UNIT", "VECTOR"):
                return True
        return False

    def _generate_matrix_literal(self, node: MatrixLiteralNode,
                                 dims=None) -> str:
        """MATRIX(v1..vn) -> Matrix([[..],[..],..]). Dimensions come from the
        assignment/declaration target when known, else a square is inferred."""
        vals = [self._generate_expression(v) for v in node.values]
        n = len(vals)
        # MATRIX(v1, v2, ...) from VECTOR rows: each vector's backing array is a
        # row. Vector<C>.0 is [f32; C], so [v1.0, v2.0, ...] is [[f32;C];R].
        if node.values and all(self._is_vector_expr(v) for v in node.values):
            rows = [f"({self._generate_expression(v)}).0" for v in node.values]
            return f"Matrix([{', '.join(rows)}])"
        if dims and len(dims) >= 2:
            rows, cols = dims[0], dims[1]
        else:
            import math
            side = int(math.isqrt(n))
            rows = cols = side if side * side == n else 1
            if rows == 1:
                cols = n
        chunks = [vals[i * cols:(i + 1) * cols] for i in range(rows)]
        body = ", ".join("[" + ", ".join(row) + "]" for row in chunks)
        return f"Matrix([{body}])"

    def _generate_matrix_subscript(self, node: MatrixSubscriptNode) -> str:
        """M(<row>, <col>) -> row/column (Vector), element (f32), or submatrix
        (Matrix, constant ranges only)."""
        operand = self._rust_ident(node.name)
        if len(node.args) != 2:
            return f"/* unsupported matrix subscript arity {len(node.args)} */"
        row_spec, col_spec = node.args

        def is_star(a): return isinstance(a, StarNode)
        def is_range(a): return isinstance(a, RangeNode)

        # Row r, all columns -> row vector (1-indexed).
        if not is_star(row_spec) and not is_range(row_spec) and is_star(col_spec):
            r = self._generate_expression(row_spec)
            return f"{operand}.row({r})"
        # All rows, column c -> column vector.
        if is_star(row_spec) and not is_star(col_spec) and not is_range(col_spec):
            c = self._generate_expression(col_spec)
            return f"{operand}.col({c})"
        # Constant ranges -> submatrix built inline.
        if is_range(row_spec) and is_range(col_spec):
            r1 = self._const_int(row_spec.lo); r2 = self._const_int(row_spec.hi)
            c1 = self._const_int(col_spec.lo); c2 = self._const_int(col_spec.hi)
            if None not in (r1, r2, c1, c2):
                rows = []
                for i in range(r1, r2 + 1):
                    cells = [f"{operand}.0[{i - 1}][{j - 1}]"
                             for j in range(c1, c2 + 1)]
                    rows.append("[" + ", ".join(cells) + "]")
                return f"Matrix([{', '.join(rows)}])"
            return "/* submatrix requires constant ranges */"
        # Element access M(r, c).
        if not is_star(row_spec) and not is_range(row_spec) \
                and not is_star(col_spec) and not is_range(col_spec):
            r = self._generate_expression(row_spec)
            c = self._generate_expression(col_spec)
            return f"{operand}.0[({r}) as usize - 1][({c}) as usize - 1]"
        return "/* unsupported matrix subscript form */"

    def _generate_for(self, node: ForNode, label: str = "") -> None:
        """Generate a DO FOR loop that mutates the declared index variable.

        Rust `for i in …` would shadow the HAL/S INTEGER `I`, so after EXIT
        (or normal completion) the declared `I` would still be its pre-loop
        value. A `loop` that assigns the existing binding matches HAL/S:
        after EXIT, I is the current index; after falling out, I is one step
        past the last value. A `label` becomes a Rust loop label for
        `EXIT <label>`.

        Optional UNTIL: skipped on the first cycle, then tested after the
        bound check (USA003088 §7.6.5). An empty range still never enters.
        """
        var = self._rust_ident(node.var)
        start = self._generate_expression(node.start)
        end = self._generate_expression(node.end)
        step = (self._generate_expression(node.step)
                if node.step is not None else "1")
        lbl = f"'{self._rust_ident(label)}: " if label else ""
        has_until = node.until is not None
        until_src = (
            self._generate_expression(node.until) if has_until else None)
        self.emitter.emit_line(f"{var} = ({start}) as i32;")
        if has_until:
            self.emitter.emit_line("let mut __first = true;")
        self.emitter.emit_line(f"{lbl}loop {{")
        self.emitter.indent()
        if node.descending:
            self.emitter.emit_line(
                f"if {var} < ({end}) as i32 {{ break; }}")
        else:
            self.emitter.emit_line(
                f"if {var} > ({end}) as i32 {{ break; }}")
        if has_until:
            self.emitter.emit_line(
                f"if !__first && ({until_src}) {{ break; }}")
            self.emitter.emit_line("__first = false;")
        for stmt in node.statements:
            self._generate_node(stmt)
        if node.descending:
            self.emitter.emit_line(
                f"{var} = {var}.wrapping_sub(({step}) as i32);")
        else:
            self.emitter.emit_line(
                f"{var} = {var}.wrapping_add(({step}) as i32);")
        self.emitter.dedent()
        self.emitter.emit_line("}")

    def _operand(self, node: ASTNode, parent_prec: int, right_side: bool) -> str:
        """Render a sub-expression, parenthesizing only when precedence needs it."""
        rendered = self._generate_expression(node)
        if isinstance(node, BinaryOpNode) and node.operator != "**":
            child_prec = self._PRECEDENCE.get(node.operator, 0)
            # Lower precedence always needs parens; equal precedence needs
            # parens on the right for left-associative operators.
            if child_prec < parent_prec or (
                child_prec == parent_prec and right_side
            ):
                return f"({rendered})"
        return rendered

    def _generate_function_call(self, node: FunctionCallNode) -> str:
        """Generate a Rust function call, e.g. ABVAL(V) -> abval(v).

        LENGTH(s) is the string-length intrinsic -> s.len() as i32.
        """
        if node.name.upper() == "LENGTH" and len(node.arguments) == 1:
            arg = self._generate_expression(node.arguments[0])
            # HAL/S LENGTH excludes trailing blanks.
            return (
                f"({arg}.trim_end_matches(' ').chars().count() as i32)"
            )
        # Type-coercion casts: INTEGER(x) / SCALAR(x).
        if node.name.upper() == "INTEGER" and len(node.arguments) == 1:
            arg = self._generate_expression(node.arguments[0])
            return f"(({arg}) as i32)"
        if node.name.upper() == "SCALAR" and len(node.arguments) == 1:
            arg = self._generate_expression(node.arguments[0])
            return f"(({arg}) as f32)"
        # Scalar math built-ins -> f32 methods, e.g. SQRT(x) -> (x).sqrt().
        # Cast an integer argument to f32 since these methods are f32-only.
        method = self._MATH_METHODS.get(node.name.upper())
        if method is not None and len(node.arguments) == 1:
            arg = self._generate_expression(node.arguments[0])
            if self._numeric_kind(node.arguments[0]) == "int":
                arg = f"({arg}) as f32"
            return f"({arg}).{method}()"
        # ARCTAN2(y, x) -> (y).atan2(x).
        if node.name.upper() == "ARCTAN2" and len(node.arguments) == 2:
            y = self._generate_expression(node.arguments[0])
            x = self._generate_expression(node.arguments[1])
            return f"({y}).atan2({x})"
        # SHIFT(val, n): HAL/S signed shift -- n < 0 shifts right, n > 0 left.
        # A compile-time-known sign lowers to a direct >> / <<; a runtime count
        # branches at runtime.
        if node.name.upper() == "SHIFT" and len(node.arguments) == 2:
            val = self._generate_expression(node.arguments[0])
            direction, mag = self._shift_direction(node.arguments[1])
            if direction == "right":
                return f"({val} >> {mag})"
            if direction == "left":
                return f"({val} << {mag})"
            n = self._generate_expression(node.arguments[1])
            return (f"{{ let __n = ({n}) as i32; "
                    f"if __n >= 0 {{ {val} << __n }} "
                    f"else {{ {val} >> (-__n) }} }}")
        # If the name is a declared MATRIX/VECTOR, parenthesized indices are
        # element access (1-indexed), not a function call.
        ts = self._var_info.get(node.name.lower())
        if ts is not None:
            base = self._var_base(node.name)
            # Plain ARRAY element access: A(i) -> a[(i) as usize - 1],
            # A(i,j) -> a[..][..]. 1-indexed HAL/S -> 0-indexed Rust.
            if ts.array_size is not None and len(node.arguments) >= 1:
                out = base
                for a in node.arguments:
                    ci = self._const_int(a)
                    if ci is not None:
                        out += f"[{ci - 1}]"
                    else:
                        out += f"[({self._generate_expression(a)}) as usize - 1]"
                return out
            if ts.data_type == DataType.MATRIX and len(node.arguments) == 2:
                r = self._generate_expression(node.arguments[0])
                c = self._generate_expression(node.arguments[1])
                return (f"{base}.0[({r}) as usize - 1]"
                        f"[({c}) as usize - 1]")
            if ts.data_type == DataType.VECTOR and len(node.arguments) == 1:
                i = self._generate_expression(node.arguments[0])
                return f"{base}.0[({i}) as usize - 1]"
        args = ", ".join(self._generate_expression(a) for a in node.arguments)
        rust_name = self._rust_ident(node.name)
        # NONHAL external calls cross the FFI boundary and are unsafe to call.
        if node.name.lower() in getattr(self, "_extern_fns", set()):
            return f"unsafe {{ {rust_name}({args}) }}"
        return f"{rust_name}({args})"

    def _generate_extern(self, node) -> None:
        """Emit a body-less procedure/function as an `extern "C"` declaration so
        the (separately compiled) implementation resolves at link time. Used for
        NONHAL FFI functions and for TEMPLATE procedures that are CALLed:
        inputs pass by value, ASSIGN outputs by &mut, matching the call site."""
        name = self._rust_ident(node.name)
        ptypes = {}
        for d in node.statements:
            if isinstance(d, DeclareNode):
                for ident in d.identifiers:
                    ptypes[ident.lower()] = d.type_spec
        parts = [
            f"{self._rust_ident(pn)}: "
            f"{self._full_rust_type(ptypes.get(pn.lower()))}"
            for pn in node.parameters
        ]
        parts += [
            f"{self._rust_ident(an)}: "
            f"&mut {self._full_rust_type(ptypes.get(an.lower()))}"
            for an in node.assign_params
        ]
        ret = (f" -> {self._full_rust_type(node.return_type)}"
               if node.return_type is not None else "")
        self.emitter.emit_line(
            f'extern "C" {{ fn {name}({", ".join(parts)}){ret}; }}')

    def _full_rust_type(self, ts) -> str:
        """Complete Rust type including array dimensions, e.g.
        ARRAY(3) OF VECTOR(3) -> [Vector<3>; 3]. map_type alone collapses an
        array to its element type, which is wrong for a parameter signature."""
        if (ts is not None and ts.array_dims is not None
                and ts.element_type is not None):
            inner = self._full_rust_type(ts.element_type)
            for d in reversed(ts.array_dims):
                inner = f"[{inner}; {d}]"
            return inner
        return self.type_mapper.map_type(ts)

    def _var_base(self, name: str) -> str:
        """Base accessor for a variable used as a subscript/element operand:
        a locked deref for mutable COMPOOL globals, the bare name for global
        constants, an ASSIGN-param deref, else snake_case (Rust-keyword-safe)."""
        up = name.upper()
        if up in getattr(self, "_compool_guard", {}):
            return f"(*{self._compool_guard[up]})"
        if up in self._globals:
            kind, _ = self._globals[up]
            return up if kind == "const" else f"(*{up}.lock().unwrap())"
        if name.lower() in getattr(self, "_assign_params", set()):
            return f"(*{self._rust_ident(name)})"
        return self._rust_ident(name)

    def _generate_if(self, node: IfNode) -> None:
        """Generate an IF / THEN / ELSE statement."""
        condition = self._generate_expression(node.condition)
        self.emitter.emit_line(f"if {condition} {{")
        self.emitter.indent()
        self._generate_branch_body(node.then_block)
        self.emitter.dedent()
        if node.else_block is not None:
            self.emitter.emit_line("} else {")
            self.emitter.indent()
            self._generate_branch_body(node.else_block)
            self.emitter.dedent()
        self.emitter.emit_line("}")

    def _generate_branch_body(self, node: Optional[ASTNode]) -> None:
        """Emit the body of an IF branch (a DO block's statements, or one stmt)."""
        if isinstance(node, DoBlockNode):
            for stmt in node.statements:
                self._generate_node(stmt)
        elif node is not None:
            self._generate_node(node)

    def _generate_do_block(self, node: DoBlockNode) -> None:
        """Generate a standalone DO; ... END; group as a Rust block."""
        self.emitter.emit_line("{")
        self.emitter.indent()
        for stmt in node.statements:
            self._generate_node(stmt)
        self.emitter.dedent()
        self.emitter.emit_line("}")

    def _generate_schedule(self, node: ScheduleNode) -> None:
        """Generate a SCHEDULE statement.
\n
        Timed/cyclic form (SCHEDULE T IN d [REPEAT EVERY p]) spawns a thread
        that sleeps for the initial delay, then either runs once or repeats on
        the period until a per-task cancel flag is set. Priority form keeps the
        existing fire-and-join model.
        """
        task_fn = node.task_name.lower()

        if self.spec.embedded:
            self._generate_schedule_embedded(node)
            return

        if node.delay is not None:
            # Timed/cyclic: self-managed thread + cancel flag (for CANCEL).
            delay = self._generate_expression(node.delay)
            cflag = f"__cancel_{task_fn}"
            handle = f"__h_{task_fn}"
            self.emitter.emit_comment(
                f"SCHEDULE {node.task_name} IN {delay}"
                + (f" REPEAT EVERY {self._generate_expression(node.period)}"
                   if node.period is not None else "")
            )
            self.emitter.emit_line(
                f"let {cflag} = std::sync::Arc::new("
                "std::sync::atomic::AtomicBool::new(false));"
            )
            self.emitter.emit_line(
                f"let {cflag}_t = std::sync::Arc::clone(&{cflag});"
            )
            self.emitter.emit_line(f"let {handle} = std::thread::spawn(move || {{")
            self.emitter.indent()
            self.emitter.emit_line(
                f"std::thread::sleep(std::time::Duration::from_secs_f64("
                f"{delay} as f64));"
            )
            if node.period is not None:
                period = self._generate_expression(node.period)
                self.emitter.emit_line(
                    f"while !{cflag}_t.load(std::sync::atomic::Ordering::Relaxed) {{"
                )
                self.emitter.indent()
                self.emitter.emit_line(f"{task_fn}();")
                self.emitter.emit_line(
                    f"std::thread::sleep(std::time::Duration::from_secs_f64("
                    f"{period} as f64));"
                )
                self.emitter.dedent()
                self.emitter.emit_line("}")
            else:
                self.emitter.emit_line(
                    f"if !{cflag}_t.load(std::sync::atomic::Ordering::Relaxed) "
                    f"{{ {task_fn}(); }}"
                )
            self.emitter.dedent()
            self.emitter.emit_line("});")
            return

        priority = (
            self._generate_expression(node.priority)
            if node.priority is not None
            else "0"
        )
        if node.period is not None:
            # Cyclic-at-priority: host build runs one representative cycle.
            self.emitter.emit_comment(
                f"SCHEDULE {node.task_name} PRIORITY({priority}) REPEAT EVERY "
                f"{self._generate_expression(node.period)} "
                "(period advisory on host; runs one cycle)"
            )
        if task_fn in self._task_names:
            self.emitter.emit_line(
                f'__tasks.push(schedule("{node.task_name}", {priority}, {task_fn}));'
            )
        else:
            self.emitter.emit_line(
                f'schedule_pending("{node.task_name}", {priority});'
            )

    def _generate_schedule_embedded(self, node: ScheduleNode) -> None:
        """SCHEDULE on bare metal: arm the task's executive slot.

        HAL/S times are seconds; the executive ticks in microseconds
        (design decision 1), so delay/period convert at the arming site.
        """
        known = {n.lower() for n in getattr(self, "_exec_tasks", [])}
        if node.task_name.lower() not in known:
            self._emit_debug_print(
                f"[hal_exec] SCHEDULE {node.task_name} (task undefined here)")
            return
        slot = f"__SLOT_{node.task_name.upper()}"
        us = "1_000_000.0"
        delay = (f"((({self._generate_expression(node.delay)}) as f64 * {us}) as u64)"
                 if node.delay is not None else "0u64")
        period = (f"((({self._generate_expression(node.period)}) as f64 * {us}) as u64)"
                  if node.period is not None else "0u64")
        prio = (f"(({self._generate_expression(node.priority)}) as i32)"
                if node.priority is not None else "0i32")
        self.emitter.emit_comment(f"SCHEDULE {node.task_name}")
        self.emitter.emit_line(
            f"hal_exec::arm(&{slot}, __exec_now_us(), {delay}, {period}, {prio});")

    def _emit_debug_print(self, msg: str) -> None:
        """One line on the target's console sink."""
        if self.spec.write_sink == "hprintln":
            self.emitter.emit_line(f'hprintln!("{msg}");')
        else:
            self.emitter.emit_line(f'println!("{msg}");')

    def _generate_wait(self, node: WaitNode) -> None:
        """WAIT UNTIL <cond>; -> spin-wait until the condition holds.
        WAIT <event>; -> host stub (advisory; does NOT block, does NOT model
        latched/unlatched semantics -- see RTOS backend for faithful behavior).

        For WAIT UNTIL: faithful to HAL/S blocking semantics: if nothing ever
        makes the condition true, this blocks indefinitely (as a real executive
        would, pending an interrupt or another task clearing the condition).
        """
        if node.event is not None:
            ev = node.event
            self.emitter.emit_comment(
                f"WAIT {ev} (host stub: advisory, non-blocking)")
            self.emitter.emit_line(
                f'eprintln!("[hal_rt] WAIT {ev} '
                '(advisory host stub; not real-time, does not block)");')
            if ev.lower() in getattr(self, "_event_vars", set()):
                self.emitter.emit_line(
                    f"let _ = {ev.upper()}"
                    ".load(core::sync::atomic::Ordering::SeqCst);")
            else:
                self.emitter.emit_line(f"let _ = {ev.lower()};")
            return
        cond = self._generate_expression(node.condition)
        self.emitter.emit_comment("WAIT UNTIL <cond> (blocks until satisfied)")
        self.emitter.emit_line(f"while !({cond}) {{")
        self.emitter.indent()
        self.emitter.emit_line(
            "std::thread::sleep(std::time::Duration::from_millis(1));"
        )
        self.emitter.dedent()
        self.emitter.emit_line("}")

    def _generate_signal(self, node: SignalNode) -> None:
        """SIGNAL / SET / RESET -> atomic event write OR bit-mask update
        on a BIT variable, depending on shape:

          Event form (no `bits`):
            * SIGNAL: latched set (host stub -- a real unlatched signal
              would pulse and be lost with no waiter; RTOS backend gives
              real-time-faithful behaviour).
            * SET:    latched set (state-based, explicit).
            * RESET:  latched clear.

          Bit-list form (`bits` populated):
            * SET B(i, j, ...):   B |= (1 << (w - k))   for each k
            * RESET B(i, j, ...): B &= !(1 << (w - k))  for each k
          where `w` is B's declared BIT width and the positions are
          1-indexed MSB-first (same convention as the $ bit-extract).

        Non-EVENT identifiers in the event form fall back to a plain
        assignment so a rebound BOOLEAN still works.
        """
        op = (getattr(node, "op", "SIGNAL") or "SIGNAL").upper()
        # Bit-list form takes priority: same source verb (SET/RESET) but
        # very different lowering when a bit list is present.
        bits = getattr(node, "bits", None)
        if bits:
            self._generate_bit_op(node, op)
            return
        # SIGNAL and SET both raise the flag; only RESET clears it.
        value_expr = "false" if op == "RESET" else "true"
        rust_value_word = "cleared" if op == "RESET" else "raised"
        ev = node.name.lower()
        self.emitter.emit_comment(
            f"{op} {node.name} (latched flag)")
        if ev in getattr(self, "_event_vars", set()):
            self.emitter.emit_line(
                f"{node.name.upper()}.store({value_expr}, "
                "core::sync::atomic::Ordering::SeqCst);")
        else:
            self.emitter.emit_line(f"{ev} = {value_expr};")
        # On embedded the latched atomic store IS the semantic; the advisory
        # notice is host-stub noise only.
        if not self.spec.embedded:
            self.emitter.emit_line(
                f'eprintln!("[hal_rt] {op} {node.name} '
                f'(flag {rust_value_word}; advisory host stub, not real-time)");')

    def _generate_bit_op(self, node: SignalNode, op: str) -> None:
        """SET/RESET on a BIT variable with an explicit position list.
        Each listed position is 1-indexed MSB-first; the emitted mask is
        `1 << (width - pos)`. Constant positions fold to a literal hex
        mask (`|= 0x8000u16;`); runtime positions emit a live shift."""
        var = self._rust_ident(node.name)
        ts = self._var_info.get(node.name.lower())
        # Resolve the BIT width used for MSB-first indexing; fall back to
        # the Rust type's default so we still emit valid code even if the
        # declaration wasn't captured (best-effort).
        width = self._bit_widths.get(node.name.lower())
        if width is None and ts is not None and ts.data_type == DataType.BIT \
                and ts.dimensions:
            width = ts.dimensions[0]
        if width is None:
            width = 32
        # Rust type of the mask -- matches the variable's declared width
        # so `var |= 0x8000u16;` type-checks without a cast.
        rust_type = (self.type_mapper.map_type(ts)
                     if ts is not None else "u32")
        self.emitter.emit_comment(
            f"{op} {node.name}(bit list) -- 1-indexed, MSB-first "
            f"on BIT({width})")
        for bit in node.bits or []:
            const = self._const_int(bit)
            if const is not None and 1 <= const <= width:
                mask = 1 << (width - const)
                if op == "SET":
                    self.emitter.emit_line(
                        f"{var} |= 0x{mask:X}{rust_type};")
                else:  # RESET
                    self.emitter.emit_line(
                        f"{var} &= !0x{mask:X}{rust_type};")
            else:
                # Runtime position (identifier or expression). Rust's `<<`
                # accepts any integer shift count and yields the LHS type;
                # keep the `1` typed so the mask width matches `var`.
                idx_src = self._generate_expression(bit)
                mask_expr = (
                    f"(1{rust_type} "
                    f"<< ({width} - ({idx_src}) as i32) as u32)"
                )
                if op == "SET":
                    self.emitter.emit_line(f"{var} |= {mask_expr};")
                else:  # RESET
                    self.emitter.emit_line(f"{var} &= !{mask_expr};")

    def _generate_cancel(self, node: CancelNode) -> None:
        """CANCEL <task>; -> host: set the task's cancel flag and join its
        thread. Embedded: idle the task's executive slot (no more cycles),
        same lowering as a named TERMINATE."""
        if self.spec.embedded:
            known = {n.lower() for n in getattr(self, "_exec_tasks", [])}
            self.emitter.emit_comment(f"CANCEL {node.task_name}")
            if node.task_name.lower() in known:
                self.emitter.emit_line(
                    f"hal_exec::idle(&__SLOT_{node.task_name.upper()});")
            else:
                self._emit_debug_print(
                    f"[hal_exec] CANCEL {node.task_name} (task undefined here)")
            return
        task_fn = node.task_name.lower()
        cflag = f"__cancel_{task_fn}"
        handle = f"__h_{task_fn}"
        self.emitter.emit_comment(f"CANCEL {node.task_name}")
        self.emitter.emit_line(
            f"{cflag}.store(true, std::sync::atomic::Ordering::Relaxed);"
        )
        self.emitter.emit_line(f"let _ = {handle}.join();")

    def _generate_terminate(self, node: TerminateNode) -> None:
        """Generate a TERMINATE statement.

        Bare TERMINATE ends the current thread/process (return). A named
        TERMINATE is a logged stub -- std::thread has no cancellation.
        """
        if self.spec.embedded:
            known = {n.lower() for n in getattr(self, "_exec_tasks", [])}
            if node.task_name:
                if node.task_name.lower() in known:
                    self.emitter.emit_comment(f"TERMINATE {node.task_name}")
                    self.emitter.emit_line(
                        f"hal_exec::idle(&__SLOT_{node.task_name.upper()});")
                else:
                    self._emit_debug_print(
                        f"[hal_exec] TERMINATE {node.task_name} "
                        "(task undefined here)")
                return
            cur = getattr(self, "_cur_task_name", None)
            if cur is not None:
                # spec 8.5 rule 2: bare TERMINATE = self-termination.
                self.emitter.emit_comment("TERMINATE (self)")
                self.emitter.emit_line(
                    f"hal_exec::idle(&__SLOT_{cur.upper()});")
                self.emitter.emit_line("return;")
            else:
                # In the program body: terminate the program AND its
                # dependents (all tasks) -- idle everything and halt.
                self.emitter.emit_comment(
                    "TERMINATE (program + dependents; spec 8.5)")
                if getattr(self, "_exec_tasks", []):
                    self.emitter.emit_line(
                        "for __s in __EXEC_TABLE.iter() "
                        "{ hal_exec::idle(__s); }")
                self.emitter.emit_line("loop {}")
            return
        if node.task_name:
            self.emitter.emit_comment(
                f"TERMINATE {node.task_name} (std::thread has no cancellation)"
            )
            self.emitter.emit_line(f'terminate("{node.task_name}");')
        else:
            self.emitter.emit_comment("TERMINATE -> end current thread/process")
            self.emitter.emit_line("return;")

    def write_to_file(self, filepath: str, code: str) -> None:
        """
        Write generated Rust code to file.
        
        Args:
            filepath: Output file path
            code: Rust source code to write
        """
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, 'w', encoding='utf-8') as f:
            f.write(code)

    def compile_to_executable(
        self,
        code: str,
        output_path: str,
        *,
        opt_level: str = "2",
        edition: str = "2021",
        target: Optional[str] = None,
        keep_source: bool = True,
        link_search: Optional[list] = None,
        link_libs: Optional[list] = None,
        link_objs: Optional[list] = None,
    ) -> "CompileResult":
        """
        Compile generated Rust source into a native executable with rustc.

        This writes a sibling .rs file next to ``output_path`` and invokes
        ``rustc`` on it. Requires a Rust toolchain on PATH (install via
        https://rustup.rs).

        Args:
            code: Rust source produced by ``generate()``.
            output_path: Path for the resulting executable.
            opt_level: rustc optimization level (0-3, "s", or "z").
            edition: Rust edition to compile against.
            target: Optional target triple for cross-compilation
                (e.g. "thumbv7em-none-eabihf"). Requires the target to be
                installed via ``rustup target add``.
            keep_source: Keep the intermediate .rs file (else delete on success).

        Returns:
            CompileResult with success flag, executable path, and rustc output.

        Raises:
            RuntimeError: If rustc is not found on PATH.
        """
        rustc = shutil.which("rustc")
        if rustc is None:
            raise RuntimeError(
                "rustc not found on PATH. Install the Rust toolchain via "
                "https://rustup.rs, then ensure 'rustc' is on PATH."
            )

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        src = out.with_suffix(".rs")
        src.write_text(code, encoding="utf-8")

        # A unit with no PROGRAM (e.g. a TEMPLATE / prototype-only file) emits
        # no `fn main`. Building it as a binary would fail with E0601; instead
        # validate it as a library so "does it compile?" still gets answered.
        has_main = "fn main" in code
        cmd = self._rustc_command(
            rustc, str(src), str(out), has_main=has_main, opt_level=opt_level,
            edition=edition, target=target, link_search=link_search,
            link_libs=link_libs, link_objs=link_objs)

        proc = subprocess.run(cmd, capture_output=True, text=True)
        success = proc.returncode == 0

        if success and not keep_source:
            src.unlink(missing_ok=True)

        return CompileResult(
            success=success,
            executable=str(out) if (success and has_main) else None,
            source=str(src) if (keep_source or not success) else None,
            stdout=proc.stdout,
            stderr=proc.stderr,
            library=not has_main,
        )

    @staticmethod
    def _rustc_command(
        rustc: str, src: str, out: str, *, has_main: bool, opt_level: str,
        edition: str, target: Optional[str] = None,
        link_search: Optional[list] = None, link_libs: Optional[list] = None,
        link_objs: Optional[list] = None,
    ) -> list:
        """Build the rustc argv. A unit with no `fn main` is compiled as a
        library (validated, not linked); EXTERNAL symbols are resolved via
        linker passthrough: -L (search), -l (library), -C link-arg (object)."""
        cmd = [rustc, src, "--edition", edition, "-C", f"opt-level={opt_level}"]
        if has_main:
            cmd += ["-o", out]
        else:
            cmd += ["--crate-type", "lib", "-o", out]
        if target:
            cmd += ["--target", target]
        for d in (link_search or []):
            cmd += ["-L", str(d)]
        for lib in (link_libs or []):
            cmd += ["-l", str(lib)]
        for obj in (link_objs or []):
            cmd += ["-C", f"link-arg={obj}"]
        return cmd

    @staticmethod
    def _sanitize_crate_name(raw: str) -> str:
        """Turn an arbitrary name into a valid Cargo crate name.

        Cargo permits hyphens, but we normalize to lowercase with underscores
        so the package name and the produced binary file name stay predictable.
        """
        name = re.sub(r"[^a-z0-9_]+", "_", raw.strip().lower()).strip("_")
        if not name:
            name = "program"
        if name[0].isdigit():
            name = "_" + name
        return name

    @staticmethod
    def _toml_opt_level(opt_level: str) -> str:
        """Render an opt-level for TOML: bare int for 0-3, quoted for s/z."""
        return opt_level if opt_level.isdigit() else f'"{opt_level}"'

    # The scaffold is *yours once it exists*: only src/main.rs is generated
    # output. Everything else is created if absent and then left alone, so
    # hand-added dependencies and edits survive recompiles.
    @staticmethod
    def _scaffold_file(path, content: str, force: bool, kept: list) -> None:
        path = Path(path)
        if path.exists() and not force:
            kept.append(str(path))
            return
        path.write_text(content, encoding="utf-8")

    # User I/O shims: HAL/S EXTERNAL symbols resolve against extern "C" items
    # in src/shim.rs. Created once; NEVER overwritten (not even with force).
    _SHIM_STUB = (
        '//! User I/O shims for the generated firmware.\n'
        '//!\n'
        '//! This file is yours: Resnik creates it once and never overwrites it\n'
        '//! (src/main.rs is regenerated on every compile; Cargo.toml and the\n'
        '//! other scaffold files are also left alone once they exist).\n'
        '//!\n'
        '//! HAL/S EXTERNAL procedures/variables link against #[no_mangle]\n'
        '//! extern "C" items defined here. Example — for the HAL/S prototype\n'
        '//!\n'
        '//!     READ_SENSOR: PROCEDURE(CH) EXTERNAL;\n'
        '//!       DECLARE CH INTEGER;\n'
        '//!     CLOSE READ_SENSOR;\n'
        '//!\n'
        '//! provide:\n'
        '//!\n'
        '//!     #[no_mangle]\n'
        '//!     pub extern "C" fn read_sensor(ch: i32) { /* ... */ }\n'
        '//!\n'
        '//! On the cortex-m profile, programs that use SCHEDULE/TASK also\n'
        '//! need a monotonic microsecond clock from this file (the esp32-c3\n'
        '//! profile reads its own hardware timer instead):\n'
        '//!\n'
        '//!     #[no_mangle]\n'
        '//!     pub extern "C" fn hal_exec_now_us() -> u64 { /* SysTick/DWT */ }\n'
        '#![allow(dead_code, unused_imports)]\n'
    )

    @staticmethod
    def _inject_shim_mod(code: str) -> str:
        """Add ``mod shim;`` to a generated main.rs (after the inner
        ``#![...]`` attributes, which must stay first)."""
        if "\nmod shim;" in code or code.startswith("mod shim;"):
            return code
        lines = code.split("\n")
        insert_at = 0
        for i, ln in enumerate(lines):
            if ln.startswith("#!["):
                insert_at = i + 1
        lines.insert(insert_at, "mod shim;")
        return "\n".join(lines)

    def emit_cargo_project(
        self,
        code: str,
        project_dir: str,
        *,
        name: Optional[str] = None,
        edition: str = "2021",
        version: str = "0.1.0",
        opt_level: str = "3",
        build: bool = False,
        release: bool = True,
        target: Optional[str] = None,
        force_scaffold: bool = False,
    ) -> "CargoProjectResult":
        """
        Scaffold a Cargo project around generated Rust source.

        Creates ``<project_dir>/Cargo.toml`` and ``<project_dir>/src/main.rs``,
        and optionally runs ``cargo build`` (use this once the output grows
        past a single file or needs dependencies / build profiles).

        Args:
            code: Rust source produced by ``generate()``.
            project_dir: Directory to create the project in.
            name: Crate name (defaults to the sanitized project dir name).
            edition: Rust edition for Cargo.toml.
            version: Package version for Cargo.toml.
            opt_level: opt-level written to the release profile.
            build: If True, run ``cargo build`` after scaffolding.
            release: Build with ``--release`` (else debug). Only used if build.
            target: Optional target triple for cross-compilation. Requires the
                target to be installed via ``rustup target add``.

        Returns:
            CargoProjectResult with the written paths and, if built, the
            build outcome and executable path.

        Raises:
            RuntimeError: If build is requested but cargo is not on PATH.
        """
        proj = Path(project_dir)
        src_dir = proj / "src"
        src_dir.mkdir(parents=True, exist_ok=True)

        crate_name = self._sanitize_crate_name(name or proj.name)
        kept: list = []

        if self.spec.embedded:
            triple = target or self.spec.triple
            if self.spec.entry == "cortex-m-rt":
                self._emit_cortex_m_scaffold(
                    proj, crate_name, version, edition, opt_level, triple,
                    force_scaffold, kept)
            elif self.spec.entry == "esp-hal":
                self._emit_esp32_c3_scaffold(
                    proj, crate_name, version, edition, opt_level, triple,
                    force_scaffold, kept)
            else:
                raise RuntimeError(
                    f"No cargo scaffold for target '{self.spec.name}' "
                    f"(entry={self.spec.entry!r})")
            shim_path = src_dir / "shim.rs"
            if not shim_path.exists():
                shim_path.write_text(self._SHIM_STUB, encoding="utf-8")
            main_path = src_dir / "main.rs"
            main_path.write_text(self._inject_shim_mod(code), encoding="utf-8")
            result = CargoProjectResult(
                project_dir=str(proj),
                cargo_toml=str(proj / "Cargo.toml"),
                main_rs=str(main_path),
                kept_files=kept,
            )
            if not build:
                return result
            return self._cargo_build(
                proj, crate_name, result, release=release, target=triple)

        cargo_toml = (
            "[package]\n"
            f'name = "{crate_name}"\n'
            f'version = "{version}"\n'
            f'edition = "{edition}"\n'
            "\n"
            "[profile.release]\n"
            f"opt-level = {self._toml_opt_level(opt_level)}\n"
        )
        toml_path = proj / "Cargo.toml"
        main_path = src_dir / "main.rs"
        self._scaffold_file(toml_path, cargo_toml, force_scaffold, kept)
        main_path.write_text(code, encoding="utf-8")

        result = CargoProjectResult(
            project_dir=str(proj),
            cargo_toml=str(toml_path),
            main_rs=str(main_path),
            kept_files=kept,
        )

        if not build:
            return result

        return self._cargo_build(
            proj, crate_name, result, release=release, target=target)

    def _emit_cortex_m_scaffold(self, proj, crate_name, version, edition,
                                opt_level, triple, force=False, kept=None) -> None:
        """Write a buildable bare-metal Cargo project for ARM Cortex-M:
        Cargo.toml with the embedded deps, .cargo/config.toml (target + linker
        args + probe-rs runner), a generic memory.x, and the cortex-m-rt
        build.rs that puts memory.x on the linker search path."""
        (proj / ".cargo").mkdir(parents=True, exist_ok=True)

        cargo_toml = (
            "[package]\n"
            f'name = "{crate_name}"\n'
            f'version = "{version}"\n'
            f'edition = "{edition}"\n'
            "\n"
            "[dependencies]\n"
            'libm = "0.2"\n'
            'critical-section = "1.2"\n'
            'cortex-m = { version = "0.7", features = ["critical-section-single-core"] }\n'
            'cortex-m-rt = "0.7"\n'
            'panic-halt = "1.0"\n'
            'cortex-m-semihosting = "0.5"\n'
            "\n"
            "[profile.release]\n"
            f"opt-level = {self._toml_opt_level(opt_level)}\n"
            "lto = true\n"
            "codegen-units = 1\n"
            "debug = true\n"
        )
        kept = [] if kept is None else kept
        self._scaffold_file(proj / "Cargo.toml", cargo_toml, force, kept)

        # cortex-m-rt provides link.x; --nmagic avoids page-aligned sections.
        config_toml = (
            f'[build]\ntarget = "{triple}"\n\n'
            f'[target.{triple}]\n'
            '# Flash + run on hardware with a probe (set your chip):\n'
            '# runner = "probe-rs run --chip STM32F411CEUx"\n'
            '# Or run under QEMU semihosting (no hardware needed):\n'
            '# runner = "qemu-system-arm -cpu cortex-m4 -machine '
            'lm3s6965evb -nographic -semihosting-config enable=on,target=native '
            '-kernel"\n'
            'rustflags = [\n'
            '  "-C", "link-arg=-Tlink.x",\n'
            '  "-C", "link-arg=--nmagic",\n'
            ']\n'
        )
        self._scaffold_file(proj / ".cargo" / "config.toml", config_toml, force, kept)

        # Generic layout -- ADJUST ORIGIN/LENGTH to your specific MCU's
        # datasheet (this example fits many STM32F4 parts).
        memory_x = (
            "/* Set these to match your target MCU's memory map. */\n"
            "MEMORY\n"
            "{\n"
            "  FLASH : ORIGIN = 0x08000000, LENGTH = 512K\n"
            "  RAM   : ORIGIN = 0x20000000, LENGTH = 128K\n"
            "}\n"
        )
        self._scaffold_file(proj / "memory.x", memory_x, force, kept)

        build_rs = (
            "use std::env;\n"
            "use std::fs::File;\n"
            "use std::io::Write;\n"
            "use std::path::PathBuf;\n\n"
            "fn main() {\n"
            "    // Put `memory.x` where the linker (via cortex-m-rt) finds it.\n"
            "    let out = PathBuf::from(env::var(\"OUT_DIR\").unwrap());\n"
            "    File::create(out.join(\"memory.x\"))\n"
            "        .unwrap()\n"
            "        .write_all(include_bytes!(\"memory.x\"))\n"
            "        .unwrap();\n"
            "    println!(\"cargo:rustc-link-search={}\", out.display());\n"
            "    println!(\"cargo:rerun-if-changed=memory.x\");\n"
            "}\n"
        )
        self._scaffold_file(proj / "build.rs", build_rs, force, kept)

    def _emit_esp32_c3_scaffold(self, proj, crate_name, version, edition,
                                opt_level, triple, force=False, kept=None) -> None:
        """Write a buildable no_std Cargo project for ESP32-C3 (RISC-V).

        Pins the 2026 esp-hal 1.x crate family. Flash layout comes from
        esp-hal's ``linkall.x`` — unlike Cortex-M, there is no ``memory.x``.
        Classic Xtensa ESP32 is a different profile and is not this scaffold.
        """
        (proj / ".cargo").mkdir(parents=True, exist_ok=True)

        cargo_toml = (
            "[package]\n"
            f'name = "{crate_name}"\n'
            f'version = "{version}"\n'
            f'edition = "{edition}"\n'
            "\n"
            "[dependencies]\n"
            # Chip feature required; `rt` is on by default (#[main] + init).
            'libm = "0.2"\n'
            'critical-section = "1.2"\n'
            'esp-hal = { version = "1.1", features = ["esp32c3"] }\n'
            "esp-backtrace = { version = \"0.19\", "
            'features = ["esp32c3", "panic-handler", "println"] }\n'
            'esp-println = { version = "0.17", features = ["esp32c3"] }\n'
            # 2nd-stage bootloader app descriptor (esp_app_desc! in main.rs).
            "esp-bootloader-esp-idf = { version = \"0.5\", "
            'features = ["esp32c3"] }\n'
            "\n"
            "[profile.release]\n"
            f"opt-level = {self._toml_opt_level(opt_level)}\n"
            "lto = true\n"
            "codegen-units = 1\n"
            "debug = true\n"
        )
        kept = [] if kept is None else kept
        self._scaffold_file(proj / "Cargo.toml", cargo_toml, force, kept)

        config_toml = (
            f'[build]\ntarget = "{triple}"\n\n'
            f"[target.{triple}]\n"
            'runner = "espflash flash --monitor"\n'
            "rustflags = [\n"
            '  "-C", "link-arg=-Tlinkall.x",\n'
            "  # Backtraces via esp-backtrace (RISC-V).\n"
            '  "-C", "force-frame-pointers",\n'
            "]\n"
        )
        self._scaffold_file(proj / ".cargo" / "config.toml", config_toml, force, kept)

        toolchain = (
            "[toolchain]\n"
            'channel = "stable"\n'
            'components = ["rust-src"]\n'
            f'targets = ["{triple}"]\n'
        )
        self._scaffold_file(proj / "rust-toolchain.toml", toolchain, force, kept)

    def _cargo_build(self, proj, crate_name, result, *, release, target):
        """Run `cargo build` for a scaffolded project and record the outcome."""
        cargo = shutil.which("cargo")
        if cargo is None:
            raise RuntimeError(
                "cargo not found on PATH. Install the Rust toolchain via "
                "https://rustup.rs, then ensure 'cargo' is on PATH."
            )

        cmd = [cargo, "build"]
        if release:
            cmd.append("--release")
        if target:
            cmd += ["--target", target]

        proc = subprocess.run(
            cmd, cwd=str(proj), capture_output=True, text=True
        )
        success = proc.returncode == 0

        profile_dir = "release" if release else "debug"
        if target:
            bin_path = proj / "target" / target / profile_dir / crate_name
        else:
            bin_path = proj / "target" / profile_dir / crate_name

        result.built = True
        result.success = success
        result.executable = (
            str(bin_path) if (success and bin_path.exists()) else None
        )
        result.stdout = proc.stdout
        result.stderr = proc.stderr
        return result

    def generate_from_file(self, ast_file: str) -> str:
        """
        Generate Rust code from serialized AST file.
        
        Args:
            ast_file: Path to JSON AST file
            
        Returns:
            Generated Rust code
        """
        import json
        from resnik.parser.ast_nodes import ProgramNode
        
        with open(ast_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # This is a simplified version - full implementation would
        # deserialize the entire AST from JSON
        ast_dict = data.get('ast', {})
        
        # For now, create a basic program node
        # Full implementation would reconstruct entire AST
        program = ProgramNode(name=ast_dict.get('name', 'Main'))
        
        return self.generate(program)
