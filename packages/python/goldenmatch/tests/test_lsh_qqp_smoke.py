"""CI-fast smoke test for the QQP recall harness (#1081).

Runs the measurement function end-to-end over the tiny SYNTHETIC, QQP-shaped
sample (``tests/fixtures/qqp_sample.csv`` — not real Quora data). Asserts the
metrics are well-formed, NOT a recall threshold (the sample is far too small to
be meaningful). The real-corpus recall number comes from the bench job
(``bench-lsh-recall.yml`` -> ``scripts/bench_lsh_recall_qqp.py`` on full QQP).
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[4] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from bench_lsh_recall_qqp import load_sample, measure_qqp_recall  # noqa: E402

_SAMPLE = Path(__file__).parent / "fixtures" / "qqp_sample.csv"


def test_qqp_sample_measurement_well_formed():
    pairs = load_sample(_SAMPLE)
    assert pairs and all(isinstance(d, bool) for _, _, d in pairs)

    m = measure_qqp_recall(pairs, mode="word", k=2, num_perms=128, threshold=0.5, seed=0)
    assert 0.0 <= m["recall"] <= 1.0
    assert 0.0 <= m["reduction"] <= 1.0
    assert m["labeled_duplicate_pairs"] > 0
    assert m["num_questions"] > 0
    assert m["precision_on_labeled"] is None or 0.0 <= m["precision_on_labeled"] <= 1.0


def test_qqp_sample_recovers_obvious_duplicates():
    # The synthetic duplicates are near-identical, so a permissive config should
    # recover most of them — a sanity check that the harness isn't inert.
    pairs = load_sample(_SAMPLE)
    m = measure_qqp_recall(pairs, mode="word", k=2, num_perms=128, threshold=0.3, seed=0)
    assert m["recall"] >= 0.5
