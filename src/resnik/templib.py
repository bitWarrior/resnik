"""Template-library (.tpl) generation for HAL/S units.

Mimics the original HAL/S compiler's TEMPLIB behavior: on a successful compile,
emit an auto-generated TEMPLATE -- the unit's signature plus its parameter/data
declarations -- for each reusable unit (PROCEDURE, FUNCTION, COMPOOL) as
re-parseable HAL/S source. A downstream `D INCLUDE TEMPLATE <name>` directive
can then pull the stored prototype in and register its signature before parsing
the rest of a file.
"""

from pathlib import Path
from typing import Optional

from resnik.parser.ast_nodes import (
    ProcedureNode, CompoolNode, CompilationNode, DeclareNode,
)
from resnik.types import DataType


def hal_type(ts) -> str:
    """Render a TypeSpec back to HAL/S declaration syntax (round-trips for the
    common scalar/vector/matrix/bit/character/boolean/structure types)."""
    if ts is None:
        return "INTEGER"
    dt = ts.data_type
    dims = ts.dimensions or []
    if dt == DataType.INTEGER:
        return "INTEGER"
    if dt == DataType.SCALAR:
        prec = getattr(ts, "precision", None)
        if prec is not None and getattr(prec, "name", "") == "DOUBLE":
            return "SCALAR DOUBLE"
        return "SCALAR"
    if dt == DataType.VECTOR:
        return f"VECTOR({dims[0] if dims else 3})"
    if dt == DataType.MATRIX:
        r = dims[0] if dims else 3
        c = dims[1] if len(dims) > 1 else 3
        return f"MATRIX({r}, {c})"
    if dt == DataType.BIT:
        return f"BIT({dims[0]})" if dims else "BIT"
    if dt == DataType.CHARACTER:
        return f"CHARACTER({dims[0]})" if dims else "CHARACTER"
    if dt == DataType.BOOLEAN:
        return "BOOLEAN"
    if dt == DataType.STRUCTURE:
        return f"{ts.type_name} STRUCTURE" if ts.type_name else "STRUCTURE"
    return dt.value


def _body_decls(node) -> dict:
    """Lower-cased declared name -> TypeSpec, from a unit's body."""
    out = {}
    src = getattr(node, "statements", None)
    if src is None:
        src = getattr(node, "declarations", [])
    for d in src:
        if isinstance(d, DeclareNode):
            for ident in d.identifiers:
                out[ident.lower()] = d.type_spec
    return out


def render_template(node) -> Optional[str]:
    """Render a re-parseable HAL/S TEMPLATE for a PROCEDURE / FUNCTION / COMPOOL,
    or None for a unit that has no reusable prototype (e.g. a PROGRAM)."""
    banner = "/* Auto-generated TEMPLATE by Resnik -- do not edit */\n"
    if isinstance(node, ProcedureNode):
        decls = _body_decls(node)
        params = ", ".join(node.parameters)
        assign = (f" ASSIGN({', '.join(node.assign_params)})"
                  if node.assign_params else "")
        if node.return_type is not None:
            header = (f"{node.name}: FUNCTION({params}) "
                      f"{hal_type(node.return_type)} TEMPLATE;")
        else:
            header = f"{node.name}: PROCEDURE({params}){assign} TEMPLATE;"
        lines = [header]
        for pn in list(node.parameters) + list(node.assign_params):
            lines.append(f"  DECLARE {pn} {hal_type(decls.get(pn.lower()))};")
        lines.append(f"CLOSE {node.name};")
        return banner + "\n".join(lines) + "\n"
    if isinstance(node, CompoolNode):
        lines = [f"{node.name}: COMPOOL;"]
        for d in node.declarations:
            for ident in d.identifiers:
                lines.append(f"  DECLARE {ident} {hal_type(d.type_spec)};")
        lines.append(f"CLOSE {node.name};")
        return banner + "\n".join(lines) + "\n"
    return None


def reusable_units(ast):
    """Yield the reusable units (procedures, functions, compools) in an AST,
    including procedures nested inside a program."""
    if ast is None:
        return
    units = ast.units if isinstance(ast, CompilationNode) else [ast]
    for u in units:
        if isinstance(u, (CompoolNode, ProcedureNode)):
            yield u
        for proc in getattr(u, "procedures", []):
            yield proc


def write_templates(ast, dirpath) -> list:
    """Write a <NAME>.tpl for each reusable unit into `dirpath` (the TEMPLIB).
    Returns the list of written file paths."""
    d = Path(dirpath)
    d.mkdir(parents=True, exist_ok=True)
    written = []
    for unit in reusable_units(ast):
        text = render_template(unit)
        if text is None:
            continue
        path = d / f"{unit.name.upper()}.tpl"
        path.write_text(text, encoding="utf-8")
        written.append(str(path))
    return written
