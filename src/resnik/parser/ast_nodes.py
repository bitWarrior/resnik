"""
Abstract Syntax Tree node definitions.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from resnik.types import NodeType, TypeSpec


@dataclass
class ASTNode:
    """Base class for all AST nodes."""

    node_type: NodeType = NodeType.PROGRAM  # Default value, overridden in __post_init__
    line: int = 0
    column: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert node to dictionary for JSON serialization."""
        return {
            "node_type": self.node_type.value,
            "line": self.line,
            "column": self.column,
        }


@dataclass
class ProgramNode(ASTNode):
    """PROGRAM block node."""

    name: str = ""
    declarations: List[ASTNode] = field(default_factory=list)
    statements: List[ASTNode] = field(default_factory=list)
    procedures: List[ASTNode] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.node_type = NodeType.PROGRAM

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "name": self.name,
                "declarations": [d.to_dict() for d in self.declarations],
                "statements": [s.to_dict() for s in self.statements],
                "procedures": [p.to_dict() for p in self.procedures],
            }
        )
        return result


@dataclass
class DeclareNode(ASTNode):
    """DECLARE statement node."""

    identifiers: List[str] = field(default_factory=list)
    type_spec: Optional[TypeSpec] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    initial_value: Optional[ASTNode] = None
    is_constant: bool = False

    def __post_init__(self) -> None:
        self.node_type = NodeType.DECLARE

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "identifiers": self.identifiers,
                "type_spec": self.type_spec.to_dict() if self.type_spec else None,
                "attributes": self.attributes,
                "initial_value": (
                    self.initial_value.to_dict() if self.initial_value else None
                ),
                "is_constant": self.is_constant,
            }
        )
        return result


@dataclass
class ProcedureNode(ASTNode):
    """PROCEDURE block node."""

    name: str = ""
    parameters: List[str] = field(default_factory=list)
    declarations: List[ASTNode] = field(default_factory=list)
    statements: List[ASTNode] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.node_type = NodeType.PROCEDURE

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "name": self.name,
                "parameters": self.parameters,
                "declarations": [d.to_dict() for d in self.declarations],
                "statements": [s.to_dict() for s in self.statements],
            }
        )
        return result


@dataclass
class AssignmentNode(ASTNode):
    """Assignment statement node."""

    target: Optional[ASTNode] = None
    value: Optional[ASTNode] = None

    def __post_init__(self) -> None:
        self.node_type = NodeType.ASSIGNMENT

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "target": self.target.to_dict() if self.target else None,
                "value": self.value.to_dict() if self.value else None,
            }
        )
        return result


@dataclass
class BinaryOpNode(ASTNode):
    """Binary operation node."""

    operator: str = ""
    left: Optional[ASTNode] = None
    right: Optional[ASTNode] = None

    def __post_init__(self) -> None:
        self.node_type = NodeType.BINARY_OP

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "operator": self.operator,
                "left": self.left.to_dict() if self.left else None,
                "right": self.right.to_dict() if self.right else None,
            }
        )
        return result


@dataclass
class LiteralNode(ASTNode):
    """Literal value node."""

    value: Any = None
    literal_type: str = ""

    def __post_init__(self) -> None:
        self.node_type = NodeType.LITERAL

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update({"value": self.value, "literal_type": self.literal_type})
        return result


@dataclass
class IdentifierNode(ASTNode):
    """Identifier reference node."""

    name: str = ""

    def __post_init__(self) -> None:
        self.node_type = NodeType.IDENTIFIER

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update({"name": self.name})
        return result
