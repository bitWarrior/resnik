# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Steven Doran
"""
Minimal Rust syntax highlighting for the read-only "Generated Rust" viewer.

A small regex tokenizer (comments, strings/chars, numbers, keywords,
primitive/std types, ``macro!`` names, ``#[attributes]``) producing the same
``(tag, start, end)`` spans the HAL/S highlighter does, so the editor widget
can render it with its existing tags.  Pure function, no Tk.
"""

from __future__ import annotations

import re
from typing import List, Tuple

Span = Tuple[str, str, str]

KEYWORDS = {
    "as", "async", "await", "break", "const", "continue", "crate", "dyn", "else", "enum",
    "extern", "false", "fn", "for", "if", "impl", "in", "let", "loop", "match", "mod",
    "move", "mut", "pub", "ref", "return", "self", "Self", "static", "struct", "super",
    "trait", "true", "type", "unsafe", "use", "where", "while",
}
TYPES = {
    "i8", "i16", "i32", "i64", "i128", "isize", "u8", "u16", "u32", "u64", "u128", "usize",
    "f32", "f64", "bool", "char", "str", "String", "Vec", "Option", "Result", "Box", "Rc",
    "Arc", "Mutex", "HashMap", "Vector", "Matrix",
}

_TOKEN = re.compile(
    r"""
    (?P<comment>//[^\n]*|/\*.*?\*/)
  | (?P<attr>\#!?\[[^\]\n]*\])
  | (?P<string>r\#*"(?:.|\n)*?"\#*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])'(?!\w))
  | (?P<number>\b(?:0x[0-9a-fA-F_]+|0b[01_]+|0o[0-7_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?)(?:[iu](?:8|16|32|64|128|size)|f32|f64)?\b)
  | (?P<macro>\b[A-Za-z_][A-Za-z0-9_]*!)
  | (?P<ident>\b[A-Za-z_][A-Za-z0-9_]*\b)
    """,
    re.VERBOSE | re.DOTALL,
)

MAX_BYTES = 1024 * 1024


def _index(line_starts: List[int], off: int) -> str:
    lo, hi = 0, len(line_starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if line_starts[mid] <= off:
            lo = mid
        else:
            hi = mid - 1
    return f"{lo + 1}.{off - line_starts[lo]}"


def compute_spans(text: str) -> List[Span]:
    if not text or len(text) > MAX_BYTES:
        return []
    line_starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            line_starts.append(i + 1)
    spans: List[Span] = []
    for m in _TOKEN.finditer(text):
        kind = m.lastgroup
        word = m.group(0)
        if kind == "comment":
            tag = "hl_comment"
        elif kind == "attr":
            tag = "hl_directive"
        elif kind == "string":
            tag = "hl_string"
        elif kind == "number":
            tag = "hl_number"
        elif kind == "macro":
            tag = "hl_macro"
        elif kind == "ident":
            if word in KEYWORDS:
                tag = "hl_keyword"
            elif word in TYPES:
                tag = "hl_type"
            else:
                continue
        else:  # pragma: no cover
            continue
        spans.append((tag, _index(line_starts, m.start()), _index(line_starts, m.end())))
    return spans
