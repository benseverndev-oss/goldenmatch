"""SCD / temporal dimension detection (PR-15).

Flag a Slowly-Changing-Dimension (Type 2) table: validity columns (`valid_from`/
`valid_to`, `effective_*`, `start_date`/`end_date`, `from_date`/`to_date`) and/or an
`is_current`/`is_active` flag NAME-propose it, then a STRUCTURE check confirms it is
really versioned — a business key that repeats (multiple versions) but is unique combined
with the validity anchor `(business_key, valid_from)`. Deterministic, default-on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_FROM_HINTS = ("valid_from", "effective_from", "effective_date", "start_date",
               "from_date", "date_from", "begin_date", "valid_start")
_TO_HINTS = ("valid_to", "effective_to", "end_date", "to_date", "date_to",
             "expiry_date", "expiration_date", "valid_end")
_CURRENT_HINTS = ("is_current", "is_active", "current_flag", "active_flag")
_KEY_HINTS = ("id", "key", "code", "number")


@dataclass(frozen=True)
class SCDDimension:
    """A detected Slowly-Changing-Dimension table (Type 2): the `business_key` versions
    over `valid_from`/`valid_to`, with an optional `current_flag`."""

    table: str
    business_key: str
    valid_from: str | None
    valid_to: str | None
    current_flag: str | None
    scd_type: int = 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "business_key": self.business_key,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "current_flag": self.current_flag,
            "scd_type": self.scd_type,
        }


def _match(col: str, hints: tuple[str, ...]) -> bool:
    c = col.lower()
    return any(h in c for h in hints)


def _distinct(table: Any, col: str) -> int:
    import pyarrow.compute as pc

    return len(pc.unique(table.column(col)))


def _pair_distinct(table: Any, c1: str, c2: str) -> int:
    import pyarrow as pa

    t = pa.table({"a": table.column(c1), "b": table.column(c2)})
    return t.group_by(["a", "b"]).aggregate([]).num_rows


def discover_scd(table: Any, columns: list[str], *, table_name: str = "") -> SCDDimension | None:
    """Return the SCD-Type-2 shape of `table`, or None when it is not a versioned
    dimension. Requires a validity anchor (`valid_from`-style column) to confirm."""
    cols = [c for c in columns if c in table.column_names]
    from_col = next((c for c in cols if _match(c, _FROM_HINTS)), None)
    if from_col is None:  # no validity anchor -> can't structure-confirm versioning
        return None
    to_col = next((c for c in cols if _match(c, _TO_HINTS)), None)
    current_col = next((c for c in cols if _match(c, _CURRENT_HINTS)), None)

    n = table.num_rows
    validity = {from_col, to_col, current_col}
    candidates: list[tuple[str, int]] = []
    for c in cols:
        if c in validity:
            continue
        dc = _distinct(table, c)
        if dc <= 0 or dc >= n:  # unique (a surrogate key) or empty -> not a business key
            continue
        if _pair_distinct(table, c, from_col) == n:  # (bk, valid_from) unique -> versioned
            candidates.append((c, dc))
    if not candidates:
        return None

    # business key: a key-hinted name wins, else the highest-cardinality candidate.
    business_key = max(
        candidates, key=lambda t: (any(h in t[0].lower() for h in _KEY_HINTS), t[1])
    )[0]
    return SCDDimension(table=table_name, business_key=business_key, valid_from=from_col,
                        valid_to=to_col, current_flag=current_col, scd_type=2)
