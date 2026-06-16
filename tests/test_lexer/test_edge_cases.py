"""
Additional lexer tests for edge cases and error handling to improve coverage.
"""

import pytest
import tempfile
import os
import sys
import json
from io import StringIO


class TestLexerEdgeCases:
    """Test edge cases and error handling."""

    def test_unclosed_block_comment(self, lexer) -> None:
        """Test unclosed block comment detection."""
        code = """
PROGRAM TEST;
/* This comment is never closed
X = 5;
"""
        tokens = lexer.tokenize(code, "<test>")
        
        # Should report unclosed block comment error
        assert len(lexer.errors) > 0
        assert any("Unclosed block comment" in e.message for e in lexer.errors)

    def test_unclosed_string(self, lexer) -> None:
        """Test unclosed string literal detection."""
        # Note: This is hard to trigger with rply patterns
        # The pattern 'STRING' in lexer matches '([^']|'')*'
        # But we can test the validation method directly
        from resnik.lexer.lexer import Lexer
        
        test_lexer = Lexer()
        test_lexer.source_file = "<test>"
        
        # Test unclosed string validation
        result = test_lexer._validate_string("'unclosed", 1, 1)
        
        assert result is False
        assert len(test_lexer.errors) > 0
        assert "Unclosed string literal" in test_lexer.errors[0].message

    def test_invalid_file_extension(self, lexer) -> None:
        """Test that invalid file extensions are rejected."""
        with pytest.raises(ValueError) as exc_info:
            lexer.tokenize_file("program.txt")
        
        assert "Invalid file extension" in str(exc_info.value)
        assert ".txt" in str(exc_info.value)

    def test_export_json(self, lexer) -> None:
        """Test JSON export functionality."""
        # Tokenize some code
        code = "PROGRAM TEST; CLOSE TEST;"
        tokens = lexer.tokenize(code, "<test>")
        
        # Export to temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_file = f.name
        
        try:
            lexer.export_json(temp_file)
            
            # Read and verify JSON
            with open(temp_file, 'r') as f:
                data = json.load(f)
            
            assert "source_file" in data
            assert "tokens" in data
            assert "errors" in data
            assert "statistics" in data
            assert data["source_file"] == "<test>"
            assert len(data["tokens"]) == len(tokens)
            assert data["statistics"]["total_tokens"] == len(tokens)
        finally:
            # Clean up
            if os.path.exists(temp_file):
                os.unlink(temp_file)

    def test_export_json_with_errors(self, lexer) -> None:
        """Test JSON export includes errors."""
        # Tokenize code with errors
        code = "BIN '102'"  # Invalid bit string
        tokens = lexer.tokenize(code, "<test>")
        
        # Export to temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_file = f.name
        
        try:
            lexer.export_json(temp_file)
            
            # Read and verify JSON
            with open(temp_file, 'r') as f:
                data = json.load(f)
            
            assert len(data["errors"]) > 0
            assert data["statistics"]["total_errors"] > 0
            assert "Invalid bit string" in data["errors"][0]["message"]
        finally:
            if os.path.exists(temp_file):
                os.unlink(temp_file)

    def test_report_errors_with_errors(self, lexer) -> None:
        """Test error reporting to stderr."""
        # Tokenize code with errors
        code = "BIN '102'"  # Invalid bit string
        lexer.tokenize(code, "<test>")
        
        # Capture stderr
        old_stderr = sys.stderr
        sys.stderr = StringIO()
        
        try:
            lexer.report_errors()
            error_output = sys.stderr.getvalue()
            
            # Verify error report contains expected content
            assert "LEXICAL ERRORS" in error_output
            assert "error(s) found" in error_output
            assert "Invalid bit string" in error_output
            assert "Line" in error_output
            assert "Column" in error_output
        finally:
            sys.stderr = old_stderr

    def test_report_errors_no_errors(self, lexer) -> None:
        """Test error reporting when there are no errors."""
        # Tokenize valid code
        code = "PROGRAM TEST; CLOSE TEST;"
        lexer.tokenize(code, "<test>")
        
        # Capture stderr
        old_stderr = sys.stderr
        sys.stderr = StringIO()
        
        try:
            lexer.report_errors()
            error_output = sys.stderr.getvalue()
            
            # Should be empty (early return)
            assert error_output == ""
        finally:
            sys.stderr = old_stderr

    def test_report_errors_shows_source_line(self, lexer) -> None:
        """Test that error reporting shows the source line."""
        code = """PROGRAM TEST;
BIN '102'
CLOSE TEST;"""
        lexer.tokenize(code, "<test>")
        
        # Capture stderr
        old_stderr = sys.stderr
        sys.stderr = StringIO()
        
        try:
            lexer.report_errors()
            error_output = sys.stderr.getvalue()
            
            # Should show the line with the error
            assert "BIN '102'" in error_output
            # Should show error pointer
            assert "^" in error_output
        finally:
            sys.stderr = old_stderr

    def test_empty_file(self, lexer) -> None:
        """Test tokenizing empty source."""
        code = ""
        tokens = lexer.tokenize(code, "<test>")
        
        assert len(tokens) == 0
        assert len(lexer.errors) == 0

    def test_only_comments(self, lexer) -> None:
        """Test file with only comments."""
        code = """C This is a comment
/* Another comment */
C More comments"""
        tokens = lexer.tokenize(code, "<test>")
        
        # Should have no tokens (all comments removed)
        assert len(tokens) == 0
        assert len(lexer.errors) == 0

    def test_tokenize_file_with_valid_extension_hs(self, lexer) -> None:
        """Test tokenizing .hs file."""
        # Create temporary .hs file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.hs', delete=False) as f:
            f.write("PROGRAM TEST; CLOSE TEST;")
            temp_file = f.name
        
        try:
            tokens = lexer.tokenize_file(temp_file)
            assert len(tokens) > 0
        finally:
            os.unlink(temp_file)

    def test_tokenize_file_with_valid_extension_hal(self, lexer) -> None:
        """Test tokenizing .hal file."""
        # Create temporary .hal file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.hal', delete=False) as f:
            f.write("PROGRAM TEST; CLOSE TEST;")
            temp_file = f.name
        
        try:
            tokens = lexer.tokenize_file(temp_file)
            assert len(tokens) > 0
        finally:
            os.unlink(temp_file)

    def test_multiline_block_comment(self, lexer) -> None:
        """Test multiline block comment handling."""
        code = """PROGRAM TEST;
/* This is a
   multiline
   comment */
X = 5;
CLOSE TEST;"""
        tokens = lexer.tokenize(code, "<test>")
        
        # Should have tokens but comment removed
        assert len(tokens) > 0
        assert len(lexer.errors) == 0
        # Verify comment was removed
        token_values = [t.lexeme for t in tokens]
        assert "/*" not in " ".join(token_values)
        assert "*/" not in " ".join(token_values)

    def test_nested_quotes_in_string(self, lexer) -> None:
        """Test string with embedded quotes."""
        code = "PROGRAM TEST; X = 'DON''T'; CLOSE TEST;"
        tokens = lexer.tokenize(code, "<test>")
        
        # Should tokenize successfully
        assert len(lexer.errors) == 0
        # Find the string token
        string_tokens = [t for t in tokens if t.type == "STRING"]
        assert len(string_tokens) == 1
        assert "''" in string_tokens[0].lexeme

    def test_multiple_errors_in_one_file(self, lexer) -> None:
        """Test that multiple errors are all reported."""
        code = """PROGRAM TEST;
BIN '102'
HEX 'GHIJ'
OCT '89'
CLOSE TEST;"""
        tokens = lexer.tokenize(code, "<test>")
        
        # Should have multiple errors
        assert len(lexer.errors) >= 3
        error_messages = [e.message for e in lexer.errors]
        assert any("bit string" in msg.lower() for msg in error_messages)
        assert any("hex string" in msg.lower() for msg in error_messages)
        assert any("octal string" in msg.lower() for msg in error_messages)

    def test_very_long_identifier(self, lexer) -> None:
        """Test identifier length limit."""
        # HAL/S identifiers can be up to 32 characters
        valid_id = "A" * 32
        code = f"PROGRAM {valid_id};"
        tokens = lexer.tokenize(code, "<test>")
        
        # Should tokenize successfully
        assert len(tokens) >= 2
        assert len(lexer.errors) == 0

    def test_scientific_notation_variations(self, lexer) -> None:
        """Test various scientific notation formats."""
        code = "1.5E10 2.5E-5 3.0E+3 4E10"
        tokens = lexer.tokenize(code, "<test>")
        
        # All should be recognized as numbers
        assert all(t.type == "NUMBER" for t in tokens)
        assert len(tokens) == 4

    def test_all_operators(self, lexer) -> None:
        """Test all operator types are recognized."""
        code = "+ - * / ** = < > <= >= ¬= . # @"
        tokens = lexer.tokenize(code, "<test>")
        
        expected_types = ["PLUS", "MINUS", "MULTIPLY", "DIVIDE", "POWER",
                         "ASSIGN", "LT", "GT", "LE", "GE", "NE",
                         "DOT", "HASH", "AT"]
        
        actual_types = [t.type for t in tokens]
        for expected in expected_types:
            assert expected in actual_types

    def test_position_tracking_multiline(self, lexer) -> None:
        """Test that line and column tracking works correctly."""
        code = "PROGRAM TEST;\nX = 5;\nY = 10;\nCLOSE TEST;"
        tokens = lexer.tokenize(code, "<test>")
        
        # Check that line numbers are tracked (should have multiple lines)
        lines = [t.line for t in tokens]
        unique_lines = set(lines)
        
        # Should have tokens on at least 3 different lines
        assert len(unique_lines) >= 3, f"Expected multiple lines, got: {unique_lines}"
        
        # First token should be on line 1
        assert tokens[0].line == 1, f"First token should be on line 1, got {tokens[0].line}"
        
        # Should have some tokens with line > 1
        assert any(t.line > 1 for t in tokens), "Should have tokens on multiple lines"

    def test_special_characters_in_strings(self, lexer) -> None:
        """Test strings with special characters."""
        code = "X = 'ABC123!@#$%^&*()';"
        tokens = lexer.tokenize(code, "<test>")
        
        # Should tokenize successfully
        assert len(lexer.errors) == 0
        string_tokens = [t for t in tokens if t.type == "STRING"]
        assert len(string_tokens) == 1
