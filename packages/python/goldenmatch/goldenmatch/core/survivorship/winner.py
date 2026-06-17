"""Group-winner selection for lock-step field-group survivorship. Spec section 3.2/4.2."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GroupResult:
    winner_pos: int
    values: dict           # {column: pinned value (may be None)}
    confidence: float
    tie: bool


def _populated(row, cols) -> int:
    return sum(1 for c in cols if row.get(c) is not None)


def group_winner(rows, columns, strategy="most_complete", *,
                 source_priority=None, dates=None) -> GroupResult:
    """Pick ONE winning row; pin every group column to that row (strict lock-step,
    nulls included). `rows` is a list of dicts carrying the group columns.
    `dates`/source come from the parallel arrays the caller slices per cluster."""
    n = len(rows)
    if n == 0:
        return GroupResult(-1, {c: None for c in columns}, 0.0, False)

    if strategy == "source_priority":
        rank = {s: i for i, s in enumerate(source_priority or [])}
        best = min(range(n), key=lambda i: rank.get(rows[i].get("__source__"), len(rank)))
        tie = False
    elif strategy == "most_recent":
        def keyf(i):
            d = dates[i] if dates and i < len(dates) else None
            return (d is not None, d)
        best = max(range(n), key=keyf)
        tie = False
    else:  # most_complete
        counts = [_populated(rows[i], columns) for i in range(n)]
        top = max(counts)
        winners = [i for i in range(n) if counts[i] == top]
        best = winners[0]
        tie = len(winners) > 1

    populated = _populated(rows[best], columns)
    base_conf = populated / len(columns) if columns else 0.0
    conf = base_conf * 0.7 if tie else base_conf
    values = {c: rows[best].get(c) for c in columns}
    return GroupResult(rows[best].get("__pos__", best), values, conf, tie)
