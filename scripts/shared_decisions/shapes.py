"""What DECISION does each reader of a shared config field make?

`readers.py` answers "which modules touch this field". That is the inventory.
It does not say whether those modules had to AGREE about anything, and most do
not: five modules iterating `config.blocking.keys` share a field but no
decision.

A decision is a reader supplying something the FIELD DOES NOT CARRY:

  * a FALLBACK value, when the field is unset -- `cfg.x or <value>`
  * a THRESHOLD to compare against -- `cfg.x >= <value>`

Two modules supplying DIFFERENT ones is the 1c843c8a5 shape, and it is what
this module reports.

WHAT IS DELIBERATELY NOT A SIGNAL: comparison against different literals.
`strategy == "static"` here and `strategy == "learned"` there is dispatch on an
enum-ish field, which is what those fields are for. Counting it produced 13
"candidates" of which 10 were that -- `strategy` alone contributed 20 distinct
"decisions" and no defect. Only FALLBACK divergence is reported.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from shared_decisions.readers import (
    _base_chain,
    _chain_looks_like_config,
    _config_look_targets,
    _known_field_names,
    _module_alias_names,
)

# Shapes in which a reader has demonstrably coped with the field being unset or
# empty. A module whose every access is one of these has thought about None.
GUARD_SHAPES = frozenset({"FALLBACK", "TRUTHY", "COMPARE", "ITER"})

# Fields annotated `| None` that a `model_validator(mode="after")` nonetheless
# guarantees non-None on any CONSTRUCTED model, so a plain read is correct and
# the nullable annotation overstates the risk.
#
# `GoldenRulesConfig._validate_default` RAISES unless `default_strategy`
# resolves (directly, or backfilled from `default`), which is why
# `core/golden.py:645`'s `or "most_complete"` is unreachable defence and the
# plain reads in `core/survivorship/` and `identity/survivorship.py` are right.
#
# This set is a judgement recorded by hand, not a derivation: proving a
# validator makes a field total means reasoning about every branch that could
# leave it None, which AST inspection does not do. An entry here is a claim
# that someone read the validator.
VALIDATOR_TOTAL = frozenset({"default_strategy"})

_ITER_BUILTINS = frozenset({"list", "set", "tuple", "len", "sorted", "iter", "any", "all"})


@dataclass(frozen=True)
class Access:
    """One syntactic access to a config field, and the decision it makes."""

    field: str
    module: str
    line: int
    shape: str
    detail: str


def _snippet(node: ast.AST, limit: int = 60) -> str:
    try:
        text = " ".join(ast.unparse(node).split())
    except Exception:  # pragma: no cover - unparse is total on parsed trees
        return "?"
    return text if len(text) <= limit else text[: limit - 1] + "~"


def classify(node: ast.Attribute, parent: ast.AST | None) -> tuple[str, str]:
    """The (shape, detail) of one access, given its parent node.

    FALLBACK's detail is the operand the reader falls back TO, which is the
    thing two modules can disagree about.
    """
    if isinstance(node.ctx, ast.Store):
        return "WRITE", ""
    if isinstance(node.ctx, ast.Del):
        return "DELETE", ""
    if isinstance(parent, ast.BoolOp):
        if isinstance(parent.op, ast.Or):
            values = parent.values
            index = values.index(node) if node in values else -1
            if index >= 0 and index + 1 < len(values):
                return "FALLBACK", _snippet(values[index + 1])
        return "TRUTHY", ""
    if isinstance(parent, ast.Compare):
        if parent.left is node:
            return "COMPARE", f"{type(parent.ops[0]).__name__} {_snippet(parent.comparators[0])}"
        return "COMPARE", f"rhs-of {type(parent.ops[0]).__name__} {_snippet(parent.left)}"
    if isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.Not):
        return "TRUTHY", ""
    if isinstance(parent, (ast.If, ast.While, ast.IfExp)) and parent.test is node:
        return "TRUTHY", ""
    if isinstance(parent, ast.Subscript):
        return ("INDEX", _snippet(parent.slice)) if parent.value is node else ("OTHER", "")
    if isinstance(parent, (ast.For, ast.comprehension)) and parent.iter is node:
        return "ITER", ""
    if isinstance(parent, ast.Attribute):
        return "ATTR_BASE", f".{parent.attr}"
    if isinstance(parent, ast.Call):
        if parent.func is node:
            return "CALLED", ""
        name = _snippet(parent.func, 24)
        return ("ITER" if name in _ITER_BUILTINS else "CALL_ARG"), name
    return type(parent).__name__.upper() if parent is not None else "OTHER", ""


def access_shapes(root: Path) -> list[Access]:
    """Every config-field access under `root`, with its decision shape.

    Accessor detection is `readers.py`'s -- the same base-chain and alias rules
    -- so this cannot report a site the inventory does not, nor miss one it
    does.
    """
    known = _known_field_names()
    targets = _config_look_targets()
    out: list[Access] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="ignore"))
        except SyntaxError:
            continue
        rel = path.relative_to(root).as_posix()
        aliases = _module_alias_names(tree, targets)
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in known:
                continue
            segments = _base_chain(node.value)
            if segments is None or not _chain_looks_like_config(segments, targets, aliases):
                continue
            shape, detail = classify(node, parents.get(node))
            out.append(Access(node.attr, rel, node.lineno, shape, detail))
    return out


def fallback_divergence(accesses: list[Access]) -> dict[str, dict[str, set[str]]]:
    """Fields where more than one module falls back to a DIFFERENT value.

    Both conditions are required. One module writing two different fallbacks in
    two branches is a local choice, not a cross-module disagreement, so the
    distinct fallbacks must be spread over more than one module.
    """
    by_field: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for access in accesses:
        if access.shape == "FALLBACK":
            by_field[access.field][access.detail].add(access.module)
    out: dict[str, dict[str, set[str]]] = {}
    for field, fallbacks in by_field.items():
        modules = {m for group in fallbacks.values() for m in group}
        if len(fallbacks) > 1 and len(modules) > 1:
            out[field] = {value: set(group) for value, group in fallbacks.items()}
    return out


def unguarded_optional(
    accesses: list[Access], nullable: dict[str, set[str]]
) -> dict[str, set[str]]:
    """Nullable fields some module falls back on and another reads bare.

    One reader has thought about None and another has not. Weaker than
    `fallback_divergence`: it does not prove the bare reader can ever SEE None,
    only that nothing in that module says it cannot.

    A module whose only access is a WRITE is not a reader and never has to cope
    with None -- omitting that check flagged `cli/dedupe.py` and `cli/match.py`
    on `format` and `run_name`, where their single access sets the field from a
    command-line flag.
    """
    by_field: dict[str, list[Access]] = defaultdict(list)
    for access in accesses:
        by_field[access.field].append(access)
    out: dict[str, set[str]] = {}
    for field, group in by_field.items():
        if field not in nullable or field in VALIDATOR_TOTAL:
            continue
        modules = {access.module for access in group}
        if len(modules) < 2:
            continue
        with_fallback = {a.module for a in group if a.shape == "FALLBACK"}
        if not with_fallback:
            continue
        bare: set[str] = set()
        for module in modules:
            shapes = {a.shape for a in group if a.module == module}
            if shapes <= {"WRITE"}:
                continue
            if not (shapes & GUARD_SHAPES):
                bare.add(module)
        if bare and (with_fallback - bare):
            out[field] = bare
    return out


def nullable_fields(schemas: Path) -> dict[str, set[str]]:
    """Field name -> the config classes that annotate it `| None`."""
    tree = ast.parse(schemas.read_text(encoding="utf-8-sig"))
    out: dict[str, set[str]] = defaultdict(set)
    for cls in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
        for body in cls.body:
            if isinstance(body, ast.AnnAssign) and isinstance(body.target, ast.Name):
                if "None" in ast.unparse(body.annotation):
                    out[body.target.id].add(cls.name)
    return dict(out)


def declaring_classes() -> dict[str, set[str]]:
    """Field name -> the config classes that declare it.

    The inventory is keyed by field NAME, because an access
    (`cfg.blocking.transforms`) does not say which class the object is. When
    one name is declared on several classes, readers grouped under it may be
    reading DIFFERENT fields, and a "divergence" between them can be no
    divergence at all: `transforms` is declared on `BlockingKeyConfig`,
    `MatchkeyField`, `NegativeEvidenceField` and `SortKeyField`, and the
    `or ["lowercase", "strip"]` fallback that made it look divergent is a
    `MatchkeyField`, compared against ten `BlockingKeyConfig` readers.

    So a signal on a single-class field is ACTIONABLE, and a signal on a
    multi-class field needs class resolution before it means anything. Both
    confirmed findings -- `golden_rules` and `passes` -- are single-class.
    """
    from shared_decisions.fields import config_fields

    out: dict[str, set[str]] = defaultdict(set)
    for cls, fields in config_fields().items():
        for field in fields:
            out[field].add(cls)
    return dict(out)


def split_by_ambiguity(fields: set[str]) -> tuple[set[str], set[str]]:
    """(actionable, ambiguous) -- declared on exactly one class, or more."""
    declared = declaring_classes()
    ambiguous = {f for f in fields if len(declared.get(f, set())) > 1}
    return fields - ambiguous, ambiguous
