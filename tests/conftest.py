"""
Pytest configuration and fixtures for Resnik tests.
"""

import pytest
from pathlib import Path
from typing import Generator


@pytest.fixture
def fixture_dir() -> Path:
    """Return path to test fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def valid_fixtures(fixture_dir: Path) -> Path:
    """Return path to valid test fixtures."""
    return fixture_dir / "valid"


@pytest.fixture
def invalid_fixtures(fixture_dir: Path) -> Path:
    """Return path to invalid test fixtures."""
    return fixture_dir / "invalid"


@pytest.fixture
def simple_hal_code() -> str:
    """Return simple HAL/S code for testing."""
    return """PROGRAM TEST;
CLOSE TEST;
"""


@pytest.fixture
def lexer():
    """Return a fresh Lexer instance."""
    # Import here to avoid coverage warning
    from resnik import Lexer
    return Lexer()


@pytest.fixture
def parser():
    """Return a fresh Parser instance."""
    # Import here to avoid coverage warning
    from resnik import Parser
    return Parser()


@pytest.fixture
def sample_tokens(lexer, simple_hal_code: str) -> list:
    """Return tokenized simple HAL/S code."""
    return lexer.tokenize(simple_hal_code, "<test>")
