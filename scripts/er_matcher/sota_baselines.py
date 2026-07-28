#!/usr/bin/env python3
"""Pure lookup table of published SOTA F1 numbers for ER benchmarks (SP3 Task 2).

These are widely-cited reference figures reported on each benchmark's OWN
test split in the DeepMatcher and Ditto papers. They exist purely for the
zero-shot eval's display-only comparison column -- a human-readable "how
does this compare to what the literature reports" data point -- and are
NOT a gate threshold. The zero-shot eval measures the ER-matcher model on
data it hasn't seen; these baselines were trained/tuned on the benchmark's
own train split, so the comparison is illustrative, not apples-to-apples.

Stdlib only (a dict), box-safe (no torch/network).
"""
from __future__ import annotations

SOTA: dict[str, dict] = {
    "walmart_amazon": {
        "deepmatcher_f1": 0.669,
        "ditto_f1": 0.868,
        "source": "DeepMatcher (Mudgal+ 2018) / Ditto (Li+ 2020)",
    },
    "beer": {
        "deepmatcher_f1": 0.727,
        "ditto_f1": 0.944,
        "source": "DeepMatcher (Mudgal+ 2018) / Ditto (Li+ 2020)",
    },
    # Fodors-Zagats is a near-saturated "Structured" benchmark -- both papers
    # report (effectively) perfect F1 on its own test split. Display-only,
    # not a gate: these numbers carry no discriminating signal.
    "fodors_zagats": {
        "deepmatcher_f1": 1.0,
        "ditto_f1": 1.0,
        "source": "DeepMatcher (Mudgal+ 2018) / Ditto (Li+ 2020)",
    },
}


def sota_for(name: str) -> dict | None:
    """Return the SOTA baseline entry for ``name``, or ``None`` if unknown."""
    return SOTA.get(name)
