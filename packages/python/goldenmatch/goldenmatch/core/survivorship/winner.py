"""Group-winner selection for lock-step field-group survivorship. Spec section 3.2/4.2."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GroupResult:
    winner_pos: int
    values: dict[str, Any]  # {column: pinned value (may be None)}
    confidence: float
    tie: bool
    filled: dict[str, int] = field(default_factory=dict)


def _populated(row, cols) -> int:
    return sum(1 for c in cols if row.get(c) is not None)


def _ranking(rows, columns, strategy, *, source_priority=None, dates=None, anchor=None):
    """Return (order, tie) where order is a list of row indices from best to worst."""
    n = len(rows)
    if strategy == "source_priority":
        rank = {s: i for i, s in enumerate(source_priority or [])}
        order = sorted(range(n), key=lambda i: rank.get(rows[i].get("__source__"), len(rank)))
        return order, False
    if strategy == "most_recent":
        def keyf(i):
            d = dates[i] if dates and i < len(dates) else None
            return (d is not None, d)
        order = sorted(range(n), key=keyf, reverse=True)
        return order, False
    if strategy == "anchor":
        counts = [_populated(rows[i], columns) for i in range(n)]
        present = [rows[i].get(anchor) is not None for i in range(n)]
        order = sorted(range(n), key=lambda i: (present[i], counts[i]), reverse=True)
        w = order[0]
        top_key = (present[w], counts[w])
        tie = sum(1 for i in range(n) if (present[i], counts[i]) == top_key) > 1
        return order, tie
    # most_complete
    counts = [_populated(rows[i], columns) for i in range(n)]
    order = sorted(range(n), key=lambda i: counts[i], reverse=True)
    top = counts[order[0]]
    tie = sum(1 for c in counts if c == top) > 1
    return order, tie


def group_winner(rows, columns, strategy="most_complete", *,
                 source_priority=None, dates=None, anchor=None, allow_fill=False) -> GroupResult:
    """Pick ONE winning row; pin every group column to that row (strict lock-step,
    nulls included unless allow_fill=True). `rows` is a list of dicts carrying
    the group columns. `dates`/source come from the parallel arrays the caller
    slices per cluster."""
    n = len(rows)
    if n == 0:
        return GroupResult(-1, {c: None for c in columns}, 0.0, False)

    ranking, tie = _ranking(rows, columns, strategy,
                            source_priority=source_priority, dates=dates, anchor=anchor)
    best = ranking[0]
    values = {c: rows[best].get(c) for c in columns}
    filled: dict[str, int] = {}
    if allow_fill:
        for c in columns:
            if values[c] is None:
                for j in ranking[1:]:
                    if rows[j].get(c) is not None:
                        values[c] = rows[j].get(c)
                        filled[c] = rows[j].get("__pos__", j)
                        break

    winner_populated = _populated(rows[best], columns)
    base_conf = (winner_populated + len(filled)) / len(columns) if columns else 0.0
    conf = base_conf * 0.7 if tie else base_conf
    return GroupResult(rows[best].get("__pos__", best), values, conf, tie, filled)
