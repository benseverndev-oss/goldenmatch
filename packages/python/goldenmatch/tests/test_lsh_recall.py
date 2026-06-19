"""Always-on synthetic LSH recall gate (#1081).

Pins a fixed config and asserts the sketch+LSH path recovers near-duplicate
pairs at a real (non-tautological) recall while cutting most of the comparison
work. Deterministic (fixed seed). Measured at authoring time: recall 0.978 /
reduction 0.989 -> gate at >= 0.90 / >= 0.95 carries margin against minor drift.

Imports the measurement function from ``scripts/bench_lsh_recall.py`` (single
source of the generator + measurement; no duplicated logic).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Repo-root/scripts is importable regardless of CWD (local pkg-dir vs CI root).
_SCRIPTS = Path(__file__).resolve().parents[4] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from bench_lsh_recall import measure_recall  # noqa: E402

# The pinned gate config. Do NOT retune to chase a number — that defeats the gate.
_GATE = dict(
    num_seed=60, variants=3, edit_rate=0.1, mode="word", k=2, num_perms=128, threshold=0.5, seed=0
)
_MIN_RECALL = 0.90
_MIN_REDUCTION = 0.95


def test_synthetic_recall_gate():
    m = measure_recall(**_GATE)
    assert m["recall"] >= _MIN_RECALL, f"LSH recall regressed: {m}"
    assert m["reduction"] >= _MIN_REDUCTION, f"LSH reduction regressed: {m}"
    # sanity: optimal_bands(128, 0.5) splits into 32 bands of 4 rows
    assert m["num_bands"] == 32 and m["rows_per_band"] == 4


def test_recall_degrades_with_edit_rate_monotonic():
    # Higher corruption -> lower recall (confidence the metric is meaningful,
    # not a constant). All deterministic.
    low = measure_recall(**{**_GATE, "edit_rate": 0.1})["recall"]
    high = measure_recall(**{**_GATE, "edit_rate": 0.3})["recall"]
    assert high < low
