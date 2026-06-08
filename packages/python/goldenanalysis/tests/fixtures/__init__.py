"""Deterministic test fixtures shared across the Python suite (and, later, TS parity).

``customers_small`` is a 20-row frame engineered so every ``frame.summary`` metric
is exercised with a hand-verifiable value:

- rows 0 and 1 are a full-row duplicate => ``duplicate_row_ratio`` = 2/20 = 0.1
- null counts per column: name 4, email 6, city 2, age 10 (of 20)
  => ``null_ratio_mean`` = (0.2 + 0.3 + 0.1 + 0.5) / 4 = 0.275

The ``.parquet`` is committed; ``build_customers_small`` is the deterministic
regenerator (no RNG — fixed literal columns).
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

FIXTURES_DIR = Path(__file__).parent
CUSTOMERS_SMALL = FIXTURES_DIR / "customers_small.parquet"

_NAMES = [
    "Alice", "Alice", "Bob", "Carol", None, "Dave", "Eve", None, "Frank", "Grace",
    "Heidi", None, "Ivan", "Judy", "Karl", None, "Liam", "Mona", "Nina", "Omar",
]  # 4 nulls (idx 4, 7, 11, 15)
_EMAILS = [
    "alice@x.com", "alice@x.com", None, "carol@x.com", "e@x.com", None, "eve@x.com",
    "g@x.com", None, "grace@x.com", None, "h@x.com", "ivan@x.com", None, "karl@x.com",
    "l@x.com", None, "mona@x.com", "nina@x.com", "omar@x.com",
]  # 6 nulls (idx 2, 5, 8, 10, 13, 16)
_CITIES = [
    "NYC", "NYC", "LA", "SF", "SF", "Chicago", "Boston", "Miami", "Denver", None,
    "Seattle", "Austin", "Portland", "Reno", "Tucson", "Mesa", "Provo", "Ogden", None, "Boise",
]  # 2 nulls (idx 9, 18)
_AGES = [
    30, 30, None, None, None, 40, None, 35, None, 28,
    None, 33, None, 45, None, 22, None, 38, None, 50,
]  # 10 nulls


def build_customers_small() -> pl.DataFrame:
    """The canonical 20-row fixture frame (deterministic, no RNG)."""
    return pl.DataFrame(
        {"name": _NAMES, "email": _EMAILS, "city": _CITIES, "age": _AGES},
        schema={"name": pl.String, "email": pl.String, "city": pl.String, "age": pl.Int64},
    )


def ensure_customers_small() -> Path:
    """Write the committed parquet if it's missing; return its path."""
    if not CUSTOMERS_SMALL.exists():
        build_customers_small().write_parquet(CUSTOMERS_SMALL)
    return CUSTOMERS_SMALL
