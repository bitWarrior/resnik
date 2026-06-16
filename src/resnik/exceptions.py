"""
Custom exceptions for the Resnik compiler.
"""

from typing import Optional


class ResnikError(Exception):
    """Base exception for all Resnik compiler errors."""

    def __init__(
        self, message: str, line: int = 0, column: int = 0, filename: str = ""
    ) -> None:
        self.message = message
        self.line = line
        self.column = column
        self.filename = filename
        super().__init__(self.format_error())

    def format_error(self) -> str:
        """Format the error message with location information."""
        if self.line > 0:
            location = f"File {self.filename}, " if self.filename else ""
            return f"{location}Line {self.line}, Column {self.column}: {self.message}"
        return self.message


class LexerError(ResnikError):
    """Lexical analysis error."""

    pass


class ParserError(ResnikError):
    """Syntax parsing error."""

    pass


class SemanticError(ResnikError):
    """Semantic analysis error (type checking, scope, etc.)."""

    pass


class CompilerError(ResnikError):
    """General compiler error."""

    pass


class BackendError(ResnikError):
    """Code generation error."""

    pass
