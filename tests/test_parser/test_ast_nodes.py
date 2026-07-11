"""
Comprehensive tests for AST node classes.
"""

import pytest
from resnik.parser.ast_nodes import (
    ASTNode,
    ProgramNode,
    DeclareNode,
    ProcedureNode,
    AssignmentNode,
    BinaryOpNode,
    LiteralNode,
    IdentifierNode,
)
from resnik.types import NodeType, DataType, TypeSpec


class TestASTNodeBase:
    """Test base ASTNode class."""

    def test_base_node_creation(self) -> None:
        """Test creating a base AST node."""
        node = ASTNode(node_type=NodeType.PROGRAM, line=1, column=5)
        
        assert node.node_type == NodeType.PROGRAM
        assert node.line == 1
        assert node.column == 5

    def test_base_node_to_dict(self) -> None:
        """Test base node to_dict method."""
        node = ASTNode(node_type=NodeType.PROGRAM, line=10, column=20)
        result = node.to_dict()
        
        assert result["node_type"] == "Program"
        assert result["line"] == 10
        assert result["column"] == 20


class TestProgramNode:
    """Test ProgramNode class."""

    def test_program_node_creation(self) -> None:
        """Test creating a program node."""
        node = ProgramNode(
            name="TEST_PROGRAM",
            declarations=[],
            statements=[],
            procedures=[],
            line=1,
            column=1
        )
        
        assert node.name == "TEST_PROGRAM"
        assert node.declarations == []
        assert node.statements == []
        assert node.procedures == []
        assert node.node_type == NodeType.PROGRAM

    def test_program_node_with_content(self) -> None:
        """Test program node with declarations and statements."""
        decl = DeclareNode(identifiers=["X"], line=2, column=1)
        stmt = AssignmentNode(line=3, column=1)
        
        node = ProgramNode(
            name="MY_PROGRAM",
            declarations=[decl],
            statements=[stmt],
            procedures=[],
            line=1,
            column=1
        )
        
        assert len(node.declarations) == 1
        assert len(node.statements) == 1
        assert len(node.procedures) == 0

    def test_program_node_to_dict(self) -> None:
        """Test program node serialization."""
        node = ProgramNode(
            name="SERIALIZE_TEST",
            declarations=[],
            statements=[],
            procedures=[],
            line=1,
            column=1
        )
        
        result = node.to_dict()
        
        assert result["node_type"] == "Program"
        assert result["name"] == "SERIALIZE_TEST"
        assert result["declarations"] == []
        assert result["statements"] == []
        assert result["procedures"] == []
        assert result["line"] == 1


class TestDeclareNode:
    """Test DeclareNode class."""

    def test_declare_node_simple(self) -> None:
        """Test simple declare node."""
        node = DeclareNode(
            identifiers=["X", "Y"],
            line=5,
            column=3
        )
        
        assert node.identifiers == ["X", "Y"]
        assert node.type_spec is None
        assert node.node_type == NodeType.DECLARE

    def test_declare_node_with_type(self) -> None:
        """Test declare node with type specification."""
        type_spec = TypeSpec(data_type=DataType.INTEGER)
        node = DeclareNode(
            identifiers=["COUNT"],
            type_spec=type_spec,
            line=5,
            column=3
        )
        
        assert node.identifiers == ["COUNT"]
        assert node.type_spec.data_type == DataType.INTEGER

    def test_declare_node_with_attributes(self) -> None:
        """Test declare node with attributes."""
        node = DeclareNode(
            identifiers=["CONST_VAL"],
            attributes={"STATIC": True, "INITIAL": 100},
            is_constant=True,
            line=5,
            column=3
        )
        
        assert node.attributes["STATIC"] is True
        assert node.attributes["INITIAL"] == 100
        assert node.is_constant is True

    def test_declare_node_to_dict(self) -> None:
        """Test declare node serialization."""
        type_spec = TypeSpec(data_type=DataType.SCALAR)
        node = DeclareNode(
            identifiers=["ALTITUDE"],
            type_spec=type_spec,
            is_constant=False,
            line=10,
            column=5
        )
        
        result = node.to_dict()
        
        assert result["node_type"] == "Declare"
        assert result["identifiers"] == ["ALTITUDE"]
        assert result["type_spec"]["data_type"] == "SCALAR"
        assert result["is_constant"] is False


class TestProcedureNode:
    """Test ProcedureNode class."""

    def test_procedure_node_simple(self) -> None:
        """Test simple procedure node."""
        node = ProcedureNode(
            name="CALCULATE",
            parameters=[],
            declarations=[],
            statements=[],
            line=15,
            column=1
        )
        
        assert node.name == "CALCULATE"
        assert node.parameters == []
        assert node.node_type == NodeType.PROCEDURE

    def test_procedure_node_with_parameters(self) -> None:
        """Test procedure with parameters."""
        node = ProcedureNode(
            name="ADD",
            parameters=["A", "B"],
            declarations=[],
            statements=[],
            line=20,
            column=1
        )
        
        assert node.parameters == ["A", "B"]
        assert len(node.parameters) == 2

    def test_procedure_node_to_dict(self) -> None:
        """Test procedure node serialization."""
        node = ProcedureNode(
            name="PROCESS",
            parameters=["INPUT"],
            declarations=[],
            statements=[],
            line=25,
            column=1
        )
        
        result = node.to_dict()
        
        assert result["node_type"] == "Procedure"
        assert result["name"] == "PROCESS"
        assert result["parameters"] == ["INPUT"]


class TestAssignmentNode:
    """Test AssignmentNode class."""

    def test_assignment_node_simple(self) -> None:
        """Test simple assignment node."""
        target = IdentifierNode(name="X", line=10, column=1)
        value = LiteralNode(value=5, literal_type="INTEGER", line=10, column=5)
        
        node = AssignmentNode(
            target=target,
            value=value,
            line=10,
            column=1
        )
        
        assert node.target.name == "X"
        assert node.value.value == 5
        assert node.node_type == NodeType.ASSIGNMENT

    def test_assignment_node_to_dict(self) -> None:
        """Test assignment node serialization."""
        target = IdentifierNode(name="Y", line=15, column=1)
        value = LiteralNode(value=10.5, literal_type="SCALAR", line=15, column=5)
        
        node = AssignmentNode(
            target=target,
            value=value,
            line=15,
            column=1
        )
        
        result = node.to_dict()
        
        assert result["node_type"] == "Assignment"
        assert result["target"]["name"] == "Y"
        assert result["value"]["value"] == 10.5


class TestBinaryOpNode:
    """Test BinaryOpNode class."""

    def test_binary_op_node(self) -> None:
        """Test binary operation node."""
        left = LiteralNode(value=5, literal_type="INTEGER", line=20, column=1)
        right = LiteralNode(value=3, literal_type="INTEGER", line=20, column=5)
        
        node = BinaryOpNode(
            operator="+",
            left=left,
            right=right,
            line=20,
            column=1
        )
        
        assert node.operator == "+"
        assert node.left.value == 5
        assert node.right.value == 3
        assert node.node_type == NodeType.BINARY_OP

    def test_binary_op_to_dict(self) -> None:
        """Test binary operation serialization."""
        left = IdentifierNode(name="A", line=25, column=1)
        right = IdentifierNode(name="B", line=25, column=5)
        
        node = BinaryOpNode(
            operator="*",
            left=left,
            right=right,
            line=25,
            column=1
        )
        
        result = node.to_dict()
        
        assert result["node_type"] == "BinaryOp"
        assert result["operator"] == "*"
        assert result["left"]["name"] == "A"
        assert result["right"]["name"] == "B"


class TestLiteralNode:
    """Test LiteralNode class."""

    def test_literal_node_integer(self) -> None:
        """Test integer literal node."""
        node = LiteralNode(
            value=42,
            literal_type="INTEGER",
            line=30,
            column=10
        )
        
        assert node.value == 42
        assert node.literal_type == "INTEGER"
        assert node.node_type == NodeType.LITERAL

    def test_literal_node_float(self) -> None:
        """Test float literal node."""
        node = LiteralNode(
            value=3.14159,
            literal_type="SCALAR",
            line=35,
            column=10
        )
        
        assert node.value == 3.14159
        assert node.literal_type == "SCALAR"

    def test_literal_node_string(self) -> None:
        """Test string literal node."""
        node = LiteralNode(
            value="HELLO",
            literal_type="STRING",
            line=40,
            column=10
        )
        
        assert node.value == "HELLO"
        assert node.literal_type == "STRING"

    def test_literal_node_to_dict(self) -> None:
        """Test literal node serialization."""
        node = LiteralNode(
            value=100,
            literal_type="INTEGER",
            line=45,
            column=5
        )
        
        result = node.to_dict()
        
        assert result["node_type"] == "Literal"
        assert result["value"] == 100
        assert result["literal_type"] == "INTEGER"


class TestIdentifierNode:
    """Test IdentifierNode class."""

    def test_identifier_node(self) -> None:
        """Test identifier node."""
        node = IdentifierNode(
            name="VARIABLE_NAME",
            line=50,
            column=5
        )
        
        assert node.name == "VARIABLE_NAME"
        assert node.node_type == NodeType.IDENTIFIER

    def test_identifier_node_to_dict(self) -> None:
        """Test identifier node serialization."""
        node = IdentifierNode(
            name="MY_VAR",
            line=55,
            column=10
        )
        
        result = node.to_dict()
        
        assert result["node_type"] == "Identifier"
        assert result["name"] == "MY_VAR"
        assert result["line"] == 55
        assert result["column"] == 10


class TestComplexAST:
    """Test complex AST structures."""

    def test_nested_binary_operations(self) -> None:
        """Test nested binary operations."""
        # (A + B) * C
        a = IdentifierNode(name="A", line=60, column=1)
        b = IdentifierNode(name="B", line=60, column=5)
        c = IdentifierNode(name="C", line=60, column=11)
        
        add = BinaryOpNode(operator="+", left=a, right=b, line=60, column=3)
        mult = BinaryOpNode(operator="*", left=add, right=c, line=60, column=9)
        
        result = mult.to_dict()
        
        assert result["operator"] == "*"
        assert result["left"]["operator"] == "+"
        assert result["left"]["left"]["name"] == "A"
        assert result["left"]["right"]["name"] == "B"
        assert result["right"]["name"] == "C"

    def test_program_with_full_structure(self) -> None:
        """Test complete program structure."""
        # DECLARE X INTEGER;
        decl = DeclareNode(
            identifiers=["X"],
            type_spec=TypeSpec(data_type=DataType.INTEGER),
            line=65,
            column=1
        )
        
        # X = 5;
        target = IdentifierNode(name="X", line=66, column=1)
        value = LiteralNode(value=5, literal_type="INTEGER", line=66, column=5)
        stmt = AssignmentNode(target=target, value=value, line=66, column=1)
        
        # PROGRAM TEST;
        program = ProgramNode(
            name="COMPLEX_TEST",
            declarations=[decl],
            statements=[stmt],
            procedures=[],
            line=64,
            column=1
        )
        
        result = program.to_dict()

        assert result["name"] == "COMPLEX_TEST"
        assert len(result["declarations"]) == 1
        assert len(result["statements"]) == 1
        assert result["declarations"][0]["identifiers"] == ["X"]
        assert result["statements"][0]["target"]["name"] == "X"


class TestNoShadowedDefinitions:
    """Guard against re-introducing duplicate class definitions in ast_nodes.
    The module previously defined OnErrorNode and ResumeNode twice each, so
    the second definition silently shadowed the first — a latent divergence
    trap the moment either copy was edited."""

    def test_ast_node_classes_defined_exactly_once(self) -> None:
        import ast
        import inspect
        from resnik.parser import ast_nodes as mod

        src = inspect.getsource(mod)
        tree = ast.parse(src)
        seen: dict = {}
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                seen[node.name] = seen.get(node.name, 0) + 1
        duplicates = {name: n for name, n in seen.items() if n > 1}
        assert not duplicates, (
            f"Duplicate class definitions in ast_nodes: {duplicates}. "
            "Each class must appear exactly once — a second @dataclass "
            "silently shadows the first and hides divergence.")
