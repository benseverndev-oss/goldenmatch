"""Build the cheap data-shape facts the lint rules consult.

Only the columns the config actually references are profiled (cardinality +
null rate), so this is a single light pass — not the full auto-config profiler.
"""
from __future__ import annotations

from typing import Any

from goldenmatch.core.config_lint.registry import LintInput
from goldenmatch.core.config_lint.rules import referenced_columns


def build_lint_input(df: Any, config: object, *, available_ram_gb: float | None = None) -> LintInput:
    cols = referenced_columns(config) & set(df.columns)
    n = int(df.height)
    cardinality_ratio: dict[str, float] = {}
    null_rate: dict[str, float] = {}
    for c in cols:
        s = df.get_column(c)
        non_null = s.drop_nulls()
        n_nn = int(non_null.len())
        cardinality_ratio[c] = (int(non_null.n_unique()) / n_nn) if n_nn else 0.0
        null_rate[c] = (1 - (n_nn / n)) if n else 0.0
    return LintInput(
        row_count=n,
        cardinality_ratio=cardinality_ratio,
        null_rate=null_rate,
        col_type={},
        available_ram_gb=available_ram_gb,
    )
