"""Config-model field names, read from the Pydantic schemas by AST.

Parsed rather than imported: importing goldenmatch.config.schemas pulls the
whole package and its optional extras, and this must run in a bare CI step.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SCHEMAS = REPO / "packages" / "python" / "goldenmatch" / "goldenmatch" / "config" / "schemas.py"


@lru_cache(maxsize=1)
def _config_fields_cached() -> tuple[tuple[str, frozenset[str]], ...]:
    """Immutable cached form of `config_fields`.

    `_annotated_config_names` needs the class list once PER MODULE, and the
    accessor scan walks ~493 of them. Uncached, that re-parsed schemas.py (a
    2,500-class-line file) once per module and pushed the readers tests past a
    two-minute timeout. Returned as tuples because `lru_cache` results must not
    be mutable -- `config_fields` rebuilds the dict for callers.
    """
    return tuple(
        (cls, frozenset(fields)) for cls, fields in _config_fields_uncached().items()
    )


def config_fields() -> dict[str, set[str]]:
    """Map each Pydantic config class to the field names it declares."""
    return {cls: set(fields) for cls, fields in _config_fields_cached()}


def _config_fields_uncached() -> dict[str, set[str]]:
    """The real AST parse. See `config_fields`.

    A field is an annotated assignment at class-body level (`name: type = ...`),
    which is how Pydantic models declare fields. Methods, ClassVars and private
    names are skipped.
    """
    tree = ast.parse(SCHEMAS.read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        names: set[str] = set()
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                name = stmt.target.id
                if name.startswith("_"):
                    continue
                if isinstance(stmt.annotation, ast.Subscript):
                    head = stmt.annotation.value
                    if isinstance(head, ast.Name) and head.id == "ClassVar":
                        continue
                    if isinstance(head, ast.Attribute) and head.attr == "ClassVar":
                        continue
                names.add(name)
        if names:
            out[node.name] = names
    return out
