"""Conditional-predicate lowering for the fused golden-record kernel.

TEMPORARY Stage-0 stub: every `when:` predicate is treated as NOT lowerable, so
`golden_fused_ready` declines any list-form conditional field_rule. Stage 6
replaces this with a real AST->IR lowering (reusing
`core/survivorship/conditions.py` parsing).
"""

from __future__ import annotations

from typing import Any


def predicate_lowerable(_expr: Any) -> bool:
    """Stage-0: nothing lowers yet, so decline every conditional predicate."""
    return False
