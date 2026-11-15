# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Steven Doran
"""
Resnik HAL/S Compiler
====================

A modern, type-safe compiler for the HAL/S aerospace programming language.

Example usage:
    >>> from resnik import Lexer, Parser
    >>> lexer = Lexer()
    >>> tokens = lexer.tokenize("PROGRAM TEST; CLOSE TEST;")
    >>> parser = Parser()
    >>> ast = parser.parse(tokens)
"""

__version__ = "1.0.1"
__author__ = "Resnik Compiler Project"

from resnik.lexer import Lexer, Token, LexerError
from resnik.parser import Parser, ASTNode, ParserError, SemanticError
from resnik.exceptions import ResnikError

__all__ = [
    # Version
    "__version__",
    "__author__",
    # Lexer
    "Lexer",
    "Token",
    "LexerError",
    # Parser
    "Parser",
    "ASTNode",
    "ParserError",
    "SemanticError",
    # Exceptions
    "ResnikError",
]
