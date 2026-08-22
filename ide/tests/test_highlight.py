"""highlight.compute_spans — pure function over the real lexer."""

from resnik_ide.highlight import TAGS, compute_spans

SRC = (
    "/* block comment */\n"                                  # 1
    "HELLO: PROGRAM;\n"                                       # 2
    "  DECLARE X INTEGER INITIAL(5);   /* trailing */\n"      # 3
    "C this is a comment card\n"                              # 4
    "  X = X + 2.5E3;\n"                                      # 5
    "  WRITE(6) 'HELLO', X;\n"                                # 6
    "L1: CLOSE HELLO;\n"                                      # 7
)


def _by_tag(spans):
    out = {}
    for tag, a, b in spans:
        out.setdefault(tag, []).append((a, b))
    return out


class TestComputeSpans:
    def test_empty_and_huge_inputs(self):
        assert compute_spans("") == []
        assert compute_spans("x" * (600 * 1024)) == []

    def test_tags_and_positions(self):
        by = _by_tag(compute_spans(SRC))
        assert ("1.0", "1.19") in by["hl_comment"]
        assert ("3.34", "3.48") in by["hl_comment"]      # trailing comment
        assert ("4.0", "4.24") in by["hl_comment"]       # C card
        assert ("2.0", "2.5") in by["hl_label"] and ("7.0", "7.2") in by["hl_label"]
        assert ("2.7", "2.14") in by["hl_keyword"]       # PROGRAM
        assert ("3.2", "3.9") in by["hl_keyword"]        # DECLARE
        assert ("3.12", "3.19") in by["hl_type"]         # INTEGER
        assert ("3.28", "3.29") in by["hl_number"]
        assert ("5.10", "5.15") in by["hl_number"]       # 2.5E3
        assert ("6.11", "6.18") in by["hl_string"]
        assert ("7.4", "7.9") in by["hl_keyword"]        # CLOSE
        assert set(by) <= set(TAGS)

    def test_repeated_lexemes_on_one_line_are_located_sequentially(self):
        # The lexer reports the first occurrence's column for every repeat;
        # the highlighter must not trust it.
        spans = compute_spans("  DECLARE S CHARACTER(10) INITIAL('HI');\n")
        by = _by_tag(spans)
        assert by["hl_string"] == [("1.34", "1.38")]
        assert ("1.22", "1.24") in by["hl_number"]       # 10 inside the first parens

    def test_directive_card_and_unterminated_comment(self):
        by = _by_tag(compute_spans("D INCLUDE TEMPLATE NAV\nA: PROGRAM;\n/* open"))
        assert by["hl_directive"] == [("1.0", "1.22")]
        assert ("3.0", "3.7") in by["hl_comment"]

    def test_lexer_errors_do_not_raise(self):
        # unterminated string while typing
        spans = compute_spans("A: PROGRAM;\n  WRITE(6) 'oops;\nCLOSE A;\n")
        assert isinstance(spans, list)
        assert any(t == "hl_keyword" for t, _a, _b in spans)

    def test_lexeme_inside_comment_is_not_mis_tagged(self):
        # 'X' appears in the comment before the real token X
        by = _by_tag(compute_spans("/* X marks */ X = 1;\n"))
        assert by["hl_comment"] == [("1.0", "1.13")]
        assert by["hl_number"] == [("1.18", "1.19")]
