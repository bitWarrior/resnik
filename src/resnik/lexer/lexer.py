"""
Modern HAL/S Lexer with full type annotations.

This module provides lexical analysis for HAL/S source code, converting
raw text into a stream of typed tokens for the parser.
"""

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional

from rply import LexerGenerator

from resnik.exceptions import LexerError
from resnik.lexer.tokens import Token, KEYWORDS


class Lexer:
    """
    HAL/S Lexical Analyzer using rply.

    This lexer tokenizes HAL/S source code according to the language specification.
    It handles comments, keywords, operators, literals, and identifiers with
    proper position tracking for error reporting.

    Example:
        >>> lexer = Lexer()
        >>> tokens = lexer.tokenize("PROGRAM TEST; CLOSE TEST;")
        >>> print(len(tokens))
        6
    """

    def __init__(self) -> None:
        """Initialize the lexer with token rules."""
        self.lg = LexerGenerator()
        self.errors: List[LexerError] = []
        self.tokens: List[Token] = []
        self.source_file: str = ""
        self.source_lines: List[str] = []
        # Directories searched by `D INCLUDE TEMPLATE <name>` for <name>.tpl.
        self.template_dirs: List[str] = []

        # Build the lexer
        self._define_tokens()
        self.lexer = self.lg.build()

    def _define_tokens(self) -> None:
        """Define all token patterns for the lexer."""

        # Statement terminator
        self.lg.add("SEMICOLON", r";")

        # Multi-character operators (must come before single-char)
        self.lg.add("POWER", r"\*\*")
        self.lg.add("CONCAT", r"\|\|")
        self.lg.add("LE", r"<=")
        self.lg.add("GE", r">=")
        self.lg.add("NE", r"¬=|~=")

        # Single character operators
        self.lg.add("PLUS", r"\+")
        self.lg.add("MINUS", r"-")
        self.lg.add("MULTIPLY", r"\*")
        self.lg.add("DIVIDE", r"/")
        self.lg.add("ASSIGN", r"=")
        self.lg.add("LT", r"<")
        self.lg.add("GT", r">")
        # HAL/S bitwise/logical OR (|) and AND (&). CONCAT (||) is registered
        # earlier, so a doubled bar still tokenizes as concatenation.
        self.lg.add("VBAR", r"\|")
        self.lg.add("AMPERSAND", r"&")

        # Special notation
        self.lg.add("DOT", r"\.")
        self.lg.add("HASH", r"#")
        self.lg.add("AT", r"@")
        self.lg.add("DOLLAR", r"\$")

        # Delimiters
        self.lg.add("LPAREN", r"\(")
        self.lg.add("RPAREN", r"\)")
        self.lg.add("COMMA", r",")
        self.lg.add("COLON", r":")

        # Three-line format indicators
        self.lg.add("CARET", r"\^")
        self.lg.add("UNDERSCORE", r"_")

        # Numeric literals
        self.lg.add("NUMBER", r"\d+(\.\d+)?([Ee][+-]?\d+)?")

        # Bit/Hex/Oct strings - match any content, validate later
        # (?i) so bin'..'/Hex'..' tokenize consistently with HAL/S case-insensitivity
        self.lg.add("BITSTRING", r"(?i)BIN\s*'[^']*'")
        self.lg.add("HEXSTRING", r"(?i)HEX\s*'[^']*'")
        self.lg.add("OCTSTRING", r"(?i)OCT\s*'[^']*'")
        self.lg.add("DECSTRING", r"(?i)DEC\s*'[^']*'")

        # Suffix form bit/hex/oct literals: '1010'B, 'FF'H, '777'O.
        # Must precede STRING so '1'B does not split into STRING + 'B'.
        self.lg.add("BITLITERAL", r"'[0-9A-Fa-f]+'[BHObho]")

        # Character strings
        self.lg.add("STRING", r"'([^']|'')*'")

        # Identifiers and keywords. Length is capped at 32 after the match so a
        # longer name is one token plus a lexer error, not a silent split
        # (which juxtaposition-multiply would then treat as a product).
        self.lg.add("IDENTIFIER", r"[A-Za-z][A-Za-z0-9_]*")

        # Ignore whitespace
        self.lg.ignore(r"[ \t]+")

        # Newlines for position tracking
        self.lg.add("NEWLINE", r"\n")

    # `D INCLUDE TEMPLATE <name>` -- D strictly in column 1 (HAL/S directive
    # card). The `\s+` after D keeps DECLARE/DO/etc. (no space after D) as code.
    _INCLUDE_RE = re.compile(
        r"^D[ \t]+INCLUDE[ \t]+TEMPLATE[ \t]+([A-Za-z_]\w*)[ \t]*$",
        re.IGNORECASE)

    def _find_template(self, name: str, search_dirs: List[str]) -> Optional[str]:
        """Locate <name>.tpl (case-insensitively) in the search dirs."""
        for d in search_dirs:
            for candidate in (f"{name}.tpl", f"{name.upper()}.tpl",
                              f"{name.lower()}.tpl"):
                p = Path(d) / candidate
                if p.is_file():
                    return p.read_text(encoding="utf-8")
        return None

    def _expand_template_includes(
            self, source_code: str, filename: str) -> str:
        """Replace `D INCLUDE TEMPLATE <name>` directives with the contents of
        the referenced template-library file. Missing templates are reported as
        lexer errors."""
        if "INCLUDE" not in source_code.upper():
            return source_code  # fast path: no directive present

        search_dirs = list(self.template_dirs)
        # Search the including file's own directory too (if it's a real path).
        if filename and not filename.startswith("<"):
            src_dir = str(Path(filename).parent)
            if src_dir and src_dir not in search_dirs:
                search_dirs.append(src_dir)
        if not search_dirs:
            search_dirs = ["."]

        out: List[str] = []
        for lineno, line in enumerate(source_code.split("\n"), start=1):
            m = self._INCLUDE_RE.match(line)
            if m:
                name = m.group(1)
                tpl = self._find_template(name, search_dirs)
                if tpl is None:
                    self.errors.append(LexerError(
                        f"D INCLUDE TEMPLATE {name}: '{name}.tpl' not found in "
                        f"template library ({', '.join(search_dirs)})",
                        lineno, 1))
                    out.append("")  # preserve line structure
                    continue
                out.append(f"/* ---- D INCLUDE TEMPLATE {name} ---- */")
                out.append(tpl.rstrip("\n"))
            else:
                out.append(line)
        return "\n".join(out)

    def _preprocess_source(self, source_code: str, filename: str) -> str:
        """
        Preprocess HAL/S source to remove comments.

        Args:
            source_code: Raw HAL/S source code
            filename: Source file name for error reporting

        Returns:
            Preprocessed source code with comments removed
        """
        # First resolve `D INCLUDE TEMPLATE <name>` directives (D in column 1),
        # splicing in the referenced .tpl so its signature is registered when
        # the injected TEMPLATE unit is parsed. Done before comment stripping so
        # the template's banner comment is handled like any other comment.
        source_code = self._expand_template_includes(source_code, filename)

        lines = source_code.split("\n")
        processed_lines = []
        in_block_comment = False
        line_num = 0

        for line in lines:
            line_num += 1

            # HAL/S 'C' comment card: 'C' strictly in COLUMN 1 (the official
            # card rule, same as the 'D' directive card), followed by
            # whitespace, end-of-line, or a banner char (* - =). Column 1
            # only: an indented "C = C + 1;" is a statement on a variable
            # named C, and the earlier lenient (indented) rule silently
            # swallowed such statements as comments. Keywords (CLOSE,
            # CONSTANT) and C-subscripts (C$1.M) put a letter or '$' right
            # after the C and are never cards.
            # Only consulted outside an open /* */ block.
            if not in_block_comment and line[:1] == "C":
                after = line[1:2]
                if after == "" or after in " \t*-=":
                    processed_lines.append("")
                    continue

            # Block comments /* */ and '...' strings.
            processed_line: List[str] = []
            i = 0
            in_string = False
            while i < len(line):
                ch = line[i]
                if in_block_comment:
                    if i < len(line) - 1 and line[i : i + 2] == "*/":
                        in_block_comment = False
                        i += 2
                        # HAL/S comments are whitespace; keep a token boundary
                        # so `X/*c*/Y` stays two identifiers, not `XY`.
                        processed_line.append(" ")
                        continue
                    i += 1
                    continue
                if in_string:
                    processed_line.append(ch)
                    if ch == "'":
                        in_string = False
                    i += 1
                    continue
                if i < len(line) - 1 and line[i : i + 2] == "/*":
                    in_block_comment = True
                    i += 2
                    continue
                if ch == "'":
                    in_string = True
                    processed_line.append(ch)
                    i += 1
                    continue
                processed_line.append(ch)
                i += 1

            processed_lines.append("".join(processed_line))

        # Check for unclosed block comment
        if in_block_comment:
            self.errors.append(
                LexerError(
                    message="Unclosed block comment /* at end of file",
                    line=line_num,
                    column=0,
                    filename=filename,
                )
            )

        return "\n".join(processed_lines)

    def _validate_bitstring(
        self, token_value: str, line: int, column: int
    ) -> bool:
        """Validate bit string format."""
        # Extract the content between quotes
        match = re.search(r"BIN\s*'([^']*)'", token_value, re.IGNORECASE)
        if match:
            content = match.group(1)
            # Check if all characters are 0 or 1
            if not all(c in '01' for c in content):
                self.errors.append(
                    LexerError(
                        message=f"Invalid bit string: {token_value}. "
                        "Only 0 and 1 are allowed in BIN strings.",
                        line=line,
                        column=column,
                        filename=self.source_file,
                    )
                )
                return False
        return True

    def _validate_hexstring(
        self, token_value: str, line: int, column: int
    ) -> bool:
        """Validate hex string format."""
        # Extract the content between quotes
        match = re.search(r"HEX\s*'([^']*)'", token_value, re.IGNORECASE)
        if match:
            content = match.group(1)
            # Check if all characters are valid hex (0-9, A-F, a-f)
            if not all(c in '0123456789ABCDEFabcdef' for c in content):
                self.errors.append(
                    LexerError(
                        message=f"Invalid hex string: {token_value}. "
                        "Only 0-9 and A-F are allowed in HEX strings.",
                        line=line,
                        column=column,
                        filename=self.source_file,
                    )
                )
                return False
        return True

    def _validate_octstring(
        self, token_value: str, line: int, column: int
    ) -> bool:
        """Validate octal string format."""
        # Extract the content between quotes
        match = re.search(r"OCT\s*'([^']*)'", token_value, re.IGNORECASE)
        if match:
            content = match.group(1)
            # Check if all characters are valid octal (0-7)
            if not all(c in '01234567' for c in content):
                self.errors.append(
                    LexerError(
                        message=f"Invalid octal string: {token_value}. "
                        "Only 0-7 are allowed in OCT strings.",
                        line=line,
                        column=column,
                        filename=self.source_file,
                    )
                )
                return False
        return True

    def _validate_decstring(
        self, token_value: str, line: int, column: int
    ) -> bool:
        """Validate decimal bit-literal DEC 'digits'."""
        match = re.search(r"DEC\s*'([^']*)'", token_value, re.IGNORECASE)
        if match:
            content = match.group(1)
            if not content or not all(c in "0123456789" for c in content):
                self.errors.append(
                    LexerError(
                        message=f"Invalid decimal string: {token_value}. "
                        "Only 0-9 are allowed in DEC strings.",
                        line=line,
                        column=column,
                        filename=self.source_file,
                    )
                )
                return False
        return True

    def _validate_bitliteral(
        self, token_value: str, line: int, column: int
    ) -> bool:
        """Validate suffix-form 'digits'[BHO] against the declared base."""
        match = re.search(r"'([^']*)'([BHObho])\s*$", token_value)
        if not match:
            return True
        content, suffix = match.group(1), match.group(2).upper()
        if suffix == "B":
            allowed, kind = "01", "bit"
        elif suffix == "O":
            allowed, kind = "01234567", "octal"
        else:
            allowed, kind = "0123456789ABCDEFabcdef", "hex"
        if not all(c in allowed for c in content):
            self.errors.append(
                LexerError(
                    message=(
                        f"Invalid {kind} string: {token_value}. "
                        f"Only {allowed} are allowed in '{suffix}' suffix literals."
                    ),
                    line=line,
                    column=column,
                    filename=self.source_file,
                )
            )
            return False
        return True

    def _is_keyword(self, identifier: str) -> bool:
        """Check if identifier is a HAL/S keyword (case-insensitive)."""
        return identifier.upper() in KEYWORDS

    def _get_token_type(self, token: Any) -> str:
        """Determine token type, handling keywords vs identifiers."""
        token_type = token.gettokentype()

        if token_type == "IDENTIFIER":
            if self._is_keyword(token.value):
                return "KEYWORD_" + token.value.upper()

        return token_type

    def tokenize(self, source_code: str, filename: str = "<input>") -> List[Token]:
        """
        Tokenize HAL/S source code.

        Args:
            source_code: HAL/S source code string
            filename: Source file name for error reporting

        Returns:
            List of Token objects

        Raises:
            LexerError: If lexical errors are encountered
        """
        self.source_file = filename
        self.source_lines = source_code.split("\n")
        self.errors = []
        self.tokens = []

        # Preprocess to remove comments
        preprocessed = self._preprocess_source(source_code, filename)

        # Track position
        line = 1
        column = 1
        last_newline_pos = 0

        try:
            token_stream = self.lexer.lex(preprocessed)

            for token in token_stream:
                # Calculate position
                token_pos = preprocessed.find(token.value, last_newline_pos)
                if token_pos != -1:
                    newlines_before = preprocessed[last_newline_pos:token_pos].count(
                        "\n"
                    )
                    if newlines_before > 0:
                        line += newlines_before
                        last_newline = preprocessed.rfind(
                            "\n", last_newline_pos, token_pos
                        )
                        column = token_pos - last_newline
                        last_newline_pos = last_newline + 1
                    else:
                        column = token_pos - last_newline_pos + 1

                # Handle newlines
                if token.gettokentype() == "NEWLINE":
                    line += 1
                    column = 1
                    last_newline_pos = token_pos + 1
                    continue

                # Validate special token types
                token_type = token.gettokentype()
                if token_type == "BITSTRING":
                    self._validate_bitstring(token.value, line, column)
                elif token_type == "HEXSTRING":
                    self._validate_hexstring(token.value, line, column)
                elif token_type == "OCTSTRING":
                    self._validate_octstring(token.value, line, column)
                elif token_type == "DECSTRING":
                    self._validate_decstring(token.value, line, column)
                elif token_type == "BITLITERAL":
                    self._validate_bitliteral(token.value, line, column)
                elif token_type == "IDENTIFIER" and len(token.value) > 32:
                    self.errors.append(
                        LexerError(
                            message=(
                                f"Identifier '{token.value}' exceeds the HAL/S "
                                f"32-character limit ({len(token.value)} characters)"
                            ),
                            line=line,
                            column=column,
                            filename=self.source_file,
                        )
                    )

                # Get actual token type
                actual_type = self._get_token_type(token)

                # Create token object
                tok = Token(
                    type=actual_type,
                    lexeme=token.value,
                    line=line,
                    column=column,
                    file=filename,
                )

                self.tokens.append(tok)

        except Exception as e:
            self.errors.append(
                LexerError(
                    message=f"Lexical error: {str(e)}. Invalid character or token.",
                    line=line,
                    column=column,
                    filename=filename,
                )
            )

        return self.tokens
    def tokenize_file(self, filepath: str) -> List[Token]:
        """
        Tokenize a HAL/S source file.

        Args:
            filepath: Path to HAL/S source file (.hs or .hal)

        Returns:
            List of Token objects

        Raises:
            ValueError: If file extension is invalid
        """
        path = Path(filepath)
        if path.suffix not in [".hs", ".hal"]:
            raise ValueError(
                f"Invalid file extension. Expected .hs or .hal, got {path.suffix}"
            )

        with open(filepath, "r", encoding="utf-8") as f:
            source_code = f.read()

        return self.tokenize(source_code, filepath)

    def export_json(self, output_file: str) -> None:
        """
        Export tokenized output to JSON.

        Args:
            output_file: Path to output JSON file
        """
        output_data = {
            "source_file": self.source_file,
            "tokens": [token.to_dict() for token in self.tokens],
            "errors": [
                {
                    "message": error.message,
                    "line": error.line,
                    "column": error.column,
                    "file": error.filename,
                }
                for error in self.errors
            ],
            "statistics": {
                "total_tokens": len(self.tokens),
                "total_errors": len(self.errors),
                "lines_processed": max((t.line for t in self.tokens), default=0),
            },
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2)

    def report_errors(self) -> None:
        """Report all lexical errors to stderr."""
        if not self.errors:
            return

        print(f"\n{'=' * 70}", file=sys.stderr)
        print(f"LEXICAL ERRORS: {len(self.errors)} error(s) found", file=sys.stderr)
        print(f"{'=' * 70}", file=sys.stderr)

        for error in self.errors:
            print(f"\nFile: {error.filename}", file=sys.stderr)
            print(f"Line {error.line}, Column {error.column}", file=sys.stderr)
            print(f"Error: {error.message}", file=sys.stderr)

            # Show the offending line
            if 0 < error.line <= len(self.source_lines):
                line_content = self.source_lines[error.line - 1]
                print(f"\n  {line_content}", file=sys.stderr)
                if error.column > 0:
                    print(f"  {' ' * (error.column - 1)}^", file=sys.stderr)

        print(f"\n{'=' * 70}\n", file=sys.stderr)
