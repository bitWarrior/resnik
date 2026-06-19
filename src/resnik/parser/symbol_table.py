"""
Symbol table for tracking declarations and types.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from resnik.exceptions import SemanticError
from resnik.types import TypeSpec


@dataclass
class Symbol:
    """Symbol table entry."""

    name: str
    type_spec: TypeSpec
    scope: str
    is_constant: bool = False
    line: int = 0
    column: int = 0


class SymbolTable:
    """
    Symbol table for tracking declarations and types with scope management.

    Example:
        >>> table = SymbolTable()
        >>> table.enter_scope("procedure")
        >>> symbol = Symbol("x", TypeSpec(DataType.INTEGER), "procedure")
        >>> table.declare(symbol)
    """

    def __init__(self) -> None:
        """Initialize symbol table with global scope."""
        self.scopes: List[Dict[str, Symbol]] = [{}]
        self._scope_names: List[str] = ["global"]
        self.current_scope_name = "global"

    def enter_scope(self, scope_name: str) -> None:
        """
        Enter a new scope.

        Args:
            scope_name: Name of the new scope
        """
        self.scopes.append({})
        self._scope_names.append(scope_name)
        self.current_scope_name = scope_name

    def exit_scope(self) -> None:
        """Exit current scope and return to parent."""
        if len(self.scopes) > 1:
            self.scopes.pop()
            self._scope_names.pop()
            self.current_scope_name = self._scope_names[-1]

    def declare(self, symbol: Symbol) -> None:
        """
        Declare a symbol in current scope.

        Args:
            symbol: Symbol to declare

        Raises:
            SemanticError: If symbol already declared in current scope
        """
        if symbol.name in self.scopes[-1]:
            raise SemanticError(
                f"Variable '{symbol.name}' already declared in this scope",
                symbol.line,
                symbol.column,
            )
        self.scopes[-1][symbol.name] = symbol

    def lookup(self, name: str) -> Optional[Symbol]:
        """
        Look up a symbol in current and parent scopes.

        Args:
            name: Symbol name to look up

        Returns:
            Symbol if found, None otherwise
        """
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    def is_declared(self, name: str) -> bool:
        """
        Check if symbol is declared in any visible scope.

        Args:
            name: Symbol name to check

        Returns:
            True if declared, False otherwise
        """
        return self.lookup(name) is not None
