"""
Type mapping from HAL/S to Rust types.
"""

from typing import Optional
from resnik.types import DataType, TypeSpec


class TypeMapper:
    """
    Maps HAL/S types to equivalent Rust types.
    
    HAL/S Type System:
    - INTEGER: 32-bit signed integer
    - SCALAR: 32-bit floating point (single precision, can be double with precision modifier)
    - VECTOR: Array of SCALAR values
    - MATRIX: 2D array of SCALAR values
    - BIT: Boolean value
    - CHARACTER: String/character data
    
    Example:
        >>> mapper = TypeMapper()
        >>> mapper.map_type(TypeSpec(data_type=DataType.INTEGER))
        'i32'
        >>> mapper.map_type(TypeSpec(data_type=DataType.SCALAR))
        'f32'
    """
    
    # Core type mappings
    TYPE_MAP = {
        DataType.INTEGER: "i32",
        DataType.SCALAR: "f32",  # Default to single precision
        DataType.BIT: "bool",
        DataType.CHARACTER: "String",
    }
    
    def map_type(self, type_spec: Optional[TypeSpec]) -> str:
        """
        Map a HAL/S type specification to Rust type.
        
        Args:
            type_spec: HAL/S type specification
            
        Returns:
            Rust type string
        """
        if type_spec is None:
            # No type specified - use i32 as default
            return "i32"
        
        # Handle array types
        if type_spec.data_type == DataType.VECTOR:
            # VECTOR uses dimensions[0] for size
            size = type_spec.dimensions[0] if type_spec.dimensions else 3
            return f"[f32; {size}]"
        
        if type_spec.data_type == DataType.MATRIX:
            # MATRIX uses dimensions[0]=rows, dimensions[1]=cols
            if type_spec.dimensions and len(type_spec.dimensions) >= 2:
                rows = type_spec.dimensions[0]
                cols = type_spec.dimensions[1]
            else:
                rows, cols = 3, 3
            return f"[[f32; {cols}]; {rows}]"
        
        # Get base type
        base_type = self.TYPE_MAP.get(type_spec.data_type, "i32")
        
        # For SCALAR, check precision (if specified)
        if type_spec.data_type == DataType.SCALAR:
            # Check if precision is specified (would be in a precision field)
            # Default to f32 (single precision)
            # If double precision is needed, would return f64
            base_type = "f32"
        
        return base_type
    
    def map_literal_type(self, literal_type: str) -> str:
        """
        Map a literal type string to Rust type.
        
        Args:
            literal_type: Literal type ("INTEGER", "SCALAR", "STRING", etc.)
            
        Returns:
            Rust type string
        """
        type_map = {
            "INTEGER": "i32",
            "SCALAR": "f32",
            "STRING": "String",
            "BIT": "bool",
        }
        return type_map.get(literal_type.upper(), "i32")
    
    def get_default_value(self, type_spec: Optional[TypeSpec]) -> str:
        """
        Get default initialization value for a type.
        
        Args:
            type_spec: HAL/S type specification
            
        Returns:
            Rust default value expression
        """
        if type_spec is None:
            return "0"
        
        defaults = {
            DataType.INTEGER: "0",
            DataType.SCALAR: "0.0",
            DataType.BIT: "false",
            DataType.CHARACTER: 'String::new()',
        }
        
        if type_spec.data_type == DataType.VECTOR:
            size = type_spec.dimensions[0] if type_spec.dimensions else 3
            return f"[0.0; {size}]"
        
        if type_spec.data_type == DataType.MATRIX:
            if type_spec.dimensions and len(type_spec.dimensions) >= 2:
                rows = type_spec.dimensions[0]
                cols = type_spec.dimensions[1]
            else:
                rows, cols = 3, 3
            return f"[[0.0; {cols}]; {rows}]"
        
        return defaults.get(type_spec.data_type, "0")
