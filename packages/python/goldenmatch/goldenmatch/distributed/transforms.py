"""Serializable transform plans for distributed pipelines.

Replaces closure-based transforms from `core/transform.py`. Closures don't
round-trip cleanly across Ray worker boundaries (cloudpickle works but the
cost is high and closed-over state is silently shared). Plan objects are
frozen dataclasses with stable fields -- trivial to serialize and inspect.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import polars as pl

Op = Literal["lower", "upper", "strip_punctuation", "strip", "nfkc"]


@dataclass(frozen=True)
class TransformPlan:
    column: str
    op: Op
    target: str | None = None


def apply_plan(df: pl.DataFrame, plan: TransformPlan) -> pl.DataFrame:
    src = pl.col(plan.column).cast(pl.Utf8)
    if plan.op == "lower":
        expr = src.str.to_lowercase()
    elif plan.op == "upper":
        expr = src.str.to_uppercase()
    elif plan.op == "strip":
        expr = src.str.strip_chars()
    elif plan.op == "strip_punctuation":
        expr = src.str.replace_all(r"[^\w\s]", "")
    elif plan.op == "nfkc":
        expr = src.str.normalize("NFKC")
    else:
        raise ValueError(f"unknown op: {plan.op}")
    out_col = plan.target or plan.column
    return df.with_columns(expr.alias(out_col))
