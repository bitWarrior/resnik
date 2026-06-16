"""
Rust code emission utilities for formatting and output.
"""

from typing import List, Optional


class RustEmitter:
    """
    Utilities for emitting formatted Rust code.
    
    Handles:
    - Indentation management
    - Code formatting
    - Common Rust constructs
    
    Example:
        >>> emitter = RustEmitter()
        >>> emitter.emit_line("fn main() {")
        >>> emitter.indent()
        >>> emitter.emit_line("println!(\"Hello\");")
        >>> emitter.dedent()
        >>> emitter.emit_line("}")
        >>> print(emitter.get_code())
    """
    
    def __init__(self, indent_size: int = 4):
        """
        Initialize emitter.
        
        Args:
            indent_size: Number of spaces per indent level
        """
        self.lines: List[str] = []
        self.indent_level: int = 0
        self.indent_size: int = indent_size
    
    def indent(self) -> None:
        """Increase indentation level."""
        self.indent_level += 1
    
    def dedent(self) -> None:
        """Decrease indentation level."""
        if self.indent_level > 0:
            self.indent_level -= 1
    
    def emit_line(self, line: str = "") -> None:
        """
        Emit a line of code with current indentation.
        
        Args:
            line: Code line to emit (without indentation)
        """
        if line:
            indent = " " * (self.indent_level * self.indent_size)
            self.lines.append(f"{indent}{line}")
        else:
            # Empty line
            self.lines.append("")
    
    def emit_comment(self, comment: str) -> None:
        """
        Emit a Rust comment.
        
        Args:
            comment: Comment text
        """
        self.emit_line(f"// {comment}")
    
    def emit_doc_comment(self, doc: str) -> None:
        """
        Emit a Rust doc comment.
        
        Args:
            doc: Documentation text
        """
        self.emit_line(f"/// {doc}")
    
    def emit_block_start(self, header: str) -> None:
        """
        Emit block header and increase indentation.
        
        Args:
            header: Block header (e.g., "fn main()")
        """
        self.emit_line(f"{header} {{")
        self.indent()
    
    def emit_block_end(self) -> None:
        """Emit block closing brace and decrease indentation."""
        self.dedent()
        self.emit_line("}")
    
    def emit_function_header(
        self,
        name: str,
        params: List[tuple[str, str]],
        return_type: Optional[str] = None,
        is_public: bool = False
    ) -> None:
        """
        Emit function signature.
        
        Args:
            name: Function name
            params: List of (name, type) tuples
            return_type: Return type (None for unit type)
            is_public: Whether function is public
        """
        visibility = "pub " if is_public else ""
        
        # Format parameters
        param_strs = [f"{pname}: {ptype}" for pname, ptype in params]
        params_str = ", ".join(param_strs)
        
        # Format return type
        ret_str = f" -> {return_type}" if return_type else ""
        
        self.emit_line(f"{visibility}fn {name}({params_str}){ret_str} {{")
        self.indent()
    
    def emit_variable_declaration(
        self,
        name: str,
        var_type: str,
        value: Optional[str] = None,
        is_mut: bool = True
    ) -> None:
        """
        Emit variable declaration.
        
        Args:
            name: Variable name
            var_type: Variable type
            value: Initial value (None for default)
            is_mut: Whether variable is mutable
        """
        mut = "mut " if is_mut else ""
        
        if value:
            self.emit_line(f"let {mut}{name}: {var_type} = {value};")
        else:
            # Use type's default value
            self.emit_line(f"let {mut}{name}: {var_type};")
    
    def emit_assignment(self, target: str, value: str) -> None:
        """
        Emit assignment statement.
        
        Args:
            target: Assignment target
            value: Value expression
        """
        self.emit_line(f"{target} = {value};")
    
    def emit_return(self, value: Optional[str] = None) -> None:
        """
        Emit return statement.
        
        Args:
            value: Return value (None for unit return)
        """
        if value:
            self.emit_line(f"return {value};")
        else:
            self.emit_line("return;")
    
    def get_code(self) -> str:
        """
        Get generated code as string.
        
        Returns:
            Complete generated code
        """
        return "\n".join(self.lines)
    
    def clear(self) -> None:
        """Clear all generated code."""
        self.lines = []
        self.indent_level = 0
