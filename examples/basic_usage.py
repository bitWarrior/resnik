"""
Example: Basic Usage of Resnik Compiler
========================================

This example demonstrates the basic usage of the Resnik compiler
to tokenize and parse HAL/S source code.
"""

from resnik import Lexer, Parser

# Example HAL/S code
hal_code = """
PROGRAM NAVIGATION;
   DECLARE ALTITUDE SCALAR DOUBLE INITIAL(10000.0);
   DECLARE VELOCITY VECTOR(3) SINGLE;
   
   ALTITUDE = ALTITUDE + 100.0;
CLOSE NAVIGATION;
"""

def main() -> None:
    """Main example function."""
    print("=" * 70)
    print("Resnik Compiler - Basic Usage Example")
    print("=" * 70)
    print()

    # Step 1: Lexical Analysis
    print("Step 1: Lexical Analysis")
    print("-" * 70)

    lexer = Lexer()
    tokens = lexer.tokenize(hal_code, "<example>")

    if lexer.errors:
        print(f"✗ Lexer found {len(lexer.errors)} errors")
        lexer.report_errors()
        return

    print(f"✓ Tokenized {len(tokens)} tokens")
    print()

    # Show first few tokens
    print("First 10 tokens:")
    for token in tokens[:10]:
        print(f"  {token.type:20s} '{token.lexeme}'")
    print()

    # Step 2: Syntactic Analysis
    print("Step 2: Syntactic Analysis")
    print("-" * 70)

    parser = Parser()
    ast = parser.parse(tokens)

    if parser.errors:
        print(f"✗ Parser found {len(parser.errors)} errors")
        parser.report_errors()
        return

    if ast:
        print(f"✓ Successfully parsed")
        print(f"  Program name: {ast.name}")
        print(f"  AST node type: {ast.node_type.value}")
        print()

        # Export AST
        parser.export_ast_json("example_ast.json")
        print("✓ AST exported to example_ast.json")
    else:
        print("✗ Parsing failed")

    print()
    print("=" * 70)
    print("Example completed successfully!")
    print("=" * 70)


if __name__ == "__main__":
    main()
