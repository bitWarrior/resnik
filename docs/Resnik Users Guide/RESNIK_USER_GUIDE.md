# 📚 Resnik HAL/S Compiler - Complete User Guide

**From HAL/S Source Code to Executable Binary**

Version: 1.0  
Last Updated: February 2026

---

## 📋 Table of Contents

1. [Introduction](#introduction)
2. [Installation](#installation)
3. [Quick Start](#quick-start)
4. [Using the Lexer](#using-the-lexer)
5. [Using the Parser](#using-the-parser)
6. [Using the Backend (Code Generator)](#using-the-backend)
7. [Complete Workflow](#complete-workflow)
8. [Compiling Rust to Executable](#compiling-rust-to-executable)
9. [Command-Line Interface](#command-line-interface)
10. [Examples](#examples)
11. [Troubleshooting](#troubleshooting)
12. [API Reference](#api-reference)

---

## 🎯 Introduction

### What is Resnik?

Resnik is a modern HAL/S compiler that translates HAL/S (High-order Assembly Language/Shuttle) source code into Rust, which can then be compiled into native executables.

**Compiler Pipeline:**
```
HAL/S Source Code (.hal)
    ↓
[Lexer] → Tokens
    ↓
[Parser] → Abstract Syntax Tree (AST)
    ↓
[Backend] → Rust Source Code (.rs)
    ↓
[rustc] → Executable Binary
```

### System Requirements

- **Python:** 3.10 or higher
- **Rust:** 1.70 or higher (for compiling generated code)
- **Operating System:** Linux, macOS, or Windows
- **Memory:** 512 MB minimum
- **Disk Space:** 100 MB for compiler + space for Rust toolchain

---

## 📦 Installation

### Step 1: Install Rust (if not already installed)

```bash
# Install Rust using rustup
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Verify installation
rustc --version
cargo --version
```

### Step 2: Install Resnik

```bash
# Extract the Resnik package
unzip resnik-COMPLETE.zip
cd resnik-modern

# Install in development mode
pip install -e ".[dev]"

# Verify installation
python -c "import resnik; print('Resnik installed successfully!')"
```

### Step 3: Verify Everything Works

```bash
# Run all tests
pytest -v

# Expected: 181 passed

# Run demo
python demo_backend.py
```

---

## 🚀 Quick Start

### 5-Minute Tutorial

**1. Create a HAL/S Program**

Create `hello.hal`:
```hal
PROGRAM Hello;
    DECLARE MESSAGE CHARACTER;
    DECLARE COUNT INTEGER;
    
    MESSAGE = 'Hello from HAL/S!';
    COUNT = 42;
CLOSE Hello;
```

**2. Compile to Rust**

```bash
python -c "
from resnik import Lexer, Parser
from resnik.backend import RustCodeGenerator

# Parse HAL/S
lexer = Lexer()
tokens = lexer.tokenize_file('hello.hal')

parser = Parser()
ast = parser.parse(tokens)

# Generate Rust
generator = RustCodeGenerator()
rust_code = generator.generate(ast)

# Save to file
with open('hello.rs', 'w') as f:
    f.write(rust_code)

print('✓ Generated hello.rs')
"
```

**3. Compile to Executable**

```bash
# Compile with rustc
rustc hello.rs -o hello

# Run the executable
./hello

# No output (program just declares variables)
```

**That's it!** You've compiled HAL/S to a native executable!

---

## 🔤 Using the Lexer

### What the Lexer Does

The **lexer** (lexical analyzer) breaks HAL/S source code into **tokens** - the basic building blocks of the language.

**Example:**
```hal
PROGRAM Test;
```

**Becomes:**
```
Token(type='KEYWORD_PROGRAM', lexeme='PROGRAM', line=1, col=1)
Token(type='IDENTIFIER', lexeme='Test', line=1, col=9)
Token(type='SEMICOLON', lexeme=';', line=1, col=13)
```

### Basic Lexer Usage

```python
from resnik import Lexer

# Create lexer
lexer = Lexer()

# Option 1: Tokenize string
code = "PROGRAM Test; CLOSE Test;"
tokens = lexer.tokenize(code, filename="<string>")

# Option 2: Tokenize file
tokens = lexer.tokenize_file("program.hal")

# Print tokens
for token in tokens:
    print(f"{token.type:20s} {token.lexeme:15s} Line {token.line}")

# Check for errors
if lexer.errors:
    print(f"\nFound {len(lexer.errors)} lexical errors:")
    for error in lexer.errors:
        print(f"  Line {error.line}: {error.message}")
```

### Lexer Features

**Supported HAL/S Constructs:**
- ✅ Keywords: `PROGRAM`, `DECLARE`, `PROCEDURE`, `CLOSE`, etc.
- ✅ Identifiers: Variable and procedure names
- ✅ Numbers: Integers (`42`) and floats (`3.14`, `1.5E-10`)
- ✅ Strings: `'Hello World'`, `'DON''T'` (embedded quotes)
- ✅ Operators: `+`, `-`, `*`, `/`, `**`, `=`, `<`, `>`, etc.
- ✅ Comments: `C Comment` (line comments), `/* block */` (block comments)
- ✅ Bit strings: `BIN '101010'`
- ✅ Hex strings: `HEX 'DEADBEEF'`
- ✅ Octal strings: `OCT '755'`

### Error Detection

The lexer detects these errors:
- Invalid bit strings: `BIN '102'` (2 is not binary)
- Invalid hex strings: `HEX 'GHIJ'` (G-J are not hex)
- Invalid octal strings: `OCT '89'` (8-9 are not octal)
- Unclosed block comments: `/* ... EOF`

### Export Tokens to JSON

```python
from resnik import Lexer

lexer = Lexer()
tokens = lexer.tokenize_file("program.hal")

# Export to JSON
lexer.export_json("tokens.json")

# The JSON file contains:
# {
#   "source_file": "program.hal",
#   "tokens": [...],
#   "errors": [...],
#   "statistics": {...}
# }
```

### Complete Lexer Example

```python
from resnik import Lexer

# Create HAL/S source
hal_code = """
C This is a HAL/S program
PROGRAM Calculate;
    DECLARE X, Y, RESULT SCALAR;
    
    X = 3.14;
    Y = 2.0;
    RESULT = X * Y;
CLOSE Calculate;
"""

# Tokenize
lexer = Lexer()
tokens = lexer.tokenize(hal_code, "calculate.hal")

# Display results
print(f"✓ Tokenized successfully")
print(f"  Total tokens: {len(tokens)}")
print(f"  Total errors: {len(lexer.errors)}")

# Show first 10 tokens
print("\nFirst 10 tokens:")
for i, token in enumerate(tokens[:10]):
    print(f"  {i+1}. {token.type:20s} '{token.lexeme}'")

# Check for errors
if lexer.errors:
    print("\n⚠ Lexical Errors Found:")
    lexer.report_errors()
```

---

## 🌳 Using the Parser

### What the Parser Does

The **parser** takes tokens from the lexer and builds an **Abstract Syntax Tree (AST)** - a structured representation of the program.

**Example:**
```hal
PROGRAM Test;
    DECLARE X INTEGER;
    X = 5;
CLOSE Test;
```

**Becomes:**
```
ProgramNode(
    name="Test",
    declarations=[
        DeclareNode(identifiers=["X"], type_spec=TypeSpec(INTEGER))
    ],
    statements=[
        AssignmentNode(
            target=IdentifierNode("X"),
            value=LiteralNode(5)
        )
    ]
)
```

### Basic Parser Usage

```python
from resnik import Lexer, Parser

# Step 1: Tokenize
lexer = Lexer()
tokens = lexer.tokenize_file("program.hal")

# Step 2: Parse
parser = Parser()
ast = parser.parse(tokens)

# Check results
if ast:
    print(f"✓ Parsed successfully")
    print(f"  Program name: {ast.name}")
    print(f"  Declarations: {len(ast.declarations)}")
    print(f"  Statements: {len(ast.statements)}")
else:
    print("✗ Parsing failed")

# Check for errors
if parser.errors:
    print(f"\n⚠ Found {len(parser.errors)} parser errors:")
    parser.report_errors()
```

### Parser Features

**Supported HAL/S Constructs:**
- ✅ PROGRAM blocks
- ✅ DECLARE statements (variables)
- ✅ PROCEDURE definitions
- ✅ Assignment statements
- ✅ Binary operations (+, -, *, /, **)
- ✅ Literals (numbers, strings, booleans)
- ✅ Identifiers

**Semantic Checking:**
- ✅ PROGRAM/CLOSE name matching
- ✅ Symbol table (variable declarations)
- ✅ Scope management

### Parse from File

```python
from resnik import Parser

# Parse directly from file
parser = Parser()
ast = parser.parse_file("program.hal")

if ast:
    print(f"✓ Successfully parsed {ast.name}")
else:
    print("✗ Parsing failed")
    parser.report_errors()
```

### Export AST to JSON

```python
from resnik import Lexer, Parser

# Parse
lexer = Lexer()
tokens = lexer.tokenize_file("program.hal")
parser = Parser()
ast = parser.parse(tokens)

# Export AST to JSON
if ast:
    parser.export_ast_json("ast.json")
    print("✓ Exported AST to ast.json")
```

### Inspect the AST

```python
from resnik import Lexer, Parser

lexer = Lexer()
tokens = lexer.tokenize_file("program.hal")
parser = Parser()
ast = parser.parse(tokens)

if ast:
    # Access AST components
    print(f"Program: {ast.name}")
    
    print(f"\nDeclarations ({len(ast.declarations)}):")
    for decl in ast.declarations:
        print(f"  - {', '.join(decl.identifiers)}")
    
    print(f"\nStatements ({len(ast.statements)}):")
    for stmt in ast.statements:
        print(f"  - {type(stmt).__name__}")
    
    print(f"\nProcedures ({len(ast.procedures)}):")
    for proc in ast.procedures:
        print(f"  - {proc.name}()")
```

### Complete Parser Example

```python
from resnik import Lexer, Parser

# HAL/S source
hal_code = """
PROGRAM Physics;
    DECLARE VELOCITY, TIME, DISTANCE SCALAR;
    
    VELOCITY = 9.8;
    TIME = 5.0;
    DISTANCE = VELOCITY * TIME;
CLOSE Physics;
"""

# Tokenize
lexer = Lexer()
tokens = lexer.tokenize(hal_code, "physics.hal")

if lexer.errors:
    print("✗ Lexer errors found:")
    lexer.report_errors()
    exit(1)

# Parse
parser = Parser()
ast = parser.parse(tokens)

if not ast:
    print("✗ Parsing failed:")
    parser.report_errors()
    exit(1)

# Success!
print(f"✓ Successfully parsed program '{ast.name}'")
print(f"  Declarations: {len(ast.declarations)}")
print(f"  Statements: {len(ast.statements)}")

# Inspect declarations
print("\nVariables declared:")
for decl in ast.declarations:
    for var in decl.identifiers:
        print(f"  - {var}")
```

---

## 🦀 Using the Backend (Code Generator)

### What the Backend Does

The **backend** (code generator) translates the AST into **Rust source code**.

**Example:**
```python
AssignmentNode(
    target=IdentifierNode("X"),
    value=LiteralNode(5)
)
```

**Becomes:**
```rust
x = 5;
```

### Basic Backend Usage

```python
from resnik import Lexer, Parser
from resnik.backend import RustCodeGenerator

# Parse HAL/S
lexer = Lexer()
tokens = lexer.tokenize_file("program.hal")
parser = Parser()
ast = parser.parse(tokens)

# Generate Rust
generator = RustCodeGenerator()
rust_code = generator.generate(ast)

# Print generated code
print(rust_code)

# Save to file
generator.write_to_file("output.rs", rust_code)
print("✓ Generated output.rs")
```

### Type Mapping

The backend maps HAL/S types to Rust types:

| HAL/S Type | Rust Type | Example |
|------------|-----------|---------|
| `INTEGER` | `i32` | `42` |
| `SCALAR` | `f32` | `3.14` |
| `BIT` | `bool` | `true` |
| `CHARACTER` | `String` | `"Hello"` |
| `VECTOR(N)` | `[f32; N]` | `[0.0; 3]` |
| `MATRIX(R,C)` | `[[f32; C]; R]` | `[[0.0; 3]; 3]` |

### Code Generation Features

**HAL/S Construct → Rust Output:**

```hal
PROGRAM MyProg;          →  fn main() {
DECLARE X INTEGER;       →      let mut x: i32;
X = 5;                   →      x = 5;
CLOSE MyProg;            →  }
```

```hal
PROCEDURE Add(A, B);     →  fn add(a: i32, b: i32) {
    DECLARE SUM INTEGER; →      let mut sum: i32;
    SUM = A + B;         →      sum = (a + b);
CLOSE Add;               →  }
```

### Naming Conventions

- **Variables:** `UPPER_CASE` → `snake_case`
  - `VELOCITY` → `velocity`
  - `TIME_DELTA` → `time_delta`

- **Mutability:** All variables are `mut` by default (HAL/S allows reassignment)

### Complete Backend Example

```python
from resnik import Lexer, Parser
from resnik.backend import RustCodeGenerator

# HAL/S program
hal_code = """
PROGRAM Calculate;
    DECLARE A, B, RESULT SCALAR;
    
    A = 3.14;
    B = 2.0;
    RESULT = A * B + 1.0;
CLOSE Calculate;
"""

# Parse
lexer = Lexer()
tokens = lexer.tokenize(hal_code, "calculate.hal")
parser = Parser()
ast = parser.parse(tokens)

# Generate Rust
generator = RustCodeGenerator()
rust_code = generator.generate(ast)

# Display
print("=" * 70)
print("Generated Rust Code:")
print("=" * 70)
print(rust_code)
print("=" * 70)

# Save
generator.write_to_file("calculate.rs", rust_code)
print("\n✓ Saved to calculate.rs")
```

**Output:**
```rust
// Generated by Resnik HAL/S Compiler
// https://github.com/user/resnik

#![allow(unused_variables)]
#![allow(unused_mut)]

// HAL/S Program: Calculate
fn main() {
    let mut a: f32;
    let mut b: f32;
    let mut result: f32;
    
    a = 3.14;
    b = 2.0;
    result = ((a * b) + 1.0);
}
```

---

## 🔄 Complete Workflow

### End-to-End Compilation

**Complete script** from HAL/S to executable:

```python
#!/usr/bin/env python3
"""
Complete HAL/S to executable compiler workflow.
"""

import sys
from resnik import Lexer, Parser
from resnik.backend import RustCodeGenerator

def compile_hals_to_rust(input_file, output_file):
    """Compile HAL/S file to Rust."""
    
    print(f"📝 Compiling {input_file} → {output_file}")
    
    # Step 1: Lexical Analysis
    print("  [1/3] Lexical analysis...")
    lexer = Lexer()
    tokens = lexer.tokenize_file(input_file)
    
    if lexer.errors:
        print(f"  ✗ Lexer found {len(lexer.errors)} error(s)")
        lexer.report_errors()
        return False
    
    print(f"  ✓ Tokenized: {len(tokens)} tokens")
    
    # Step 2: Parsing
    print("  [2/3] Parsing...")
    parser = Parser()
    ast = parser.parse(tokens)
    
    if not ast or parser.errors:
        print(f"  ✗ Parser found {len(parser.errors)} error(s)")
        parser.report_errors()
        return False
    
    print(f"  ✓ Parsed: Program '{ast.name}'")
    
    # Step 3: Code Generation
    print("  [3/3] Generating Rust code...")
    generator = RustCodeGenerator()
    rust_code = generator.generate(ast)
    generator.write_to_file(output_file, rust_code)
    
    print(f"  ✓ Generated: {output_file}")
    print(f"\n✅ Compilation successful!")
    return True

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python compile.py input.hal output.rs")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2]
    
    success = compile_hals_to_rust(input_file, output_file)
    sys.exit(0 if success else 1)
```

**Usage:**
```bash
# Save the script as compile.py
python compile.py program.hal program.rs
```

---

## 🏗️ Compiling Rust to Executable

### Step 1: Generate Rust Code

```bash
# Using the compile script
python compile.py program.hal program.rs
```

### Step 2: Compile with rustc

```bash
# Basic compilation
rustc program.rs -o program

# With optimizations (release mode)
rustc -O program.rs -o program

# With debug info
rustc -g program.rs -o program
```

### Step 3: Run the Executable

```bash
# Run it
./program

# On Windows
program.exe
```

### Compilation Options

**Optimization Levels:**
```bash
# No optimization (fast compile, slow runtime)
rustc program.rs -o program

# Optimize (-O)
rustc -O program.rs -o program

# Maximum optimization
rustc -C opt-level=3 program.rs -o program
```

**Debug vs Release:**
```bash
# Debug build (with symbols)
rustc -g program.rs -o program_debug

# Release build (optimized, no symbols)
rustc -O program.rs -o program_release
```

### Using Cargo (Recommended for Larger Projects)

**1. Create a Cargo project:**
```bash
cargo new my_program
cd my_program
```

**2. Replace `src/main.rs` with generated code:**
```bash
python compile.py program.hal src/main.rs
```

**3. Build and run:**
```bash
# Build and run in debug mode
cargo run

# Build release binary
cargo build --release

# Run release binary
./target/release/my_program
```

---

## 💻 Command-Line Interface

### Quick Compilation Script

Create `resnik-compile.py`:

```python
#!/usr/bin/env python3
"""
Resnik HAL/S Compiler CLI
Usage: resnik-compile input.hal [output.rs]
"""

import sys
import argparse
from pathlib import Path
from resnik import Lexer, Parser
from resnik.backend import RustCodeGenerator

def main():
    parser = argparse.ArgumentParser(
        description="Compile HAL/S to Rust"
    )
    parser.add_argument("input", help="Input HAL/S file (.hal)")
    parser.add_argument("output", nargs="?", help="Output Rust file (.rs)")
    parser.add_argument("-v", "--verbose", action="store_true",
                       help="Verbose output")
    parser.add_argument("--tokens", help="Export tokens to JSON file")
    parser.add_argument("--ast", help="Export AST to JSON file")
    
    args = parser.parse_args()
    
    # Determine output file
    if args.output:
        output_file = args.output
    else:
        output_file = Path(args.input).with_suffix('.rs')
    
    if args.verbose:
        print(f"Input:  {args.input}")
        print(f"Output: {output_file}")
    
    # Compile
    try:
        # Lexer
        lexer = Lexer()
        tokens = lexer.tokenize_file(args.input)
        
        if lexer.errors:
            print(f"✗ Lexical errors found ({len(lexer.errors)}):")
            lexer.report_errors()
            return 1
        
        if args.verbose:
            print(f"✓ Lexer: {len(tokens)} tokens")
        
        # Export tokens if requested
        if args.tokens:
            lexer.export_json(args.tokens)
            if args.verbose:
                print(f"✓ Exported tokens to {args.tokens}")
        
        # Parser
        parser_obj = Parser()
        ast = parser_obj.parse(tokens)
        
        if not ast or parser_obj.errors:
            print(f"✗ Parser errors found ({len(parser_obj.errors)}):")
            parser_obj.report_errors()
            return 1
        
        if args.verbose:
            print(f"✓ Parser: Program '{ast.name}'")
        
        # Export AST if requested
        if args.ast:
            parser_obj.export_ast_json(args.ast)
            if args.verbose:
                print(f"✓ Exported AST to {args.ast}")
        
        # Code Generator
        generator = RustCodeGenerator()
        rust_code = generator.generate(ast)
        generator.write_to_file(output_file, rust_code)
        
        if args.verbose:
            print(f"✓ Generated Rust code")
        
        print(f"✅ Successfully compiled {args.input} → {output_file}")
        return 0
        
    except Exception as e:
        print(f"✗ Error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
```

**Make it executable:**
```bash
chmod +x resnik-compile.py
```

**Usage:**
```bash
# Basic compilation
./resnik-compile.py program.hal

# Specify output file
./resnik-compile.py program.hal output.rs

# Verbose mode
./resnik-compile.py -v program.hal

# Export intermediate results
./resnik-compile.py program.hal --tokens tokens.json --ast ast.json
```

---

## 📚 Examples

### Example 1: Hello World

**File: `hello.hal`**
```hal
PROGRAM Hello;
    DECLARE MESSAGE CHARACTER;
    MESSAGE = 'Hello from HAL/S!';
CLOSE Hello;
```

**Compile and run:**
```bash
python compile.py hello.hal hello.rs
rustc hello.rs -o hello
./hello
```

### Example 2: Arithmetic

**File: `math.hal`**
```hal
PROGRAM Mathematics;
    DECLARE A, B, SUM, PRODUCT SCALAR;
    
    A = 3.14;
    B = 2.0;
    SUM = A + B;
    PRODUCT = A * B;
CLOSE Mathematics;
```

**Compile and run:**
```bash
python compile.py math.hal math.rs
rustc math.rs -o math
./math
```

### Example 3: With Procedure

**File: `procedure.hal`**
```hal
PROGRAM WithProc;
    PROCEDURE Calculate(X, Y);
        DECLARE RESULT INTEGER;
        RESULT = X + Y;
    CLOSE Calculate;
    
    DECLARE VALUE INTEGER;
    VALUE = 10;
CLOSE WithProc;
```

**Compile and run:**
```bash
python compile.py procedure.hal procedure.rs
rustc procedure.rs -o procedure
./procedure
```

### Example 4: Physics Calculation

**File: `physics.hal`**
```hal
PROGRAM PhysicsSimulation;
    DECLARE VELOCITY, TIME, DISTANCE SCALAR;
    DECLARE ACCELERATION SCALAR;
    
    C Initial conditions
    VELOCITY = 9.8;
    TIME = 5.0;
    ACCELERATION = 2.0;
    
    C Calculate distance
    DISTANCE = VELOCITY * TIME;
CLOSE PhysicsSimulation;
```

**Complete workflow:**
```bash
# Compile HAL/S to Rust
python compile.py physics.hal physics.rs

# Compile Rust to executable
rustc -O physics.rs -o physics

# Run
./physics

# Check binary size
ls -lh physics
```

---

## 🔧 Troubleshooting

### Common Issues

**1. "No module named 'resnik'"**
```bash
# Solution: Install Resnik
pip install -e .
```

**2. "rustc: command not found"**
```bash
# Solution: Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source $HOME/.cargo/env
```

**3. Lexer errors for valid HAL/S**
```
Check that your HAL/S file:
- Uses correct keywords (PROGRAM, not Program)
- Has matching PROGRAM/CLOSE names
- Has proper semicolons
- Has valid number formats
```

**4. Parser errors**
```
Current parser supports:
- PROGRAM blocks
- DECLARE statements
- Assignments
- PROCEDURE definitions

Not yet implemented:
- IF/THEN/ELSE
- DO loops
- FUNCTION blocks
- Arrays (VECTOR/MATRIX declarations work, but not usage)
```

**5. Generated Rust doesn't compile**
```bash
# Check the Rust code
cat output.rs

# Try compiling with verbose errors
rustc --error-format=human output.rs

# Common issue: unused variables
# Solution: Rust code has #![allow(unused_variables)]
```

### Debugging Tips

**Enable verbose output:**
```python
from resnik import Lexer

lexer = Lexer()
tokens = lexer.tokenize_file("program.hal")

# Print all tokens
for token in tokens:
    print(f"{token.line:3d}:{token.column:3d}  {token.type:20s}  '{token.lexeme}'")

# Print all errors
if lexer.errors:
    for error in lexer.errors:
        print(f"Error at {error.line}:{error.column} - {error.message}")
```

**Inspect AST:**
```python
from resnik import Lexer, Parser
import json

lexer = Lexer()
tokens = lexer.tokenize_file("program.hal")
parser = Parser()
ast = parser.parse(tokens)

# Export to JSON for inspection
parser.export_ast_json("ast.json")

# View pretty-printed JSON
with open("ast.json") as f:
    data = json.load(f)
    print(json.dumps(data, indent=2))
```

---

## 📖 API Reference

### Lexer API

```python
from resnik import Lexer

lexer = Lexer()

# Methods
tokens = lexer.tokenize(source_code, filename)
tokens = lexer.tokenize_file(filepath)
lexer.export_json(output_file)
lexer.report_errors()

# Properties
lexer.errors       # List[LexerError]
lexer.tokens       # List[Token]
lexer.source_file  # str
```

### Parser API

```python
from resnik import Parser

parser = Parser()

# Methods
ast = parser.parse(tokens)
ast = parser.parse_file(filepath)
parser.export_ast_json(output_file)
parser.report_errors()

# Properties
parser.errors        # List[ParserError]
parser.ast           # Optional[ASTNode]
parser.symbol_table  # SymbolTable
```

### Backend API

```python
from resnik.backend import RustCodeGenerator

generator = RustCodeGenerator()

# Methods
rust_code = generator.generate(ast)
generator.write_to_file(filepath, code)

# Properties
generator.emitter      # RustEmitter
generator.type_mapper  # TypeMapper
```

### Type Mapper API

```python
from resnik.backend import TypeMapper
from resnik.types import TypeSpec, DataType

mapper = TypeMapper()

# Methods
rust_type = mapper.map_type(type_spec)
rust_type = mapper.map_literal_type("INTEGER")
default = mapper.get_default_value(type_spec)
```

---

## 🎓 Advanced Usage

### Custom Code Generation

```python
from resnik.backend import RustEmitter

# Create custom Rust code
emitter = RustEmitter()

emitter.emit_comment("Custom function")
emitter.emit_function_header("custom", [("x", "i32")], "i32", is_public=True)
emitter.emit_variable_declaration("result", "i32", "x * 2", is_mut=False)
emitter.emit_return("result")
emitter.emit_block_end()

# Get generated code
code = emitter.get_code()
print(code)
```

### Batch Compilation

```python
from pathlib import Path
from resnik import Lexer, Parser
from resnik.backend import RustCodeGenerator

def compile_directory(input_dir, output_dir):
    """Compile all .hal files in directory."""
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    
    for hal_file in input_path.glob("*.hal"):
        print(f"Compiling {hal_file.name}...")
        
        # Parse
        lexer = Lexer()
        tokens = lexer.tokenize_file(str(hal_file))
        parser = Parser()
        ast = parser.parse(tokens)
        
        if ast and not parser.errors:
            # Generate
            generator = RustCodeGenerator()
            rust_code = generator.generate(ast)
            
            # Save
            output_file = output_path / hal_file.with_suffix('.rs').name
            generator.write_to_file(str(output_file), rust_code)
            print(f"  ✓ Generated {output_file.name}")
        else:
            print(f"  ✗ Failed to compile {hal_file.name}")

# Usage
compile_directory("hal_programs", "rust_output")
```

---

## 📝 Summary

**Complete Workflow:**

1. **Write HAL/S** → Save as `.hal` file
2. **Tokenize** → Lexer breaks into tokens
3. **Parse** → Parser builds AST
4. **Generate** → Backend produces Rust code
5. **Compile** → rustc creates executable
6. **Run** → Execute the binary!

**Quick Commands:**
```bash
# One-liner compilation
python -c "from resnik import *; from resnik.backend import *; l=Lexer(); p=Parser(); g=RustCodeGenerator(); t=l.tokenize_file('prog.hal'); a=p.parse(t); g.write_to_file('prog.rs', g.generate(a))"

# Compile to executable
rustc prog.rs -o prog && ./prog
```

**Resources:**
- Tests: `pytest tests/`
- Examples: `demo_backend.py`
- Documentation: `BACKEND_DOCUMENTATION.md`

---

**Happy Compiling!** 🚀

For questions or issues, check the test suite or examine the demo files included with Resnik.
