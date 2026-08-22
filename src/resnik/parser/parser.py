"""
Modern HAL/S Parser with full type annotations.
"""

import copy
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from rply import ParserGenerator
from rply.errors import ParsingError as RplyParsingError

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
from resnik.types import DataType, Precision, TypeSpec
from resnik.parser.symbol_table import Symbol, SymbolTable


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
                "KEYWORD_SET",
                "KEYWORD_RESET",
                "KEYWORD_ASSIGN",
                "KEYWORD_CANCEL",
                "KEYWORD_COMPOOL",
                "KEYWORD_STATIC",
                "KEYWORD_CONSTANT",
                "KEYWORD_STRUCTURE",
                "KEYWORD_TEMPLATE",
                "KEYWORD_RESUME",
                "KEYWORD_EQUATE",
                "KEYWORD_ARRAY",
                "KEYWORD_OF",
                "KEYWORD_ALIGNED",
                "KEYWORD_DENSE",
                "KEYWORD_AUTOMATIC",
                "DOT",
                "KEYWORD_CALL",
                "KEYWORD_WHILE",
                "KEYWORD_CASE",
                "KEYWORD_FOR",
                "KEYWORD_TO",
                "KEYWORD_DOWNTO",
                "KEYWORD_EXIT",
                "KEYWORD_GO",
                "KEYWORD_GOTO",
                "KEYWORD_NAME",
                "KEYWORD_NONHAL",
                "KEYWORD_EXTERNAL",
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
                "KEYWORD_MOD",
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
                # HAL/S relationals share one left-associative precedence
                # level (LRM: equality `=` is a relational operator, not a
                # separate tier), so `A = B < C` reduces left-to-right as
                # `(A = B) < C`. Keeping `=` on a separate lower tier would
                # match C's convention -- `A = (B < C)` -- but is wrong for
                # HAL/S.
                ("left", ["ASSIGN", "LT", "GT", "LE", "GE", "NE"]),
                ("left", ["PLUS", "MINUS", "CONCAT"]),
                ("left", ["MULTIPLY", "DIVIDE", "KEYWORD_MOD"]),
                ("right", ["POWER"]),
                ("right", ["KEYWORD_NOT"]),     # unary NOT binds tightly
                ("left", ["DOLLAR", "DOT"]),    # postfix subscript/field, left-to-right
            ],
        )

        self.symbol_table = SymbolTable()
        self.errors: List[ParserError] = []
        self.ast: Optional[ASTNode] = None
        # Procedure signature/type maps -- populated by _build_procedure as
        # each PROCEDURE is reduced, and consulted by _check_call_signatures.
        # All three must be initialised here (not lazily in parse()) so that
        # any code path that calls _build_procedure or _check_call_signatures
        # before parse() runs -- e.g. tests exercising a single production --
        # does not AttributeError on the missing dict.
        self._proc_signatures: Dict[str, tuple] = {}
        self._proc_assign_types: Dict[str, list] = {}
        self._proc_param_types: Dict[str, list] = {}

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
        tname = (ts.type_name.upper() if getattr(ts, "type_name", None) else None)
        is_name = bool(getattr(ts, "is_name", False))
        return (ts.data_type, dims, adims, elem, tname, is_name)

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
            if isinstance(node, LiteralNode) and node.literal_type in (
                    "HEX", "OCT", "BIT") and node.bit_width is not None:
                return node.bit_width
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

        def array_shape(node):
            if isinstance(node, IdentifierNode):
                ts = var_types.get(node.name.lower())
                if ts is None:
                    return None
                if ts.array_dims:
                    return tuple(ts.array_dims)
                if ts.array_size is not None:
                    return (ts.array_size,)
            return None

        for node in self._walk_nodes(body):
            if isinstance(node, BinaryOpNode) and node.operator in (
                    "+", "-", "*", "/"):
                ls = array_shape(node.left)
                rs = array_shape(node.right)
                if ls is not None and rs is not None and ls != rs:
                    ltxt = ",".join(str(d) for d in ls)
                    rtxt = ",".join(str(d) for d in rs)
                    self.errors.append(
                        SemanticError(
                            f"array dimension mismatch: ARRAY({ltxt}) "
                            f"{node.operator} ARRAY({rtxt}) -- element-wise "
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
            # Dimension/type check: each input argument that is a plain
            # variable must match the shape of the parameter it binds to. This
            # is what catches, e.g., passing a VECTOR(4) where the (possibly
            # TEMPLATE-declared) parameter requires a VECTOR(3).
            itypes = self._proc_param_types.get(call.name.upper())
            if itypes and len(call.arguments) == len(itypes):
                for arg, pts in zip(call.arguments, itypes):
                    # Only a bare identifier is a typed variable. FunctionCall /
                    # MatrixSubscript also have .name (the callee/matrix) and
                    # must not be typed as that whole object.
                    if not isinstance(arg, IdentifierNode) or pts is None:
                        continue
                    name = arg.name
                    ats = var_types.get(name.lower())
                    if ats is None:
                        continue
                    if self._type_shape(ats) != self._type_shape(pts):
                        self.errors.append(
                            SemanticError(
                                f"CALL {call.name} argument '{name}' does not "
                                f"match the type/dimensions of the "
                                f"corresponding PROCEDURE parameter",
                                getattr(call, "line", 0),
                                getattr(call, "column", 0),
                            )
                        )
            # Dimension/type check: each ASSIGN argument that is a plain
            # variable must match the shape of the parameter it binds to.
            ptypes = self._proc_assign_types.get(call.name.upper())
            if ptypes and len(call.assign_args) == len(ptypes):
                for arg, pts in zip(call.assign_args, ptypes):
                    if not isinstance(arg, IdentifierNode) or pts is None:
                        continue
                    name = arg.name
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
        is_external: bool = False, is_template: bool = False,
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
        # Map every parameter/local name to its declared type (from the body
        # declarations), so CALL arguments can be dimension-checked against
        # both the inputs and the ASSIGN outputs -- this is how a TEMPLATE's
        # signature validates callers even when the body is elsewhere.
        _bd = {}
        for d in statements:
            if isinstance(d, DeclareNode):
                for ident in d.identifiers:
                    _bd[ident.lower()] = d.type_spec
        self._proc_param_types[name.upper()] = [
            _bd.get(p.lower()) for p in params]
        self._proc_assign_types[name.upper()] = [
            _bd.get(a.lower()) for a in assign_params]
        # Procedure bodies may not nest further tasks/procedures here; keep all
        # body items as statements (declarations are extracted by codegen).
        self._check_declaration_order(
            statements, "FUNCTION" if return_type is not None else "PROCEDURE")
        self._check_call_signatures(statements)
        self._check_array_dims(statements)
        self._check_bit_concat(statements)
        return ProcedureNode(
            name=name,
            parameters=params,
            assign_params=assign_params,
            statements=statements,
            return_type=return_type,
            is_external=is_external,
            is_template=is_template,
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
        @self.pg.production("unit : procedure_block")
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

        # TEMPLATE: a signature-only prototype (like a C header). Its params,
        # ASSIGN outputs, and body declarations are parsed and its signature is
        # recorded for type-validating CALL sites, but it generates no code and
        # allocates no memory.
        @self.pg.production(
            "procedure_block : IDENTIFIER COLON KEYWORD_PROCEDURE "
            "LPAREN param_list RPAREN KEYWORD_ASSIGN LPAREN param_list RPAREN "
            "KEYWORD_TEMPLATE SEMICOLON statement_list KEYWORD_CLOSE "
            "IDENTIFIER SEMICOLON"
        )
        def procedure_template_assign(p: List[Any]) -> ProcedureNode:
            return self._build_procedure(
                name_tok=p[0], params=p[4], statements=p[12],
                close_tok=p[14], assign_params=p[8], is_template=True
            )

        @self.pg.production(
            "procedure_block : IDENTIFIER COLON KEYWORD_PROCEDURE "
            "LPAREN param_list RPAREN KEYWORD_TEMPLATE SEMICOLON "
            "statement_list KEYWORD_CLOSE IDENTIFIER SEMICOLON"
        )
        def procedure_template(p: List[Any]) -> ProcedureNode:
            return self._build_procedure(
                name_tok=p[0], params=p[4], statements=p[8],
                close_tok=p[10], is_template=True
            )

        @self.pg.production(
            "procedure_block : IDENTIFIER COLON KEYWORD_PROCEDURE "
            "KEYWORD_TEMPLATE SEMICOLON statement_list KEYWORD_CLOSE "
            "IDENTIFIER SEMICOLON"
        )
        def procedure_template_noparams(p: List[Any]) -> ProcedureNode:
            return self._build_procedure(
                name_tok=p[0], params=[], statements=p[5],
                close_tok=p[7], is_template=True
            )

        # FUNCTION(inputs) <return-type> TEMPLATE; -- signature-only function
        # prototype (auto-generated into the template library).
        @self.pg.production(
            "procedure_block : IDENTIFIER COLON KEYWORD_FUNCTION "
            "LPAREN param_list RPAREN type_spec KEYWORD_TEMPLATE SEMICOLON "
            "statement_list KEYWORD_CLOSE IDENTIFIER SEMICOLON"
        )
        def function_template(p: List[Any]) -> ProcedureNode:
            return self._build_procedure(
                name_tok=p[0], params=p[4], statements=p[9],
                close_tok=p[11], return_type=p[6], is_template=True
            )

        # EXTERNAL: a prototype whose body is foreign (assembly/C/linker-
        # resolved), NOT another HAL/S unit. Straight FFI -> extern "C". Unlike
        # TEMPLATE, it is not type-checked across the boundary and is not banked
        # in the template library.
        @self.pg.production(
            "procedure_block : IDENTIFIER COLON KEYWORD_PROCEDURE "
            "LPAREN param_list RPAREN KEYWORD_EXTERNAL SEMICOLON "
            "statement_list KEYWORD_CLOSE IDENTIFIER SEMICOLON"
        )
        def procedure_external(p: List[Any]) -> ProcedureNode:
            return self._build_procedure(
                name_tok=p[0], params=p[4], statements=p[8],
                close_tok=p[10], is_external=True
            )

        @self.pg.production(
            "procedure_block : IDENTIFIER COLON KEYWORD_PROCEDURE "
            "LPAREN param_list RPAREN KEYWORD_ASSIGN LPAREN param_list RPAREN "
            "KEYWORD_EXTERNAL SEMICOLON statement_list KEYWORD_CLOSE "
            "IDENTIFIER SEMICOLON"
        )
        def procedure_external_assign(p: List[Any]) -> ProcedureNode:
            return self._build_procedure(
                name_tok=p[0], params=p[4], statements=p[12],
                close_tok=p[14], assign_params=p[8], is_external=True
            )

        @self.pg.production(
            "procedure_block : IDENTIFIER COLON KEYWORD_PROCEDURE "
            "KEYWORD_EXTERNAL SEMICOLON statement_list KEYWORD_CLOSE "
            "IDENTIFIER SEMICOLON"
        )
        def procedure_external_noparams(p: List[Any]) -> ProcedureNode:
            return self._build_procedure(
                name_tok=p[0], params=[], statements=p[5],
                close_tok=p[7], is_external=True
            )

        @self.pg.production(
            "procedure_block : IDENTIFIER COLON KEYWORD_FUNCTION "
            "LPAREN param_list RPAREN type_spec KEYWORD_EXTERNAL SEMICOLON "
            "statement_list KEYWORD_CLOSE IDENTIFIER SEMICOLON"
        )
        def function_external(p: List[Any]) -> ProcedureNode:
            return self._build_procedure(
                name_tok=p[0], params=p[4], statements=p[9],
                close_tok=p[11], return_type=p[6], is_external=True
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

        # GOTO <label>;  -- one-token spelling (same AST as GO TO).
        @self.pg.production(
            "statement : KEYWORD_GOTO IDENTIFIER SEMICOLON"
        )
        def goto_keyword_statement(p: List[Any]) -> GotoNode:
            return GotoNode(
                label=p[1].value,
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
                    layout=getattr(element, "layout", "aligned"),
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
            # No layout modifier -> HAL/S defaults to ALIGNED (for speed), but
            # we track it explicitly for the code generator.
            return TypeSpec(data_type=DataType.STRUCTURE, type_name=p[0].value,
                            layout="aligned")

        @self.pg.production(
            "array_element : IDENTIFIER KEYWORD_STRUCTURE KEYWORD_ALIGNED")
        def array_element_struct_aligned(p: List[Any]) -> TypeSpec:
            return TypeSpec(data_type=DataType.STRUCTURE, type_name=p[0].value,
                            layout="aligned")

        @self.pg.production(
            "array_element : IDENTIFIER KEYWORD_STRUCTURE KEYWORD_DENSE")
        def array_element_struct_dense(p: List[Any]) -> TypeSpec:
            return TypeSpec(data_type=DataType.STRUCTURE, type_name=p[0].value,
                            layout="dense")

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

        # DECLARE <name> <type> EXTERNAL; -- a variable defined in a foreign
        # module (object file / linker script). No storage is allocated here;
        # it lowers to an `extern "C"` static and reads are wrapped in `unsafe`.
        @self.pg.production(
            "declare_statement : KEYWORD_DECLARE IDENTIFIER type_spec "
            "KEYWORD_EXTERNAL SEMICOLON"
        )
        def declare_external(p: List[Any]) -> DeclareNode:
            return DeclareNode(
                identifiers=[p[1].value],
                type_spec=p[2],
                attributes={"external": True},
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # AUTOMATIC is HAL/S's default storage class (stack-local). It needs no
        # special lowering -- a normal local variable already has automatic
        # storage -- so we parse and record it, then ignore it in codegen.
        @self.pg.production(
            "declare_statement : KEYWORD_DECLARE IDENTIFIER type_spec "
            "KEYWORD_AUTOMATIC SEMICOLON"
        )
        def declare_automatic(p: List[Any]) -> DeclareNode:
            return DeclareNode(
                identifiers=[p[1].value],
                type_spec=p[2],
                attributes={"automatic": True},
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production(
            "declare_statement : KEYWORD_DECLARE IDENTIFIER type_spec "
            "KEYWORD_AUTOMATIC KEYWORD_INITIAL LPAREN arg_list RPAREN SEMICOLON"
        )
        def declare_automatic_initial(p: List[Any]) -> DeclareNode:
            values = p[6]
            node = DeclareNode(
                identifiers=[p[1].value],
                type_spec=p[2],
                attributes={"automatic": True},
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
            return TypeSpec(data_type=DataType.SCALAR, precision=Precision.SINGLE)

        @self.pg.production("type_spec : KEYWORD_SCALAR KEYWORD_DOUBLE")
        def type_scalar_double(p: List[Any]) -> TypeSpec:
            return TypeSpec(data_type=DataType.SCALAR, precision=Precision.DOUBLE)

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

        # ---- DO UNTIL <cond>; ... END; ---------------------------------
        # First cycle is unguarded; later cycles stop when <cond> is TRUE
        # (USA003088 §7.6.3). Same AST class as DO WHILE, until=True.
        @self.pg.production(
            "do_block : KEYWORD_DO KEYWORD_UNTIL expression SEMICOLON "
            "statement_list KEYWORD_END SEMICOLON"
        )
        def do_until_block(p: List[Any]) -> WhileNode:
            return WhileNode(
                condition=p[2],
                statements=p[4],
                until=True,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # ---- DO FOR <var> = <start> TO/DOWNTO <end> [BY <step>]
        #      [UNTIL <cond>]; ... END; ----------------------------------
        # Direction is forked on TO vs DOWNTO (same as before). UNTIL is an
        # optional iterative guard: first cycle unguarded, later cycles stop
        # when the condition is TRUE (USA003088 §7.6.5). Bound check still
        # runs first, so an empty range never enters.
        def _mk_for(p, *, end, statements, step=None, descending=False,
                    until=None) -> ForNode:
            return ForNode(
                var=p[2].value,
                start=p[4],
                end=end,
                step=step,
                descending=descending,
                until=until,
                statements=statements,
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production(
            "do_block : KEYWORD_DO KEYWORD_FOR IDENTIFIER ASSIGN expression "
            "KEYWORD_TO expression SEMICOLON statement_list KEYWORD_END SEMICOLON"
        )
        def do_for_block(p: List[Any]) -> ForNode:
            return _mk_for(p, end=p[6], statements=p[8])

        @self.pg.production(
            "do_block : KEYWORD_DO KEYWORD_FOR IDENTIFIER ASSIGN expression "
            "KEYWORD_TO expression KEYWORD_BY expression SEMICOLON "
            "statement_list KEYWORD_END SEMICOLON"
        )
        def do_for_step_block(p: List[Any]) -> ForNode:
            return _mk_for(p, end=p[6], step=p[8], statements=p[10])

        @self.pg.production(
            "do_block : KEYWORD_DO KEYWORD_FOR IDENTIFIER ASSIGN expression "
            "KEYWORD_TO expression KEYWORD_UNTIL expression SEMICOLON "
            "statement_list KEYWORD_END SEMICOLON"
        )
        def do_for_until_block(p: List[Any]) -> ForNode:
            return _mk_for(p, end=p[6], until=p[8], statements=p[10])

        @self.pg.production(
            "do_block : KEYWORD_DO KEYWORD_FOR IDENTIFIER ASSIGN expression "
            "KEYWORD_TO expression KEYWORD_BY expression KEYWORD_UNTIL "
            "expression SEMICOLON statement_list KEYWORD_END SEMICOLON"
        )
        def do_for_step_until_block(p: List[Any]) -> ForNode:
            return _mk_for(
                p, end=p[6], step=p[8], until=p[10], statements=p[12])

        @self.pg.production(
            "do_block : KEYWORD_DO KEYWORD_FOR IDENTIFIER ASSIGN expression "
            "KEYWORD_DOWNTO expression SEMICOLON statement_list "
            "KEYWORD_END SEMICOLON"
        )
        def do_for_down_block(p: List[Any]) -> ForNode:
            return _mk_for(p, end=p[6], descending=True, statements=p[8])

        @self.pg.production(
            "do_block : KEYWORD_DO KEYWORD_FOR IDENTIFIER ASSIGN expression "
            "KEYWORD_DOWNTO expression KEYWORD_BY expression SEMICOLON "
            "statement_list KEYWORD_END SEMICOLON"
        )
        def do_for_down_step_block(p: List[Any]) -> ForNode:
            return _mk_for(
                p, end=p[6], step=p[8], descending=True, statements=p[10])

        @self.pg.production(
            "do_block : KEYWORD_DO KEYWORD_FOR IDENTIFIER ASSIGN expression "
            "KEYWORD_DOWNTO expression KEYWORD_UNTIL expression SEMICOLON "
            "statement_list KEYWORD_END SEMICOLON"
        )
        def do_for_down_until_block(p: List[Any]) -> ForNode:
            return _mk_for(
                p, end=p[6], descending=True, until=p[8], statements=p[10])

        @self.pg.production(
            "do_block : KEYWORD_DO KEYWORD_FOR IDENTIFIER ASSIGN expression "
            "KEYWORD_DOWNTO expression KEYWORD_BY expression KEYWORD_UNTIL "
            "expression SEMICOLON statement_list KEYWORD_END SEMICOLON"
        )
        def do_for_down_step_until_block(p: List[Any]) -> ForNode:
            return _mk_for(
                p, end=p[6], step=p[8], descending=True, until=p[10],
                statements=p[12])

        # ---- EXIT [<label>]; -- break the innermost or a named loop -------
        @self.pg.production("statement : KEYWORD_EXIT SEMICOLON")
        def exit_statement(p: List[Any]) -> ExitNode:
            return ExitNode(
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production("statement : KEYWORD_EXIT IDENTIFIER SEMICOLON")
        def exit_labeled_statement(p: List[Any]) -> ExitNode:
            return ExitNode(
                label=p[1].value,
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

        # SIGNAL / SET / RESET <event>;  (real-time event; host stub)
        # All three lower to an atomic write on the event's shared flag:
        # SIGNAL and SET store `true`; RESET stores `false`. The three
        # productions build the same SignalNode with a distinguishing
        # `op` field so the backend can pick the right value and any
        # diagnostic can echo the original verb.
        @self.pg.production(
            "signal_statement : KEYWORD_SIGNAL IDENTIFIER SEMICOLON"
        )
        def signal_statement(p: List[Any]) -> SignalNode:
            return SignalNode(
                name=p[1].value,
                op="SIGNAL",
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production(
            "signal_statement : KEYWORD_SET IDENTIFIER SEMICOLON"
        )
        def set_statement(p: List[Any]) -> SignalNode:
            return SignalNode(
                name=p[1].value,
                op="SET",
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production(
            "signal_statement : KEYWORD_RESET IDENTIFIER SEMICOLON"
        )
        def reset_statement(p: List[Any]) -> SignalNode:
            return SignalNode(
                name=p[1].value,
                op="RESET",
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        # SET/RESET <bit_var>(i, j, ...);  -- HAL/S bit-list form. Sets
        # (SET) or clears (RESET) each listed bit position, 1-indexed
        # MSB-first, on a BIT-typed variable. Positions may be constant
        # or runtime expressions; the codegen handles both.
        @self.pg.production(
            "signal_statement : KEYWORD_SET IDENTIFIER LPAREN "
            "bit_position_list RPAREN SEMICOLON"
        )
        def set_bit_list_statement(p: List[Any]) -> SignalNode:
            return SignalNode(
                name=p[1].value,
                op="SET",
                bits=p[3],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production(
            "signal_statement : KEYWORD_RESET IDENTIFIER LPAREN "
            "bit_position_list RPAREN SEMICOLON"
        )
        def reset_bit_list_statement(p: List[Any]) -> SignalNode:
            return SignalNode(
                name=p[1].value,
                op="RESET",
                bits=p[3],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production(
            "bit_position_list : bit_position_list COMMA expression"
        )
        def bit_position_list(p: List[Any]) -> list:
            return p[0] + [p[2]]

        @self.pg.production("bit_position_list : expression")
        def bit_position_list_single(p: List[Any]) -> list:
            return [p[0]]

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
        # HAL/S WRITE has two channel shapes:
        #   WRITE(unit) items;              -- sequential I/O
        #   WRITE(unit, record) items;      -- direct-access I/O (e.g. MMU)
        # Both are also legal with an empty item list. `record` is an
        # arbitrary expression (identifier, arithmetic, etc.), not just a
        # NUMBER, so `WRITE(1, BLOCK_ID + 1) BUF;` parses.
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

        @self.pg.production(
            "write_statement : KEYWORD_WRITE LPAREN NUMBER COMMA expression "
            "RPAREN write_items SEMICOLON"
        )
        def write_statement_record(p: List[Any]) -> WriteNode:
            return WriteNode(
                channel=int(p[2].value),
                items=p[6],
                record=p[4],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production(
            "write_statement : KEYWORD_WRITE LPAREN NUMBER COMMA expression "
            "RPAREN SEMICOLON"
        )
        def write_statement_record_empty(p: List[Any]) -> WriteNode:
            return WriteNode(
                channel=int(p[2].value),
                items=[],
                record=p[4],
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
        # Same channel/record shape as WRITE; the empty-target form
        # (`READ(unit);`) is now accepted for symmetry with WRITE.
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

        @self.pg.production(
            "read_statement : KEYWORD_READ LPAREN NUMBER RPAREN SEMICOLON"
        )
        def read_statement_empty(p: List[Any]) -> ReadNode:
            return ReadNode(
                channel=int(p[2].value),
                targets=[],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production(
            "read_statement : KEYWORD_READ LPAREN NUMBER COMMA expression "
            "RPAREN read_items SEMICOLON"
        )
        def read_statement_record(p: List[Any]) -> ReadNode:
            return ReadNode(
                channel=int(p[2].value),
                targets=p[6],
                record=p[4],
                line=getattr(p[0], "line", 0),
                column=getattr(p[0], "column", 0),
            )

        @self.pg.production(
            "read_statement : KEYWORD_READ LPAREN NUMBER COMMA expression "
            "RPAREN SEMICOLON"
        )
        def read_statement_record_empty(p: List[Any]) -> ReadNode:
            return ReadNode(
                channel=int(p[2].value),
                targets=[],
                record=p[4],
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
        @self.pg.production("expression : expression KEYWORD_MOD expression")
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
            # HAL/S `N # expr` expands to N slots of `expr`. `[p[2]] * count`
            # would share ONE node across every slot -- any AST-mutating
            # pass (rewrites, seen-id de-dup, node-level annotation)
            # would then observe one node where the source calls for N,
            # and in-place mutations to a "single slot" would silently
            # affect all N. Deep-copy every slot so each is a distinct
            # subtree with its own identity.
            return [copy.deepcopy(p[2]) for _ in range(count)]

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

    # ------------------------------------------------------------------
    # Parameterized MACRO ... MEND expansion (token-stream preprocessor)
    # ------------------------------------------------------------------
    #
    # HAL/S parameterized macros are expanded *before* parsing: definitions are
    # captured and removed from the stream, and each invocation is replaced by
    # the macro body with formal parameters substituted by the call's argument
    # token-lists. Because expansion happens on tokens, the resulting stream is
    # exactly what the user would have written inline, so the parser and code
    # generator need no macro awareness at all.
    #
    # Lexical hygiene: identifiers DECLAREd inside a macro body (which HAL/S
    # macros are not really supposed to have, but users try) are alpha-renamed
    # with a per-expansion suffix so they cannot collide with -- or clobber --
    # like-named variables in the caller's scope.

    _MACRO_MAX_DEPTH = 64

    def _clone_tok(self, t: Token, lexeme=None, line=None, column=None) -> Token:
        """Copy a token, optionally overriding its lexeme/position."""
        return Token(
            type=t.type,
            lexeme=t.lexeme if lexeme is None else lexeme,
            line=t.line if line is None else line,
            column=t.column if column is None else column,
            file=t.file,
        )

    def _read_paren_args(self, tokens, lp):
        """Given tokens[lp] == LPAREN, split the parenthesized, comma-separated
        argument list into one token-list per argument (respecting nested
        parens). Returns (list_of_token_lists, index_after_matching_RPAREN),
        or (None, None) if the parentheses are unbalanced."""
        n = len(tokens)
        depth = 0
        cur: List[Token] = []
        args: List[List[Token]] = []
        i = lp
        while i < n:
            t = tokens[i]
            if t.type == "LPAREN":
                depth += 1
                if depth > 1:
                    cur.append(t)
            elif t.type == "RPAREN":
                depth -= 1
                if depth == 0:
                    if cur or args:
                        args.append(cur)
                    return args, i + 1
                cur.append(t)
            elif t.type == "COMMA" and depth == 1:
                args.append(cur)
                cur = []
            else:
                cur.append(t)
            i += 1
        return None, None

    def _collect_macro_defs(self, tokens):
        """Capture and strip `MACRO name(params); <body> MEND;` definitions.
        Returns (macros, remaining_tokens) where macros maps an upper-cased
        name to (param_names_upper, body_tokens)."""
        macros: dict = {}
        out: List[Token] = []
        i = 0
        n = len(tokens)
        while i < n:
            t = tokens[i]
            if t.type != "KEYWORD_MACRO":
                out.append(t)
                i += 1
                continue

            # Header: MACRO IDENTIFIER ( params ) ;
            if i + 1 >= n or tokens[i + 1].type != "IDENTIFIER":
                self.errors.append(ParserError(
                    "Malformed MACRO: expected a name", t.line, t.column))
                i += 1
                continue
            name = tokens[i + 1].lexeme.upper()
            k = i + 2
            params: List[str] = []
            if k < n and tokens[k].type == "LPAREN":
                argls, end = self._read_paren_args(tokens, k)
                if argls is None:
                    self.errors.append(ParserError(
                        f"Malformed parameter list for MACRO '{name}'",
                        t.line, t.column))
                    i += 1
                    continue
                for a in argls:
                    if len(a) == 1 and a[0].type == "IDENTIFIER":
                        params.append(a[0].lexeme.upper())
                    else:
                        self.errors.append(ParserError(
                            f"MACRO '{name}' parameters must be identifiers",
                            t.line, t.column))
                k = end
            if k < n and tokens[k].type == "SEMICOLON":
                k += 1  # skip the header terminator

            # Body: everything up to MEND.
            body: List[Token] = []
            while k < n and tokens[k].type != "KEYWORD_MEND":
                if tokens[k].type == "KEYWORD_MACRO":
                    self.errors.append(ParserError(
                        "Nested MACRO definitions are not supported",
                        tokens[k].line, tokens[k].column))
                    break
                body.append(tokens[k])
                k += 1
            if k >= n or tokens[k].type != "KEYWORD_MEND":
                self.errors.append(ParserError(
                    f"MACRO '{name}' is missing its MEND", t.line, t.column))
                i = k
                continue
            k += 1  # skip MEND
            if k < n and tokens[k].type == "SEMICOLON":
                k += 1  # skip the MEND terminator

            macros[name] = (params, body)
            i = k
        return macros, out

    def _macro_local_decls(self, body, params):
        """Identifiers DECLAREd inside a macro body that are not parameters,
        as {upper_name: original_lexeme} for hygienic renaming."""
        locals_: dict = {}
        n = len(body)
        for k in range(n):
            if body[k].type != "KEYWORD_DECLARE":
                continue
            j = k + 1
            expect_id = True
            while j < n:
                tj = body[j]
                if expect_id and tj.type == "IDENTIFIER":
                    up = tj.lexeme.upper()
                    if up not in params and up not in locals_:
                        locals_[up] = tj.lexeme
                    expect_id = False
                    j += 1
                elif (not expect_id) and tj.type == "COMMA":
                    expect_id = True
                    j += 1
                else:
                    break
        return locals_

    def _expand_one_macro(self, call_tok, params, body, args):
        """Produce the token list for a single invocation: substitute formals
        with argument token-lists and alpha-rename macro-local declarations."""
        param_map = {p: a for p, a in zip(params, args)}
        locals_ = self._macro_local_decls(body, set(params))
        rename = {}
        if locals_:
            suffix = f"__m{self._macro_uid}"
            self._macro_uid += 1
            rename = {up: f"{orig}{suffix}" for up, orig in locals_.items()}

        out: List[Token] = []
        for t in body:
            up = t.lexeme.upper() if t.type == "IDENTIFIER" else None
            if up is not None and up in param_map:
                # Splice the argument's tokens in place of the formal parameter.
                for a in param_map[up]:
                    out.append(self._clone_tok(
                        a, line=call_tok.line, column=call_tok.column))
            elif up is not None and up in rename:
                out.append(self._clone_tok(t, lexeme=rename[up]))
            else:
                out.append(self._clone_tok(t))
        return out

    def _apply_macros(self, tokens, macros, depth=0):
        """Replace each macro invocation with its expanded body, recursively
        (so a macro may invoke another), bounded by _MACRO_MAX_DEPTH."""
        if depth > self._MACRO_MAX_DEPTH:
            self.errors.append(ParserError(
                "Macro expansion too deep (recursive macro?)"))
            return tokens
        out: List[Token] = []
        i = 0
        n = len(tokens)
        while i < n:
            t = tokens[i]
            if (t.type == "IDENTIFIER" and t.lexeme.upper() in macros
                    and i + 1 < n and tokens[i + 1].type == "LPAREN"):
                params, body = macros[t.lexeme.upper()]
                args, end = self._read_paren_args(tokens, i + 1)
                if args is None:
                    out.append(t)
                    i += 1
                    continue
                j = end
                if j < n and tokens[j].type == "SEMICOLON":
                    j += 1  # a statement-form invocation swallows its ';'
                if len(args) != len(params):
                    self.errors.append(ParserError(
                        f"Macro '{t.lexeme}' expects {len(params)} argument(s), "
                        f"got {len(args)}", t.line, t.column))
                    out.append(t)
                    i += 1
                    continue
                expanded = self._expand_one_macro(t, params, body, args)
                out.extend(self._apply_macros(expanded, macros, depth + 1))
                i = j
                continue
            out.append(t)
            i += 1
        return out

    def _expand_macros(self, tokens: List[Token]) -> List[Token]:
        """Full parameterized-macro pass: capture definitions, then expand all
        invocations in the remaining stream."""
        macros, stripped = self._collect_macro_defs(tokens)
        if not macros:
            return stripped
        return self._apply_macros(stripped, macros)

    def _expand_replace(self, tokens: List[Token]) -> List[Token]:
        """Apply HAL/S REPLACE <name> BY <tokens>; and parameterized
        REPLACE <name>(params) BY <tokens>; substitution.

        Definitions are collected and removed; subsequent IDENTIFIER tokens
        whose name matches a macro are replaced by the macro's token list.
        Forward references only (definitions precede uses), which matches the
        directive-at-top convention in the source.
        """
        simple: dict = {}
        parametric: dict = {}
        out: List[Token] = []
        i = 0
        n = len(tokens)
        while i < n:
            tok = tokens[i]
            if tok.type == "KEYWORD_REPLACE":
                if i + 1 >= n or tokens[i + 1].type != "IDENTIFIER":
                    self.errors.append(ParserError(
                        "Malformed REPLACE: expected a name",
                        tok.line, tok.column))
                    i += 1
                    continue
                name = tokens[i + 1].lexeme.upper()
                k = i + 2
                if k < n and tokens[k].type == "LPAREN":
                    argls, end = self._read_paren_args(tokens, k)
                    if argls is None or end >= n or tokens[end].type != "KEYWORD_BY":
                        self.errors.append(ParserError(
                            f"Malformed parameterized REPLACE '{name}' "
                            "(expected REPLACE name(params) BY ...;)",
                            tok.line, tok.column))
                        i += 1
                        continue
                    params: List[str] = []
                    bad_params = False
                    for a in argls:
                        if len(a) == 1 and a[0].type == "IDENTIFIER":
                            params.append(a[0].lexeme.upper())
                        else:
                            self.errors.append(ParserError(
                                f"REPLACE '{name}' parameters must be "
                                "identifiers", tok.line, tok.column))
                            bad_params = True
                            break
                    if bad_params:
                        i += 1
                        continue
                    j = end + 1
                    body: List[Token] = []
                    while j < n and tokens[j].type != "SEMICOLON":
                        body.append(tokens[j])
                        j += 1
                    parametric[name] = (params, body)
                    i = j + 1
                    continue
                if k < n and tokens[k].type == "KEYWORD_BY":
                    j = k + 1
                    value: List[Token] = []
                    while j < n and tokens[j].type != "SEMICOLON":
                        value.append(tokens[j])
                        j += 1
                    simple[name] = value
                    i = j + 1
                    continue
                self.errors.append(ParserError(
                    f"Malformed REPLACE '{name}': expected BY",
                    tok.line, tok.column))
                i += 1
                continue
            if (tok.type == "IDENTIFIER" and tok.lexeme.upper() in parametric
                    and i + 1 < n and tokens[i + 1].type == "LPAREN"):
                params, body = parametric[tok.lexeme.upper()]
                args, end = self._read_paren_args(tokens, i + 1)
                if args is None:
                    out.append(tok)
                    i += 1
                    continue
                if len(args) != len(params):
                    self.errors.append(ParserError(
                        f"REPLACE '{tok.lexeme}' expects {len(params)} "
                        f"argument(s), got {len(args)}",
                        tok.line, tok.column))
                    out.append(tok)
                    i += 1
                    continue
                expanded = self._expand_one_macro(tok, params, body, args)
                out.extend(expanded)
                i = end  # do not swallow a following ';'; this is an expression
                continue
            if tok.type == "IDENTIFIER" and tok.lexeme.upper() in simple:
                out.extend(simple[tok.lexeme.upper()])
                i += 1
                continue
            out.append(tok)
            i += 1
        return out

    # Token types that mark the *end* of an operand (a left context for
    # juxtaposition-multiply): identifier, numeric literal, or the closing
    # paren of a subscript/call/grouped expression. Class-level so callers
    # can reason about the transform's boundary rules without rebuilding
    # the sets on every parse.
    _IMPLICIT_MUL_LEFT_OPS = frozenset({"IDENTIFIER", "NUMBER", "RPAREN"})
    # Token types that mark the *start* of an operand (a right context).
    # LPAREN is deliberately excluded -- `A (B)` would collide with function
    # calls and subscripts and is not handled by this pass.
    _IMPLICIT_MUL_RIGHT_OPS = frozenset({"IDENTIFIER", "NUMBER"})

    def _should_insert_multiply(self, left: str, right: str) -> bool:
        """True when juxtaposition of `left` then `right` is a product."""
        if left in self._IMPLICIT_MUL_LEFT_OPS and right in self._IMPLICIT_MUL_RIGHT_OPS:
            return True
        # 2(X+1) and (A+B)(C+D). IDENTIFIER LPAREN is a call/subscript.
        return right == "LPAREN" and left in ("NUMBER", "RPAREN")

    def _insert_implicit_multiply(self, tokens: List[Token]) -> List[Token]:
        """HAL/S multiplies adjacent operands by juxtaposition (e.g. `I W` means
        `I * W`, and `ATTITUDE(1) RAD_TO_DEG` means the element times the scalar).
        Rather than make the grammar ambiguous (a generative
        `expression : expression expression` rule yields dozens of reduce/reduce
        conflicts), synthesize an explicit MULTIPLY token between two adjacent
        operand tokens so the parser uses its existing multiply rule.

        The left operand may end in IDENTIFIER, NUMBER, or RPAREN (a subscript,
        call, or grouped expression); the right must START an operand, so it is
        IDENTIFIER or NUMBER. `2(X+1)` and `(A)(B)` also insert `*` (`NUMBER` or
        `RPAREN` followed by `LPAREN`). `IDENTIFIER LPAREN` is a call/subscript
        and is not multiplied. Insertion is SUPPRESSED:
          * inside DECLARE statements -- a variable name sits next to a
            structure-template name (`DECLARE PRIMARY_ALARM NODE STRUCTURE;`);
          * immediately after a WRITE/READ channel `)` -- there the following
            token is the first I/O item (`WRITE(6) X`), not a multiplicand.
        """
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
                    and self._should_insert_multiply(out[-1].type, t)):
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
        """Parse a HEX/BIN/OCT or suffix bit literal token into a LiteralNode.

        Preserves the source-declared bit width (digits × bits-per-digit) on
        the node so downstream passes -- BIT concatenation width checking in
        the parser and shift-and-OR lowering in the backend -- can honor the
        original register width instead of inferring from the numeric value."""
        import re
        raw = tok.value
        ttype = tok.gettokentype()
        m = re.search(r"'([^']*)'", raw)
        digits = m.group(1) if m else ""
        if ttype == "HEXSTRING":
            base, lit, bits_per_digit = 16, "HEX", 4
        elif ttype == "BITSTRING":
            base, lit, bits_per_digit = 2, "BIT", 1
        elif ttype == "OCTSTRING":
            base, lit, bits_per_digit = 8, "OCT", 3
        else:  # BITLITERAL, suffix form '....'[BHO]
            suffix = raw[-1].upper()
            base, lit, bits_per_digit = {
                "B": (2, "BIT", 1), "H": (16, "HEX", 4), "O": (8, "OCT", 3),
            }.get(suffix, (2, "BIT", 1))
        try:
            value = int(digits, base) if digits else 0
        except ValueError:
            self.errors.append(ParserError(
                f"Invalid {lit} literal {raw}",
                getattr(tok, "line", 0),
                getattr(tok, "column", 0),
            ))
            value = 0
        bit_width = len(digits) * bits_per_digit if digits else None
        return LiteralNode(
            value=value,
            literal_type=lit,
            bit_width=bit_width,
            line=getattr(tok, "line", 0),
            column=getattr(tok, "column", 0),
        )

    def _register_declare(self, node: DeclareNode) -> None:
        """Enter each identifier of a DECLARE into the current scope."""
        ts = node.type_spec or TypeSpec(data_type=DataType.UNKNOWN)
        for ident in node.identifiers:
            try:
                self.symbol_table.declare(Symbol(
                    name=ident,
                    type_spec=ts,
                    scope=self.symbol_table.current_scope_name,
                    is_constant=node.is_constant,
                    line=node.line,
                    column=node.column,
                ))
            except SemanticError as exc:
                self.errors.append(exc)

    def _collect_symbols(self, node: Optional[ASTNode]) -> None:
        """Walk the AST, recording DECLAREs and rejecting same-scope duplicates.

        PROGRAM / TASK / PROCEDURE / COMPOOL each open a scope. Structure
        field names stay inside the template and are not block-level symbols.
        """
        if node is None:
            return
        if isinstance(node, CompilationNode):
            for unit in node.units:
                self._collect_symbols(unit)
            return
        if isinstance(node, (ProgramNode, TaskNode, ProcedureNode)):
            self.symbol_table.enter_scope(node.name.upper())
            for stmt in node.statements:
                self._collect_symbols(stmt)
            for proc in getattr(node, "procedures", None) or []:
                self._collect_symbols(proc)
            for task in getattr(node, "tasks", None) or []:
                self._collect_symbols(task)
            self.symbol_table.exit_scope()
            return
        if isinstance(node, CompoolNode):
            self.symbol_table.enter_scope(node.name.upper())
            for decl in node.declarations:
                if isinstance(decl, DeclareNode):
                    self._register_declare(decl)
            self.symbol_table.exit_scope()
            return
        if isinstance(node, DeclareNode):
            self._register_declare(node)
            return
        if isinstance(node, StructureDefNode):
            return
        for value in vars(node).values():
            items = value if isinstance(value, list) else [value]
            for item in items:
                if isinstance(item, ASTNode):
                    self._collect_symbols(item)

    def _check_exit_context(self, node: ASTNode, in_loop: bool,
                            labels: frozenset = frozenset()) -> None:
        """Semantic check: EXIT is only legal inside a loop body.

        Walks the AST tracking (a) whether the current position is inside a
        DO FOR / DO WHILE loop, and (b) the set of labels of enclosing loops.
        A PROGRAM, PROCEDURE, or TASK body opens a fresh scope (an outer loop
        or label does not reach into a nested procedure). Rules:
          - `EXIT;` with no enclosing loop -> "used outside of loop".
          - `EXIT <label>;` where <label> is not an enclosing loop label ->
            "references unknown loop label".
        """
        if node is None:
            return
        if isinstance(node, ExitNode):
            if node.label:
                if node.label.upper() not in labels:
                    self.errors.append(SemanticError(
                        f"Context Error: EXIT references unknown loop label "
                        f"'{node.label}'.",
                        node.line, node.column,
                    ))
            elif not in_loop:
                self.errors.append(SemanticError(
                    "Context Error: EXIT used outside of loop.",
                    node.line, node.column,
                ))
            return

        # A procedure/task/program body is a fresh scope: no enclosing loop.
        if isinstance(node, (ProgramNode, ProcedureNode, TaskNode)):
            in_loop, labels = False, frozenset()

        # A label directly wrapping a loop names that loop; recurse into the
        # loop body with the label available as an EXIT target.
        if isinstance(node, LabelNode) and isinstance(
                node.statement, (ForNode, WhileNode)):
            body_labels = labels | {node.name.upper()}
            for stmt in node.statement.statements:
                self._check_exit_context(stmt, True, body_labels)
            return

        # Entering a (bare, unlabeled) loop: its body is in-loop.
        child_in_loop = True if isinstance(node, (ForNode, WhileNode)) else in_loop

        for value in vars(node).values():
            items = value if isinstance(value, list) else [value]
            for item in items:
                if isinstance(item, ASTNode):
                    self._check_exit_context(item, child_in_loop, labels)

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
        self._proc_param_types = {}
        self.symbol_table = SymbolTable()

        # Token-level preprocessor passes, before parsing:
        #   1. parameterized MACRO ... MEND expansion (with lexical hygiene)
        #   2. REPLACE <name> BY ...; textual macros
        #   3. implicit multiply by juxtaposition
        self._macro_uid = 0
        tokens = self._expand_macros(tokens)
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
            if self.ast is not None:
                self._collect_symbols(self.ast)
                self._check_exit_context(self.ast, in_loop=False)
            return self.ast

        except RplyParsingError as e:
            # Real syntax error from the LALR engine (no production applies).
            # Keep the message clean; attach the source position so the
            # user sees the offending line/column, not a naked stack.
            pos = e.getsourcepos()
            line = getattr(pos, "lineno", 0) if pos is not None else 0
            col = getattr(pos, "colno", 0) if pos is not None else 0
            if not self.errors:
                self.errors.append(ParserError(
                    getattr(e, "message", str(e)), line=line, column=col))
            return None
        except Exception as e:
            # Anything else here is a bug in the parser's own code (a
            # grammar action reaching for a field the production doesn't
            # have, a KeyError on an unregistered case, etc). Silently
            # converting to ParserError(str(e)) hid which production
            # broke. Always surface the exception's type; on RESNIK_DEBUG=1
            # include the full traceback so the offending action is
            # traceable without re-instrumenting the compiler.
            summary = f"internal parser error -- {type(e).__name__}: {e}"
            if os.environ.get("RESNIK_DEBUG"):
                tb = "".join(traceback.format_exception(
                    type(e), e, e.__traceback__))
                summary = f"{summary}\n{tb}"
            else:
                summary = (f"{summary} "
                           "(rerun with RESNIK_DEBUG=1 for full traceback)")
            if not self.errors:
                self.errors.append(ParserError(summary))
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
