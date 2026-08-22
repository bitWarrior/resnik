"""
Syntax highlighting driven by the real Resnik lexer.

``compute_spans(text)`` tokenizes the buffer with :class:`resnik.Lexer` and
returns ``(tag, start_index, end_index)`` triples in Tk ``line.col`` form.
Token *positions* are located by scanning each line left-to-right for the
token's lexeme (the lexer's ``column`` is unreliable for repeated lexemes on
a line), and comments — which the lexer strips before tokenizing — get a
separate pass (``/* … */`` blocks and ``C``-in-column-1 comment cards).
Pure function; no Tk dependency, so it is unit-testable.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

Span = Tuple[str, str, str]  # (tag, "l.c", "l.c")

TAGS: Dict[str, Dict[str, object]] = {
    "hl_keyword": {"foreground": "#0b3d91", "font_weight": "bold"},
    "hl_type": {"foreground": "#7a2d8c"},
    "hl_string": {"foreground": "#a31515"},
    "hl_number": {"foreground": "#098658"},
    "hl_comment": {"foreground": "#6a737d", "font_slant": "italic"},
    "hl_label": {"foreground": "#795e26"},
    "hl_directive": {"foreground": "#6a737d"},
    "hl_macro": {"foreground": "#0550ae"},
}

_TYPE_KEYWORDS = {
    "KEYWORD_INTEGER", "KEYWORD_SCALAR", "KEYWORD_BOOLEAN", "KEYWORD_CHARACTER",
    "KEYWORD_BIT", "KEYWORD_VECTOR", "KEYWORD_MATRIX", "KEYWORD_ARRAY",
    "KEYWORD_STRUCTURE", "KEYWORD_EVENT", "KEYWORD_SINGLE", "KEYWORD_DOUBLE",
}
_STRING_TYPES = {"STRING", "HEXSTRING", "BITSTRING", "OCTSTRING", "DECSTRING", "BINSTRING"}

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
MAX_HIGHLIGHT_BYTES = 512 * 1024


def _tag_for(tok_type: str, lexeme: str) -> str:
    if tok_type in _TYPE_KEYWORDS:
        return "hl_type"
    if tok_type.startswith("KEYWORD_"):
        return "hl_keyword"
    if tok_type in _STRING_TYPES or tok_type.endswith("STRING"):
        return "hl_string"
    if tok_type == "NUMBER":
        return "hl_number"
    if tok_type == "MACRO_CALL" or lexeme.startswith("%"):
        return "hl_macro"
    return ""


def compute_spans(text: str) -> List[Span]:
    """Return highlight spans for ``text``.  Never raises; lexer errors (an
    unterminated string while typing) simply yield fewer spans."""
    if not text or len(text) > MAX_HIGHLIGHT_BYTES:
        return []
    from resnik.lexer import Lexer

    lines = text.split("\n")
    spans: List[Span] = []

    # -- comments / directive cards (not tokens) ---------------------------------
    comment_ranges: List[Tuple[int, int]] = []  # absolute offsets, for overlap checks
    line_starts = [0]
    for ln in lines[:-1]:
        line_starts.append(line_starts[-1] + len(ln) + 1)

    def to_index(offset: int) -> str:
        # binary search would be overkill for editor-sized files
        lo = 0
        for i, st in enumerate(line_starts):
            if st <= offset:
                lo = i
            else:
                break
        return f"{lo + 1}.{offset - line_starts[lo]}"

    for m in _BLOCK_COMMENT.finditer(text):
        comment_ranges.append((m.start(), m.end()))
        spans.append(("hl_comment", to_index(m.start()), to_index(m.end())))
    # unterminated block comment at the end of the buffer
    last_open = text.rfind("/*")
    if last_open != -1 and text.find("*/", last_open) == -1:
        comment_ranges.append((last_open, len(text)))
        spans.append(("hl_comment", to_index(last_open), to_index(len(text))))

    for i, ln in enumerate(lines):
        if ln[:1] == "C" and (len(ln) == 1 or ln[1] in " \t"):
            comment_ranges.append((line_starts[i], line_starts[i] + len(ln)))
            spans.append(("hl_comment", f"{i + 1}.0", f"{i + 1}.{len(ln)}"))
        elif ln[:1] == "D" and (len(ln) == 1 or ln[1] in " \t"):
            spans.append(("hl_directive", f"{i + 1}.0", f"{i + 1}.{len(ln)}"))

    def in_comment(off: int) -> bool:
        return any(a <= off < b for a, b in comment_ranges)

    # -- tokens ------------------------------------------------------------------
    lexer = Lexer()
    try:
        tokens = lexer.tokenize(text, "<editor>")
    except Exception:
        return spans

    cursor: Dict[int, int] = {}  # line number -> column to resume scanning at
    prev = None
    for k, tok in enumerate(tokens):
        ln_no = tok.line
        if ln_no < 1 or ln_no > len(lines):
            continue
        line = lines[ln_no - 1]
        start = cursor.get(ln_no, 0)
        # comment stripping replaces comments by spaces, so lexemes are found
        # verbatim in the original line (case preserved).
        pos = line.find(tok.lexeme, start)
        if pos == -1:
            # injected / synthesized token (e.g. from a template include) — skip
            continue
        abs_off = line_starts[ln_no - 1] + pos
        if in_comment(abs_off):
            # lexeme text also appears inside a comment before the real token
            pos2 = line.find(tok.lexeme, pos + 1)
            if pos2 == -1:
                continue
            pos = pos2
        end = pos + len(tok.lexeme)
        cursor[ln_no] = end
        tag = _tag_for(tok.type, tok.lexeme)
        nxt = tokens[k + 1] if k + 1 < len(tokens) else None
        if (tok.type == "IDENTIFIER" and nxt is not None and nxt.type == "COLON"
                and nxt.line == ln_no and (prev is None or prev.line != ln_no
                                           or prev.type == "SEMICOLON")):
            tag = "hl_label"
        if tag:
            spans.append((tag, f"{ln_no}.{pos}", f"{ln_no}.{end}"))
        prev = tok
    return spans
