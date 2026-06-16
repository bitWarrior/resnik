"""
Type definitions and enums for the Resnik compiler.
"""

from enum import Enum, auto
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass


class DataType(Enum):
    """HAL/S data types."""

    INTEGER = "INTEGER"
    SCALAR = "SCALAR"
    VECTOR = "VECTOR"
    MATRIX = "MATRIX"
    BIT = "BIT"
    CHARACTER = "CHARACTER"
    BOOLEAN = "BOOLEAN"
    EVENT = "EVENT"
    STRUCTURE = "STRUCTURE"
    UNKNOWN = "UNKNOWN"


class Precision(Enum):
    """Numeric precision for SCALAR types."""

    SINGLE = "SINGLE"
    DOUBLE = "DOUBLE"


class NodeType(Enum):
    """AST node types."""

    # Program structure
    PROGRAM = "Program"
    COMPOOL = "Compool"
    PROCEDURE = "Procedure"
    FUNCTION = "Function"
    TASK = "Task"
    UPDATE = "Update"

    # Declarations
    DECLARE = "Declare"
    STRUCTURE = "Structure"

    # Statements
    ASSIGNMENT = "Assignment"
    IF_STATEMENT = "IfStatement"
    DO_BLOCK = "DoBlock"
    DO_FOR = "DoFor"
    DO_WHILE = "DoWhile"
    DO_UNTIL = "DoUntil"
    DO_CASE = "DoCase"
    CALL = "Call"
    RETURN = "Return"
    EXIT = "Exit"
    GOTO = "Goto"
    REPEAT = "Repeat"

    # Real-time control
    SCHEDULE = "Schedule"
    WAIT = "Wait"
    TERMINATE = "Terminate"
    CANCEL = "Cancel"
    SET = "Set"
    RESET = "Reset"
    SIGNAL = "Signal"

    # Expressions
    BINARY_OP = "BinaryOp"
    UNARY_OP = "UnaryOp"
    LITERAL = "Literal"
    IDENTIFIER = "Identifier"
    SUBSCRIPT = "Subscript"
    FUNCTION_CALL = "FunctionCall"

    # Types
    TYPE_SPEC = "TypeSpec"


@dataclass
class TypeSpec:
    """Type specification for declarations."""

    data_type: DataType
    precision: Optional[Precision] = None
    dimensions: List[int] = None
    length: Optional[int] = None

    def __post_init__(self) -> None:
        if self.dimensions is None:
            self.dimensions = []

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "data_type": self.data_type.value,
            "precision": self.precision.value if self.precision else None,
            "dimensions": self.dimensions,
            "length": self.length,
        }


# Type aliases for convenience
JSONDict = Dict[str, Any]
TokenList = List[Any]  # Will be List[Token] after Token is defined
