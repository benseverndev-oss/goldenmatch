"""Compare a current report against a baseline, producing deltas."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_METRIC_KEYS = ("f1", "top1", "mrr", "ece")
_ZERO_METRIC_SET = {k: 0.0 for k in _METRIC_KEYS} | {"n": 0}


@dataclass(frozen=True)
class Delta:
    """A computed delta between two reports (immutable)."""
    overall: dict[str, float] = field(default_factory=dict)
    by_difficulty: dict[str, dict[str, float]] = field(default_factory=dict)
    by_category: dict[str, dict[str, float]] = field(default_factory=dict)
    by_tag: dict[str, dict[str, float]] = field(default_factory=dict)
    per_case_deltas: list[tuple[str, float, float]] = field(default_factory=list)

    def is_regression(self, threshold: float = 0.02) -> bool:
        """True iff overall F1 dropped by *strictly* more than `threshold`.

        Uses a `1e-9` IEEE-754 epsilon guard so a drop exactly equal to
        `threshold` (modulo float rounding) is NOT classified as a regression.
        Mirrors the JS compare.ts `isRegression` implementation.
        """
        f1_delta = self.overall.get("f1", 0.0)
        return f1_delta < -threshold - 1e-9

    def top_movers(
        self,
        n: int = 10,
        threshold: float = 0.05,
    ) -> tuple[
        list[tuple[str, float, float, float]],
        list[tuple[str, float, float, float]],
    ]:
        """Return (regressions, improvements) — top-N cases by |Δf1|."""
        scored = [
            (cid, base, curr, curr - base)
            for cid, base, curr in self.per_case_deltas
        ]
        regressions = sorted(
            [d for d in scored if d[3] <= -threshold],
            key=lambda d: d[3],
        )[:n]
        improvements = sorted(
            [d for d in scored if d[3] >= threshold],
            key=lambda d: -d[3],
        )[:n]
        return (regressions, improvements)


def _metric_delta(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, float]:
    return {
        k: float(current.get(k, 0.0)) - float(baseline.get(k, 0.0))
        for k in _METRIC_KEYS
    }


def _slice_delta(
    baseline_slice: dict[str, Any],
    current_slice: dict[str, Any],
) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    keys = set(baseline_slice) | set(current_slice)
    for k in sorted(keys):
        b = baseline_slice.get(k, _ZERO_METRIC_SET)
        c = current_slice.get(k, _ZERO_METRIC_SET)
        out[k] = _metric_delta(b, c)
    return out


def compute_delta(baseline: dict, current: dict) -> Delta:
    """Diff two report.json dicts, producing a structured Delta.

    Computes per-metric deltas (`current - baseline`) for the overall scorecard
    and every by_* slice, plus per-case F1 triples for cases present in both
    reports. The returned Delta is frozen; all fields are populated at
    construction time.
    """
    baseline_cases = {c["id"]: c for c in baseline.get("per_case", [])}
    current_cases = {c["id"]: c for c in current.get("per_case", [])}
    common = sorted(set(baseline_cases) & set(current_cases))
    return Delta(
        overall=_metric_delta(
            baseline["scorecard"]["overall"], current["scorecard"]["overall"]
        ),
        by_difficulty=_slice_delta(
            baseline["scorecard"].get("by_difficulty", {}),
            current["scorecard"].get("by_difficulty", {}),
        ),
        by_category=_slice_delta(
            baseline["scorecard"].get("by_category", {}),
            current["scorecard"].get("by_category", {}),
        ),
        by_tag=_slice_delta(
            baseline["scorecard"].get("by_tag", {}),
            current["scorecard"].get("by_tag", {}),
        ),
        per_case_deltas=[
            (cid, float(baseline_cases[cid]["f1"]), float(current_cases[cid]["f1"]))
            for cid in common
        ],
    )
