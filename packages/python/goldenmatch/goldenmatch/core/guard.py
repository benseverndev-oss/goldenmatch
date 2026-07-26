"""Guarded / conditional matchkeys — pair-predicate evaluation.

Spec: docs/superpowers/specs/2026-07-26-guarded-matchkeys-design.md

A guard is a pair-level predicate that gates whether a matchkey (or, on a
weighted matchkey, a single field) fires for a candidate pair. It references
both records via ``a_<col>`` (left/first) and ``b_<col>`` (right/second) and is
evaluated with the EXISTING ``when:`` mini-language (``eval_predicate`` in
``core/survivorship/conditions.py``) — no second predicate dialect, no ``eval``,
AST-allowlist only. When the guard is False (or a "miss"), the matchkey does not
emit that pair; the pair can still match via another matchkey (a per-matchkey
pre-filter, not a global veto).

The naming is ``a_``/``b_`` prefixes over the raw column names. ``validate_guard``
enforces at config time that every referenced name is so prefixed and resolves
to a real column, so a bare/typo'd name is a loud config error rather than a
silent always-miss that would quietly disable the matchkey.
"""
from __future__ import annotations

from goldenmatch.core.survivorship.conditions import (
    PredicateError,
    eval_predicate,
    referenced_names,
)

_A = "a_"
_B = "b_"


class GuardError(ValueError):
    """Raised when a guard expression is malformed or references bad names."""


def guard_columns(expr: str) -> set[str]:
    """Underlying (unprefixed) columns a guard references.

    Strips the ``a_``/``b_`` prefix off each referenced name. Raises
    :class:`GuardError` if the expression doesn't parse or a name lacks the
    prefix (which would silently miss).
    """
    try:
        names = referenced_names(expr)
    except (PredicateError, SyntaxError, ValueError) as exc:
        raise GuardError(f"invalid guard expression {expr!r}: {exc}") from exc
    cols: set[str] = set()
    for name in names:
        if name.startswith(_A):
            cols.add(name[len(_A):])
        elif name.startswith(_B):
            cols.add(name[len(_B):])
        else:
            raise GuardError(
                f"guard name {name!r} in {expr!r} must be prefixed 'a_' (left "
                "record) or 'b_' (right record); a bare name never resolves and "
                "would silently disable the matchkey"
            )
    return cols


def validate_guard(expr: str, columns: set[str] | None = None) -> None:
    """Config-time validation: parse + require a_/b_ prefixes (+ known columns).

    When ``columns`` is given (the frame's column set), every referenced
    underlying column must exist. When it is ``None`` (columns not yet known at
    validation time), only the parse + prefix check runs.
    """
    cols = guard_columns(expr)
    if columns is not None:
        missing = sorted(c for c in cols if c not in columns)
        if missing:
            raise GuardError(
                f"guard {expr!r} references column(s) not in the data: {missing} "
                f"(available: {sorted(columns)})"
            )


def pair_resolved(cols: set[str], row_a: dict, row_b: dict) -> dict:
    """Build the ``a_<col>``/``b_<col>`` binding dict for ``eval_predicate``.

    Only the guard's referenced ``cols`` are bound (both sides). A column absent
    from a record is simply not bound → ``eval_predicate`` treats the reference
    as a miss (clause does not fire), the intended fail-safe.
    """
    resolved: dict = {}
    for col in cols:
        if col in row_a:
            resolved[_A + col] = row_a[col]
        if col in row_b:
            resolved[_B + col] = row_b[col]
    return resolved


def guard_passes(expr: str, cols: set[str], row_a: dict, row_b: dict) -> bool:
    """Evaluate a guard for one pair. ``cols`` = ``guard_columns(expr)`` (cache it
    once per matchkey; it never changes across pairs)."""
    return eval_predicate(expr, pair_resolved(cols, row_a, row_b))
