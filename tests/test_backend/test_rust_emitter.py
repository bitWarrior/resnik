"""
Tests for Rust code emitter.
"""

import pytest
from resnik.backend.rust_emitter import RustEmitter


class TestRustEmitterBasic:
    """Test basic emitter functionality."""

    def test_emitter_creation(self) -> None:
        """Test creating an emitter."""
        emitter = RustEmitter()
        
        assert emitter.indent_level == 0
        assert emitter.indent_size == 4
        assert len(emitter.lines) == 0

    def test_custom_indent_size(self) -> None:
        """Test custom indent size."""
        emitter = RustEmitter(indent_size=2)
        
        assert emitter.indent_size == 2

    def test_emit_single_line(self) -> None:
        """Test emitting a single line."""
        emitter = RustEmitter()
        emitter.emit_line("let x = 5;")
        
        code = emitter.get_code()
        assert code == "let x = 5;"

    def test_emit_multiple_lines(self) -> None:
        """Test emitting multiple lines."""
        emitter = RustEmitter()
        emitter.emit_line("let x = 5;")
        emitter.emit_line("let y = 10;")
        
        code = emitter.get_code()
        assert code == "let x = 5;\nlet y = 10;"

    def test_emit_empty_line(self) -> None:
        """Test emitting empty line."""
        emitter = RustEmitter()
        emitter.emit_line("let x = 5;")
        emitter.emit_line()
        emitter.emit_line("let y = 10;")
        
        code = emitter.get_code()
        assert code == "let x = 5;\n\nlet y = 10;"


class TestIndentation:
    """Test indentation management."""

    def test_indent_increases_level(self) -> None:
        """Test indent() increases indentation level."""
        emitter = RustEmitter()
        
        assert emitter.indent_level == 0
        emitter.indent()
        assert emitter.indent_level == 1

    def test_dedent_decreases_level(self) -> None:
        """Test dedent() decreases indentation level."""
        emitter = RustEmitter()
        emitter.indent()
        emitter.indent()
        
        assert emitter.indent_level == 2
        emitter.dedent()
        assert emitter.indent_level == 1

    def test_dedent_stops_at_zero(self) -> None:
        """Test dedent() doesn't go below zero."""
        emitter = RustEmitter()
        
        assert emitter.indent_level == 0
        emitter.dedent()
        assert emitter.indent_level == 0

    def test_indented_line(self) -> None:
        """Test emitting indented line."""
        emitter = RustEmitter()
        emitter.indent()
        emitter.emit_line("let x = 5;")
        
        code = emitter.get_code()
        assert code == "    let x = 5;"

    def test_nested_indentation(self) -> None:
        """Test nested indentation."""
        emitter = RustEmitter()
        emitter.emit_line("fn main() {")
        emitter.indent()
        emitter.emit_line("if true {")
        emitter.indent()
        emitter.emit_line("let x = 5;")
        emitter.dedent()
        emitter.emit_line("}")
        emitter.dedent()
        emitter.emit_line("}")
        
        code = emitter.get_code()
        expected = "fn main() {\n    if true {\n        let x = 5;\n    }\n}"
        assert code == expected


class TestComments:
    """Test comment generation."""

    def test_single_comment(self) -> None:
        """Test emitting a comment."""
        emitter = RustEmitter()
        emitter.emit_comment("This is a comment")
        
        code = emitter.get_code()
        assert code == "// This is a comment"

    def test_doc_comment(self) -> None:
        """Test emitting a doc comment."""
        emitter = RustEmitter()
        emitter.emit_doc_comment("This function does X")
        
        code = emitter.get_code()
        assert code == "/// This function does X"

    def test_indented_comment(self) -> None:
        """Test indented comment."""
        emitter = RustEmitter()
        emitter.indent()
        emitter.emit_comment("Indented comment")
        
        code = emitter.get_code()
        assert code == "    // Indented comment"


class TestBlocks:
    """Test block generation."""

    def test_block_start(self) -> None:
        """Test emit_block_start."""
        emitter = RustEmitter()
        emitter.emit_block_start("fn main()")
        
        assert emitter.indent_level == 1
        code = emitter.get_code()
        assert code == "fn main() {"

    def test_block_end(self) -> None:
        """Test emit_block_end."""
        emitter = RustEmitter()
        emitter.emit_block_start("fn main()")
        emitter.emit_block_end()
        
        assert emitter.indent_level == 0
        code = emitter.get_code()
        assert code == "fn main() {\n}"

    def test_nested_blocks(self) -> None:
        """Test nested blocks."""
        emitter = RustEmitter()
        emitter.emit_block_start("fn main()")
        emitter.emit_block_start("if true")
        emitter.emit_line("println!(\"Hello\");")
        emitter.emit_block_end()
        emitter.emit_block_end()
        
        code = emitter.get_code()
        expected = "fn main() {\n    if true {\n        println!(\"Hello\");\n    }\n}"
        assert code == expected


class TestFunctions:
    """Test function generation."""

    def test_function_no_params_no_return(self) -> None:
        """Test function with no parameters or return."""
        emitter = RustEmitter()
        emitter.emit_function_header("main", [])
        emitter.emit_block_end()
        
        code = emitter.get_code()
        assert code == "fn main() {\n}"

    def test_function_with_params(self) -> None:
        """Test function with parameters."""
        emitter = RustEmitter()
        params = [("x", "i32"), ("y", "f32")]
        emitter.emit_function_header("add", params)
        emitter.emit_block_end()
        
        code = emitter.get_code()
        assert code == "fn add(x: i32, y: f32) {\n}"

    def test_function_with_return_type(self) -> None:
        """Test function with return type."""
        emitter = RustEmitter()
        params = [("x", "i32")]
        emitter.emit_function_header("double", params, "i32")
        emitter.emit_block_end()
        
        code = emitter.get_code()
        assert code == "fn double(x: i32) -> i32 {\n}"

    def test_public_function(self) -> None:
        """Test public function."""
        emitter = RustEmitter()
        emitter.emit_function_header("calculate", [], None, is_public=True)
        emitter.emit_block_end()
        
        code = emitter.get_code()
        assert code == "pub fn calculate() {\n}"


class TestVariables:
    """Test variable declaration."""

    def test_mutable_variable_with_value(self) -> None:
        """Test mutable variable with initial value."""
        emitter = RustEmitter()
        emitter.emit_variable_declaration("x", "i32", "5", is_mut=True)
        
        code = emitter.get_code()
        assert code == "let mut x: i32 = 5;"

    def test_immutable_variable(self) -> None:
        """Test immutable variable."""
        emitter = RustEmitter()
        emitter.emit_variable_declaration("pi", "f32", "3.14", is_mut=False)
        
        code = emitter.get_code()
        assert code == "let pi: f32 = 3.14;"

    def test_variable_no_initial_value(self) -> None:
        """Test variable without initial value."""
        emitter = RustEmitter()
        emitter.emit_variable_declaration("x", "i32", None, is_mut=True)
        
        code = emitter.get_code()
        assert code == "let mut x: i32;"


class TestStatements:
    """Test statement generation."""

    def test_assignment(self) -> None:
        """Test assignment statement."""
        emitter = RustEmitter()
        emitter.emit_assignment("x", "5 + 3")
        
        code = emitter.get_code()
        assert code == "x = 5 + 3;"

    def test_return_with_value(self) -> None:
        """Test return statement with value."""
        emitter = RustEmitter()
        emitter.emit_return("x + y")
        
        code = emitter.get_code()
        assert code == "return x + y;"

    def test_return_unit(self) -> None:
        """Test return statement without value."""
        emitter = RustEmitter()
        emitter.emit_return()
        
        code = emitter.get_code()
        assert code == "return;"


class TestUtilities:
    """Test utility methods."""

    def test_clear(self) -> None:
        """Test clearing emitter state."""
        emitter = RustEmitter()
        emitter.emit_line("let x = 5;")
        emitter.indent()
        emitter.indent()
        
        assert len(emitter.lines) > 0
        assert emitter.indent_level == 2
        
        emitter.clear()
        
        assert len(emitter.lines) == 0
        assert emitter.indent_level == 0

    def test_get_code_empty(self) -> None:
        """Test get_code with no lines."""
        emitter = RustEmitter()
        
        code = emitter.get_code()
        assert code == ""


class TestComplexExample:
    """Test complex code generation."""

    def test_complete_function(self) -> None:
        """Test generating a complete function."""
        emitter = RustEmitter()
        
        emitter.emit_doc_comment("Calculates the sum of two numbers")
        emitter.emit_function_header("add", [("a", "i32"), ("b", "i32")], "i32", is_public=True)
        emitter.emit_variable_declaration("result", "i32", "a + b", is_mut=False)
        emitter.emit_return("result")
        emitter.emit_block_end()
        
        code = emitter.get_code()
        expected = (
            "/// Calculates the sum of two numbers\n"
            "pub fn add(a: i32, b: i32) -> i32 {\n"
            "    let result: i32 = a + b;\n"
            "    return result;\n"
            "}"
        )
        assert code == expected
