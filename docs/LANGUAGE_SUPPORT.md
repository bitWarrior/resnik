# HAL/S language support in Resnik

## HAL/S keywords

USA003088 Appendix B is the official HAL/S keyword list. Resnik implements a
working subset and a few convenience spellings of its own.

### Resnik-only words

Resnik also reserves and implements the words below, which are **not**
Appendix B keywords. The lexer treats them as reserved, so they cannot be used
as identifiers.

| Word | Official HAL/S | What Resnik accepts |
| ---- | -------------- | ------------------- |
| `GOTO` | Two-word `GO TO` only | One-token `GOTO <label>;` (same AST as `GO TO`) |
| `DOWNTO` | `DO FOR I = N TO 1 BY -1` | `DO FOR I = N DOWNTO 1;` |
| `OF` | `ARRAY(n) INTEGER` | Optional `OF`: `ARRAY(n) OF INTEGER` |
| `MACRO` / `MEND` | Percent-macros (`%name`) | Parameterized `MACRO name(params); ... MEND;` preprocessor |
| `TEMPLATE` | Not an Appendix B keyword | Signature-only prototype on `PROCEDURE` / `FUNCTION` / `COMPOOL` (see [Separate compilation & FFI](SEPARATE_COMPILATION_AND_FFI.md)) |
| `XOR` | Built-in function `XOR(a, b)` | Infix `A XOR B` as well as the function |
| `MOD` | Built-in function `MOD(a, b)` | Infix `A MOD B` as well as the function |
| `RESUME` | Error-handler cancel is `OFF ERROR` | Continues after the faulting statement in an `ON ERROR` handler |

The official spellings remain valid: `GO TO`, `ARRAY(n) T`, `XOR(A, B)`, and
`MOD(A, B)` all parse.

### Future work

These Appendix B keywords are not yet accepted as real language (no parser
production, or only a different construct uses the same word). Several are
already reserved by the lexer, so they also cannot be used as identifiers.
`HEX` / `BIN` / `OCT` literal prefixes and the `REPLACE` preprocessor are
implemented and are not listed here.

| Area | Keywords | Official construct still to do |
| ---- | -------- | ------------------------------ |
| Blocks and real-time | `UPDATE` | `UPDATE` block (shared-data mutual exclusion) and `UPDATE PRIORITY(...)` |
| | `AFTER`, `AT`, `DEPENDENT` | `SCHEDULE T AFTER S`, `SCHEDULE T AT <time>`, `SCHEDULE ... DEPENDENT` |
| | `ON` | `SCHEDULE ... ON <event>` (`ON ERROR` already parses) |
| | `UNTIL` | `SCHEDULE ... UNTIL ...` (`WAIT UNTIL`, `DO UNTIL`, and `DO FOR ... UNTIL` already parse) |
| | `WHILE` | `SCHEDULE ... WHILE <cond>` (`DO WHILE` already parses) |
| | `REENTRANT`, `EXCLUSIVE` | Procedure / function attributes |
| Declarations | `LATCHED` | `DECLARE E EVENT LATCHED;` |
| | `LOCK` | `DECLARE X INTEGER LOCK;` and lock groups |
| | `ACCESS`, `REMOTE`, `TEMPORARY` | Compool access control, remote data, temporary declarations |
| | `RIGID` | Structure layout (no field reordering); `ALIGNED` / `DENSE` already parse |
| | `SEND` | Process / event send |
| I/O | `READALL`, `FILE` | `READALL(channel) ...`, file/channel binding |
| | `SKIP`, `TAB`, `COLUMN`, `LINE`, `PAGE` | `WRITE` format controls (`WRITE(6) SKIP(2), TAB(10), X`) |
| Error handling | `OFF`, `IGNORE` | `OFF ERROR`, `ON ERROR IGNORE` |
| Bit / character | `SUBBIT` | Bit-view pseudo-variable (`SUBBIT(X)`, `SUBBIT 5 TO 8 (K)`) |
| | `CHAR` | Repeated character literal (`CHAR 5 'AB'`) |
| Literals | `TRUE`, `FALSE` | Reserved boolean literals. Resnik currently special-cases identifiers named `TRUE` / `FALSE` but does not reserve them, so `DECLARE TRUE INTEGER;` is accepted |
| | `DEC` | Radix conversions `CHARACTER @DEC(...)` / `BIT @DEC(...)` (`DEC 'digits'` bit literals and infix `CAT` already parse) |
| Other | `SYSTEM` | System / process attribute |
