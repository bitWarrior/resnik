# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Steven Doran
"""
Token definitions for the Resnik lexer.
"""

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class Token:
    """
    Represents a single token with metadata.

    Attributes:
        type: Token type (e.g., KEYWORD_DECLARE, IDENTIFIER, NUMBER)
        lexeme: The actual text from source code
        line: Line number in source (1-indexed)
        column: Column number in source (1-indexed)
        file: Source file path
    """

    type: str
    lexeme: str
    line: int
    column: int
    file: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert token to dictionary for JSON serialization."""
        return {
            "type": self.type,
            "lexeme": self.lexeme,
            "line": self.line,
            "column": self.column,
            "file": self.file,
        }


# HAL/S Reserved Keywords (case insensitive)
KEYWORDS = {
    # Block structure
    "PROGRAM",
    "COMPOOL",
    "PROCEDURE",
    "FUNCTION",
    "TASK",
    "UPDATE",
    "CLOSE",
    "EXTERNAL",
    # Declarations
    "DECLARE",
    "REPLACE",
    "MACRO",
    "MEND",
    "STRUCTURE",
    "TEMPLATE",
    # Data types
    "INTEGER",
    "SCALAR",
    "VECTOR",
    "MATRIX",
    "BIT",
    "CHARACTER",
    "BOOLEAN",
    "EVENT",
    # Precision
    "SINGLE",
    "DOUBLE",
    # Attributes
    "STATIC",
    "AUTOMATIC",
    "DENSE",
    "ALIGNED",
    "RIGID",
    "LATCHED",
    "LOCK",
    "REMOTE",
    "NONHAL",
    "ACCESS",
    "TEMPORARY",
    # Initialization
    "INITIAL",
    "CONSTANT",
    # Control flow
    "IF",
    "THEN",
    "ELSE",
    "DO",
    "END",
    "CASE",
    "FOR",
    "WHILE",
    "UNTIL",
    "EXIT",
    "REPEAT",
    "GOTO",
    "GO",
    "TO",
    "RETURN",
    # Procedure/Function
    "CALL",
    "ASSIGN",
    "REENTRANT",
    "EXCLUSIVE",
    # Real-time control
    "SCHEDULE",
    "WAIT",
    "TERMINATE",
    "CANCEL",
    "PRIORITY",
    "AFTER",
    "EVERY",
    "ON",
    "IN",
    "DEPENDENT",
    # I/O
    "FILE",
    "READ",
    "READALL",
    "WRITE",
    "COLUMN",
    "LINE",
    "PAGE",
    # Event handling
    "SEND",
    "SET",
    "RESET",
    "SIGNAL",
    "IGNORE",
    # Error handling
    "ERROR",
    "RESUME",
    "EQUATE",
    "OFF",
    # Boolean operators
    "AND",
    "OR",
    "NOT",
    "XOR",
    "CAT",
    # Name operations
    "NAME",
    "NULL",
    # Number formats
    "BIN",
    "HEX",
    "OCT",
    "DEC",
    # Additional keywords
    "ARRAY",
    "OF",
    "BY",
    "DOWNTO",
    "AT",
    "MOD",
}
