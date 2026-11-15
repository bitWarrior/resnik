# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Steven Doran
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

    def test_c_prefixed_line_not_comment(self, lexer) -> None:
        """Regression: a leading 'C' does not start a comment.

        Earlier preprocessing blanked any line whose first non-blank character
        was 'C', silently deleting CLOSE/CALL/CASE lines. Only block comments
        (/* */) are stripped now.
        """
        code = "CLOSE TEST"
        tokens = lexer.tokenize(code, "<test>")
        assert len(tokens) == 2
        assert tokens[0].type == "KEYWORD_CLOSE"
        assert tokens[1].type == "IDENTIFIER"  # TEST is an identifier

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

    def test_case_insensitive_number_strings(self, lexer) -> None:
        """Lowercase bin/hex/oct prefixes tokenize like their uppercase forms."""
        for code, expected in [
            ("bin '1010'", "BITSTRING"),
            ("Hex 'ABCD'", "HEXSTRING"),
            ("oct '1234567'", "OCTSTRING"),
        ]:
            lx_tokens = lexer.tokenize(code, "<test>")
            assert len(lexer.errors) == 0, code
            assert len(lx_tokens) == 1, code
            assert lx_tokens[0].type == expected, code

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

    def test_valid_dec_string(self, lexer) -> None:
        code = "DEC '255'"
        tokens = lexer.tokenize(code, "<test>")
        assert len(lexer.errors) == 0
        assert len(tokens) == 1
        assert tokens[0].type == "DECSTRING"

    def test_case_insensitive_dec_string(self, lexer) -> None:
        tokens = lexer.tokenize("dec '10'", "<test>")
        assert len(lexer.errors) == 0
        assert tokens[0].type == "DECSTRING"

    def test_invalid_dec_string(self, lexer) -> None:
        tokens = lexer.tokenize("DEC '2A'", "<test>")
        assert len(lexer.errors) > 0
        assert "decimal string" in lexer.errors[0].message.lower()

    def test_cat_is_a_keyword(self, lexer) -> None:
        tokens = lexer.tokenize("A CAT B", "<test>")
        types = [t.type for t in tokens]
        assert "KEYWORD_CAT" in types
        assert types == ["IDENTIFIER", "KEYWORD_CAT", "IDENTIFIER"]


class TestCommentCardColumnOne:
    """Comment cards are 'C' strictly in COLUMN 1 (official HAL/S card
    rule). The earlier lenient rule also accepted indented cards, which
    silently swallowed statements on a variable actually named C
    (found on hardware: an indented `C = C + 1;` vanished and the
    firmware span forever)."""

    def _lex(self, src):
        from resnik import Lexer
        lx = Lexer()
        return lx, lx.tokenize(src, "<t>")

    def test_variable_named_c_is_code(self):
        src = "T: PROGRAM;\n  DECLARE C INTEGER;\n  C = C + 1;\nCLOSE T;\n"
        _lx, toks = self._lex(src)
        assert [t.lexeme for t in toks if t.line == 3] == ["C", "=", "C", "+", "1", ";"]
        from resnik import Parser
        p = Parser()
        ast = p.parse(toks)
        assert not p.errors and ast is not None

    def test_column_one_card_is_still_a_comment(self):
        src = "C this is a comment card\nT: PROGRAM;\nC ===== banner =====\nCLOSE T;\n"
        _lx, toks = self._lex(src)
        assert [t.lexeme for t in toks if t.line in (1, 3)] == []

    def test_indented_c_line_is_no_longer_a_comment(self):
        # an indented "C blah" is now CODE (here: an identifier soup the
        # parser will reject) — nothing is silently dropped any more
        src = "T: PROGRAM;\n   C stray indented card\nCLOSE T;\n"
        _lx, toks = self._lex(src)
        assert [t.lexeme for t in toks if t.line == 2][:2] == ["C", "stray"]

    def test_close_and_subscripts_never_cards(self):
        src = "T: PROGRAM;\n  DECLARE C INTEGER;\nCLOSE T;\n"
        _lx, toks = self._lex(src)
        assert any(t.lexeme == "CLOSE" for t in toks)
