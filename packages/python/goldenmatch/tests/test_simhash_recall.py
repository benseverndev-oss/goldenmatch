"""Always-on synthetic SimHash recall gate (#1082).

Pins a fixed config and asserts the SimHash/LSH semantic-blocking path recovers
near-duplicate (cosine-near) vector pairs at a real (non-tautological) recall
while cutting most of the comparison work. Deterministic (fixed seed). Measured
at authoring time: recall 1.0 / reduction 0.86 -> gate at >= 0.95 / >= 0.70
carries comfortable margin against minor drift.

Imports the measurement function from ``scripts/bench_simhash_recall.py`` (single
source of the generator + measurement; no duplicated logic). Mirrors the MinHash
gate in ``test_lsh_recall.py``.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Repo-root/scripts is importable regardless of CWD (local pkg-dir vs CI root).
_SCRIPTS = Path(__file__).resolve().parents[4] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from bench_simhash_recall import measure_simhash_recall  # noqa: E402

# The pinned gate config. num_bands=32 is the auto-config routing default (NOT
# 16). Do NOT retune to chase a number — that defeats the gate.
_GATE = dict(
    num_seed=60, variants=3, noise=0.3, dim=64, num_planes=256, num_bands=32, seed=1
)
_MIN_RECALL = 0.95
_MIN_REDUCTION = 0.70


def test_synthetic_simhash_recall_gate():
    m = measure_simhash_recall(**_GATE)
    assert m["recall"] >= _MIN_RECALL, f"SimHash recall regressed: {m}"
    assert m["reduction"] >= _MIN_REDUCTION, f"SimHash reduction regressed: {m}"
    assert m["num_bands"] == 32


def test_simhash_recall_degrades_with_noise_monotonic():
    # Higher noise -> not-higher recall (confidence the metric is meaningful,
    # not a constant). All deterministic.
    low = measure_simhash_recall(**{**_GATE, "noise": 0.3})["recall"]
    high = measure_simhash_recall(**{**_GATE, "noise": 0.9})["recall"]
    assert high <= low
