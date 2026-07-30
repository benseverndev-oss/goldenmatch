"""Metric-aware key-integrity certificate (semantic-layer wedge).

A semantic layer (dbt/MetricFlow, Cube, OSI) is a join graph, and every join
runs on entity-key equality. Measures are defined *relative to* an entity, so a
metric is only correct if the declared key genuinely, uniquely identifies one
real-world entity. These layers ASSUME that — they don't resolve it.

`KeyIntegrityCertificate` is the advisory artifact GoldenMatch emits over a
declared key: is it unique at grain, how much does a duplicated key inflate a
`SUM`/`COUNT(DISTINCT)` (fan-out), and — optionally, via entity resolution —
would distinct declared keys collapse onto one real entity (fragmentation /
undercount). It never mutates a number; it reports and quantifies.

Mirrors the shape + normalization contract of `RecallCertificate`
(`core/recall_certificate.py`): a duck-typed `{estimate, safe_bound}` reading so
a downstream reporter (goldenanalysis `key.integrity`) can consume it the same
way `match.rates` consumes a recall certificate.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class KeyIntegrityCertificate:
    """Advisory certificate for a declared entity key.

    Structural fields are always populated. The resolution fields
    (`resolved_entities` / `fragmented_entities` / `undercount_estimate`) are
    populated only when `resolve=True` was requested AND entity resolution was
    measurable; otherwise they are None and `note` explains why.
    """

    key_columns: list[str]
    grain: list[str] | None
    n_rows: int
    n_key_groups: int                       # distinct key(-at-grain) tuples
    is_unique_at_grain: bool                 # n_key_groups == n_rows
    duplicate_key_groups: int                # key groups with >1 row
    max_fan_out: float                       # worst-case row multiplicity for a key group
    measure_fan_out: dict[str, float] = field(default_factory=dict)  # per-measure SUM inflation ratio

    # Resolution tier (opt-in): does ER collapse distinct declared keys?
    resolved_entities: int | None = None     # multi-member clusters found by dedupe
    fragmented_entities: int | None = None   # resolved entities spanning >1 declared key value
    undercount_estimate: float | None = None # fragmented_entities / resolved_entities

    estimable: bool = True
    note: str = ""

    # --- {estimate, safe_bound} normalization contract (see module docstring) ---
    @property
    def estimate(self) -> float:
        """Point score in [0,1]: the fraction of key groups that are clean
        (unique at grain). 1.0 == the declared key is a true key."""
        if self.n_key_groups == 0:
            return 1.0
        return 1.0 - (self.duplicate_key_groups / self.n_key_groups)

    @property
    def safe_bound(self) -> float | None:
        """Conservative score: also discounts entity fragmentation when it was
        measured (a fragmented entity is a silent join defect the structural
        pass can't see). None-collapses to the structural estimate when
        resolution wasn't run."""
        if self.undercount_estimate is None:
            return self.estimate
        return min(self.estimate, 1.0 - self.undercount_estimate)

    def is_trustworthy(self, *, max_fan_out: float = 1.0, min_estimate: float = 1.0) -> bool:
        """Advisory pass/fail: the declared key is unique at grain, doesn't fan
        out beyond `max_fan_out`, and clears `min_estimate`. Never enforced —
        callers decide what to do with it."""
        return (
            self.is_unique_at_grain
            and self.max_fan_out <= max_fan_out
            and self.estimate >= min_estimate
        )
