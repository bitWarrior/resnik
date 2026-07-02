"""
Type definitions and enums for the Resnik compiler.
"""

from enum import Enum, auto
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass, field


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
    COMPILATION = "Compilation"
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
    LABEL = "Label"
    REPEAT = "Repeat"
    WRITE = "Write"
    READ = "Read"

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
    MATRIX_LITERAL = "MatrixLiteral"
    MATRIX_SUBSCRIPT = "MatrixSubscript"
    VECTOR_LITERAL = "VectorLiteral"
    FIELD_ACCESS = "FieldAccess"
    ON_ERROR = "OnError"
    RESUME = "Resume"
    MULTI_ASSIGN = "MultiAssign"
    INDEX_LIST = "IndexList"
    RANGE = "Range"
    STAR = "Star"
    FUNCTION_CALL = "FunctionCall"

    # Types
    TYPE_SPEC = "TypeSpec"


@dataclass
class TypeSpec:
    """Type specification for declarations."""

    data_type: DataType
    precision: Precision = Precision.SINGLE  # SCALAR precision: f32 (SINGLE) or f64 (DOUBLE)
    dimensions: List[int] = field(default_factory=list)
    length: Optional[int] = None
    type_name: Optional[str] = None  # structure template name, when STRUCTURE
    array_size: Optional[int] = None  # element count, when an ARRAY(n) OF ...
    array_dims: Optional[List[int]] = None  # multi-dim ARRAY(d1, d2, ...)
    element_type: Optional["TypeSpec"] = None  # ARRAY element type
    is_name: bool = False  # HAL/S NAME (pointer) declaration
    # Memory layout of an ARRAY OF STRUCTURE: "aligned" (repr(C), padded for
    # speed -- the HAL/S default) or "dense" (repr(C, packed), for storage /
    # downlink). Tracked explicitly so codegen can pick the Rust repr.
    layout: str = "aligned"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        # precision is a Precision enum; serialize as a lowercase string
        # ("single"/"double") to keep the JSON output stable. A plain string
        # or None is tolerated for robustness.
        precision = self.precision
        if precision is None:
            precision_out: Optional[str] = None
        elif hasattr(precision, "value"):
            precision_out = precision.value.lower()
        else:
            precision_out = str(precision).lower()

        return {
            "data_type": self.data_type.value,
            "precision": precision_out,
            "dimensions": self.dimensions,
            "length": self.length,
            "type_name": self.type_name,
            "array_size": self.array_size,
            "array_dims": self.array_dims,
            "element_type": (
                self.element_type.to_dict() if self.element_type else None
            ),
            "is_name": self.is_name,
            "layout": self.layout,
        }


# Type aliases for convenience
JSONDict = Dict[str, Any]
TokenList = List[Any]  # Will be List[Token] after Token is defined
