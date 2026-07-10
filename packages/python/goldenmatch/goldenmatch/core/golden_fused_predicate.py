"""Conditional-predicate lowering for the fused golden-record kernel (Stage 6).

A conditional ``field_rules`` entry is a LIST of ``GoldenFieldRule`` clauses,
each with an optional ``when:`` predicate over already-resolved winner values
(``select_conditional_strategy`` picks the first clause whose ``when`` holds,
else the when-less default). To evaluate those predicates inside the Rust kernel
(which works in CODE space, never raw values), Python:

1. reuses ``core/survivorship/conditions.py`` to parse + validate the predicate
   (its vetted AST allowlist), and
2. lowers the validated tree to a small RPN IR the kernel evaluates against the
   already-resolved winner CODE of each referenced column.

Covered subset (v1): boolean ``and`` / ``or`` / ``not``, equality ``==`` / ``!=``,
and membership ``in`` / ``not in`` over a list/tuple of literals, with the left
operand a bare column name and the right a literal (or list of literals).

DECLINED (``predicate_lowerable`` returns False, so ``golden_fused_ready``
declines the whole config to the classic path): ordering comparators
``<`` / ``<=`` / ``>`` / ``>=`` (they need a numeric value lane the v1 kernel
does not carry), reversed / chained comparisons, function calls, attribute
access, bare names/constants, and anything the ``conditions.py`` allowlist
rejects. Loud fall-through, never silent.

Code-space literal resolution (done in ``lower_predicate`` via a ``code_of``
callback bound to the referenced column's factorization map):
- a literal ``None`` -> the null code ``-1`` (so ``x == None`` reproduces the
  reference's ``value == None`` -- a null winner's code is ``-1``);
- a present literal -> that column's factorization code;
- a literal ABSENT from the column -> the ``_ABSENT_CODE`` sentinel (``-2``),
  which equals no present code and differs from the null code, so ``col == "X"``
  with ``"X"`` absent is False and ``col != "X"`` is True -- byte-identical to the
  reference comparing the winner VALUE to the literal.

Miss semantics match ``conditions.eval_predicate`` exactly: a comparison whose
referenced name is not a resolvable column is a MISS; a MISS propagates through
``not`` and is treated as a False arm inside ``and`` / ``or`` and at the top
level (the clause does not fire). For the covered subset the ONLY miss source is
an unknown name -- ``==`` / ``!=`` / ``in`` / ``not in`` never raise on ``None`` or
mismatched types, unlike the ordering comparators (which is part of why those are
declined).
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from goldenmatch.core.survivorship.conditions import PredicateError, _validate

# ── IR opcodes (shared with the Rust kernel's `PredInstr.op`) ────────────────
OP_EQ = 0
OP_NE = 1
OP_IN = 2
OP_NOT_IN = 3
OP_AND = 4
OP_OR = 5
OP_NOT = 6
OP_MISS = 7

# Sentinel for a literal not present in the referenced column's value set. Any
# present code is >= 0 and the null code is -1, so -2 collides with neither: it
# makes `col == <absent>` False and `col != <absent>` True, matching the
# reference comparing the winner value to a literal it never equals.
_ABSENT_CODE = -2


@dataclass
class PredInstr:
    """One RPN instruction (a Rust ``#[derive(FromPyObject)]`` struct reads these
    attrs). ``op`` is an ``OP_*`` code; ``col_index`` is the referenced column's
    output-column index for the comparison ops (``-1`` unused); ``codes`` holds
    the literal code(s) (one for ``EQ`` / ``NE``, the list for ``IN`` / ``NOT_IN``);
    ``arity`` is the operand count popped by ``AND`` / ``OR`` (``NOT`` always pops 1).
    """

    op: int
    col_index: int = -1
    codes: list[int] = field(default_factory=list)
    arity: int = 0


def _parse_body(when: str) -> ast.AST:
    """Parse + validate ``when`` via the vetted ``conditions.py`` allowlist and
    return the inner expression body. Raises ``PredicateError`` / ``SyntaxError``
    on a disallowed / malformed predicate."""
    tree = ast.parse(when, mode="eval")
    _validate(tree)  # PredicateError on any node outside the allowlist
    return tree.body


def _lowerable_node(node: ast.AST) -> bool:
    """True iff ``node`` maps onto the kernel IR (see module docstring)."""
    if isinstance(node, ast.BoolOp):
        return isinstance(node.op, (ast.And, ast.Or)) and all(
            _lowerable_node(v) for v in node.values
        )
    if isinstance(node, ast.UnaryOp):
        # Only boolean `not` lowers; unary +/- (numeric) has no code-space home.
        return isinstance(node.op, ast.Not) and _lowerable_node(node.operand)
    if isinstance(node, ast.Compare):
        # Single comparison, `Name <op> literal(s)` only. Chained (`a < b < c`)
        # and reversed (`"x" == a`) forms decline.
        if len(node.ops) != 1 or not isinstance(node.left, ast.Name):
            return False
        op = node.ops[0]
        comp = node.comparators[0]
        if isinstance(op, (ast.Eq, ast.NotEq)):
            return isinstance(comp, ast.Constant)
        if isinstance(op, (ast.In, ast.NotIn)):
            return isinstance(comp, (ast.List, ast.Tuple)) and all(
                isinstance(e, ast.Constant) for e in comp.elts
            )
        # Ordering comparators (<, <=, >, >=) are DECLINED in v1.
        return False
    # Bare Name / Constant / anything else does not lower.
    return False


def predicate_lowerable(when: Any) -> bool:
    """True iff every node of the ``when:`` predicate lowers to the kernel IR.

    Returns False (decline) for a non-string, a parse/validate failure, or any
    unsupported construct (ordering comparators, function calls, attribute
    access, reversed/chained comparisons, bare names). The gate
    (``golden_fused_ready``) declines the whole config when this is False.
    """
    if not isinstance(when, str):
        return False
    try:
        body = _parse_body(when)
    except (PredicateError, SyntaxError, ValueError):
        return False
    return _lowerable_node(body)


def _lower_node(
    node: ast.AST,
    col_index_of: dict[str, int],
    code_of: Callable[[str, Any], int],
    out: list[PredInstr],
) -> None:
    """Post-order (RPN) emit of ``node`` into ``out``."""
    if isinstance(node, ast.BoolOp):
        for v in node.values:
            _lower_node(v, col_index_of, code_of, out)
        op = OP_AND if isinstance(node.op, ast.And) else OP_OR
        out.append(PredInstr(op=op, arity=len(node.values)))
        return
    if isinstance(node, ast.UnaryOp):  # ast.Not (guaranteed by _lowerable_node)
        _lower_node(node.operand, col_index_of, code_of, out)
        out.append(PredInstr(op=OP_NOT))
        return
    if isinstance(node, ast.Compare):
        name = node.left.id  # type: ignore[attr-defined]
        op = node.ops[0]
        comp = node.comparators[0]
        if name not in col_index_of:
            # Unknown name -> the reference's eval_predicate raises _Miss here.
            out.append(PredInstr(op=OP_MISS))
            return
        ci = col_index_of[name]
        if isinstance(op, ast.Eq):
            out.append(PredInstr(op=OP_EQ, col_index=ci, codes=[code_of(name, comp.value)]))
        elif isinstance(op, ast.NotEq):
            out.append(PredInstr(op=OP_NE, col_index=ci, codes=[code_of(name, comp.value)]))
        elif isinstance(op, ast.In):
            out.append(
                PredInstr(
                    op=OP_IN,
                    col_index=ci,
                    codes=[code_of(name, e.value) for e in comp.elts],
                )
            )
        elif isinstance(op, ast.NotIn):
            out.append(
                PredInstr(
                    op=OP_NOT_IN,
                    col_index=ci,
                    codes=[code_of(name, e.value) for e in comp.elts],
                )
            )
        return
    # _lowerable_node guarantees we never reach here for a lowerable predicate.
    raise PredicateError(f"cannot lower node {type(node).__name__}")


def lower_predicate(
    when: str,
    col_index_of: dict[str, int],
    code_of: Callable[[str, Any], int],
) -> list[PredInstr]:
    """Lower a validated, lowerable ``when:`` predicate to the RPN IR.

    ``col_index_of`` maps a referenced column name to its output-column index;
    ``code_of(col_name, literal)`` resolves a literal to that column's code space
    (``-1`` for ``None``, the factorization code for a present value, ``_ABSENT_CODE``
    for an absent one). A reference to a name not in ``col_index_of`` lowers to a
    single ``MISS`` instruction (matching ``eval_predicate``'s unknown-name miss).
    """
    body = _parse_body(when)
    out: list[PredInstr] = []
    _lower_node(body, col_index_of, code_of, out)
    return out
