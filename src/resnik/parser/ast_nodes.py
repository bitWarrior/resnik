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
    tasks: List[ASTNode] = field(default_factory=list)

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
                "tasks": [t.to_dict() for t in self.tasks],
            }
        )
        return result


@dataclass
class TaskNode(ASTNode):
    """TASK block node (a schedulable real-time process)."""

    name: str = ""
    declarations: List[ASTNode] = field(default_factory=list)
    statements: List[ASTNode] = field(default_factory=list)
    procedures: List[ASTNode] = field(default_factory=list)
    tasks: List[ASTNode] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.node_type = NodeType.TASK

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "name": self.name,
                "declarations": [d.to_dict() for d in self.declarations],
                "statements": [s.to_dict() for s in self.statements],
                "procedures": [p.to_dict() for p in self.procedures],
                "tasks": [t.to_dict() for t in self.tasks],
            }
        )
        return result


@dataclass
class CompoolNode(ASTNode):
    """COMPOOL block: a named pool of shared (global) declarations."""

    name: str = ""
    declarations: List[ASTNode] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.node_type = NodeType.COMPOOL

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "name": self.name,
                "declarations": [d.to_dict() for d in self.declarations],
            }
        )
        return result


@dataclass
class CompilationNode(ASTNode):
    """A compilation: a sequence of top-level units (COMPOOL/PROGRAM/TASK)."""

    units: List[ASTNode] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.node_type = NodeType.COMPILATION

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update({"units": [u.to_dict() for u in self.units]})
        return result


@dataclass
class DeclareNode(ASTNode):
    """DECLARE statement node."""

    identifiers: List[str] = field(default_factory=list)
    type_spec: Optional[TypeSpec] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    initial_value: Optional[ASTNode] = None
    initial_values: List[ASTNode] = field(default_factory=list)
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
                "initial_values": [v.to_dict() for v in self.initial_values],
                "is_constant": self.is_constant,
            }
        )
        return result


@dataclass
class ProcedureNode(ASTNode):
    """PROCEDURE block node."""

    name: str = ""
    parameters: List[str] = field(default_factory=list)
    assign_params: List[str] = field(default_factory=list)  # ASSIGN outputs
    declarations: List[ASTNode] = field(default_factory=list)
    statements: List[ASTNode] = field(default_factory=list)
    return_type: Optional["TypeSpec"] = None  # set when this is a FUNCTION
    is_external: bool = False  # NONHAL: opaque external (FFI) function

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
                "return_type": (
                    self.return_type.to_dict() if self.return_type else None
                ),
            }
        )
        return result


@dataclass
class ReturnNode(ASTNode):
    """RETURN <expr>; — return a value from a FUNCTION."""

    value: Optional[ASTNode] = None

    def __post_init__(self) -> None:
        self.node_type = NodeType.RETURN

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update({"value": self.value.to_dict() if self.value else None})
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
class UnaryOpNode(ASTNode):
    """Unary operation node (e.g. NOT x)."""

    operator: str = ""
    operand: Optional[ASTNode] = None

    def __post_init__(self) -> None:
        self.node_type = NodeType.UNARY_OP

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "operator": self.operator,
                "operand": self.operand.to_dict() if self.operand else None,
            }
        )
        return result


@dataclass
class SubscriptNode(ASTNode):
    """Bit/element subscript: <operand>$<index> (1-indexed, MSB-first for bits).

    `index` may be a literal int (back-compat) or an expression node for
    dynamic subscripting (e.g. VEC$IDX).
    """

    operand: Optional[ASTNode] = None
    index: Any = 0

    def __post_init__(self) -> None:
        self.node_type = NodeType.SUBSCRIPT

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "operand": self.operand.to_dict() if self.operand else None,
                "index": (
                    self.index.to_dict()
                    if hasattr(self.index, "to_dict") else self.index
                ),
            }
        )
        return result


@dataclass
class StructureDefNode(ASTNode):
    """STRUCTURE <name>: <field declares> CLOSE <name>; template definition."""

    name: str = ""
    fields: List[ASTNode] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.node_type = NodeType.STRUCTURE

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "name": self.name,
                "fields": [f.to_dict() for f in self.fields],
            }
        )
        return result


@dataclass
class FieldAccessNode(ASTNode):
    """Structure field access: <operand>.<field>."""

    operand: Optional[ASTNode] = None
    field_name: str = ""

    def __post_init__(self) -> None:
        self.node_type = NodeType.FIELD_ACCESS

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "operand": self.operand.to_dict() if self.operand else None,
                "field_name": self.field_name,
            }
        )
        return result


@dataclass
class VectorLiteralNode(ASTNode):
    """VECTOR(v1, v2, ...) constructor."""

    values: List[ASTNode] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.node_type = NodeType.VECTOR_LITERAL

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update({"values": [v.to_dict() for v in self.values]})
        return result


@dataclass
class OnErrorNode(ASTNode):
    """ON ERROR(code): <handler>  — resumable fault handler (approximated)."""

    code: int = 0
    body: Optional[ASTNode] = None

    def __post_init__(self) -> None:
        self.node_type = NodeType.ON_ERROR

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {"code": self.code,
             "body": self.body.to_dict() if self.body else None}
        )
        return result


@dataclass
class ResumeNode(ASTNode):
    """RESUME; — return control to the statement after the faulting one."""

    def __post_init__(self) -> None:
        self.node_type = NodeType.RESUME


@dataclass
class OnErrorNode(ASTNode):
    """ON ERROR(code): <handler> — a registered fault handler (callback)."""

    code: int = 0
    body: Optional[ASTNode] = None

    def __post_init__(self) -> None:
        self.node_type = NodeType.ON_ERROR

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "code": self.code,
                "body": self.body.to_dict() if self.body else None,
            }
        )
        return result


@dataclass
class ResumeNode(ASTNode):
    """RESUME — return control to the statement after the faulting one."""

    def __post_init__(self) -> None:
        self.node_type = NodeType.RESUME


@dataclass
class MultiAssignNode(ASTNode):
    """Multiple assignment: A, B, ... = <expr> (one RHS, several lvalues)."""

    targets: List[ASTNode] = field(default_factory=list)
    value: Optional[ASTNode] = None

    def __post_init__(self) -> None:
        self.node_type = NodeType.MULTI_ASSIGN

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "targets": [t.to_dict() for t in self.targets],
                "value": self.value.to_dict() if self.value else None,
            }
        )
        return result


@dataclass
class StarNode(ASTNode):
    """Wildcard `*` in a matrix subscript (all rows or all columns)."""

    def __post_init__(self) -> None:
        self.node_type = NodeType.STAR


@dataclass
class RangeNode(ASTNode):
    """An index range `lo TO hi` in a matrix subscript."""

    lo: Optional[ASTNode] = None
    hi: Optional[ASTNode] = None

    def __post_init__(self) -> None:
        self.node_type = NodeType.RANGE

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "lo": self.lo.to_dict() if self.lo else None,
                "hi": self.hi.to_dict() if self.hi else None,
            }
        )
        return result


@dataclass
class IndexListNode(ASTNode):
    """A comma-separated subscript coordinate, e.g. X$(i, j) -> [i, j]."""

    indices: List[ASTNode] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.node_type = NodeType.INDEX_LIST

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update({"indices": [i.to_dict() for i in self.indices]})
        return result


@dataclass
class MatrixLiteralNode(ASTNode):
    """MATRIX(v1, v2, ...) constructor; values are row-major."""

    values: List[ASTNode] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.node_type = NodeType.MATRIX_LITERAL

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update({"values": [v.to_dict() for v in self.values]})
        return result


@dataclass
class MatrixSubscriptNode(ASTNode):
    """Matrix subscript M(<row>, <col>) where each spec is an index, a range,
    or `*` (all)."""

    name: str = ""
    args: List[ASTNode] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.node_type = NodeType.MATRIX_SUBSCRIPT

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "name": self.name,
                "args": [a.to_dict() for a in self.args],
            }
        )
        return result


@dataclass
class ForNode(ASTNode):
    """DO FOR <var> = <start> TO <end> [BY <step>]; ... END; counted loop."""

    var: str = ""
    start: Optional[ASTNode] = None
    end: Optional[ASTNode] = None
    step: Optional[ASTNode] = None
    statements: List[ASTNode] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.node_type = NodeType.DO_FOR

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "var": self.var,
                "start": self.start.to_dict() if self.start else None,
                "end": self.end.to_dict() if self.end else None,
                "step": self.step.to_dict() if self.step else None,
                "statements": [s.to_dict() for s in self.statements],
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


@dataclass
class WriteNode(ASTNode):
    """WRITE statement node: WRITE(channel) item, item, ...;"""

    channel: int = 6
    items: List[ASTNode] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.node_type = NodeType.WRITE

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "channel": self.channel,
                "items": [i.to_dict() for i in self.items],
            }
        )
        return result


@dataclass
class ReadNode(ASTNode):
    """READ statement node: READ(channel) target, target, ...;"""

    channel: int = 5
    targets: List[ASTNode] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.node_type = NodeType.READ

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "channel": self.channel,
                "targets": [t.to_dict() for t in self.targets],
            }
        )
        return result


@dataclass
class FunctionCallNode(ASTNode):
    """Function/built-in call used as an expression, e.g. ABVAL(V)."""

    name: str = ""
    arguments: List[ASTNode] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.node_type = NodeType.FUNCTION_CALL

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "name": self.name,
                "arguments": [a.to_dict() for a in self.arguments],
            }
        )
        return result


@dataclass
class DoBlockNode(ASTNode):
    """DO; ... END; statement group."""

    statements: List[ASTNode] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.node_type = NodeType.DO_BLOCK

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update({"statements": [s.to_dict() for s in self.statements]})
        return result


@dataclass
class WhileNode(ASTNode):
    """DO WHILE <condition>; ... END; loop."""

    condition: Optional[ASTNode] = None
    statements: List[ASTNode] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.node_type = NodeType.DO_WHILE

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "condition": self.condition.to_dict() if self.condition else None,
                "statements": [s.to_dict() for s in self.statements],
            }
        )
        return result


@dataclass
class CaseNode(ASTNode):
    """DO CASE <selector>; <branch1> <branch2> ... END; computed case.

    The selector's integer value (1-indexed) selects which branch runs.
    """

    selector: Optional[ASTNode] = None
    branches: List[ASTNode] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.node_type = NodeType.DO_CASE

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "selector": self.selector.to_dict() if self.selector else None,
                "branches": [b.to_dict() for b in self.branches],
            }
        )
        return result


@dataclass
class CallNode(ASTNode):
    """CALL <procedure>(<args>); procedure invocation."""

    name: str = ""
    arguments: List[ASTNode] = field(default_factory=list)
    assign_args: List[ASTNode] = field(default_factory=list)  # ASSIGN outputs

    def __post_init__(self) -> None:
        self.node_type = NodeType.CALL

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "name": self.name,
                "arguments": [a.to_dict() for a in self.arguments],
            }
        )
        return result


@dataclass
class IfNode(ASTNode):
    """IF condition THEN <then> [ELSE <else>]."""

    condition: Optional[ASTNode] = None
    then_block: Optional[ASTNode] = None
    else_block: Optional[ASTNode] = None

    def __post_init__(self) -> None:
        self.node_type = NodeType.IF_STATEMENT

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "condition": self.condition.to_dict() if self.condition else None,
                "then_block": (
                    self.then_block.to_dict() if self.then_block else None
                ),
                "else_block": (
                    self.else_block.to_dict() if self.else_block else None
                ),
            }
        )
        return result


@dataclass
class ScheduleNode(ASTNode):
    """SCHEDULE <task> PRIORITY(<expr>); real-time dispatch."""

    task_name: str = ""
    priority: Optional[ASTNode] = None
    delay: Optional[ASTNode] = None   # SCHEDULE T IN <delay> ...
    period: Optional[ASTNode] = None  # ... REPEAT EVERY <period>

    def __post_init__(self) -> None:
        self.node_type = NodeType.SCHEDULE

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "task_name": self.task_name,
                "priority": self.priority.to_dict() if self.priority else None,
                "delay": self.delay.to_dict() if self.delay else None,
                "period": self.period.to_dict() if self.period else None,
            }
        )
        return result


@dataclass
class TerminateNode(ASTNode):
    """TERMINATE [<task>]; (no task name terminates the current process)."""

    task_name: Optional[str] = None

    def __post_init__(self) -> None:
        self.node_type = NodeType.TERMINATE

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update({"task_name": self.task_name})
        return result


@dataclass
class WaitNode(ASTNode):
    """WAIT UNTIL <condition>; — block until the condition holds.
    WAIT <event>; — wait on a real-time EVENT (host stub: advisory)."""

    condition: Optional[ASTNode] = None
    event: Optional[str] = None

    def __post_init__(self) -> None:
        self.node_type = NodeType.WAIT

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "condition": self.condition.to_dict() if self.condition else None,
                "event": self.event,
            }
        )
        return result


@dataclass
class SignalNode(ASTNode):
    """SIGNAL <event>; — signal a real-time EVENT (host stub: advisory)."""

    name: str = ""

    def __post_init__(self) -> None:
        self.node_type = NodeType.SIGNAL

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update({"name": self.name})
        return result


@dataclass
class CancelNode(ASTNode):
    """CANCEL <task>; — stop a scheduled (cyclic) task."""

    task_name: str = ""

    def __post_init__(self) -> None:
        self.node_type = NodeType.CANCEL

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result.update({"task_name": self.task_name})
        return result


@dataclass
class GotoNode(ASTNode):
    """GO TO <label>; unconditional branch."""

    label: str = ""

    def __post_init__(self) -> None:
        self.node_type = NodeType.GOTO


@dataclass
class LabelNode(ASTNode):
    """A statement label: <name>: <statement>. `statement` is the single
    statement the label directly prefixes; in a labelled body the label also
    marks the start of a segment that runs until the next label."""

    name: str = ""
    statement: Optional[ASTNode] = None

    def __post_init__(self) -> None:
        self.node_type = NodeType.LABEL


@dataclass
class NameAssignNode(ASTNode):
    """ASSIGN <pointer> TO <target>; -- HAL/S NAME (pointer) binding. Binds the
    NAME variable `pointer` to the address of `target`, distinct from a value
    assignment (=)."""

    pointer: str = ""
    target: str = ""

    def __post_init__(self) -> None:
        self.node_type = NodeType.ASSIGNMENT
