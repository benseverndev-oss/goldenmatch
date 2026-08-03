"""Metrics derivation (PR-12).

Turns the grain-gated measures into certifiable business metrics. A metric is only
proposed when the grain is trustworthy (the measures are `safe_to_sum`), so the ratios
can't silently double-count:

  * **average** — `avg_<m> = SUM(m) / COUNT(grain)` per sum-safe measure (always
    meaningful at a clean grain; MetricFlow's canonical *ratio* metric).
  * **ratio** — `<m1>_per_<m2> = SUM(m1) / SUM(m2)` per sum-safe measure PAIR
    (pool-capped so the pair count stays C(cap, 2), not combinatorial on wide facts).

Deterministic, default-on. *Derived semantic* metrics (`profit = revenue - cost`) need
to know which measure is revenue vs cost, so they stay a namer/advisory follow-on.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any

# Cap on the sum-safe measure pool the ratio search pairs over.
_METRIC_PAIR_POOL_CAP = 5


@dataclass(frozen=True)
class Metric:
    """A proposed business metric. `denominator` is `None` for an average (the
    denominator is `COUNT(grain)`); `expression` is the human-readable formula."""

    name: str
    kind: str  # average | ratio
    numerator: str
    denominator: str | None
    expression: str
    table: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "expression": self.expression,
            "table": self.table,
        }


def discover_metrics(
    measures: list[Any],
    grain: list[str],
    *,
    table_name: str = "",
) -> list[Metric]:
    """Propose average + ratio metrics from the sum-safe `measures` at `grain`.

    Returns `[]` when nothing is sum-safe (a fanned-out / untrustworthy grain — a ratio
    there would double-count) or the grain is empty.
    """
    sum_safe = [m.column for m in measures if getattr(m, "safe_to_sum", False)]
    if not sum_safe or not grain:
        return []
    grain_key = grain[0]

    out: list[Metric] = []
    for col in sum_safe:
        out.append(Metric(
            name=f"avg_{col}", kind="average", numerator=col, denominator=None,
            expression=f"SUM({col}) / COUNT({grain_key})", table=table_name,
        ))

    pool = sorted(sum_safe)[:_METRIC_PAIR_POOL_CAP]
    for c1, c2 in itertools.combinations(pool, 2):
        out.append(Metric(
            name=f"{c1}_per_{c2}", kind="ratio", numerator=c1, denominator=c2,
            expression=f"SUM({c1}) / SUM({c2})", table=table_name,
        ))
    return out
