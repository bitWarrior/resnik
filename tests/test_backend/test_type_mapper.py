# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Steven Doran
"""
Tests for type mapper (HAL/S to Rust type conversion).
"""

import pytest
from resnik.backend.type_mapper import TypeMapper
from resnik.types import DataType, TypeSpec


class TestTypeMapperBasic:
    """Test basic type mapping."""

    def test_integer_type(self) -> None:
        """Test INTEGER maps to i32."""
        mapper = TypeMapper()
        type_spec = TypeSpec(data_type=DataType.INTEGER)
        
        assert mapper.map_type(type_spec) == "i32"

    def test_scalar_type(self) -> None:
        """Test SCALAR maps to f32."""
        mapper = TypeMapper()
        type_spec = TypeSpec(data_type=DataType.SCALAR)
        
        assert mapper.map_type(type_spec) == "f32"

    def test_bit_type(self) -> None:
        """BIT maps to u32 (a bit string is an unsigned register)."""
        mapper = TypeMapper()
        type_spec = TypeSpec(data_type=DataType.BIT)
        
        assert mapper.map_type(type_spec) == "u32"

    def test_character_type(self) -> None:
        """Test CHARACTER maps to String."""
        mapper = TypeMapper()
        type_spec = TypeSpec(data_type=DataType.CHARACTER)
        
        assert mapper.map_type(type_spec) == "String"

    def test_none_type(self) -> None:
        """Test None type spec defaults to i32."""
        mapper = TypeMapper()
        
        assert mapper.map_type(None) == "i32"


class TestTypeMapperArrays:
    """Test array type mapping."""

    def test_vector_default_size(self) -> None:
        """Test VECTOR with default size."""
        mapper = TypeMapper()
        type_spec = TypeSpec(data_type=DataType.VECTOR)
        
        result = mapper.map_type(type_spec)
        assert result == "Vector<3>"

    def test_vector_custom_size(self) -> None:
        """Test VECTOR with custom size."""
        mapper = TypeMapper()
        type_spec = TypeSpec(data_type=DataType.VECTOR, dimensions=[5])
        
        result = mapper.map_type(type_spec)
        assert result == "Vector<5>"

    def test_matrix_default_size(self) -> None:
        """Test MATRIX with default size."""
        mapper = TypeMapper()
        type_spec = TypeSpec(data_type=DataType.MATRIX)
        
        result = mapper.map_type(type_spec)
        assert result == "Matrix<3, 3>"

    def test_matrix_custom_size(self) -> None:
        """Test MATRIX with custom dimensions."""
        mapper = TypeMapper()
        type_spec = TypeSpec(
            data_type=DataType.MATRIX,
            dimensions=[4, 5]  # rows=4, cols=5
        )
        
        result = mapper.map_type(type_spec)
        assert result == "Matrix<4, 5>"


class TestLiteralTypeMapping:
    """Test literal type string mapping."""

    def test_literal_integer(self) -> None:
        """Test INTEGER literal type."""
        mapper = TypeMapper()
        assert mapper.map_literal_type("INTEGER") == "i32"

    def test_literal_scalar(self) -> None:
        """Test SCALAR literal type."""
        mapper = TypeMapper()
        assert mapper.map_literal_type("SCALAR") == "f32"

    def test_literal_string(self) -> None:
        """Test STRING literal type."""
        mapper = TypeMapper()
        assert mapper.map_literal_type("STRING") == "String"

    def test_literal_bit(self) -> None:
        """Test BIT literal type."""
        mapper = TypeMapper()
        assert mapper.map_literal_type("BIT") == "u32"

    def test_literal_case_insensitive(self) -> None:
        """Test literal type mapping is case-insensitive."""
        mapper = TypeMapper()
        assert mapper.map_literal_type("integer") == "i32"
        assert mapper.map_literal_type("InTeGeR") == "i32"

    def test_literal_unknown(self) -> None:
        """Test unknown literal type defaults to i32."""
        mapper = TypeMapper()
        assert mapper.map_literal_type("UNKNOWN") == "i32"


class TestDefaultValues:
    """Test default value generation."""

    def test_integer_default(self) -> None:
        """Test INTEGER default value."""
        mapper = TypeMapper()
        type_spec = TypeSpec(data_type=DataType.INTEGER)
        
        assert mapper.get_default_value(type_spec) == "0"

    def test_scalar_default(self) -> None:
        """Test SCALAR default value."""
        mapper = TypeMapper()
        type_spec = TypeSpec(data_type=DataType.SCALAR)
        
        assert mapper.get_default_value(type_spec) == "0.0"

    def test_bit_default(self) -> None:
        """Test BIT default value."""
        mapper = TypeMapper()
        type_spec = TypeSpec(data_type=DataType.BIT)
        
        assert mapper.get_default_value(type_spec) == "0"

    def test_character_default(self) -> None:
        """Test CHARACTER default value."""
        mapper = TypeMapper()
        type_spec = TypeSpec(data_type=DataType.CHARACTER)
        
        assert mapper.get_default_value(type_spec) == "String::new()"

    def test_vector_default(self) -> None:
        """Test VECTOR default value."""
        mapper = TypeMapper()
        type_spec = TypeSpec(data_type=DataType.VECTOR, dimensions=[3])
        
        assert mapper.get_default_value(type_spec) == "Vector::<3>::zero()"

    def test_matrix_default(self) -> None:
        """Test MATRIX default value."""
        mapper = TypeMapper()
        type_spec = TypeSpec(data_type=DataType.MATRIX, dimensions=[2, 3])
        
        assert mapper.get_default_value(type_spec) == "Matrix::<2, 3>::zero()"

    def test_none_default(self) -> None:
        """Test None type spec default value."""
        mapper = TypeMapper()
        
        assert mapper.get_default_value(None) == "0"


class TestBooleanMapping:
    def test_boolean_maps_to_bool(self) -> None:
        from resnik.backend.type_mapper import TypeMapper
        from resnik.types import TypeSpec, DataType
        assert TypeMapper().map_type(TypeSpec(data_type=DataType.BOOLEAN)) == "bool"
