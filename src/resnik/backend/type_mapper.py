"""
Type mapping from HAL/S to Rust types.
"""

from typing import Optional
from resnik.types import DataType, Precision, TypeSpec


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
        DataType.BIT: "u32",   # bit string -> unsigned register (see _bit_type)
        DataType.BOOLEAN: "bool",
        DataType.EVENT: "bool",  # host stub: latched flag (advisory)
        DataType.CHARACTER: "String",
    }

    def _scalar_type(self, precision) -> str:
        """Resolve SCALAR element type from precision (f32 single, f64 double).
        Accepts the Precision enum or a plain string."""
        if precision in (Precision.DOUBLE, "double", "DOUBLE"):
            return "f64"
        return "f32"

    @staticmethod
    def _bit_type(width: Optional[int]) -> str:
        """Pick the narrowest unsigned Rust type that holds a BIT(width). BIT
        without a declared width falls back to u32. Element-narrowing matters
        because an ARRAY(N) OF BIT(w) is [T; N] — using u32 for BIT(8) wastes
        24 bits per element, and misrepresents the on-wire width of packed
        register / downlink buffers."""
        if width is None or width <= 0:
            return "u32"
        if width <= 8:
            return "u8"
        if width <= 16:
            return "u16"
        if width <= 32:
            return "u32"
        return "u64"

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
            # VECTOR(n) -> hal_rt Vector<n> (element type is f32)
            size = type_spec.dimensions[0] if type_spec.dimensions else 3
            return f"Vector<{size}>"
        
        if type_spec.data_type == DataType.MATRIX:
            # MATRIX(r,c) -> hal_rt Matrix<r, c> (element type is f32)
            if type_spec.dimensions and len(type_spec.dimensions) >= 2:
                rows = type_spec.dimensions[0]
                cols = type_spec.dimensions[1]
            else:
                rows, cols = 3, 3
            return f"Matrix<{rows}, {cols}>"
        
        # BIT(w) -> narrowest fitting unsigned type (u8/u16/u32/u64).
        if type_spec.data_type == DataType.BIT:
            width = type_spec.dimensions[0] if type_spec.dimensions else None
            return self._bit_type(width)

        # Get base type
        base_type = self.TYPE_MAP.get(type_spec.data_type, "i32")

        # For SCALAR, single precision -> f32, double precision -> f64
        if type_spec.data_type == DataType.SCALAR:
            base_type = self._scalar_type(type_spec.precision)

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
            "BIT": "u32",
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
            DataType.BIT: "0",
            DataType.BOOLEAN: "false",
            DataType.EVENT: "false",
            DataType.CHARACTER: 'String::new()',
        }
        
        if type_spec.data_type == DataType.VECTOR:
            size = type_spec.dimensions[0] if type_spec.dimensions else 3
            return f"Vector::<{size}>::zero()"
        
        if type_spec.data_type == DataType.MATRIX:
            if type_spec.dimensions and len(type_spec.dimensions) >= 2:
                rows = type_spec.dimensions[0]
                cols = type_spec.dimensions[1]
            else:
                rows, cols = 3, 3
            return f"Matrix::<{rows}, {cols}>::zero()"
        
        return defaults.get(type_spec.data_type, "0")
