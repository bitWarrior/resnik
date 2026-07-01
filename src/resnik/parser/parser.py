"""
Modern HAL/S Parser with full type annotations.
"""

import json
import sys
from pathlib import Path
from typing import Any, List, Optional

from rply import ParserGenerator

from resnik.exceptions import ParserError, SemanticError
from resnik.lexer import Lexer, Token
from resnik.parser.ast_nodes import (
    NameAssignNode,
    GotoNode,
    LabelNode,
    ASTNode,
    ProgramNode,
    TaskNode,
    ProcedureNode,
    CompoolNode,
    CompilationNode,
    DeclareNode,
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
    LiteralNode,
    IdentifierNode,
)
from resnik.types import DataType, TypeSpec
from resnik.parser.symbol_table import SymbolTable


class Parser:
    """
    HAL/S Syntactic and Semantic Analyzer using rply.

    Example:
        >>> parser = Parser()
        >>> ast = parser.parse_file("program.hal")
        >>> if ast:
        ...     print(f"Program: {ast.name}")
    """

    def __init__(self) -> None:
        """Initialize the parser."""
        self.pg = ParserGenerator(
            tokens=[
                "KEYWORD_PROGRAM",
                "KEYWORD_CLOSE",
                "KEYWORD_TASK",
                "KEYWORD_PROCEDURE",
                "KEYWORD_FUNCTION",
                "KEYWORD_RETURN",
                "KEYWORD_IN",
                "KEYWORD_REPEAT",
                "KEYWORD_EVERY",
                "KEYWORD_WAIT",
                "KEYWORD_UNTIL",
                "KEYWORD_EVENT",
                "KEYWORD_SIGNAL",
                "KEYWORD_ASSIGN",
                "KEYWORD_CANCEL",
                "KEYWORD_COMPOOL",
                "KEYWORD_STATIC",
                "KEYWORD_CONSTANT",
                "KEYWORD_STRUCTURE",
                "KEYWORD_RESUME",
                "KEYWORD_EQUATE",
                "KEYWORD_ARRAY",
                "KEYWORD_OF",
                "DOT",
                "KEYWORD_CALL",
                "KEYWORD_WHILE",
                "KEYWORD_CASE",
                "KEYWORD_FOR",
                "KEYWORD_TO",
                "KEYWORD_GO",
                "KEYWORD_NAME",
                "KEYWORD_NONHAL",
                "KEYWORD_DOUBLE",
                "KEYWORD_SINGLE",
                "HASH",
                "KEYWORD_BY",
                "KEYWORD_INITIAL",
                "KEYWORD_WRITE",
                "KEYWORD_READ",
                "KEYWORD_DECLARE",
                "KEYWORD_INTEGER",
                "KEYWORD_SCALAR",
                "KEYWORD_BOOLEAN",
                "KEYWORD_BIT",
                "KEYWORD_CHARACTER",
                "KEYWORD_VECTOR",
                "KEYWORD_MATRIX",
                "KEYWORD_IF",
                "KEYWORD_THEN",
                "KEYWORD_ELSE",
                "KEYWORD_DO",
                "KEYWORD_END",
                "KEYWORD_SCHEDULE",
                "KEYWORD_PRIORITY",
                "KEYWORD_TERMINATE",
                "KEYWORD_ON",
                "KEYWORD_ERROR",
                "KEYWORD_RESUME",
                "IDENTIFIER",
                "NUMBER",
                "STRING",
                "SEMICOLON",
                "COLON",
                "COMMA",
                "LPAREN",
                "RPAREN",
                "ASSIGN",
                "PLUS",
                "MINUS",
                "MULTIPLY",
                "DIVIDE",
                "POWER",
                "CONCAT",
                "VBAR",
                "AMPERSAND",
                "LT",
                "GT",
                "LE",
                "GE",
                "NE",
                "DOLLAR",
                "KEYWORD_AND",
                "KEYWORD_OR",
                "KEYWORD_XOR",
                "KEYWORD_NOT",
                "HEXSTRING",
                "BITSTRING",
                "OCTSTRING",
                "BITLITERAL",
            ],
            precedence=[
                # ELSE binds to the nearest IF (shift over reduce)
                ("nonassoc", ["KEYWORD_THEN"]),
                ("nonassoc", ["KEYWORD_ELSE"]),
                # Logical / bitwise operators (lowest): OR < XOR < AND
                ("left", ["KEYWORD_OR", "VBAR"]),
                ("left", ["KEYWORD_XOR"]),
                ("left", ["KEYWORD_AND", "AMPERSAND"]),
                # `=` as equality in expressions
                ("left", ["ASSIGN"]),
                ("left", ["LT", "GT", "LE", "GE", "NE"]),
                ("left", ["PLUS", "MINUS", "CONCAT"]),
                ("left", ["MULTIPLY", "DIVIDE"]),
                ("right", ["POWER"]),
                ("right", ["KEYWORD_NOT"]),     # unary NOT binds tightly
                ("left", ["DOLLAR", "DOT"]),    # postfix subscript/field, left-to-right
            ],
        )

        self.symbol_table = SymbolTable()
        self.errors: List[ParserError] = []
        self.ast: Optional[ASTNode] = None
        self._proc_signatures: Dict[str, tuple] = {}
        self._proc_assign_types: Dict[str, list] = {}

        self._define_grammar()
        self.parser = self.pg.build()

    def _find_nested_declares(self, node: ASTNode, out: list) -> None:
        """Collect DeclareNodes inside a node's nested executable blocks
        (DO/IF/FOR). Scope-defining blocks (TASK/PROCEDURE/COMPOOL/PROGRAM)
        own their declaration regions and are order-checked separately, so we
        do not descend into them here."""
        scope_blocks = (ProgramNode, TaskNode, ProcedureNode, CompoolNode)
        for value in vars(node).values():
            children = (value if isinstance(value, list)
                        else [value] if isinstance(value, ASTNode) else [])
            for child in children:
                if not isinstance(child, ASTNode):
                    continue
                if isinstance(child, scope_blocks):
                    continue
                if isinstance(child, DeclareNode):
                    out.append(child)
                self._find_nested_declares(child, out)

    def _check_declaration_order(self, body: list, kind: str) -> None:
        """HAL/S requires all DECLAREs to precede the first executable
        statement in a block, and forbids declarations inside nested
        executable blocks (DO/IF/FOR). Flag both."""
        declarative = (DeclareNode, StructureDefNode)
        scope_blocks = (TaskNode, ProcedureNode, CompoolNode)
        seen_executable = False
        for stmt in body:
            # Nested unit definitions (TASK/PROCEDURE/COMPOOL) have their own
            # declaration regions, checked separately. They are neither a
            # declaration nor executable code for ordering purposes.
            if isinstance(stmt, scope_blocks):
                continue
            if isinstance(stmt, declarative):
                if seen_executable:
                    self.errors.append(
                        SemanticError(
                            f"DECLARE must precede executable statements in a "
                            f"{kind}; this declaration appears after code has "
                            f"already run (HAL/S forbids late declarations)",
                            getattr(stmt, "line", 0),
                            getattr(stmt, "column", 0),
                        )
                    )
            else:
                seen_executable = True
                nested: list = []
                self._find_nested_declares(stmt, nested)
                for decl in nested:
                    self.errors.append(
                        SemanticError(
                            f"DECLARE may not appear inside an executable block "
                            f"in a {kind}; all declarations must be at the top "
                            f"of the block, before any executable statement",
                            getattr(decl, "line", 0),
                            getattr(decl, "column", 0),
                        )
                    )

    def _type_shape(self, ts):
        """A comparable structural signature of a type (data type, vector/matrix
        dimensions, array dimensions, and element shape) for verifying that a
        CALL argument matches the PROCEDURE parameter it binds to."""
        if ts is None:
            return None
        dims = tuple(ts.dimensions) if getattr(ts, "dimensions", None) else ()
        adims = tuple(ts.array_dims) if getattr(ts, "array_dims", None) else ()
        elem = self._type_shape(ts.element_type) \
            if getattr(ts, "element_type", None) else None
        return (ts.data_type, dims, adims, elem)

    def _check_bit_concat(self, body: list) -> None:
        """`||` on BIT operands concatenates their bit widths. Assigning the
        result to a BIT target whose declared width differs is a compile-time
        size mismatch."""
        var_types: dict = {}
        for stmt in body:
            if isinstance(stmt, DeclareNode):
                for ident in stmt.identifiers:
                    var_types[ident.lower()] = stmt.type_spec

        def bit_width(node):
            if isinstance(node, IdentifierNode):
                ts = var_types.get(node.name.lower())
                if (ts is not None and ts.data_type == DataType.BIT
                        and ts.dimensions):
                    return ts.dimensions[0]
                return None
            if isinstance(node, BinaryOpNode) and node.operator == "||":
                lw = bit_width(node.left)
                rw = bit_width(node.right)
                if lw is not None and rw is not None:
                    return lw + rw
            return None

        for node in self._walk_nodes(body):
            if not isinstance(node, AssignmentNode):
                continue
            val = node.value
            if not (isinstance(val, BinaryOpNode) and val.operator == "||"):
                continue
            concat_w = bit_width(val)
            if concat_w is None:
                continue  # not a BIT concatenation (string concat, etc.)
            tgt = node.target
            if isinstance(tgt, IdentifierNode):
                tts = var_types.get(tgt.name.lower())
                if (tts is not None and tts.data_type == DataType.BIT
                        and tts.dimensions):
                    tw = tts.dimensions[0]
                    if tw != concat_w:
                        self.errors.append(
                            SemanticError(
                                f"BIT concatenation produces BIT({concat_w}) "
                                f"but target '{tgt.name}' is BIT({tw})",
                                getattr(node, "line", 0),
                                getattr(node, "column", 0),
                            )
                        )

    def _check_array_dims(self, body: list) -> None:
        """Element-wise array arithmetic (+, -, *, /) requires both array
        operands to have the same length. Scalar broadcasting (array op scalar)
        is allowed. Mismatched array dimensions are a semantic error."""
        var_types: dict = {}
        for stmt in body:
            if isinstance(stmt, DeclareNode):
                for ident in stmt.identifiers:
                    var_types[ident.lower()] = stmt.type_spec

        def array_size(node):
            if isinstance(node, IdentifierNode):
                ts = var_types.get(node.name.lower())
                if ts is not None and ts.array_size is not None:
                    return ts.array_size
            return None

        for node in self._walk_nodes(body):
            if isinstance(node, BinaryOpNode) and node.operator in (
                    "+", "-", "*", "/"):
                ls = array_size(node.left)
                rs = array_size(node.right)
                if ls is not None and rs is not None and ls != rs:
                    self.errors.append(
                        SemanticError(
                            f"array dimension mismatch: ARRAY({ls}) "
                            f"{node.operator} ARRAY({rs}) -- element-wise "
                            "operations require equal lengths",
                            getattr(node, "line", 0),
                            getattr(node, "column", 0),
                        )
                    )

    def _walk_nodes(self, root):
        """Yield every AST node under a list of statements (or a single node)."""
        stack = list(root) if isinstance(root, list) else [root]
        seen = set()
        while stack:
            n = stack.pop()
            if not hasattr(n, "__dict__") or id(n) in seen:
                continue
            seen.add(id(n))
            yield n
            for v in vars(n).values():
                for x in (v if isinstance(v, list) else [v]):
                    if hasattr(x, "__dict__"):
                        stack.append(x)

    def _check_call_signatures(self, body: list) -> None:
        """Enforce that each CALL matches its PROCEDURE's input/output
        structure: same number of input arguments and ASSIGN (output)
        arguments. HAL/S separates the two at the syntax level, so a CALL
        that supplies the wrong count of either is an error."""
        calls: list = []
        # Declared variable types in this scope, for dimension checking of
        # ASSIGN arguments. Procedure parameters mask (shadow) these names.
        var_types: dict = {}
        for stmt in body:
            if isinstance(stmt, DeclareNode):
                for ident in stmt.identifiers:
                    var_types[ident.lower()] = stmt.type_spec

        def collect(node: ASTNode) -> None:
            for value in vars(node).values():
                children = (value if isinstance(value, list)
                            else [value] if isinstance(value, ASTNode) else [])
                for child in children:
                    if not isinstance(child, ASTNode):
                        continue
                    if isinstance(child, CallNode):
                        calls.append(child)
                    collect(child)

        for stmt in body:
            if isinstance(stmt, CallNode):
                calls.append(stmt)
            collect(stmt)

        for call in calls:
            sig = self._proc_signatures.get(call.name.upper())
            if sig is None:
                continue  # unknown/forward procedure: not checked here
            n_in, n_out = sig
            if len(call.arguments) != n_in:
                self.errors.append(
                    SemanticError(
                        f"CALL {call.name} passes {len(call.arguments)} input "
                        f"argument(s) but the PROCEDURE declares {n_in}",
                        getattr(call, "line", 0), getattr(call, "column", 0),
                    )
                )
            if len(call.assign_args) != n_out:
                self.errors.append(
                    SemanticError(
                        f"CALL {call.name} passes {len(call.assign_args)} "
                        f"ASSIGN output(s) but the PROCEDURE declares {n_out}",
                        getattr(call, "line", 0), getattr(call, "column", 0),
                    )
                )
            # Dimension/type check: each ASSIGN argument that is a plain
            # variable must match the shape of the parameter it binds to.
            ptypes = self._proc_assign_types.get(call.name.upper())
            if ptypes and len(call.assign_args) == len(ptypes):
                for arg, pts in zip(call.assign_args, ptypes):
                    name = getattr(arg, "name", None)
                    if name is None or pts is None:
                        continue
                    ats = var_types.get(name.lower())
                    if ats is None:
                        continue
                    if self._type_shape(ats) != self._type_shape(pts):
                        self.errors.append(
                            SemanticError(
                                f"CALL {call.name} ASSIGN argument "
                                f"'{name}' does not match the type/dimensions "
                                f"of the corresponding PROCEDURE parameter",
                                getattr(call, "line", 0),
                                getattr(call, "column", 0),
                            )
                        )

    def _build_unit(
        self,
        name_tok: Any,
        close_tok: Any,
        statements: list,
        header_tok: Any,
        node_cls=ProgramNode,
        kind: str = "PROGRAM",
    ):
        """Build a PROGRAM or TASK node and check the CLOSE name matches."""
        name = name_tok.value
        close_name = close_tok.value

        if name.upper() != close_name.upper():
            self.errors.append(
                SemanticError(
                    f"CLOSE name '{close_name}' does not match "
                    f"{kind} name '{name}'",
                    getattr(close_tok, "line", 0),
                    getattr(close_tok, "column", 0),
                )
            )

        # Nested TASK / PROCEDURE blocks are pulled out of the statement stream
        # into the unit's `tasks` / `procedures` collections; the rest stay as
        # executable statements.
        tasks = [s for s in statements if isinstance(s, TaskNode)]
        procedures = [s for s in statements if isinstance(s, ProcedureNode)]
        body = [
            s for s in statements
            if not isinstance(s, (TaskNode, ProcedureNode))
        ]

        self._check_declaration_order(body, kind)
        self._check_call_signatures(body)
        self._check_array_dims(body)
        self._check_bit_concat(body)

        return node_cls(
            name=name,
            statements=body,
            tasks=tasks,
            procedures=procedures,
            line=getattr(header_tok, "line", 0),
            column=getattr(header_tok, "column", 0),
        )

    def _build_program(
        self, name_tok: Any, close_tok: Any, statements: list, header_tok: Any
    ) -> ProgramNode:
        """Build a ProgramNode and check the CLOSE name matches the label."""
        return self._build_unit(
            name_tok, close_tok, statements, header_tok, ProgramNode, "PROGRAM"
        )

    def _build_procedure(
        self, name_tok: Any, params: list, statements: list, close_tok: Any,
        return_type: Any = None, assign_params: Any = None,
        is_external: bool = False,
    ) -> ProcedureNode:
        """Build a ProcedureNode and check the CLOSE name matches the label."""
        name = name_tok.value
        close_name = close_tok.value
        assign_params = assign_params or []
        if name.upper() != close_name.upper():
            self.errors.append(
                SemanticError(
                    f"CLOSE name '{close_name}' does not match "
                    f"PROCEDURE name '{name}'",
                    getattr(close_tok, "line", 0),
                    getattr(close_tok, "column", 0),
                )
            )
        # Record the signature (input arity, output arity) so CALL sites can be
        # checked for the same input/output structure HAL/S enforces.
        self._proc_signatures[name.upper()] = (len(params), len(assign_params))
        _bd = {d.identifiers[0].lower(): d.type_spec
               for d in statements if isinstance(d, DeclareNode)
               and len(d.identifiers) == 1}
        self._proc_assign_types[name.upper()] = [
            _bd.get(a.lower()) for a in assign_params]
        # Procedure bodies may not nest further tasks/procedures here; keep all
        # body items as statements (declarations are extracted by codegen).
        self._check_declaration_order(
            statements, "FUNCTION" if return_type is not None else "PROCEDURE")
        return ProcedureNode(
            name=name,
            parameters=params,
            assign_params=assign_params,
            statements=statements,
            return_type=return_type,
            is_external=is_external,
            line=getattr(name_tok, "line", 0),
            column=getattr(name_tok, "column", 0),
        )

    def _define_grammar(self) -> None:
        """Define HAL/S grammar rules (program/task header + statements)."""

        # ---- top level: a compilation is a sequence of units --------------
        # (COMPOOL / PROGRAM / TASK). A single-unit file returns that unit
        # directly for backward compatibility; multiple units return a
        # CompilationNode.
        @self.pg.production("compilation : unit_list")
        def compilation(p: List[Any]) -> ASTNode:
            units = p[0]
            if len(units) == 1:
                return units[0]
            return CompilationNode(units=units)

        @self.pg.production("unit_list : unit_list unit")
        def unit_list(p: List[Any]) -> list:
            return p[0] + [p[1]]

        @self.pg.production("unit_list : unit")
        def unit_list_single(p: List[Any]) -> list:
            return [p[0]]

        @self.pg.production("unit : program")
        @self.pg.production("unit : compool_block")
        def unit(p: List[Any]) -> ASTNode:
            return p[0]

        # ---- COMPOOL: <name>: COMPOOL; <declares> CLOSE <name>; -----------
        @self.pg.production(
            "compool_block : IDENTIFIER COLON KEYWORD_COMPOOL SEMICOLON "
            "statement_list KEYWORD_CLOSE IDENTIFIER SEMICOLON"
        )
        def compool_block(p: List[Any]) -> CompoolNode:
            name = p[0].value
            close_name = p[6].value
            if name.upper() != close_name.upper():
                self.errors.append(
                    SemanticError(
                        f"CLOSE name '{close_name}' does not match "
                        f"COMPOOL name '{name}'",
                        getattr(p[6], "line", 0),
                        getattr(p[6], "column", 0),
                    )
                )
            decls = [s for s in p[4] if isinstance(s, DeclareNode)]
            return CompoolNode(
                name=name,
                declarations=decls,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # Correct HAL/S form:  <name>: PROGRAM; <statements> CLOSE <name>;
        @self.pg.production(
            "program : IDENTIFIER COLON KEYWORD_PROGRAM SEMICOLON "
            "statement_list KEYWORD_CLOSE IDENTIFIER SEMICOLON"
        )
        def program_labeled(p: List[Any]) -> ProgramNode:
            return self._build_program(
                name_tok=p[0], close_tok=p[6], statements=p[4], header_tok=p[0]
            )

        # Legacy/foundation form: PROGRAM <name>; <statements> CLOSE <name>;
        @self.pg.production(
            "program : KEYWORD_PROGRAM IDENTIFIER SEMICOLON "
            "statement_list KEYWORD_CLOSE IDENTIFIER SEMICOLON"
        )
        def program_keyword_first(p: List[Any]) -> ProgramNode:
            return self._build_program(
                name_tok=p[1], close_tok=p[5], statements=p[3], header_tok=p[0]
            )

        # TASK block:  <name>: TASK; <statements> CLOSE <name>;
        # Reusable as a standalone unit (a whole file) or nested in a PROGRAM.
        @self.pg.production(
            "task_block : IDENTIFIER COLON KEYWORD_TASK SEMICOLON "
            "statement_list KEYWORD_CLOSE IDENTIFIER SEMICOLON"
        )
        def task_block(p: List[Any]) -> TaskNode:
            return self._build_unit(
                name_tok=p[0],
                close_tok=p[6],
                statements=p[4],
                header_tok=p[0],
                node_cls=TaskNode,
                kind="TASK",
            )

        # A file whose top-level unit is a bare TASK.
        @self.pg.production("program : task_block")
        def program_task(p: List[Any]) -> TaskNode:
            return p[0]

        # PROCEDURE block:  <name>: PROCEDURE[(<params>)]; <stmts> CLOSE <name>;
        # Nested within a PROGRAM (or TASK); pulled into `procedures` by
        # _build_unit. Parameter types are declared in the body (HAL/S style).
        @self.pg.production(
            "procedure_block : IDENTIFIER COLON KEYWORD_PROCEDURE "
            "LPAREN param_list RPAREN SEMICOLON "
            "statement_list KEYWORD_CLOSE IDENTIFIER SEMICOLON"
        )
        def procedure_block(p: List[Any]) -> ProcedureNode:
            return self._build_procedure(
                name_tok=p[0], params=p[4], statements=p[7], close_tok=p[9]
            )

        # PROCEDURE(inputs) ASSIGN(outputs); -- HAL/S separates inputs from
        # outputs at the syntax level; ASSIGN names are the writable outputs.
        @self.pg.production(
            "procedure_block : IDENTIFIER COLON KEYWORD_PROCEDURE "
            "LPAREN param_list RPAREN KEYWORD_ASSIGN LPAREN param_list RPAREN "
            "SEMICOLON statement_list KEYWORD_CLOSE IDENTIFIER SEMICOLON"
        )
        def procedure_block_assign(p: List[Any]) -> ProcedureNode:
            return self._build_procedure(
                name_tok=p[0], params=p[4], statements=p[11],
                close_tok=p[13], assign_params=p[8]
            )

        # PROCEDURE ASSIGN(outputs);  (no input parameters)
        @self.pg.production(
            "procedure_block : IDENTIFIER COLON KEYWORD_PROCEDURE "
            "KEYWORD_ASSIGN LPAREN param_list RPAREN "
            "SEMICOLON statement_list KEYWORD_CLOSE IDENTIFIER SEMICOLON"
        )
        def procedure_block_assign_noinput(p: List[Any]) -> ProcedureNode:
            return self._build_procedure(
                name_tok=p[0], params=[], statements=p[8],
                close_tok=p[10], assign_params=p[5]
            )

        @self.pg.production(
            "procedure_block : IDENTIFIER COLON KEYWORD_PROCEDURE SEMICOLON "
            "statement_list KEYWORD_CLOSE IDENTIFIER SEMICOLON"
        )
        def procedure_block_noparams(p: List[Any]) -> ProcedureNode:
            return self._build_procedure(
                name_tok=p[0], params=[], statements=p[4], close_tok=p[6]
            )

        # NONHAL: opaque external (FFI) function -- a signature with no HAL/S
        # body. The body holds only parameter declarations; the code generator
        # emits an `extern "C"` binding and leaves resolution to the linker.
        @self.pg.production(
            "procedure_block : IDENTIFIER COLON KEYWORD_FUNCTION "
            "LPAREN param_list RPAREN type_spec KEYWORD_NONHAL SEMICOLON "
            "statement_list KEYWORD_CLOSE IDENTIFIER SEMICOLON"
        )
        def function_block_nonhal(p: List[Any]) -> ProcedureNode:
            return self._build_procedure(
                name_tok=p[0], params=p[4], statements=p[9],
                close_tok=p[11], return_type=p[6], is_external=True,
            )

        # FUNCTION: like a procedure but with an explicit return type.
        @self.pg.production(
            "procedure_block : IDENTIFIER COLON KEYWORD_FUNCTION "
            "LPAREN param_list RPAREN type_spec SEMICOLON "
            "statement_list KEYWORD_CLOSE IDENTIFIER SEMICOLON"
        )
        def function_block(p: List[Any]) -> ProcedureNode:
            return self._build_procedure(
                name_tok=p[0], params=p[4], statements=p[8],
                close_tok=p[10], return_type=p[6]
            )

        @self.pg.production(
            "procedure_block : IDENTIFIER COLON KEYWORD_FUNCTION type_spec "
            "SEMICOLON statement_list KEYWORD_CLOSE IDENTIFIER SEMICOLON"
        )
        def function_block_noparams(p: List[Any]) -> ProcedureNode:
            return self._build_procedure(
                name_tok=p[0], params=[], statements=p[5],
                close_tok=p[7], return_type=p[3]
            )

        # Return type before FUNCTION: NAME: SCALAR FUNCTION(params);
        @self.pg.production(
            "procedure_block : IDENTIFIER COLON type_spec KEYWORD_FUNCTION "
            "LPAREN param_list RPAREN SEMICOLON "
            "statement_list KEYWORD_CLOSE IDENTIFIER SEMICOLON"
        )
        def function_block_prefix_type(p: List[Any]) -> ProcedureNode:
            return self._build_procedure(
                name_tok=p[0], params=p[5], statements=p[8],
                close_tok=p[10], return_type=p[2]
            )

        @self.pg.production("param_list : param_list COMMA IDENTIFIER")
        def param_list(p: List[Any]) -> list:
            return p[0] + [p[2].value]

        @self.pg.production("param_list : IDENTIFIER")
        def param_list_single(p: List[Any]) -> list:
            return [p[0].value]

        @self.pg.production("statement_list : statement_list statement")
        def statement_list(p: List[Any]) -> list:
            return p[0] + [p[1]]

        @self.pg.production("statement_list : ")
        def statement_list_empty(p: List[Any]) -> list:
            return []

        # ---- statements -------------------------------------------------
        # TASK blocks may appear nested within a PROGRAM body, interspersed
        # with statements (HAL/S Language Spec sec. 3); they are pulled out
        # into the unit's `tasks` collection by _build_unit.
        @self.pg.production("statement : task_block")
        @self.pg.production("statement : procedure_block")
        @self.pg.production("statement : compool_block")
        @self.pg.production("statement : structure_def")
        @self.pg.production("statement : on_error_block")
        @self.pg.production("statement : resume_statement")
        @self.pg.production("statement : return_statement")
        @self.pg.production("statement : declare_statement")
        @self.pg.production("statement : assignment_statement")
        @self.pg.production("statement : call_statement")
        @self.pg.production("statement : if_statement")
        @self.pg.production("statement : do_block")
        @self.pg.production("statement : schedule_statement")
        @self.pg.production("statement : wait_statement")
        @self.pg.production("statement : signal_statement")
        @self.pg.production("statement : cancel_statement")
        @self.pg.production("statement : terminate_statement")
        @self.pg.production("statement : write_statement")
        @self.pg.production("statement : read_statement")
        def statement(p: List[Any]) -> ASTNode:
            return p[0]

        # ASSIGN <pointer> TO <target>;  -- NAME (pointer) binding, distinct
        # from value assignment (=). KEYWORD_ASSIGN is otherwise always followed
        # by '(' (procedure/CALL output lists), so an IDENTIFIER after it is
        # unambiguous.
        @self.pg.production(
            "statement : KEYWORD_ASSIGN IDENTIFIER KEYWORD_TO IDENTIFIER "
            "SEMICOLON"
        )
        def name_assign_statement(p: List[Any]) -> NameAssignNode:
            return NameAssignNode(
                pointer=p[1].value,
                target=p[3].value,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # GO TO <label>;  -- HAL/S unconditional branch.
        @self.pg.production(
            "statement : KEYWORD_GO KEYWORD_TO IDENTIFIER SEMICOLON"
        )
        def goto_statement(p: List[Any]) -> GotoNode:
            return GotoNode(
                label=p[2].value,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # Labelled statement: <name>: <statement>. The block keywords
        # (TASK/COMPOOL/PROCEDURE/FUNCTION) are not in FIRST(statement), so this
        # does not collide with the `NAME: PROGRAM`-style block headers.
        @self.pg.production("statement : IDENTIFIER COLON statement")
        def labeled_statement(p: List[Any]) -> LabelNode:
            return LabelNode(
                name=p[0].value,
                statement=p[2],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # ---- DECLARE ----------------------------------------------------
        @self.pg.production(
            "declare_statement : KEYWORD_DECLARE IDENTIFIER type_spec SEMICOLON"
        )
        def declare_statement(p: List[Any]) -> DeclareNode:
            return DeclareNode(
                identifiers=[p[1].value],
                type_spec=p[2],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # NAME (pointer) declaration: DECLARE X NAME <type>; -- X is a pointer
        # to a value of <type>, bound later with ASSIGN X TO Y.
        @self.pg.production(
            "declare_statement : KEYWORD_DECLARE IDENTIFIER KEYWORD_NAME "
            "type_spec SEMICOLON"
        )
        def declare_name(p: List[Any]) -> DeclareNode:
            ts = p[3]
            ts.is_name = True
            return DeclareNode(
                identifiers=[p[1].value],
                type_spec=ts,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # Array of structures: DECLARE C ARRAY(5) OF STAR_DATA STRUCTURE;
        # Unified array: DECLARE G ARRAY(d1, d2, ...) OF <element>;
        # <element> is a type_spec (VECTOR(3), INTEGER, ...) or a structure
        # template (IDENTIFIER STRUCTURE). Both dimension forms flow through
        # array_dim_list, so there is a single ARRAY( ... ) prefix (no conflict).
        # `OF` is optional in HAL/S array declarations: `ARRAY(n) SCALAR` is the
        # canonical form, `ARRAY(n) OF SCALAR` an accepted variant. Both flow
        # through array_of_element so the rest of the rule is shared. An optional
        # INITIAL(...) clause supplies element values.
        @self.pg.production("array_of_element : KEYWORD_OF array_element")
        def array_of_with_of(p: List[Any]) -> TypeSpec:
            return p[1]

        @self.pg.production("array_of_element : array_element")
        def array_of_bare(p: List[Any]) -> TypeSpec:
            return p[0]

        def _mk_array(p, ident_tok, dims, element, init=None):
            return DeclareNode(
                identifiers=[ident_tok.value],
                type_spec=TypeSpec(
                    data_type=element.data_type,
                    array_dims=dims,
                    array_size=dims[0],
                    element_type=element,
                ),
                attributes={"initial": init} if init is not None else {},
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production(
            "declare_statement : KEYWORD_DECLARE IDENTIFIER KEYWORD_ARRAY "
            "LPAREN array_dim_list RPAREN array_of_element SEMICOLON"
        )
        def declare_array_no_of(p: List[Any]) -> DeclareNode:
            return _mk_array(p, p[1], p[4], p[6])

        @self.pg.production(
            "declare_statement : KEYWORD_DECLARE IDENTIFIER KEYWORD_ARRAY "
            "LPAREN array_dim_list RPAREN array_of_element "
            "KEYWORD_INITIAL LPAREN arg_list RPAREN SEMICOLON"
        )
        def declare_array_initial(p: List[Any]) -> DeclareNode:
            return _mk_array(p, p[1], p[4], p[6], init=p[9])

        @self.pg.production("array_element : type_spec")
        def array_element_type(p: List[Any]) -> TypeSpec:
            return p[0]

        @self.pg.production("array_element : IDENTIFIER KEYWORD_STRUCTURE")
        def array_element_struct(p: List[Any]) -> TypeSpec:
            return TypeSpec(data_type=DataType.STRUCTURE, type_name=p[0].value)

        @self.pg.production("array_dim_list : NUMBER")
        def array_dim_single(p: List[Any]) -> list:
            return [int(p[0].value)]

        @self.pg.production("array_dim_list : array_dim_list COMMA NUMBER")
        def array_dim_more(p: List[Any]) -> list:
            return p[0] + [int(p[2].value)]

        # Memory overlay: DECLARE VIEW <type> EQUATE TARGET;
        @self.pg.production(
            "declare_statement : KEYWORD_DECLARE IDENTIFIER type_spec "
            "KEYWORD_EQUATE IDENTIFIER SEMICOLON"
        )
        def declare_equate(p: List[Any]) -> DeclareNode:
            return DeclareNode(
                identifiers=[p[1].value],
                type_spec=p[2],
                attributes={"equate": p[4].value},
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # Multiple variables sharing one type: DECLARE A, B, C VECTOR(3);
        @self.pg.production(
            "declare_statement : KEYWORD_DECLARE IDENTIFIER COMMA "
            "id_comma_list type_spec SEMICOLON"
        )
        def declare_multi(p: List[Any]) -> DeclareNode:
            return DeclareNode(
                identifiers=[p[1].value] + p[3],
                type_spec=p[4],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # Comma-separated identifier list (shared by multi-declare/assign).
        @self.pg.production("id_comma_list : IDENTIFIER")
        def id_comma_single(p: List[Any]) -> List[str]:
            return [p[0].value]

        @self.pg.production("id_comma_list : id_comma_list COMMA IDENTIFIER")
        def id_comma_more(p: List[Any]) -> List[str]:
            return p[0] + [p[2].value]

        @self.pg.production(
            "declare_statement : KEYWORD_DECLARE IDENTIFIER type_spec "
            "KEYWORD_INITIAL LPAREN arg_list RPAREN SEMICOLON"
        )
        def declare_initial(p: List[Any]) -> DeclareNode:
            values = p[5]
            node = DeclareNode(
                identifiers=[p[1].value],
                type_spec=p[2],
                initial_values=values,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )
            # A single scalar INITIAL also populates initial_value for
            # backward compatibility with single-value consumers.
            if len(values) == 1:
                node.initial_value = values[0]
            return node

        @self.pg.production(
            "declare_statement : KEYWORD_DECLARE IDENTIFIER type_spec "
            "KEYWORD_STATIC SEMICOLON"
        )
        def declare_static(p: List[Any]) -> DeclareNode:
            return DeclareNode(
                identifiers=[p[1].value],
                type_spec=p[2],
                attributes={"static": True},
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production(
            "declare_statement : KEYWORD_DECLARE IDENTIFIER type_spec "
            "KEYWORD_STATIC KEYWORD_INITIAL LPAREN arg_list RPAREN SEMICOLON"
        )
        def declare_static_initial(p: List[Any]) -> DeclareNode:
            values = p[6]
            node = DeclareNode(
                identifiers=[p[1].value],
                type_spec=p[2],
                attributes={"static": True},
                initial_values=values,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )
            if len(values) == 1:
                node.initial_value = values[0]
            return node

        # CONSTANT before the type: DECLARE NAME CONSTANT SCALAR(value);
        # Restricted to bare scalar/integer types so the value parens cannot be
        # confused with a VECTOR(n)/MATRIX type_spec's own parens (no conflict).
        @self.pg.production("const_scalar_type : KEYWORD_SCALAR")
        @self.pg.production("const_scalar_type : KEYWORD_INTEGER")
        def const_scalar_type(p: List[Any]) -> TypeSpec:
            dt = (DataType.SCALAR
                  if p[0].gettokentype() == "KEYWORD_SCALAR"
                  else DataType.INTEGER)
            return TypeSpec(data_type=dt)

        @self.pg.production(
            "declare_statement : KEYWORD_DECLARE IDENTIFIER KEYWORD_CONSTANT "
            "const_scalar_type LPAREN expression RPAREN SEMICOLON"
        )
        def declare_constant_prefix(p: List[Any]) -> DeclareNode:
            return DeclareNode(
                identifiers=[p[1].value],
                type_spec=p[3],
                is_constant=True,
                initial_value=p[5],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # Real-time EVENT variable: DECLARE name EVENT;
        @self.pg.production(
            "declare_statement : KEYWORD_DECLARE IDENTIFIER KEYWORD_EVENT "
            "SEMICOLON"
        )
        def declare_event(p: List[Any]) -> DeclareNode:
            return DeclareNode(
                identifiers=[p[1].value],
                type_spec=TypeSpec(data_type=DataType.EVENT),
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # Untyped: DECLARE PI CONSTANT (value);  -> inferred SCALAR.
        @self.pg.production(
            "declare_statement : KEYWORD_DECLARE IDENTIFIER KEYWORD_CONSTANT "
            "LPAREN expression RPAREN SEMICOLON"
        )
        def declare_constant_untyped(p: List[Any]) -> DeclareNode:
            return DeclareNode(
                identifiers=[p[1].value],
                type_spec=TypeSpec(data_type=DataType.SCALAR),
                is_constant=True,
                initial_value=p[4],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production(
            "declare_statement : KEYWORD_DECLARE IDENTIFIER type_spec "
            "KEYWORD_CONSTANT LPAREN arg_list RPAREN SEMICOLON"
        )
        def declare_constant(p: List[Any]) -> DeclareNode:
            values = p[5]
            node = DeclareNode(
                identifiers=[p[1].value],
                type_spec=p[2],
                is_constant=True,
                initial_values=values,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )
            if len(values) == 1:
                node.initial_value = values[0]
            return node

        # ON ERROR(code): <handler statement>  (registers a fault handler)
        @self.pg.production(
            "on_error_block : KEYWORD_ON KEYWORD_ERROR LPAREN NUMBER "
            "RPAREN COLON do_block"
        )
        def on_error_block(p: List[Any]) -> OnErrorNode:
            return OnErrorNode(
                code=int(p[3].value),
                body=p[6],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # RESUME;  (return control to the statement after the fault)
        @self.pg.production("resume_statement : KEYWORD_RESUME SEMICOLON")
        def resume_statement(p: List[Any]) -> ResumeNode:
            return ResumeNode(
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # RETURN <expr>;  and bare RETURN;
        @self.pg.production("return_statement : KEYWORD_RETURN expression SEMICOLON")
        def return_statement(p: List[Any]) -> ReturnNode:
            return ReturnNode(
                value=p[1],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production("return_statement : KEYWORD_RETURN SEMICOLON")
        def return_statement_bare(p: List[Any]) -> ReturnNode:
            return ReturnNode(
                value=None,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # STRUCTURE <name>: <field declares> CLOSE <name>;
        @self.pg.production(
            "structure_def : KEYWORD_STRUCTURE IDENTIFIER COLON "
            "statement_list KEYWORD_CLOSE IDENTIFIER SEMICOLON"
        )
        def structure_def(p: List[Any]) -> StructureDefNode:
            name = p[1].value
            close_name = p[5].value
            if name.upper() != close_name.upper():
                self.errors.append(
                    SemanticError(
                        f"CLOSE name '{close_name}' does not match "
                        f"STRUCTURE name '{name}'",
                        getattr(p[5], "line", 0),
                        getattr(p[5], "column", 0),
                    )
                )
            fields = [s for s in p[3] if isinstance(s, DeclareNode)]
            return StructureDefNode(
                name=name,
                fields=fields,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # DECLARE <var> <template> STRUCTURE;  (instantiate a structure)
        @self.pg.production(
            "declare_statement : KEYWORD_DECLARE IDENTIFIER IDENTIFIER "
            "KEYWORD_STRUCTURE SEMICOLON"
        )
        def declare_structure(p: List[Any]) -> DeclareNode:
            return DeclareNode(
                identifiers=[p[1].value],
                type_spec=TypeSpec(
                    data_type=DataType.STRUCTURE, type_name=p[2].value
                ),
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production("type_spec : KEYWORD_INTEGER")
        def type_integer(p: List[Any]) -> TypeSpec:
            return TypeSpec(data_type=DataType.INTEGER)

        @self.pg.production("type_spec : KEYWORD_SCALAR")
        def type_scalar(p: List[Any]) -> TypeSpec:
            return TypeSpec(data_type=DataType.SCALAR)

        # Explicit precision: SCALAR SINGLE -> f32 (32-bit), SCALAR DOUBLE ->
        # f64 (64-bit). The precision is tracked so mixed-precision arithmetic
        # can be promoted to the wider type.
        @self.pg.production("type_spec : KEYWORD_SCALAR KEYWORD_SINGLE")
        def type_scalar_single(p: List[Any]) -> TypeSpec:
            return TypeSpec(data_type=DataType.SCALAR, precision="single")

        @self.pg.production("type_spec : KEYWORD_SCALAR KEYWORD_DOUBLE")
        def type_scalar_double(p: List[Any]) -> TypeSpec:
            return TypeSpec(data_type=DataType.SCALAR, precision="double")

        @self.pg.production("type_spec : KEYWORD_BOOLEAN")
        def type_boolean(p: List[Any]) -> TypeSpec:
            return TypeSpec(data_type=DataType.BOOLEAN)

        @self.pg.production("type_spec : KEYWORD_BIT")
        def type_bit(p: List[Any]) -> TypeSpec:
            return TypeSpec(data_type=DataType.BIT)

        @self.pg.production("type_spec : KEYWORD_BIT LPAREN NUMBER RPAREN")
        def type_bit_sized(p: List[Any]) -> TypeSpec:
            return TypeSpec(
                data_type=DataType.BIT, dimensions=[int(p[2].value)]
            )

        @self.pg.production("type_spec : KEYWORD_CHARACTER")
        def type_character(p: List[Any]) -> TypeSpec:
            return TypeSpec(data_type=DataType.CHARACTER)

        @self.pg.production(
            "type_spec : KEYWORD_CHARACTER LPAREN NUMBER RPAREN"
        )
        def type_character_sized(p: List[Any]) -> TypeSpec:
            return TypeSpec(
                data_type=DataType.CHARACTER, dimensions=[int(p[2].value)]
            )

        @self.pg.production(
            "type_spec : KEYWORD_VECTOR LPAREN NUMBER RPAREN"
        )
        def type_vector(p: List[Any]) -> TypeSpec:
            return TypeSpec(
                data_type=DataType.VECTOR, dimensions=[int(p[2].value)]
            )

        @self.pg.production(
            "type_spec : KEYWORD_MATRIX LPAREN NUMBER COMMA NUMBER RPAREN"
        )
        def type_matrix(p: List[Any]) -> TypeSpec:
            return TypeSpec(
                data_type=DataType.MATRIX,
                dimensions=[int(p[2].value), int(p[4].value)],
            )

        # ---- assignment -------------------------------------------------
        @self.pg.production(
            "assignment_statement : IDENTIFIER ASSIGN expression SEMICOLON"
        )
        def assignment_statement(p: List[Any]) -> AssignmentNode:
            target = IdentifierNode(
                name=p[0].value,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )
            return AssignmentNode(
                target=target,
                value=p[2],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # Multiple assignment: A, B, ... = <expr>;  (one RHS to several targets)
        @self.pg.production(
            "assignment_statement : IDENTIFIER COMMA id_comma_list "
            "ASSIGN expression SEMICOLON"
        )
        def multi_assignment(p: List[Any]) -> MultiAssignNode:
            names = [p[0].value] + p[2]
            targets = [
                IdentifierNode(name=n, line=getattr(p[0], "line", 0),
                               column=getattr(p[0], "column", 0))
                for n in names
            ]
            return MultiAssignNode(
                targets=targets,
                value=p[4],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # Structure field lvalue: VAR.FIELD = expr;
        @self.pg.production(
            "assignment_statement : IDENTIFIER DOT IDENTIFIER "
            "ASSIGN expression SEMICOLON"
        )
        def field_assignment(p: List[Any]) -> AssignmentNode:
            operand = IdentifierNode(
                name=p[0].value,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )
            target = FieldAccessNode(
                operand=operand,
                field_name=p[2].value,
                line=getattr(p[1], "line", 0),
                column=getattr(p[1], "column", 0),
            )
            return AssignmentNode(
                target=target,
                value=p[4],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # Subscripted lvalue: VEC$IDX = expr;  (bit or vector element)
        @self.pg.production(
            "assignment_statement : IDENTIFIER DOLLAR subscript_index "
            "ASSIGN expression SEMICOLON"
        )
        def subscript_assignment(p: List[Any]) -> AssignmentNode:
            operand = IdentifierNode(
                name=p[0].value,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )
            target = SubscriptNode(
                operand=operand,
                index=p[2],
                line=getattr(p[1], "line", 0),
                column=getattr(p[1], "column", 0),
            )
            return AssignmentNode(
                target=target,
                value=p[4],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # Chained lvalue: ARRAY$IDX.FIELD = expr;  (struct-array element field)
        @self.pg.production(
            "assignment_statement : IDENTIFIER DOLLAR subscript_index DOT "
            "IDENTIFIER ASSIGN expression SEMICOLON"
        )
        def subscript_field_assignment(p: List[Any]) -> AssignmentNode:
            operand = IdentifierNode(
                name=p[0].value,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )
            sub = SubscriptNode(
                operand=operand,
                index=p[2],
                line=getattr(p[1], "line", 0),
                column=getattr(p[1], "column", 0),
            )
            target = FieldAccessNode(
                operand=sub,
                field_name=p[4].value,
                line=getattr(p[3], "line", 0),
                column=getattr(p[3], "column", 0),
            )
            return AssignmentNode(
                target=target,
                value=p[6],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # Paren-subscript lvalue: MAT(r, c) = expr;  or  VEC(i) = expr;
        # Matrix/vector element assignment. Shares MAT(r,c) read syntax; the
        # trailing ASSIGN disambiguates a target from an equality expression.
        @self.pg.production(
            "assignment_statement : IDENTIFIER LPAREN subscript_arg_list RPAREN "
            "ASSIGN expression SEMICOLON"
        )
        def paren_element_assignment(p: List[Any]) -> AssignmentNode:
            operand = IdentifierNode(
                name=p[0].value,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )
            target = SubscriptNode(
                operand=operand,
                index=IndexListNode(
                    indices=p[2],
                    line=getattr(p[1], "line", 0),
                    column=getattr(p[1], "column", 0),
                ),
                line=getattr(p[1], "line", 0),
                column=getattr(p[1], "column", 0),
            )
            return AssignmentNode(
                target=target,
                value=p[5],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # ---- DO; ... END; ----------------------------------------------
        @self.pg.production(
            "do_block : KEYWORD_DO SEMICOLON statement_list KEYWORD_END SEMICOLON"
        )
        def do_block(p: List[Any]) -> DoBlockNode:
            return DoBlockNode(
                statements=p[2],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # ---- DO CASE <selector>; <branch> <branch> ... END; ------------
        @self.pg.production(
            "do_block : KEYWORD_DO KEYWORD_CASE expression SEMICOLON "
            "statement_list KEYWORD_END SEMICOLON"
        )
        def do_case_block(p: List[Any]) -> CaseNode:
            return CaseNode(
                selector=p[2],
                branches=p[4],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # ---- DO WHILE <cond>; ... END; ---------------------------------
        @self.pg.production(
            "do_block : KEYWORD_DO KEYWORD_WHILE expression SEMICOLON "
            "statement_list KEYWORD_END SEMICOLON"
        )
        def do_while_block(p: List[Any]) -> WhileNode:
            return WhileNode(
                condition=p[2],
                statements=p[4],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # ---- DO FOR <var> = <start> TO <end> [BY <step>]; ... END; -------
        @self.pg.production(
            "do_block : KEYWORD_DO KEYWORD_FOR IDENTIFIER ASSIGN expression "
            "KEYWORD_TO expression SEMICOLON statement_list KEYWORD_END SEMICOLON"
        )
        def do_for_block(p: List[Any]) -> ForNode:
            return ForNode(
                var=p[2].value,
                start=p[4],
                end=p[6],
                step=None,
                statements=p[8],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production(
            "do_block : KEYWORD_DO KEYWORD_FOR IDENTIFIER ASSIGN expression "
            "KEYWORD_TO expression KEYWORD_BY expression SEMICOLON "
            "statement_list KEYWORD_END SEMICOLON"
        )
        def do_for_step_block(p: List[Any]) -> ForNode:
            return ForNode(
                var=p[2].value,
                start=p[4],
                end=p[6],
                step=p[8],
                statements=p[10],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # ---- CALL <proc>[(<args>)]; -----------------------------------
        @self.pg.production(
            "call_statement : KEYWORD_CALL IDENTIFIER LPAREN arg_list RPAREN SEMICOLON"
        )
        def call_statement(p: List[Any]) -> CallNode:
            return CallNode(
                name=p[1].value,
                arguments=p[3],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # CALL <proc>(<inputs>) ASSIGN(<outputs>);
        @self.pg.production(
            "call_statement : KEYWORD_CALL IDENTIFIER LPAREN arg_list RPAREN "
            "KEYWORD_ASSIGN LPAREN arg_list RPAREN SEMICOLON"
        )
        def call_statement_assign(p: List[Any]) -> CallNode:
            return CallNode(
                name=p[1].value,
                arguments=p[3],
                assign_args=p[7],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # CALL <proc> ASSIGN(<outputs>);  (no input parameters)
        @self.pg.production(
            "call_statement : KEYWORD_CALL IDENTIFIER KEYWORD_ASSIGN "
            "LPAREN arg_list RPAREN SEMICOLON"
        )
        def call_statement_assign_noinput(p: List[Any]) -> CallNode:
            return CallNode(
                name=p[1].value,
                arguments=[],
                assign_args=p[4],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production(
            "call_statement : KEYWORD_CALL IDENTIFIER LPAREN RPAREN SEMICOLON"
        )
        def call_statement_noargs(p: List[Any]) -> CallNode:
            return CallNode(
                name=p[1].value,
                arguments=[],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production("call_statement : KEYWORD_CALL IDENTIFIER SEMICOLON")
        def call_statement_bare(p: List[Any]) -> CallNode:
            return CallNode(
                name=p[1].value,
                arguments=[],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # ---- IF / THEN / ELSE ------------------------------------------
        @self.pg.production(
            "if_statement : KEYWORD_IF expression KEYWORD_THEN statement"
        )
        def if_statement(p: List[Any]) -> IfNode:
            return IfNode(
                condition=p[1],
                then_block=p[3],
                else_block=None,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production(
            "if_statement : KEYWORD_IF expression KEYWORD_THEN statement "
            "KEYWORD_ELSE statement"
        )
        def if_else_statement(p: List[Any]) -> IfNode:
            return IfNode(
                condition=p[1],
                then_block=p[3],
                else_block=p[5],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # ---- SCHEDULE / TERMINATE --------------------------------------
        @self.pg.production(
            "schedule_statement : KEYWORD_SCHEDULE IDENTIFIER KEYWORD_PRIORITY "
            "LPAREN expression RPAREN SEMICOLON"
        )
        def schedule_statement(p: List[Any]) -> ScheduleNode:
            return ScheduleNode(
                task_name=p[1].value,
                priority=p[4],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # Cyclic at a priority: SCHEDULE T PRIORITY(p) REPEAT EVERY <period>;
        # A periodic task dispatched at a fixed priority (e.g. a 25 Hz display
        # loop). The host build runs one representative cycle; the period is
        # advisory (see the RTOS backend for faithful cyclic dispatch).
        @self.pg.production(
            "schedule_statement : KEYWORD_SCHEDULE IDENTIFIER KEYWORD_PRIORITY "
            "LPAREN expression RPAREN KEYWORD_REPEAT KEYWORD_EVERY expression "
            "SEMICOLON"
        )
        def schedule_priority_cyclic(p: List[Any]) -> ScheduleNode:
            return ScheduleNode(
                task_name=p[1].value,
                priority=p[4],
                period=p[8],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # Timed/cyclic: SCHEDULE T IN <delay> REPEAT EVERY <period>;
        @self.pg.production(
            "schedule_statement : KEYWORD_SCHEDULE IDENTIFIER KEYWORD_IN "
            "expression KEYWORD_REPEAT KEYWORD_EVERY expression SEMICOLON"
        )
        def schedule_cyclic(p: List[Any]) -> ScheduleNode:
            return ScheduleNode(
                task_name=p[1].value,
                delay=p[3],
                period=p[6],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # One-shot delayed: SCHEDULE T IN <delay>;
        @self.pg.production(
            "schedule_statement : KEYWORD_SCHEDULE IDENTIFIER KEYWORD_IN "
            "expression SEMICOLON"
        )
        def schedule_delayed(p: List[Any]) -> ScheduleNode:
            return ScheduleNode(
                task_name=p[1].value,
                delay=p[3],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # WAIT UNTIL <condition>;
        @self.pg.production(
            "wait_statement : KEYWORD_WAIT KEYWORD_UNTIL expression SEMICOLON"
        )
        def wait_statement(p: List[Any]) -> WaitNode:
            return WaitNode(
                condition=p[2],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # WAIT FOR <boolean expression>;  -- blocks until an expression over
        # EVENT operands holds. Routed through the standard expression parser,
        # so `(A AND B) OR C` builds the usual AND/OR tree with EVENT leaves.
        @self.pg.production(
            "wait_statement : KEYWORD_WAIT KEYWORD_FOR expression SEMICOLON"
        )
        def wait_for_statement(p: List[Any]) -> WaitNode:
            return WaitNode(
                condition=p[2],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # WAIT <event>;  (real-time event; host stub)
        @self.pg.production(
            "wait_statement : KEYWORD_WAIT IDENTIFIER SEMICOLON"
        )
        def wait_event_statement(p: List[Any]) -> WaitNode:
            return WaitNode(
                event=p[1].value,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # SIGNAL <event>;  (real-time event; host stub)
        @self.pg.production(
            "signal_statement : KEYWORD_SIGNAL IDENTIFIER SEMICOLON"
        )
        def signal_statement(p: List[Any]) -> SignalNode:
            return SignalNode(
                name=p[1].value,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # CANCEL <task>;
        @self.pg.production("cancel_statement : KEYWORD_CANCEL IDENTIFIER SEMICOLON")
        def cancel_statement(p: List[Any]) -> CancelNode:
            return CancelNode(
                task_name=p[1].value,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production("terminate_statement : KEYWORD_TERMINATE SEMICOLON")
        def terminate_statement(p: List[Any]) -> TerminateNode:
            return TerminateNode(
                task_name=None,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production(
            "terminate_statement : KEYWORD_TERMINATE IDENTIFIER SEMICOLON"
        )
        def terminate_named(p: List[Any]) -> TerminateNode:
            return TerminateNode(
                task_name=p[1].value,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # ---- WRITE ------------------------------------------------------
        @self.pg.production(
            "write_statement : KEYWORD_WRITE LPAREN NUMBER RPAREN "
            "write_items SEMICOLON"
        )
        def write_statement(p: List[Any]) -> WriteNode:
            return WriteNode(
                channel=int(p[2].value),
                items=p[4],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production(
            "write_statement : KEYWORD_WRITE LPAREN NUMBER RPAREN SEMICOLON"
        )
        def write_statement_empty(p: List[Any]) -> WriteNode:
            return WriteNode(
                channel=int(p[2].value),
                items=[],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production("write_items : write_items COMMA expression")
        def write_items(p: List[Any]) -> list:
            return p[0] + [p[2]]

        @self.pg.production("write_items : expression")
        def write_items_single(p: List[Any]) -> list:
            return [p[0]]

        # ---- READ -------------------------------------------------------
        @self.pg.production(
            "read_statement : KEYWORD_READ LPAREN NUMBER RPAREN "
            "read_items SEMICOLON"
        )
        def read_statement(p: List[Any]) -> ReadNode:
            return ReadNode(
                channel=int(p[2].value),
                targets=p[4],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production("read_items : read_items COMMA expression")
        def read_items(p: List[Any]) -> list:
            return p[0] + [p[2]]

        @self.pg.production("read_items : expression")
        def read_items_single(p: List[Any]) -> list:
            return [p[0]]

        # ---- expressions (precedence handled by the table above) -------
        @self.pg.production("expression : expression PLUS expression")
        @self.pg.production("expression : expression MINUS expression")
        @self.pg.production("expression : expression MULTIPLY expression")
        @self.pg.production("expression : expression DIVIDE expression")
        @self.pg.production("expression : expression POWER expression")
        @self.pg.production("expression : expression ASSIGN expression")
        @self.pg.production("expression : expression LT expression")
        @self.pg.production("expression : expression GT expression")
        @self.pg.production("expression : expression LE expression")
        @self.pg.production("expression : expression GE expression")
        @self.pg.production("expression : expression NE expression")
        @self.pg.production("expression : expression KEYWORD_AND expression")
        @self.pg.production("expression : expression KEYWORD_OR expression")
        @self.pg.production("expression : expression KEYWORD_XOR expression")
        @self.pg.production("expression : expression VBAR expression")
        @self.pg.production("expression : expression AMPERSAND expression")
        @self.pg.production("expression : expression CONCAT expression")
        def expr_binop(p: List[Any]) -> BinaryOpNode:
            return BinaryOpNode(
                operator=p[1].value,
                left=p[0],
                right=p[2],
                line=getattr(p[1], "line", 0),
                column=getattr(p[1], "column", 0),
            )

        @self.pg.production("expression : KEYWORD_NOT expression")
        def expr_not(p: List[Any]) -> UnaryOpNode:
            return UnaryOpNode(
                operator=p[0].value,
                operand=p[1],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # `NOT =` two-token spelling of not-equal (HAL/S `¬=`)
        @self.pg.production(
            "expression : expression KEYWORD_NOT ASSIGN expression"
        )
        def expr_not_equal(p: List[Any]) -> BinaryOpNode:
            return BinaryOpNode(
                operator="¬=",
                left=p[0],
                right=p[3],
                line=getattr(p[1], "line", 0),
                column=getattr(p[1], "column", 0),
            )

        @self.pg.production("expression : expression DOLLAR subscript_index")
        def expr_subscript(p: List[Any]) -> SubscriptNode:
            return SubscriptNode(
                operand=p[0],
                index=p[2],
                line=getattr(p[1], "line", 0),
                column=getattr(p[1], "column", 0),
            )

        # Subscript index: a literal, a variable, or a parenthesized expression.
        @self.pg.production("subscript_index : NUMBER")
        def subscript_index_num(p: List[Any]) -> LiteralNode:
            return LiteralNode(
                value=int(p[0].value),
                literal_type="INTEGER",
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production("subscript_index : IDENTIFIER")
        def subscript_index_id(p: List[Any]) -> IdentifierNode:
            return IdentifierNode(
                name=p[0].value,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production("subscript_index : LPAREN expression RPAREN")
        def subscript_index_expr(p: List[Any]) -> ASTNode:
            return p[1]

        # Bit-slice range: X$(lo TO hi)
        @self.pg.production(
            "subscript_index : LPAREN expression KEYWORD_TO expression RPAREN"
        )
        def subscript_index_range(p: List[Any]) -> RangeNode:
            return RangeNode(
                lo=p[1],
                hi=p[3],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # 2-D subscript: X$(i, j) -> a unified IndexListNode coordinate.
        @self.pg.production(
            "subscript_index : LPAREN expression COMMA expression RPAREN"
        )
        def subscript_index_2d(p: List[Any]) -> IndexListNode:
            return IndexListNode(
                indices=[p[1], p[3]],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production("expression : HEXSTRING")
        @self.pg.production("expression : BITSTRING")
        @self.pg.production("expression : OCTSTRING")
        @self.pg.production("expression : BITLITERAL")
        def expr_based_literal(p: List[Any]) -> LiteralNode:
            return self._make_based_literal(p[0])

        @self.pg.production("expression : LPAREN expression RPAREN")
        def expr_group(p: List[Any]) -> ASTNode:
            return p[1]

        # IDENTIFIER ( ... ) is either a function call or a matrix subscript.
        # If any argument is `*` or a range, it's a matrix subscript; otherwise
        # a function call (codegen may still treat it as matrix element access
        # when the name is a known matrix).
        # Type-coercion casts that use type keywords: INTEGER(x), SCALAR(x).
        @self.pg.production("expression : KEYWORD_INTEGER LPAREN expression RPAREN")
        def expr_cast_integer(p: List[Any]) -> FunctionCallNode:
            return FunctionCallNode(
                name="INTEGER", arguments=[p[2]],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production("expression : KEYWORD_SCALAR LPAREN expression RPAREN")
        def expr_cast_scalar(p: List[Any]) -> FunctionCallNode:
            return FunctionCallNode(
                name="SCALAR", arguments=[p[2]],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production(
            "expression : IDENTIFIER LPAREN subscript_arg_list RPAREN"
        )
        def expr_call_or_subscript(p: List[Any]) -> ASTNode:
            name = p[0].value
            args = p[2]
            line = getattr(p[0], "line", 0)
            col = getattr(p[0], "column", 0)
            if any(isinstance(a, (StarNode, RangeNode)) for a in args):
                return MatrixSubscriptNode(
                    name=name, args=args, line=line, column=col
                )
            return FunctionCallNode(
                name=name, arguments=args, line=line, column=col
            )

        @self.pg.production("subscript_arg_list : subscript_arg_list COMMA subscript_arg")
        def subscript_arg_list(p: List[Any]) -> list:
            return p[0] + [p[2]]

        @self.pg.production("subscript_arg_list : subscript_arg")
        def subscript_arg_list_single(p: List[Any]) -> list:
            return [p[0]]

        @self.pg.production("subscript_arg : expression")
        def subscript_arg_expr(p: List[Any]) -> ASTNode:
            return p[0]

        @self.pg.production("subscript_arg : MULTIPLY")
        def subscript_arg_star(p: List[Any]) -> StarNode:
            return StarNode(
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production("subscript_arg : expression KEYWORD_TO expression")
        def subscript_arg_range(p: List[Any]) -> RangeNode:
            return RangeNode(
                lo=p[0],
                hi=p[2],
                line=getattr(p[1], "line", 0),
                column=getattr(p[1], "column", 0),
            )

        # MATRIX(v1, v2, ...) constructor (row-major values).
        @self.pg.production("expression : KEYWORD_MATRIX LPAREN arg_list RPAREN")
        def expr_matrix_literal(p: List[Any]) -> MatrixLiteralNode:
            return MatrixLiteralNode(
                values=p[2],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # VECTOR(v1, v2, ...) constructor.
        @self.pg.production("expression : KEYWORD_VECTOR LPAREN arg_list RPAREN")
        def expr_vector_literal(p: List[Any]) -> VectorLiteralNode:
            return VectorLiteralNode(
                values=p[2],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # Bare parenthesized list as a vector literal: (a, b, c).
        # Distinct from grouping (expr) by the comma after the first element.
        @self.pg.production(
            "expression : LPAREN expression COMMA arg_list RPAREN"
        )
        def expr_paren_vector(p: List[Any]) -> VectorLiteralNode:
            return VectorLiteralNode(
                values=[p[1]] + p[3],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # Structure field access: <expr>.<field>
        @self.pg.production("expression : expression DOT IDENTIFIER")
        def expr_field_access(p: List[Any]) -> FieldAccessNode:
            return FieldAccessNode(
                operand=p[0],
                field_name=p[2].value,
                line=getattr(p[1], "line", 0),
                column=getattr(p[1], "column", 0),
            )

        # Unary minus.
        @self.pg.production("expression : MINUS expression")
        def expr_negate(p: List[Any]) -> UnaryOpNode:
            return UnaryOpNode(
                operator="-",
                operand=p[1],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production("expression : IDENTIFIER LPAREN RPAREN")
        def expr_call_noargs(p: List[Any]) -> FunctionCallNode:
            return FunctionCallNode(
                name=p[0].value,
                arguments=[],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production("arg_list : arg_list COMMA arg_element")
        def arg_list(p: List[Any]) -> list:
            return p[0] + p[2]

        @self.pg.production("arg_list : arg_element")
        def arg_list_single(p: List[Any]) -> list:
            return p[0]

        # An argument is one expression, or a HAL/S repetition literal
        # <count>#<value> that expands to <count> copies during AST construction.
        @self.pg.production("arg_element : expression")
        def arg_element_single(p: List[Any]) -> list:
            return [p[0]]

        @self.pg.production("arg_element : NUMBER HASH expression")
        def arg_element_repeat(p: List[Any]) -> list:
            try:
                count = int(p[0].value)
            except (TypeError, ValueError):
                count = 0
            return [p[2]] * count

        @self.pg.production("expression : STRING")
        def expr_string(p: List[Any]) -> LiteralNode:
            raw = p[0].value
            inner = raw[1:-1].replace("''", "'") if len(raw) >= 2 else raw
            return LiteralNode(
                value=inner,
                literal_type="STRING",
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production("expression : NUMBER")
        def expr_number(p: List[Any]) -> LiteralNode:
            raw = p[0].value
            if "." in raw or "e" in raw.lower():
                return LiteralNode(
                    value=float(raw),
                    literal_type="SCALAR",
                    line=getattr(p[0], "line", 0),
                    column=getattr(p[0], "column", 0),
                )
            return LiteralNode(
                value=int(raw),
                literal_type="INTEGER",
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production("expression : IDENTIFIER")
        def expr_identifier(p: List[Any]) -> IdentifierNode:
            return IdentifierNode(
                name=p[0].value,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.error
        def error_handler(token: Any) -> None:
            if token:
                self.errors.append(
                    ParserError(
                        f"Unexpected token: {token.gettokentype()} "
                        f"('{token.value}')",
                        getattr(token, "line", 0),
                        getattr(token, "column", 0),
                    )
                )
            else:
                self.errors.append(ParserError("Unexpected end of input"))

    def _expand_replace(self, tokens: List[Token]) -> List[Token]:
        """Apply HAL/S REPLACE <name> BY <tokens>; macro substitution.

        Definitions are collected and removed; subsequent IDENTIFIER tokens
        whose name matches a macro are replaced by the macro's token list.
        Forward references only (definitions precede uses), which matches the
        directive-at-top convention in the source.
        """
        macros: dict = {}
        out: List[Token] = []
        i = 0
        n = len(tokens)
        while i < n:
            tok = tokens[i]
            if tok.type == "KEYWORD_REPLACE":
                # REPLACE IDENTIFIER BY <value tokens...> SEMICOLON
                if i + 2 < n and tokens[i + 1].type == "IDENTIFIER":
                    name = tokens[i + 1].lexeme.upper()
                    j = i + 3  # skip REPLACE, name, BY
                    value: List[Token] = []
                    while j < n and tokens[j].type != "SEMICOLON":
                        value.append(tokens[j])
                        j += 1
                    macros[name] = value
                    i = j + 1  # skip past the terminating SEMICOLON
                    continue
            if tok.type == "IDENTIFIER" and tok.lexeme.upper() in macros:
                out.extend(macros[tok.lexeme.upper()])
                i += 1
                continue
            out.append(tok)
            i += 1
        return out

    def _insert_implicit_multiply(self, tokens: List[Token]) -> List[Token]:
        """HAL/S multiplies adjacent operands by juxtaposition (e.g. `I W` means
        `I * W`, and `ATTITUDE(1) RAD_TO_DEG` means the element times the scalar).
        Rather than make the grammar ambiguous (a generative
        `expression : expression expression` rule yields dozens of reduce/reduce
        conflicts), synthesize an explicit MULTIPLY token between two adjacent
        operand tokens so the parser uses its existing multiply rule.

        The left operand may end in IDENTIFIER, NUMBER, or RPAREN (a subscript,
        call, or grouped expression); the right must START an operand, so it is
        IDENTIFIER or NUMBER. Insertion is SUPPRESSED:
          * inside DECLARE statements -- a variable name sits next to a
            structure-template name (`DECLARE PRIMARY_ALARM NODE STRUCTURE;`);
          * immediately after a WRITE/READ channel `)` -- there the following
            token is the first I/O item (`WRITE(6) X`), not a multiplicand.
        Paren-start juxtaposition (`A (B)`, `(A)(B)`) is still not handled, as it
        collides with calls and subscripts.
        """
        left_ops = {"IDENTIFIER", "NUMBER", "RPAREN"}
        right_ops = {"IDENTIFIER", "NUMBER"}
        out: List[Token] = []
        in_declare = False
        io_pending = False   # just saw WRITE/READ, awaiting its channel '('
        io_depth = 0         # paren depth within the channel spec
        suppress_next = False  # do not insert before the immediate next token
        for tok in tokens:
            t = tok.type
            if t == "KEYWORD_DECLARE":
                in_declare = True
            elif t == "SEMICOLON":
                in_declare = False
                io_pending = False
                io_depth = 0

            do_suppress = suppress_next
            suppress_next = False
            if (not in_declare and not do_suppress and out
                    and out[-1].type in left_ops and t in right_ops):
                out.append(Token(
                    type="MULTIPLY", lexeme="*",
                    line=tok.line, column=tok.column, file=tok.file,
                ))
            out.append(tok)

            # Track the WRITE/READ channel paren group so the first I/O item
            # after the channel `)` is not treated as a multiplicand.
            if t in ("KEYWORD_WRITE", "KEYWORD_READ"):
                io_pending = True
                io_depth = 0
            elif io_pending and t == "LPAREN":
                io_pending = False
                io_depth = 1
            elif io_depth > 0:
                if t == "LPAREN":
                    io_depth += 1
                elif t == "RPAREN":
                    io_depth -= 1
                    if io_depth == 0:
                        suppress_next = True
        return out

    def _make_based_literal(self, tok: Any) -> LiteralNode:
        """Parse a HEX/BIN/OCT or suffix bit literal token into a LiteralNode."""
        import re
        raw = tok.value
        ttype = tok.gettokentype()
        m = re.search(r"'([^']*)'", raw)
        digits = m.group(1) if m else ""
        if ttype == "HEXSTRING":
            base, lit = 16, "HEX"
        elif ttype == "BITSTRING":
            base, lit = 2, "BIT"
        elif ttype == "OCTSTRING":
            base, lit = 8, "OCT"
        else:  # BITLITERAL, suffix form '....'[BHO]
            suffix = raw[-1].upper()
            base, lit = {"B": (2, "BIT"), "H": (16, "HEX"), "O": (8, "OCT")}.get(
                suffix, (2, "BIT")
            )
        try:
            value = int(digits, base) if digits else 0
        except ValueError:
            value = 0
        return LiteralNode(
            value=value,
            literal_type=lit,
            line=getattr(tok, "line", 0),
            column=getattr(tok, "column", 0),
        )

    def parse(self, tokens: List[Token]) -> Optional[ASTNode]:
        """
        Parse token stream and generate AST.

        Args:
            tokens: List of Token objects from lexer

        Returns:
            Root AST node or None if parsing failed
        """
        self.errors = []
        self._proc_signatures = {}
        self._proc_assign_types = {}
        self.symbol_table = SymbolTable()

        # Expand REPLACE ... BY ...; macros (HAL/S preprocessor) at the token
        # level before parsing. Definitions are removed; later uses of a macro
        # name are substituted with the definition's tokens.
        tokens = self._expand_replace(tokens)
        tokens = self._insert_implicit_multiply(tokens)

        try:
            rply_tokens = []
            for token in tokens:

                class MockToken:
                    def __init__(
                        self, token_type: str, value: str, line: int, column: int
                    ):
                        self.token_type = token_type
                        self.value = value
                        self.line = line
                        self.column = column

                    def gettokentype(self) -> str:
                        return self.token_type

                mock_token = MockToken(
                    token.type, token.lexeme, token.line, token.column
                )
                rply_tokens.append(mock_token)

            self.ast = self.parser.parse(iter(rply_tokens))
            return self.ast

        except Exception as e:
            if not self.errors:
                self.errors.append(ParserError(str(e)))
            return None

    def parse_file(self, filepath: str) -> Optional[ASTNode]:
        """Parse a HAL/S source file."""
        lexer = Lexer()
        tokens = lexer.tokenize_file(filepath)

        if lexer.errors:
            print("Lexer errors found. Cannot parse.", file=sys.stderr)
            lexer.report_errors()
            return None

        return self.parse(tokens)

    def export_ast_json(self, output_file: str) -> None:
        """Export AST to JSON file."""
        if not self.ast:
            raise ValueError("No AST to export. Parse a file first.")

        output_data = {
            "ast": self.ast.to_dict(),
            "errors": [
                {"message": e.message, "line": e.line, "column": e.column}
                for e in self.errors
            ],
            "statistics": {
                "total_errors": len(self.errors),
                "has_ast": self.ast is not None,
            },
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2)

    def report_errors(self) -> None:
        """Report all parser errors to stderr."""
        if not self.errors:
            return

        print(f"\n{'=' * 70}", file=sys.stderr)
        print(f"PARSER ERRORS: {len(self.errors)} error(s) found", file=sys.stderr)
        print(f"{'=' * 70}", file=sys.stderr)

        for error in self.errors:
            print(f"\n{error}", file=sys.stderr)

        print(f"\n{'=' * 70}\n", file=sys.stderr)
