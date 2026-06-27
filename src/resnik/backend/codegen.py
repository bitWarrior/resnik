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
from resnik.types import NodeType, DataType


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
    
    def __init__(self):
        """Initialize code generator."""
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
        for node in self._walk(ast):
            if isinstance(node, StructureDefNode):
                self._structs[node.name.upper()] = node

        # Emit file header
        self._emit_file_header()

        # Emit the dependency-free runtime prelude only if the program uses
        # vectors/matrices, function calls, or real-time statements.
        if self._uses_runtime(ast):
            self._emit_prelude()
        
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
        pub fn zero() -> Self { Vector([0.0; N]) }
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
        pub fn zero() -> Self { Matrix([[0.0; C]; R]) }
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
    _PRELUDE_INTRINSICS = {"ABVAL", "CROSS", "DOT"}

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
    
    def _emit_file_header(self) -> None:
        """Emit Rust file header with metadata."""
        self.emitter.emit_comment("Generated by Resnik HAL/S Compiler")
        self.emitter.emit_comment("https://github.com/user/resnik")
        self.emitter.emit_line()
        self.emitter.emit_line("#![allow(unused_variables)]")
        self.emitter.emit_line("#![allow(unused_mut)]")
        self.emitter.emit_line("#![allow(unused_assignments)]")
        self.emitter.emit_line("#![allow(unused_parens)]")
        self.emitter.emit_line("#![allow(dead_code)]")
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
            self._generate_compool(node)
        elif isinstance(node, ProgramNode):
            self._generate_program(node)
        elif isinstance(node, TaskNode):
            self._generate_task(node)
        elif isinstance(node, DeclareNode):
            self._generate_declare(node)
        elif isinstance(node, ProcedureNode):
            self._generate_procedure(node)
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
                self.emitter.emit_line(
                    f"return {self._generate_expression(node.value)};"
                )
            else:
                self.emitter.emit_line("return;")
        elif isinstance(node, ForNode):
            self._generate_for(node)
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

        # Generate main function
        self.emitter.emit_function_header("main", [], None, is_public=False)
        self.in_main = True
        self._generate_unit_body(node.declarations, body)
        self.in_main = False
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

        for stmt in statements:
            self._generate_node(stmt)

        if threaded:
            self.emitter.emit_line()
            self.emitter.emit_comment("join scheduled tasks before exit")
            self.emitter.emit_line("for __h in __tasks { let _ = __h.join(); }")

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
            rust_t, rust_i = et, ei
            for d in reversed(node.type_spec.array_dims):
                rust_t = f"[{rust_t}; {d}]"
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
                param_types[d.identifiers[0].lower()] = self.type_mapper.map_type(
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
        ret = (self.type_mapper.map_type(node.return_type)
               if node.return_type is not None else None)
        kind = "Function" if ret is not None else "Procedure"
        self.emitter.emit_comment(f"HAL/S {kind}: {node.name}")
        self.emitter.emit_function_header(rust_name, params, ret, is_public=False)


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

        self._assign_params = prev_assign
        self.emitter.emit_block_end()

    def _generate_while(self, node: WhileNode) -> None:
        """Generate a DO WHILE <cond>; ... END; loop as a Rust while loop."""
        condition = self._generate_expression(node.condition)
        self.emitter.emit_line(f"while {condition} {{")
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
        """Register an ON ERROR(code) handler. The body is NOT emitted here;
        it is invoked from the catch_unwind trap around a faulting operation."""
        if not self._panic_hook_emitted:
            # Suppress the default panic stderr output; the trap routes faults
            # to the registered handler instead of crashing/printing.
            self.emitter.emit_line("std::panic::set_hook(Box::new(|_| {}));")
            self._panic_hook_emitted = True
        self._error_handlers[node.code] = node.body
        self.emitter.emit_comment(
            f"ON ERROR({node.code}): fault handler registered (callback)"
        )

    def _generate_handler_body(self, body: ASTNode) -> None:
        """Emit a registered handler's statements (a DO; ... END; group),
        skipping RESUME (which is modeled by falling through the trap)."""
        stmts = body.statements if isinstance(body, DoBlockNode) else [body]
        for stmt in stmts:
            self._generate_node(stmt)

    def _generate_trapped_division(self, target: ASTNode,
                                   binop: BinaryOpNode) -> None:
        """Lower `target = left / right` under an active ON ERROR handler into a
        catch_unwind trap that carries the HAL/S fault code as the panic payload.

        Integer divide-by-zero panics directly; float divide-by-zero yields a
        non-finite value, so an explicit is_finite() check raises the same trap.
        The handler whose code matches runs; any other code is re-raised.
        """
        code = 5 if 5 in self._error_handlers else next(iter(self._error_handlers))
        body = self._error_handlers[code]
        tname = target.name.lower()
        ts = self._var_info.get(tname)
        rust_t = self.type_mapper.map_type(ts) if ts is not None else "f32"
        left = self._generate_expression(binop.left)
        right = self._generate_expression(binop.right)

        self.emitter.emit_line("{")
        self.emitter.indent()
        self.emitter.emit_line(
            "let __r = std::panic::catch_unwind("
            "std::panic::AssertUnwindSafe(|| {"
        )
        self.emitter.indent()
        self.emitter.emit_line(f"let __v = {left} / ({right} as {rust_t});")
        self.emitter.emit_line(
            f"if !(__v as f64).is_finite() {{ std::panic::panic_any({code}i32); }}"
        )
        self.emitter.emit_line("__v")
        self.emitter.dedent()
        self.emitter.emit_line("}));")
        self.emitter.emit_line("match __r {")
        self.emitter.indent()
        self.emitter.emit_line(f"Ok(__v) => {{ {tname} = __v; }}")
        self.emitter.emit_line("Err(__e) => {")
        self.emitter.indent()
        self.emitter.emit_line(
            "let __code = *__e.downcast_ref::<i32>().unwrap_or(&-1);"
        )
        self.emitter.emit_line(f"if __code == {code} {{")
        self.emitter.indent()
        self._generate_handler_body(body)
        self.emitter.dedent()
        self.emitter.emit_line("} else { std::panic::resume_unwind(__e); }")
        self.emitter.dedent()
        self.emitter.emit_line("}")
        self.emitter.dedent()
        self.emitter.emit_line("}")
        self.emitter.dedent()
        self.emitter.emit_line("}")

    def _generate_call(self, node: CallNode) -> None:
        """Generate a CALL as a Rust function call. Input arguments pass by
        value; ASSIGN (output) arguments pass by &mut so the callee writes
        back to the caller's variables."""
        parts = [self._generate_expression(a) for a in node.arguments]
        parts += [
            f"&mut {self._generate_expression(a)}" for a in node.assign_args
        ]
        self.emitter.emit_line(f"{node.name.lower()}({', '.join(parts)});")
    
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
            # EQUATE overlay write: VIEW = expr  ->  target = (expr) as target_type
            # (bit-preserving, so the alias and the target stay byte-identical).
            if (isinstance(node.target, IdentifierNode)
                    and node.target.name.lower() in self._equates):
                target, _, target_type = self._equates[node.target.name.lower()]
                rhs = self._generate_expression(node.value)
                self.emitter.emit_line(f"{target} = ({rhs}) as {target_type};")
                return
            # If a fault handler is registered and this assignment performs a
            # division, route it through a catch_unwind trap (see
            # _generate_trapped_division) so a divide-by-zero invokes the
            # registered ON ERROR handler instead of producing inf/panicking.
            if (
                self._error_handlers
                and isinstance(node.value, BinaryOpNode)
                and node.value.operator == "/"
                and isinstance(node.target, IdentifierNode)
            ):
                self._generate_trapped_division(node.target, node.value)
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
    
    def _generate_binary_op(self, node: BinaryOpNode) -> str:
        """
        Generate Rust binary operation with minimal, correct parentheses.
        
        Args:
            node: BinaryOpNode
            
        Returns:
            Rust binary expression
        """
        op = node.operator
        # String concatenation: build a new String via format! (handles
        # String and &str operands uniformly, any nesting depth).
        if op == "||":
            left = self._generate_expression(node.left)
            right = self._generate_expression(node.right)
            return f'format!("{{}}{{}}", {left}, {right})'
        op_map = {
            "+": "+", "-": "-", "*": "*", "/": "/",
            "**": ".pow", "=": "==", "¬=": "!=", "~=": "!=",
            "<": "<", ">": ">", "<=": "<=", ">=": ">=",
            "AND": "&", "OR": "|", "XOR": "^",
        }
        rust_op = op_map.get(op, op)

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

        # HAL/S promotes mixed integer/scalar arithmetic to scalar; Rust has no
        # implicit numeric coercion, so cast the integer operand to f32 when the
        # other side is float (e.g. INTEGER * SCALAR -> (i as f32) * s).
        if op in ("+", "-", "*", "/"):
            lk = self._numeric_kind(node.left)
            rk = self._numeric_kind(node.right)
            if lk == "int" and rk == "float":
                left = f"({left} as f32)"
            elif rk == "int" and lk == "float":
                right = f"({right} as f32)"
        return f"{left} {rust_op} {right}"

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
            if nm in ("ABVAL", "DOT", "SCALAR"):
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

    def _generate_matrix_literal(self, node: MatrixLiteralNode,
                                 dims=None) -> str:
        """MATRIX(v1..vn) -> Matrix([[..],[..],..]). Dimensions come from the
        assignment/declaration target when known, else a square is inferred."""
        vals = [self._generate_expression(v) for v in node.values]
        n = len(vals)
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

    def _generate_for(self, node: ForNode) -> None:
        """Generate a DO FOR loop as an inclusive Rust range loop."""
        var = node.var.lower()
        start = self._generate_expression(node.start)
        end = self._generate_expression(node.end)
        if node.step is not None:
            step = self._generate_expression(node.step)
            self.emitter.emit_line(
                f"for {var} in ({start}..={end}).step_by(({step}) as usize) {{"
            )
        else:
            self.emitter.emit_line(f"for {var} in {start}..={end} {{")
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
        # If the name is a declared MATRIX/VECTOR, parenthesized indices are
        # element access (1-indexed), not a function call.
        ts = self._var_info.get(node.name.lower())
        if ts is not None:
            if ts.data_type == DataType.MATRIX and len(node.arguments) == 2:
                r = self._generate_expression(node.arguments[0])
                c = self._generate_expression(node.arguments[1])
                return (f"{node.name.lower()}.0[({r}) as usize - 1]"
                        f"[({c}) as usize - 1]")
            if ts.data_type == DataType.VECTOR and len(node.arguments) == 1:
                i = self._generate_expression(node.arguments[0])
                return f"{node.name.lower()}.0[({i}) as usize - 1]"
        args = ", ".join(self._generate_expression(a) for a in node.arguments)
        return f"{node.name.lower()}({args})"

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
            ev = node.event.lower()
            self.emitter.emit_comment(
                f"WAIT {node.event} (host stub: advisory, non-blocking)")
            self.emitter.emit_line(
                f'eprintln!("[hal_rt] WAIT {node.event} '
                '(advisory host stub; not real-time, does not block)");')
            self.emitter.emit_line(f"let _ = {ev};")
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
