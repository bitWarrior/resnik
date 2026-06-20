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
    RangeNode,
    MatrixLiteralNode,
    MatrixSubscriptNode,
    VectorLiteralNode,
    StructureDefNode,
    FieldAccessNode,
    OnErrorNode,
    ResumeNode,
    MultiAssignNode,
    WriteNode,
    FunctionCallNode,
    CallNode,
    DoBlockNode,
    WhileNode,
    CaseNode,
    IfNode,
    ScheduleNode,
    TerminateNode,
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
                "KEYWORD_COMPOOL",
                "KEYWORD_STATIC",
                "KEYWORD_CONSTANT",
                "KEYWORD_STRUCTURE",
                "KEYWORD_RESUME",
                "KEYWORD_ARRAY",
                "KEYWORD_OF",
                "DOT",
                "KEYWORD_CALL",
                "KEYWORD_WHILE",
                "KEYWORD_CASE",
                "KEYWORD_FOR",
                "KEYWORD_TO",
                "KEYWORD_BY",
                "KEYWORD_INITIAL",
                "KEYWORD_WRITE",
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
                ("left", ["KEYWORD_OR"]),
                ("left", ["KEYWORD_XOR"]),
                ("left", ["KEYWORD_AND"]),
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

        self._define_grammar()
        self.parser = self.pg.build()

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
        self, name_tok: Any, params: list, statements: list, close_tok: Any
    ) -> ProcedureNode:
        """Build a ProcedureNode and check the CLOSE name matches the label."""
        name = name_tok.value
        close_name = close_tok.value
        if name.upper() != close_name.upper():
            self.errors.append(
                SemanticError(
                    f"CLOSE name '{close_name}' does not match "
                    f"PROCEDURE name '{name}'",
                    getattr(close_tok, "line", 0),
                    getattr(close_tok, "column", 0),
                )
            )
        # Procedure bodies may not nest further tasks/procedures here; keep all
        # body items as statements (declarations are extracted by codegen).
        return ProcedureNode(
            name=name,
            parameters=params,
            statements=statements,
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

        @self.pg.production(
            "procedure_block : IDENTIFIER COLON KEYWORD_PROCEDURE SEMICOLON "
            "statement_list KEYWORD_CLOSE IDENTIFIER SEMICOLON"
        )
        def procedure_block_noparams(p: List[Any]) -> ProcedureNode:
            return self._build_procedure(
                name_tok=p[0], params=[], statements=p[4], close_tok=p[6]
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
        @self.pg.production("statement : structure_def")
        @self.pg.production("statement : on_error_block")
        @self.pg.production("statement : resume_statement")
        @self.pg.production("statement : declare_statement")
        @self.pg.production("statement : assignment_statement")
        @self.pg.production("statement : call_statement")
        @self.pg.production("statement : if_statement")
        @self.pg.production("statement : do_block")
        @self.pg.production("statement : schedule_statement")
        @self.pg.production("statement : terminate_statement")
        @self.pg.production("statement : write_statement")
        def statement(p: List[Any]) -> ASTNode:
            return p[0]

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

        # Array of structures: DECLARE C ARRAY(5) OF STAR_DATA STRUCTURE;
        @self.pg.production(
            "declare_statement : KEYWORD_DECLARE IDENTIFIER KEYWORD_ARRAY "
            "LPAREN NUMBER RPAREN KEYWORD_OF IDENTIFIER KEYWORD_STRUCTURE "
            "SEMICOLON"
        )
        def declare_array_struct(p: List[Any]) -> DeclareNode:
            return DeclareNode(
                identifiers=[p[1].value],
                type_spec=TypeSpec(
                    data_type=DataType.STRUCTURE,
                    type_name=p[7].value,
                    array_size=int(p[4].value),
                ),
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

        @self.pg.production("arg_list : arg_list COMMA expression")
        def arg_list(p: List[Any]) -> list:
            return p[0] + [p[2]]

        @self.pg.production("arg_list : expression")
        def arg_list_single(p: List[Any]) -> list:
            return [p[0]]

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
        self.symbol_table = SymbolTable()

        # Expand REPLACE ... BY ...; macros (HAL/S preprocessor) at the token
        # level before parsing. Definitions are removed; later uses of a macro
        # name are substituted with the definition's tokens.
        tokens = self._expand_replace(tokens)

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
