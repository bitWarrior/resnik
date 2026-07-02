"""
Rust code generator from HAL/S AST.
"""

import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional, List
from pathlib import Path

from resnik.parser.ast_nodes import (
    NameAssignNode,
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
from resnik.types import NodeType, DataType, Precision


@dataclass
class CompileResult:
    """Result of compiling generated Rust source to a native executable."""

    success: bool
    executable: Optional[str] = None
    source: Optional[str] = None
    stdout: str = ""
    stderr: str = ""


@dataclass
class CargoProjectResult:
    """Result of scaffolding (and optionally building) a Cargo project."""

    project_dir: str
    cargo_toml: str
    main_rs: str
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
                    return lo / ro if ro != 0 else None
                if op == "**":
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

    # Supported code-generation target profiles. "host" is the default hosted
    # std build; "cortex-m" emits no_std + #[entry] bare-metal firmware.
    _TARGETS = ("host", "cortex-m")

    # Default cross-compilation triple for a given target profile (used by the
    # CLI when the user doesn't pass an explicit triple).
    TARGET_TRIPLES = {"cortex-m": "thumbv7em-none-eabihf"}

    def __init__(self, target: str = "host"):
        """Initialize code generator.

        Args:
            target: Code-generation profile: "host" (hosted std, the default)
                or "cortex-m" (no_std bare-metal firmware for ARM Cortex-M).
        """
        if target not in self._TARGETS:
            raise ValueError(
                f"Unknown target '{target}'. Expected one of {self._TARGETS}.")
        self.target = target
        self.emitter = RustEmitter()
        self.type_mapper = TypeMapper()
        self.in_main = False
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
        for node in self._walk(ast):
            if isinstance(node, DeclareNode) and node.type_spec is not None:
                for ident in node.identifiers:
                    self._var_info[ident.lower()] = node.type_spec
                if node.type_spec.data_type == DataType.BIT:
                    width = (
                        node.type_spec.dimensions[0]
                        if node.type_spec.dimensions else 1
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

        # Emit the dependency-free runtime prelude only if the program uses
        # vectors/matrices, function calls, or real-time statements.
        if self._uses_runtime(ast):
            self._emit_prelude()

        # COMPOOL shared data lowers to module-level statics so every generated
        # function (program + scheduled tasks) can reach it. Emit each COMPOOL
        # up front wherever it appears -- a top-level unit OR nested inside a
        # PROGRAM -- and record it so it is not re-emitted inside a function.
        self._emitted_compools: set = set()
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
                    self.emitter.emit_line(
                        f"static {ident.upper()}: "
                        "std::sync::atomic::AtomicBool = "
                        "std::sync::atomic::AtomicBool::new(false);")
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
        if self._extern_fns:
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
        "*": 10, "/": 10,
        "+": 9, "-": 9,
        "AND": 7,                      # &
        "XOR": 6,                      # ^
        "OR": 5,                       # |
        "<": 4, ">": 4, "<=": 4, ">=": 4, "¬=": 4, "~=": 4, "=": 4,
    }

    # Self-contained, no external crates. f32 element type; host (std) stubs
    # for the real-time statements. The element type and the SCHEDULE/TERMINATE
    # behavior are the pieces that would change for a no_std / embedded target.
    _PRELUDE = """mod hal_rt {
    //! Minimal dependency-free runtime for generated HAL/S code (host target).
    #![allow(dead_code)]
    use std::ops::{Add, Sub, Mul, Div, Neg};

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

    impl<const N: usize> std::fmt::Display for Vector<N> {
        fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
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

    impl<const R: usize, const C: usize> std::fmt::Display for Matrix<R, C> {
        fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
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
    }

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
    }
}

use hal_rt::*;
"""

    def _emit_prelude(self) -> None:
        """Emit the dependency-free runtime support module."""
        for line in self._PRELUDE.split("\n"):
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
        """Yield node and all descendant AST nodes."""
        yield node
        list_attrs = (
            "statements", "declarations", "procedures", "tasks", "units",
            "items", "arguments", "values", "args", "fields", "targets",
            "indices", "assign_args"
        )
        for attr in list_attrs:
            for child in getattr(node, attr, None) or []:
                if hasattr(child, "node_type"):
                    yield from self._walk(child)
        single_attrs = (
            "value", "target", "left", "right",
            "condition", "then_block", "else_block",
            "priority", "initial_value",
        )
        for attr in single_attrs:
            child = getattr(node, attr, None)
            if child is not None and hasattr(child, "node_type"):
                yield from self._walk(child)
    
    @property
    def _embedded(self) -> bool:
        """True for any bare-metal (no_std) target profile."""
        return self.target != "host"

    def _emit_embedded_guard(self, ast: ASTNode) -> None:
        """For a no_std target, emit compile_error! for any feature whose
        lowering still relies on std (so the build fails with a clear message
        instead of a cryptic linker error). These are the follow-up steps of
        the embedded port."""
        reasons = []
        if self._uses_runtime(ast):
            reasons.append(
                "VECTOR/MATRIX or math intrinsics need a no_std hal_rt runtime "
                "(libm) -- backend step 2")
        if any(isinstance(n, (ScheduleNode, TaskNode)) for n in self._walk(ast)):
            reasons.append(
                "SCHEDULE/TASK lower to std::thread -- needs a superloop/RTIC "
                "executor on bare metal -- backend step 5")
        if any(isinstance(n, CompoolNode) for n in self._walk(ast)):
            reasons.append(
                "COMPOOL globals lower to std::sync::Mutex -- needs "
                "critical-section on bare metal -- backend step 5")
        for r in reasons:
            self.emitter.emit_line(f'compile_error!("resnik: {r}");')
        if reasons:
            self.emitter.emit_line()

    def _emit_file_header(self) -> None:
        """Emit Rust file header with metadata."""
        self.emitter.emit_comment("Generated by Resnik HAL/S Compiler")
        self.emitter.emit_comment("https://github.com/user/resnik")
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
        self.emitter.emit_line("#![allow(unreachable_code)]")
        self.emitter.emit_line("#![allow(dead_code)]")
        self.emitter.emit_line()
        if self.target == "cortex-m":
            # Reset vector / entry point, a halting panic handler, and a simple
            # semihosting output sink for WRITE (swap for RTT or UART later).
            self.emitter.emit_line("use cortex_m_rt::entry;")
            self.emitter.emit_line("use panic_halt as _;")
            self.emitter.emit_line("use cortex_m_semihosting::hprintln;")
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
                self.emitter.emit_line(f"break '{node.label.lower()};")
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
            # Unknown node type
            self.emitter.emit_comment(f"TODO: Generate code for {type(node).__name__}")
    
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
        self.emitter.emit_comment(
            f"HAL/S Compool (shared global memory): {node.name}"
        )
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
                            f"static {name}: std::sync::Mutex<{rust_type}> = "
                            f"std::sync::Mutex::new({init});")
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
                        f"static {name}: std::sync::Mutex<{rust_type}> = "
                        f"std::sync::Mutex::new({init});"
                    )
        self.emitter.emit_line()

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

        # Nested TASK blocks -> standalone functions (each runs on its own thread)
        for task in node.tasks:
            self._generate_task_function(task)
            self.emitter.emit_line()

        # Generate procedures (before main)
        for proc in node.procedures:
            self._generate_node(proc)
            self.emitter.emit_line()

        # Generate main function. On a bare-metal target this is the
        # cortex-m-rt reset entry point: a diverging `fn main() -> !`.
        if self.target == "cortex-m":
            self.emitter.emit_line("#[entry]")
            self.emitter.emit_line("fn main() -> ! {")
            self.emitter.indent()
        else:
            self.emitter.emit_function_header("main", [], None, is_public=False)
        self.in_main = True
        self._cur_unit = "@main"
        self._cur_unit_is_result = False
        self._generate_unit_body(node.declarations, body)
        self.in_main = False
        if self.target == "cortex-m":
            # A cortex-m-rt entry never returns; park the core when done.
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
            task.name.lower(), [], None, is_public=False
        )
        self._generate_unit_body(task.declarations, task.statements)
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
        threaded = self._contains_schedule(statements)
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
            name: i for i, (name, _) in enumerate(segments) if name is not None
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
        state = getattr(self, "_label_states", {}).get(node.label)
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
                name = ident.lower()
                self._name_vars[name] = rust_type
                self.emitter.emit_line(
                    f"let mut {name}: *mut {rust_type} = std::ptr::null_mut();")
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
                et = self._struct_name(ets.type_name or "")
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
                    identifier.lower(), rust_t, rust_i, True
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
                        identifier.lower(), f"[{sname}; {n}]",
                        f"[{sname}::new(); {n}]", True
                    )
                return
            for identifier in node.identifiers:
                self.emitter.emit_variable_declaration(
                    identifier.lower(), sname, f"{sname}::new()", True
                )
            return

        for identifier in node.identifiers:
            # Convert to snake_case for Rust conventions
            rust_name = identifier.lower()
            
            # Use the INITIAL value(s) if given, else a typed default so the
            # variable is always initialized (Rust rejects reads of an
            # uninitialized binding).
            if node.initial_value is not None:
                initial_value = self._generate_expression(node.initial_value)
                # CHARACTER variables are Rust String; a bare string literal is
                # &str, so convert it to an owned String.
                if (
                    node.type_spec is not None
                    and node.type_spec.data_type == DataType.CHARACTER
                    and isinstance(node.initial_value, LiteralNode)
                    and node.initial_value.literal_type == "STRING"
                ):
                    initial_value += ".to_string()"
            elif node.initial_values:
                initial_value = self._generate_initial_values(node)
            else:
                initial_value = self.type_mapper.get_default_value(node.type_spec)
            
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
        # generate() pre-pass; there is no HAL/S body to lower here.
        if getattr(node, "is_external", False):
            return
        rust_name = node.name.lower()
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

        # Input parameters by value; ASSIGN (output) parameters by &mut.
        params = [
            (p.lower(), param_types.get(p.lower(), "i32"))
            for p in node.parameters
        ]
        params += [
            (p.lower(), f"&mut {param_types.get(p.lower(), 'i32')}")
            for p in node.assign_params
        ]

        # Emit procedure (or function, when it carries a return type)
        base_ret = (self.type_mapper.map_type(node.return_type)
                    if node.return_type is not None else None)
        is_result = self._uses_faults and rust_name in self._faulting_procs
        if is_result:
            inner = base_ret if base_ret is not None else "()"
            ret = f"Result<{inner}, HalFault>"
        else:
            ret = base_ret
        kind = "Function" if base_ret is not None else "Procedure"
        self.emitter.emit_comment(f"HAL/S {kind}: {node.name}")
        self.emitter.emit_function_header(rust_name, params, ret, is_public=False)

        prev_unit = self._cur_unit
        prev_is_result = self._cur_unit_is_result
        self._cur_unit = rust_name
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
        """Generate a DO WHILE <cond>; ... END; loop as a Rust while loop.
        A `label` (from a labeled DO block) becomes a Rust loop label."""
        condition = self._generate_expression(node.condition)
        lbl = f"'{label.lower()}: " if label else ""
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
        tname = target.name.lower()
        ts = self._var_info.get(tname)
        rust_t = self.type_mapper.map_type(ts) if ts is not None else "f32"
        left = self._generate_expression(binop.left)
        right = self._generate_expression(binop.right)
        unit_handlers = self._unit_handlers.get(self._cur_unit, {})

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
            self.emitter.emit_line(f"{tname} = {left} / __d;")
            self.emitter.dedent()
            self.emitter.emit_line("}")
        elif self._cur_unit_is_result:
            # No local handler: unwind this procedure so the caller's handler
            # runs. Statements after the fault are skipped by the early return.
            self.emitter.emit_line(
                f"if __d == 0 as {rust_t} {{ "
                f"return Err(HalFault {{ code: {code} }}); }}")
            self.emitter.emit_line(f"{tname} = {left} / __d;")
        else:
            # Unhandled fault at top level -> HAL/S terminates the program.
            self.emitter.emit_line(f"if __d == 0 as {rust_t} {{")
            self.emitter.indent()
            self._emit_unhandled_fault(str(code))
            self.emitter.dedent()
            self.emitter.emit_line("}")
            self.emitter.emit_line(f"{tname} = {left} / __d;")
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
        panic handler (panic_halt) turns into a halt."""
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
                and node.value.operator == "/"
                and isinstance(node.target, IdentifierNode))

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

        if isinstance(ast, ProgramNode):
            self._unit_handlers["@main"] = self._collect_unit_handlers(ast)
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
        call_expr = f"{node.name.lower()}({', '.join(parts)})"
        callee = node.name.lower()
        if self._uses_faults and callee in self._faulting_procs:
            self._generate_faulting_call(call_expr, callee)
        else:
            self.emitter.emit_line(f"{call_expr};")
    
    def _generate_name_assign(self, node: NameAssignNode) -> None:
        """ASSIGN <pointer> TO <target> -> bind the raw pointer to the target's
        address (addr_of_mut! avoids forming an intermediate &mut borrow)."""
        ptr = node.pointer.lower()
        tgt = node.target.lower()
        self.emitter.emit_line(
            f"{ptr} = std::ptr::addr_of_mut!({tgt});")

    def _expr_uses_name(self, node) -> bool:
        """True if the expression reads any NAME (pointer) variable."""
        for n in self._walk(node):
            if isinstance(n, IdentifierNode) \
                    and n.name.lower() in getattr(self, "_name_vars", {}):
                return True
        return False

    def _char_length(self, node):
        """Declared CHARACTER length of an expression: dimensions[0] for a
        CHARACTER variable, or the literal length for a string literal."""
        if isinstance(node, IdentifierNode):
            ts = self._var_info.get(node.name.lower())
            if (ts is not None and ts.data_type == DataType.CHARACTER
                    and ts.dimensions):
                return ts.dimensions[0]
            return None
        if isinstance(node, LiteralNode) and isinstance(node.value, str):
            return len(node.value)
        return None

    def _is_char_source(self, node) -> bool:
        """True if an expression yields a CHARACTER (String) value that a
        fixed-length assignment should normalize."""
        if isinstance(node, LiteralNode) and isinstance(node.value, str):
            return True
        if isinstance(node, IdentifierNode):
            ts = self._var_info.get(node.name.lower())
            return ts is not None and ts.data_type == DataType.CHARACTER
        return False

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
            # HAL/S CHARACTER is fixed-length: assigning to CHARACTER(n) right-
            # truncates an oversized source and right-pads (with spaces) an
            # undersized one, decided from the declared lengths at compile time.
            if isinstance(node.target, IdentifierNode):
                tlen = self._char_length(node.target)
                if tlen is not None and self._is_char_source(node.value):
                    tgt = node.target.name.lower()
                    src = self._generate_expression(node.value)
                    slen = self._char_length(node.value)
                    if slen is not None and slen > tlen:
                        self.emitter.emit_line(
                            f"{tgt} = {src}.chars().take({tlen})"
                            ".collect::<String>();")
                    elif slen is not None and slen < tlen:
                        self.emitter.emit_line(
                            f'{tgt} = format!("{{:<{tlen}}}", {src});')
                    elif slen is not None:
                        self.emitter.emit_line(f"{tgt} = {src};")
                    else:
                        # Unknown source length: normalize to exactly tlen at
                        # runtime (truncate if longer, pad if shorter).
                        self.emitter.emit_line(
                            f"{tgt} = {{ let __s: String = {src}; "
                            f"if __s.chars().count() > {tlen} "
                            f"{{ __s.chars().take({tlen}).collect() }} "
                            f'else {{ format!("{{:<{tlen}$}}", __s, '
                            f"{tlen} = {tlen}) }} }};")
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
                        f"{node.target.name.lower()} = Matrix([{rows}]);")
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
                    lhs = f"*{node.target.name.lower()}"
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
                self.emitter.emit_line(f"{target} = ({rhs}) as {target_type};")
                return
            # In a fault-aware program (one that uses ON ERROR), a division is
            # lowered so a zero divisor raises HAL/S fault 5 -- handled inline
            # by a same-scope handler, or unwound to the caller.
            if (
                self._uses_faults
                and isinstance(node.value, BinaryOpNode)
                and node.value.operator == "/"
                and isinstance(node.target, IdentifierNode)
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
                if up in self._globals and self._globals[up][0] == "mut":
                    self.emitter.emit_line("{")
                    self.emitter.indent()
                    self.emitter.emit_line(f"let __v = {value};")
                    self.emitter.emit_line(f"*{up}.lock().unwrap() = __v;")
                    self.emitter.dedent()
                    self.emitter.emit_line("}")
                    return
            # Subscripted lvalue (vector element). For a VECTOR this renders a
            # real place expression (vec.0[i]) that can be assigned to.
            target = self._generate_expression(node.target)
            self.emitter.emit_assignment(target, value)
    
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

        # Output sink is target-dependent: hosted std uses println!; the
        # cortex-m profile uses semihosting (swap for RTT/UART downstream).
        if self.target == "cortex-m":
            if not node.items:
                self.emitter.emit_line('hprintln!("").ok();')
                return
            placeholders = " ".join(["{}"] * len(node.items))
            args = [self._generate_expression(item) for item in node.items]
            args_str = ", ".join(args)
            self.emitter.emit_line(
                f'hprintln!("{placeholders}", {args_str}).ok();')
            return

        if not node.items:
            # WRITE with no output list emits a blank line
            self.emitter.emit_line("println!();")
            return

        placeholders = " ".join(["{}"] * len(node.items))
        args = [self._generate_expression(item) for item in node.items]
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

        target_info = []
        total = 0
        for tgt in node.targets:
            ts = (self._var_info.get(tgt.name.lower())
                  if isinstance(tgt, IdentifierNode) else None)
            if ts is not None and ts.data_type == DataType.VECTOR:
                n, kind = (ts.dimensions[0] if ts.dimensions else 3), "vector"
            elif ts is not None and ts.data_type in (
                    DataType.INTEGER, DataType.BIT):
                n, kind = 1, "int"
            else:
                n, kind = 1, "scalar"
            target_info.append((self._generate_expression(tgt), kind, n))
            total += n

        self.emitter.emit_line("{")
        self.emitter.indent()
        self.emitter.emit_line(f"let __vals = hal_rt::read_f32({total});")
        self.emitter.emit_line("let mut __k = 0usize;")
        for lvalue, kind, n in target_info:
            if kind == "vector":
                self.emitter.emit_line(
                    f"{lvalue} = {{ let mut __v = [0.0f32; {n}]; "
                    f"for __j in 0..{n} {{ __v[__j] = __vals[__k]; "
                    "__k += 1; } Vector(__v) };")
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
            return "/* unknown expression */"
    
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
            # Escape quotes and return as Rust string
            escaped = str(value).replace('"', '\\"')
            return f'"{escaped}"'
        elif node.literal_type == "BIT":
            if isinstance(value, bool):
                return "true" if value else "false"
            return str(value)
        elif node.literal_type == "HEX":
            return f"0x{int(value):X}"
        elif node.literal_type == "OCT":
            return f"0o{int(value):o}"
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
        # EVENT read -> atomic load of its shared flag.
        if node.name.lower() in getattr(self, "_event_vars", set()):
            return (f"{node.name.upper()}"
                    ".load(std::sync::atomic::Ordering::SeqCst)")
        # `unsafe` the bare deref suffices; elsewhere use an inline unsafe block
        # so the expression is valid on its own.
        if node.name.lower() in getattr(self, "_name_vars", {}):
            nm = node.name.lower()
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
            return f"({target} as {view_type})"
        # ASSIGN (output) parameters are &mut references inside a procedure;
        # dereference on every use so reads and assignment targets both work.
        if node.name.lower() in getattr(self, "_assign_params", set()):
            return f"(*{node.name.lower()})"
        # Convert HAL/S UPPER_CASE to Rust snake_case
        return node.name.lower()
    
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
        # string concatenation otherwise. BIT lowers to u32 here, so no width
        # cast is needed (the u8/u16 casts in the spec are for narrower types).
        if op == "||":
            lw = self._bit_width_expr(node.left)
            rw = self._bit_width_expr(node.right)
            left = self._generate_expression(node.left)
            right = self._generate_expression(node.right)
            if lw is not None and rw is not None:
                return f"(({left}) << {rw}) | ({right})"
            return f'format!("{{}}{{}}", {left}, {right})'
        op_map = {
            "+": "+", "-": "-", "*": "*", "/": "/",
            "**": ".pow", "=": "==", "¬=": "!=", "~=": "!=",
            "<": "<", ">": ">", "<=": "<=", ">=": ">=",
            "AND": "&", "OR": "|", "XOR": "^", "MOD": "%",
        }
        # Word operators (AND/OR/XOR/MOD) are case-insensitive in HAL/S; fall
        # back to the upper-cased spelling so mixed-case source still maps.
        rust_op = op_map.get(op) or op_map.get(op.upper(), op)

        # AND/OR are logical on BOOLEAN operands (&&/||) and bitwise on BIT/
        # INTEGER operands (&/|). Default (above) is bitwise; upgrade to logical
        # only when both sides are boolean.
        if op in ("AND", "OR"):
            if (self._is_boolean_expr(node.left)
                    and self._is_boolean_expr(node.right)):
                rust_op = "&&" if op == "AND" else "||"

        # Power has no Rust infix operator. f32 uses .powi(i32); integer
        # bases use .pow(u32). Default to the float form (common in HAL/S math).
        if op == "**":
            base = self._generate_expression(node.left)
            exp = self._generate_expression(node.right)
            if self._is_integer_operand(node.left):
                return f"({base}).pow({exp} as u32)"
            return f"({base}).powi({exp} as i32)"

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
            if node.operator in ("+", "-", "*", "/", "**"):
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

        # Default: bit extract (BIT registers, or unknown -> width 32).
        width = 32
        if isinstance(node.operand, IdentifierNode):
            width = self._bit_widths.get(node.operand.name.lower(), 32)
        if idx_const is not None:
            return f"(({operand} >> {width - idx_const}) & 1)"
        return f"(({operand} >> ({width} - {idx_src})) & 1)"

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
        width = 32
        if isinstance(operand_node, IdentifierNode):
            width = self._bit_widths.get(operand_node.name.lower(), 32)
        lo = self._const_int(rng.lo)
        hi = self._const_int(rng.hi)
        if lo is not None and hi is not None:
            shift = width - hi
            mask = (1 << (hi - lo + 1)) - 1
            return f"(({operand_src} >> {shift}) & 0x{mask:X})"
        lo_s = self._generate_expression(rng.lo)
        hi_s = self._generate_expression(rng.hi)
        return (f"(({operand_src} >> ({width} - ({hi_s}))) "
                f"& ((1u32 << (({hi_s}) - ({lo_s}) + 1)) - 1))")

    def _struct_name(self, name: str) -> str:
        """HAL/S template name -> Rust PascalCase struct name (NODE -> Node)."""
        return "".join(w.capitalize() for w in name.split("_"))

    def _generate_structure_def(self, node: StructureDefNode) -> None:
        """Emit a STRUCTURE template as a Rust struct plus a zero-init new()."""
        sname = self._struct_name(node.name)
        self.emitter.emit_comment(f"HAL/S Structure template: {node.name}")
        self.emitter.emit_line("#[derive(Clone, Copy, Debug)]")
        self.emitter.emit_line(f"struct {sname} {{")
        self.emitter.indent()
        for decl in node.fields:
            rust_type = self.type_mapper.map_type(decl.type_spec)
            for ident in decl.identifiers:
                self.emitter.emit_line(f"{ident.lower()}: {rust_type},")
        self.emitter.dedent()
        self.emitter.emit_line("}")
        # constructor: zero-initialize every field
        self.emitter.emit_line(f"impl {sname} {{")
        self.emitter.indent()
        self.emitter.emit_line("fn new() -> Self {")
        self.emitter.indent()
        inits = []
        for decl in node.fields:
            default = self.type_mapper.get_default_value(decl.type_spec)
            for ident in decl.identifiers:
                inits.append(f"{ident.lower()}: {default}")
        self.emitter.emit_line(f"{sname} {{ {', '.join(inits)} }}")
        self.emitter.dedent()
        self.emitter.emit_line("}")
        self.emitter.dedent()
        self.emitter.emit_line("}")

    def _generate_field_access(self, node: FieldAccessNode) -> str:
        """Structure field access: operand.field."""
        operand = self._generate_expression(node.operand)
        return f"{operand}.{node.field_name.lower()}"

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
        operand = node.name.lower()
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

    def _const_int(self, node):
        """Return a Python int if node is a constant integer literal, else None."""
        if isinstance(node, LiteralNode) and isinstance(node.value, (int, float)):
            return int(node.value)
        return None

    def _generate_for(self, node: ForNode, label: str = "") -> None:
        """Generate a DO FOR loop as an inclusive Rust range loop.

        TO counts up (`start..=end`); DOWNTO counts down, which Rust expresses
        as the ascending range reversed (`(end..=start).rev()`), since Rust
        ranges must be low..=high. A `label` (from a labeled DO block) becomes
        a Rust loop label so `EXIT <label>` can target it.
        """
        var = node.var.lower()
        start = self._generate_expression(node.start)
        end = self._generate_expression(node.end)
        if node.descending:
            # DOWNTO: iterate start, start-1, ..., end  ->  (end..=start).rev()
            rng = f"({end}..={start}).rev()"
        else:
            rng = f"{start}..={end}"
        lbl = f"'{label.lower()}: " if label else ""
        if node.step is not None:
            step = self._generate_expression(node.step)
            self.emitter.emit_line(
                f"{lbl}for {var} in {rng}.step_by(({step}) as usize) {{"
            )
        else:
            self.emitter.emit_line(f"{lbl}for {var} in {rng} {{")
        self.emitter.indent()
        for stmt in node.statements:
            self._generate_node(stmt)
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
            return f"({arg}.len() as i32)"
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
        # NONHAL external calls cross the FFI boundary and are unsafe to call.
        if node.name.lower() in getattr(self, "_extern_fns", set()):
            return f"unsafe {{ {node.name.lower()}({args}) }}"
        return f"{node.name.lower()}({args})"

    def _generate_extern(self, node) -> None:
        """Emit a NONHAL function as an `extern "C"` FFI signature."""
        name = node.name.lower()
        ptypes = {
            d.identifiers[0].lower(): d.type_spec
            for d in node.statements
            if isinstance(d, DeclareNode) and len(d.identifiers) == 1
        }
        params = ", ".join(
            f"{pn.lower()}: {self._full_rust_type(ptypes.get(pn.lower()))}"
            for pn in node.parameters
        )
        ret = (f" -> {self._full_rust_type(node.return_type)}"
               if node.return_type is not None else "")
        self.emitter.emit_line(f'extern "C" {{ fn {name}({params}){ret}; }}')

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
        constants, an ASSIGN-param deref, else snake_case."""
        up = name.upper()
        if up in self._globals:
            kind, _ = self._globals[up]
            return up if kind == "const" else f"(*{up}.lock().unwrap())"
        if name.lower() in getattr(self, "_assign_params", set()):
            return f"(*{name.lower()})"
        return name.lower()

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

        Timed/cyclic form (SCHEDULE T IN d [REPEAT EVERY p]) spawns a thread
        that sleeps for the initial delay, then either runs once or repeats on
        the period until a per-task cancel flag is set. Priority form keeps the
        existing fire-and-join model.
        """
        task_fn = node.task_name.lower()

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
                    ".load(std::sync::atomic::Ordering::SeqCst);")
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
        """SIGNAL <event>; -> host stub. Sets the event's latched flag and
        emits an advisory marker. This is NOT real-time-faithful: a real
        unlatched event would pulse and be lost with no waiter; here the flag
        persists. See the RTOS backend for faithful task-notification."""
        ev = node.name.lower()
        self.emitter.emit_comment(
            f"SIGNAL {node.name} (host stub: advisory, latched flag)")
        if ev in getattr(self, "_event_vars", set()):
            self.emitter.emit_line(
                f"{node.name.upper()}.store(true, "
                "std::sync::atomic::Ordering::SeqCst);")
        else:
            self.emitter.emit_line(f"{ev} = true;")
        self.emitter.emit_line(
            f'eprintln!("[hal_rt] SIGNAL {node.name} '
            '(advisory host stub; not real-time)");')

    def _generate_cancel(self, node: CancelNode) -> None:
        """CANCEL <task>; -> set the task's cancel flag and join its thread."""
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

        cmd = [
            rustc,
            str(src),
            "-o", str(out),
            "--edition", edition,
            "-C", f"opt-level={opt_level}",
        ]
        if target:
            cmd += ["--target", target]

        proc = subprocess.run(cmd, capture_output=True, text=True)
        success = proc.returncode == 0

        if success and not keep_source:
            src.unlink(missing_ok=True)

        return CompileResult(
            success=success,
            executable=str(out) if success else None,
            source=str(src) if (keep_source or not success) else None,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

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

        if self.target == "cortex-m":
            triple = target or self.TARGET_TRIPLES["cortex-m"]
            self._emit_cortex_m_scaffold(
                proj, crate_name, version, edition, opt_level, triple)
            main_path = src_dir / "main.rs"
            main_path.write_text(code, encoding="utf-8")
            result = CargoProjectResult(
                project_dir=str(proj),
                cargo_toml=str(proj / "Cargo.toml"),
                main_rs=str(main_path),
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
        toml_path.write_text(cargo_toml, encoding="utf-8")
        main_path.write_text(code, encoding="utf-8")

        result = CargoProjectResult(
            project_dir=str(proj),
            cargo_toml=str(toml_path),
            main_rs=str(main_path),
        )

        if not build:
            return result

        return self._cargo_build(
            proj, crate_name, result, release=release, target=target)

    def _emit_cortex_m_scaffold(self, proj, crate_name, version, edition,
                                opt_level, triple) -> None:
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
            'cortex-m = "0.7"\n'
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
        (proj / "Cargo.toml").write_text(cargo_toml, encoding="utf-8")

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
        (proj / ".cargo" / "config.toml").write_text(
            config_toml, encoding="utf-8")

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
        (proj / "memory.x").write_text(memory_x, encoding="utf-8")

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
        (proj / "build.rs").write_text(build_rs, encoding="utf-8")

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
