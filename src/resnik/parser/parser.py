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
from resnik.parser.ast_nodes import ASTNode, ProgramNode
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
                "SEMICOLON",
                "IDENTIFIER",
            ],
            precedence=[],
        )

        self.symbol_table = SymbolTable()
        self.errors: List[ParserError] = []
        self.ast: Optional[ASTNode] = None

        self._define_grammar()
        self.parser = self.pg.build()

    def _define_grammar(self) -> None:
        """Define simplified HAL/S grammar rules."""

        @self.pg.production(
            "program : KEYWORD_PROGRAM IDENTIFIER SEMICOLON "
            "KEYWORD_CLOSE IDENTIFIER SEMICOLON"
        )
        def program(p: List[Any]) -> ProgramNode:
            name = p[1].value
            close_name = p[4].value

            if name.upper() != close_name.upper():
                self.errors.append(
                    SemanticError(
                        f"CLOSE name '{close_name}' does not match "
                        f"PROGRAM name '{name}'",
                        getattr(p[4], "line", 0),
                        getattr(p[4], "column", 0),
                    )
                )

            return ProgramNode(
                name=name,
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
