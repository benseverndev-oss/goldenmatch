"""Cardinality (PR-14): many-to-many bridge / junction detection.

A junction table (a trustworthy COMPOUND key of exactly two columns, each a certified
FK to a DIFFERENT table) realizes a many-to-many relationship between the two entities.
Reuses the compound keys (PR-10) + the certified join graph (PR-3). The complementary
one-to-one refinement lives in `discover_joins` (an FK unique on the from side).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Bridge:
    """A many-to-many bridge: `bridge_table` joins `left_table` and `right_table` on its
    two FK columns."""

    bridge_table: str
    left_table: str
    left_column: str
    right_table: str
    right_column: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "bridge_table": self.bridge_table,
            "left_table": self.left_table,
            "left_column": self.left_column,
            "right_table": self.right_table,
            "right_column": self.right_column,
        }


def discover_bridges(proposed_tables: list[Any], joins: list[Any]) -> list[Bridge]:
    """Junction tables among `proposed_tables`: a trustworthy 2-column compound key whose
    columns are trustworthy FKs to two different tables."""
    fk_map = {(j.from_table, j.from_column): j.to_table
              for j in joins if getattr(j, "is_trustworthy", False)}

    out: list[Bridge] = []
    for pt in proposed_tables:
        k = pt.key
        if k is None or not k.is_trustworthy or len(k.columns) != 2:
            continue
        c1, c2 = k.columns
        t1 = fk_map.get((pt.table, c1))
        t2 = fk_map.get((pt.table, c2))
        if t1 and t2 and t1 != t2:
            out.append(Bridge(bridge_table=pt.table, left_table=t1, left_column=c1,
                              right_table=t2, right_column=c2))
    out.sort(key=lambda b: (b.bridge_table, b.left_table, b.right_table))
    return out
