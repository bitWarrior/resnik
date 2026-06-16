"""
Tests for lexer tokenization functionality.
"""

import pytest


class TestBasicTokenization:
    """Test basic tokenization functionality."""

    def test_tokenize_keywords(self, lexer) -> None:
        """Test keyword tokenization."""
        code = "PROGRAM TEST"
        tokens = lexer.tokenize(code, "<test>")
        assert len(tokens) == 2
        assert tokens[0].type == "KEYWORD_PROGRAM"
        assert tokens[1].type == "IDENTIFIER"  # TEST is an identifier, not a keyword

    def test_tokenize_numbers(self, lexer) -> None:
        """Test numeric literal tokenization."""
        code = "123 45.67 1.5E-3"
        tokens = lexer.tokenize(code, "<test>")
        assert len(tokens) == 3
        assert all(t.type == "NUMBER" for t in tokens)

    def test_tokenize_identifiers(self, lexer) -> None:
        """Test identifier tokenization."""
        code = "VAR1 Variable_Name"
        tokens = lexer.tokenize(code, "<test>")
        assert len(tokens) == 2
        assert all(t.type == "IDENTIFIER" for t in tokens)

    @pytest.mark.parametrize(
        "code,expected_count",
        [
            ("X = 5", 3),
            ("X + Y - Z", 5),
            ("PROGRAM TEST; CLOSE TEST;", 6),  # Fixed: 6 tokens not 5
        ],
    )
    def test_token_counts(self, lexer, code: str, expected_count: int) -> None:
        """Test various token count scenarios."""
        tokens = lexer.tokenize(code, "<test>")
        assert len(tokens) == expected_count


class TestOperators:
    """Test operator tokenization."""

    def test_arithmetic_operators(self, lexer) -> None:
        """Test arithmetic operator tokens."""
        code = "+ - * / **"
        tokens = lexer.tokenize(code, "<test>")
        types = [t.type for t in tokens]
        assert "PLUS" in types
        assert "MINUS" in types
        assert "MULTIPLY" in types
        assert "DIVIDE" in types
        assert "POWER" in types

    def test_comparison_operators(self, lexer) -> None:
        """Test comparison operator tokens."""
        code = "< > <= >= = ¬="
        tokens = lexer.tokenize(code, "<test>")
        types = [t.type for t in tokens]
        assert "LT" in types
        assert "GT" in types
        assert "LE" in types
        assert "GE" in types
        assert "ASSIGN" in types
        assert "NE" in types


class TestComments:
    """Test comment handling."""

    def test_full_line_comment(self, lexer) -> None:
        """Test full-line comments are ignored."""
        code = """C This is a comment
PROGRAM TEST"""
        tokens = lexer.tokenize(code, "<test>")
        # Should only get PROGRAM and TEST tokens
        assert len(tokens) == 2

    def test_block_comment(self, lexer) -> None:
        """Test block comments are removed."""
        code = "PROGRAM /* comment */ TEST"
        tokens = lexer.tokenize(code, "<test>")
        assert len(tokens) == 2
        assert tokens[0].type == "KEYWORD_PROGRAM"
        assert tokens[1].type == "IDENTIFIER"  # TEST is an identifier


class TestErrorDetection:
    """Test error detection."""

    def test_invalid_bit_string(self, lexer) -> None:
        """Test invalid bit string detection."""
        code = "BIN '102'"  # Invalid: contains '2'
        tokens = lexer.tokenize(code, "<test>")
        # Should still tokenize but report error
        assert len(lexer.errors) > 0
        assert "bit string" in lexer.errors[0].message.lower()

    def test_invalid_hex_string(self, lexer) -> None:
        """Test invalid hex string detection."""
        code = "HEX 'GHIJ'"  # Invalid: contains G, I, J  
        tokens = lexer.tokenize(code, "<test>")
        # Should still tokenize but report error
        assert len(lexer.errors) > 0
        assert "hex string" in lexer.errors[0].message.lower()

    def test_valid_bit_string(self, lexer) -> None:
        """Test valid bit string is accepted."""
        code = "BIN '1010'"
        tokens = lexer.tokenize(code, "<test>")
        assert len(lexer.errors) == 0
        assert len(tokens) == 1
        assert tokens[0].type == "BITSTRING"

    def test_valid_hex_string(self, lexer) -> None:
        """Test valid hex string is accepted."""
        code = "HEX 'ABCD'"
        tokens = lexer.tokenize(code, "<test>")
        assert len(lexer.errors) == 0
        assert len(tokens) == 1
        assert tokens[0].type == "HEXSTRING"

    def test_valid_oct_string(self, lexer) -> None:
        """Test valid octal string is accepted."""
        code = "OCT '1234567'"
        tokens = lexer.tokenize(code, "<test>")
        assert len(lexer.errors) == 0
        assert len(tokens) == 1
        assert tokens[0].type == "OCTSTRING"

    def test_invalid_oct_string(self, lexer) -> None:
        """Test invalid octal string detection."""
        code = "OCT '12389'"  # Invalid: contains '8' and '9'
        tokens = lexer.tokenize(code, "<test>")
        assert len(lexer.errors) > 0
        assert "octal string" in lexer.errors[0].message.lower()
