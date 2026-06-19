"""
Comprehensive tests for SymbolTable class.
"""

import pytest
from resnik.parser.symbol_table import Symbol, SymbolTable
from resnik.types import TypeSpec, DataType
from resnik.exceptions import SemanticError


class TestSymbol:
    """Test Symbol class."""

    def test_symbol_creation(self) -> None:
        """Test creating a symbol."""
        type_spec = TypeSpec(data_type=DataType.INTEGER)
        symbol = Symbol(
            name="COUNT",
            type_spec=type_spec,
            scope="global",
            is_constant=False,
            line=10,
            column=5
        )
        
        assert symbol.name == "COUNT"
        assert symbol.type_spec.data_type == DataType.INTEGER
        assert symbol.scope == "global"
        assert symbol.is_constant is False
        assert symbol.line == 10
        assert symbol.column == 5

    def test_symbol_constant(self) -> None:
        """Test constant symbol."""
        type_spec = TypeSpec(data_type=DataType.SCALAR)
        symbol = Symbol(
            name="PI",
            type_spec=type_spec,
            scope="global",
            is_constant=True,
            line=5,
            column=1
        )
        
        assert symbol.is_constant is True
        assert symbol.name == "PI"


class TestSymbolTableBasic:
    """Test basic SymbolTable functionality."""

    def test_symbol_table_creation(self) -> None:
        """Test creating a symbol table."""
        table = SymbolTable()
        
        assert len(table.scopes) == 1  # Global scope
        assert table.current_scope_name == "global"

    def test_declare_symbol(self) -> None:
        """Test declaring a symbol."""
        table = SymbolTable()
        type_spec = TypeSpec(data_type=DataType.INTEGER)
        symbol = Symbol(
            name="X",
            type_spec=type_spec,
            scope="global",
            line=1,
            column=1
        )
        
        table.declare(symbol)
        
        # Symbol should be in current scope
        assert "X" in table.scopes[-1]
        assert table.scopes[-1]["X"].name == "X"

    def test_declare_duplicate_error(self) -> None:
        """Test that declaring duplicate symbol raises error."""
        table = SymbolTable()
        type_spec = TypeSpec(data_type=DataType.INTEGER)
        
        symbol1 = Symbol(name="Y", type_spec=type_spec, scope="global", line=1, column=1)
        symbol2 = Symbol(name="Y", type_spec=type_spec, scope="global", line=2, column=1)
        
        table.declare(symbol1)
        
        # Second declaration should raise error
        with pytest.raises(SemanticError) as exc_info:
            table.declare(symbol2)
        
        assert "already declared" in str(exc_info.value.message)
        assert "Y" in str(exc_info.value.message)

    def test_lookup_symbol(self) -> None:
        """Test looking up a declared symbol."""
        table = SymbolTable()
        type_spec = TypeSpec(data_type=DataType.SCALAR)
        symbol = Symbol(name="ALTITUDE", type_spec=type_spec, scope="global")
        
        table.declare(symbol)
        found = table.lookup("ALTITUDE")
        
        assert found is not None
        assert found.name == "ALTITUDE"
        assert found.type_spec.data_type == DataType.SCALAR

    def test_lookup_undeclared(self) -> None:
        """Test looking up undeclared symbol returns None."""
        table = SymbolTable()
        found = table.lookup("NONEXISTENT")
        
        assert found is None

    def test_is_declared(self) -> None:
        """Test is_declared method."""
        table = SymbolTable()
        type_spec = TypeSpec(data_type=DataType.INTEGER)
        symbol = Symbol(name="Z", type_spec=type_spec, scope="global")
        
        assert table.is_declared("Z") is False
        
        table.declare(symbol)
        
        assert table.is_declared("Z") is True


class TestSymbolTableScopes:
    """Test scope management."""

    def test_enter_scope(self) -> None:
        """Test entering a new scope."""
        table = SymbolTable()
        
        assert len(table.scopes) == 1
        
        table.enter_scope("procedure")
        
        assert len(table.scopes) == 2
        assert table.current_scope_name == "procedure"

    def test_exit_scope(self) -> None:
        """Test exiting a scope."""
        table = SymbolTable()
        
        table.enter_scope("procedure")
        assert len(table.scopes) == 2
        
        table.exit_scope()
        assert len(table.scopes) == 1

    def test_exit_scope_restores_name(self) -> None:
        """exit_scope should restore the parent scope's name, not leave it stale."""
        table = SymbolTable()

        table.enter_scope("procedure")
        table.enter_scope("block")
        assert table.current_scope_name == "block"

        table.exit_scope()
        assert table.current_scope_name == "procedure"

        table.exit_scope()
        assert table.current_scope_name == "global"

    def test_exit_global_scope(self) -> None:
        """Test that exiting global scope doesn't break."""
        table = SymbolTable()
        
        assert len(table.scopes) == 1
        
        # Try to exit global scope - should not crash
        table.exit_scope()
        
        # Should still have at least global scope
        assert len(table.scopes) == 1

    def test_nested_scopes(self) -> None:
        """Test multiple nested scopes."""
        table = SymbolTable()
        
        # Global scope
        assert len(table.scopes) == 1
        
        # Enter procedure scope
        table.enter_scope("procedure1")
        assert len(table.scopes) == 2
        
        # Enter nested scope
        table.enter_scope("block")
        assert len(table.scopes) == 3
        
        # Exit back to procedure
        table.exit_scope()
        assert len(table.scopes) == 2
        
        # Exit back to global
        table.exit_scope()
        assert len(table.scopes) == 1

    def test_scope_shadowing(self) -> None:
        """Test that inner scope can shadow outer scope."""
        table = SymbolTable()
        type_spec_int = TypeSpec(data_type=DataType.INTEGER)
        type_spec_scalar = TypeSpec(data_type=DataType.SCALAR)
        
        # Declare in global scope
        global_sym = Symbol(name="VAR", type_spec=type_spec_int, scope="global")
        table.declare(global_sym)
        
        # Enter new scope
        table.enter_scope("procedure")
        
        # Declare same name in procedure scope (shadowing)
        local_sym = Symbol(name="VAR", type_spec=type_spec_scalar, scope="procedure")
        table.declare(local_sym)  # Should NOT raise error
        
        # Lookup should find local version
        found = table.lookup("VAR")
        assert found.type_spec.data_type == DataType.SCALAR
        assert found.scope == "procedure"

    def test_lookup_in_parent_scope(self) -> None:
        """Test that lookup finds symbols in parent scopes."""
        table = SymbolTable()
        type_spec = TypeSpec(data_type=DataType.INTEGER)
        
        # Declare in global scope
        global_sym = Symbol(name="GLOBAL_VAR", type_spec=type_spec, scope="global")
        table.declare(global_sym)
        
        # Enter procedure scope
        table.enter_scope("procedure")
        
        # Declare local variable
        local_sym = Symbol(name="LOCAL_VAR", type_spec=type_spec, scope="procedure")
        table.declare(local_sym)
        
        # Should find both global and local
        assert table.lookup("GLOBAL_VAR") is not None
        assert table.lookup("LOCAL_VAR") is not None
        
        # Exit scope
        table.exit_scope()
        
        # Should find global but not local
        assert table.lookup("GLOBAL_VAR") is not None
        assert table.lookup("LOCAL_VAR") is None


class TestSymbolTableComplex:
    """Test complex symbol table scenarios."""

    def test_multiple_symbols_same_scope(self) -> None:
        """Test declaring multiple symbols in same scope."""
        table = SymbolTable()
        type_spec = TypeSpec(data_type=DataType.INTEGER)
        
        symbols = [
            Symbol(name="A", type_spec=type_spec, scope="global"),
            Symbol(name="B", type_spec=type_spec, scope="global"),
            Symbol(name="C", type_spec=type_spec, scope="global"),
        ]
        
        for symbol in symbols:
            table.declare(symbol)
        
        assert table.is_declared("A")
        assert table.is_declared("B")
        assert table.is_declared("C")

    def test_different_types_same_name_different_scopes(self) -> None:
        """Test same name with different types in different scopes."""
        table = SymbolTable()
        
        # Global scope: INTEGER
        int_spec = TypeSpec(data_type=DataType.INTEGER)
        global_sym = Symbol(name="VALUE", type_spec=int_spec, scope="global")
        table.declare(global_sym)
        
        # Procedure scope: SCALAR
        table.enter_scope("procedure")
        scalar_spec = TypeSpec(data_type=DataType.SCALAR)
        proc_sym = Symbol(name="VALUE", type_spec=scalar_spec, scope="procedure")
        table.declare(proc_sym)
        
        # In procedure scope, should see SCALAR
        found = table.lookup("VALUE")
        assert found.type_spec.data_type == DataType.SCALAR
        
        # Exit to global
        table.exit_scope()
        
        # In global scope, should see INTEGER
        found = table.lookup("VALUE")
        assert found.type_spec.data_type == DataType.INTEGER

    def test_constant_symbols(self) -> None:
        """Test handling constant symbols."""
        table = SymbolTable()
        type_spec = TypeSpec(data_type=DataType.SCALAR)
        
        const_sym = Symbol(
            name="PI",
            type_spec=type_spec,
            scope="global",
            is_constant=True
        )
        
        table.declare(const_sym)
        found = table.lookup("PI")
        
        assert found.is_constant is True

    def test_symbol_position_tracking(self) -> None:
        """Test that symbol positions are preserved."""
        table = SymbolTable()
        type_spec = TypeSpec(data_type=DataType.INTEGER)
        
        symbol = Symbol(
            name="TRACKED",
            type_spec=type_spec,
            scope="global",
            line=42,
            column=13
        )
        
        table.declare(symbol)
        found = table.lookup("TRACKED")
        
        assert found.line == 42
        assert found.column == 13
