# Resnik HAL/S Compiler 🚀

An open-sourc compiler for the HAL/S aerospace programming language, translating legacy NASA HAL/S source code to safe, modern Rust.

**Version:** 1.0.0 (Modern Edition)  
**Status:** Lexer ✓ | Parser ✓ | Backend → Planned  
**Date:** February 11, 2026

## License

This project is licensed under the GPL v3 license - see the [LICENSE](LICENSE) file for details.

## Installation

```bash
# Clone the repository
git clone https://github.com/bitWarrior/resnik
cd resnik

# Create and activate the python venv
python3 -m venv resnik-venv
. resnik-venv/bin/activate

# Install with development dependencies
pip install -e ".[dev]"

# Or just runtime dependencies
pip install -e .
```

### Usage

```bash
# Tokenize a HAL/S file  
resnik lex tests/fixtures/valid/sample_hals_code.hal

# Parse to AST
resnik parse tests/fixtures/valid/simple_parser_test.hal

# Complete compilation pipeline
resnik compile tests/fixtures/valid/sample_hals_code.hal

# Get help
resnik --help
```

### Programmatic Usage

```python
from resnik import Lexer, Parser

# Tokenize
lexer = Lexer()
tokens = lexer.tokenize_file("program.hal")

# Parse
parser = Parser()
ast = parser.parse(tokens)

# Check for errors
if parser.errors:
    parser.report_errors()
else:
    print(f"✓ Successfully parsed: {ast.name}")
```

## Project Structure
```
resnik/
├── src/resnik/              # Main package (clean imports!)
│   ├── lexer/              # Lexical analyzer
│   │   ├── lexer.py        # Modern lexer implementation
│   │   └── tokens.py       # Token definitions
│   ├── parser/             # Syntactic analyzer
│   │   ├── parser.py       # Modern parser implementation
│   │   ├── ast_nodes.py    # AST node classes
│   │   └── symbol_table.py # Symbol table
│   ├── backend/            # Code generator (planned)
│   ├── cli.py              # Beautiful CLI with Click + Rich
│   ├── types.py            # Type definitions
│   └── exceptions.py       # Custom exceptions
│
├── tests/                   # Professional test suite
│   ├── test_lexer/         # Lexer tests
│   ├── test_parser/        # Parser tests
│   ├── integration/        # End-to-end tests
│   └── fixtures/           # Test data
│
├── docs/                    # Documentation (MkDocs ready)
├── examples/                # Usage examples
├── pyproject.toml          # Single source of truth!
├── .pre-commit-config.yaml # Automated code quality
└── .github/workflows/      # CI/CD pipelines
```

## Testing

### Testing Resnik

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=resnik

# Run specific test file
pytest tests/test_lexer/test_tokenization.py

# Run with verbose output
pytest -v

# Run only fast tests
pytest -m "not slow"
```

## Namesake

Judith Resnik (April 5, 1949 – January 28, 1986) was an American electrical engineer, pilot, and NASA astronaut who became a trailblazer in space exploration.
Born in Akron, Ohio, to Jewish parents of Russian immigrant background—her father an optometrist and her mother a legal secretary—Resnik showed exceptional talent in math and science from a young age. She graduated as valedictorian from Firestone High School and earned a perfect math SAT score. She received her B.S. in electrical engineering from Carnegie Mellon University (then Carnegie Tech) in 1970 and later a Ph.D. in electrical engineering from the University of Maryland in 1977.
Her early career included work at RCA in missile and radar systems, biomedical engineering research at the National Institutes of Health, and engineering roles at Xerox. A licensed pilot, she was selected by NASA in January 1978 as part of Astronaut Group 8, the first class to include women.
Resnik became the second American woman in space (after Sally Ride) and the first Jewish woman of any nationality to fly in space. On her first mission, STS-41-D aboard Space Shuttle Discovery (August–September 1984), she served as a mission specialist, operating the robotic arm (Canadarm) and helping deploy satellites, logging about 145 hours in orbit total.
Tragically, she was killed along with the entire crew during her second mission, STS-51-L aboard Space Shuttle Challenger, when the vehicle exploded 73 seconds after launch on January 28, 1986.
Resnik was remembered as brilliant, determined, and multifaceted—she was also a classical pianist who enjoyed running, bicycling, and flying. Her legacy endures through memorials, including a lunar crater named after her, and she remains an inspiration for women and minorities in STEM and spaceflight.
