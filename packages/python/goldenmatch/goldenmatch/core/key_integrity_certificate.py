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
from typing import Any


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
    # 95% Wilson score interval on the fragmentation rate (= undercount), a
    # binomial proportion over `resolved_entities` observations. Bounds the
    # SAMPLING uncertainty in `undercount_estimate` (few resolved entities → wide
    # interval); it does NOT bound whether ER itself clustered correctly. None
    # when resolution wasn't run or found no multi-member clusters.
    undercount_ci_low: float | None = None
    undercount_ci_high: float | None = None

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

    @property
    def safe_bound_conservative(self) -> float | None:
        """Statistically-conservative trust floor: like `safe_bound` but discounts
        the WORST plausible undercount at the 95% confidence upper bound
        (`undercount_ci_high`) rather than the point estimate — so a fragmentation
        rate measured from few resolved entities (wide interval) is penalized more
        than one measured from many. Falls back to `safe_bound` when the interval
        wasn't computed (resolution not run / no multi-member clusters)."""
        if self.undercount_ci_high is None:
            return self.safe_bound
        return min(self.estimate, 1.0 - self.undercount_ci_high)

    def is_trustworthy(self, *, max_fan_out: float = 1.0, min_estimate: float = 1.0) -> bool:
        """Advisory pass/fail: the declared key is unique at grain, doesn't fan
        out beyond `max_fan_out`, and clears `min_estimate`. Never enforced —
        callers decide what to do with it."""
        return (
            self.is_unique_at_grain
            and self.max_fan_out <= max_fan_out
            and self.estimate >= min_estimate
        )


def certificate_verdict(certificate: Any) -> dict[str, Any]:
    """Project a `KeyIntegrityCertificate` into the trust-verdict metadata block a
    semantic catalog carries — the single source for the `key_integrity` tag the
    MetricFlow / Cube / OSI emitters write back onto the catalog the semantic layer
    reads. "Resolve once, the verdict travels with the join."

    Duck-typed (getattr) so a stats-only certificate-like object degrades
    gracefully: missing structural fields → None / omitted, so an older or partial
    certificate still yields a well-formed block. Insertion order is fixed so the
    emitted block is stable across surfaces (the TS `certificateVerdict` mirror
    produces the same shape).

    The block is a superset of the legacy 3-field embed
    (`uniqueness_estimate` / `max_fan_out` / `undercount_estimate`) — those keys are
    retained — extended with the advisory pass/fail `verdict`, `unique_at_grain`,
    per-measure fan-out, and the resolution-tier trust floors (`safe_bound` and the
    CI-discounted `safe_bound_conservative`, with the 95% undercount interval).
    """
    trustworthy: bool | None = None
    is_trustworthy = getattr(certificate, "is_trustworthy", None)
    if callable(is_trustworthy):
        try:
            trustworthy = bool(is_trustworthy())
        except Exception:
            trustworthy = None

    block: dict[str, Any] = {
        "verdict": None
        if trustworthy is None
        else ("trustworthy" if trustworthy else "untrustworthy"),
        "unique_at_grain": getattr(certificate, "is_unique_at_grain", None),
        "uniqueness_estimate": getattr(certificate, "estimate", None),
        "max_fan_out": getattr(certificate, "max_fan_out", None),
    }
    measure_fan_out = getattr(certificate, "measure_fan_out", None)
    if measure_fan_out:
        block["measure_fan_out"] = dict(measure_fan_out)
    block["undercount_estimate"] = getattr(certificate, "undercount_estimate", None)
    ci_low = getattr(certificate, "undercount_ci_low", None)
    ci_high = getattr(certificate, "undercount_ci_high", None)
    if ci_low is not None and ci_high is not None:
        block["undercount_ci"] = [ci_low, ci_high]
    block["safe_bound"] = getattr(certificate, "safe_bound", None)
    block["safe_bound_conservative"] = getattr(certificate, "safe_bound_conservative", None)
    return block
