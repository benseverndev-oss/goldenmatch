"""Self-contained tests for the band-override harness (Phase 0).

No external datasets — a tiny synthetic band the FS threshold provably CAN'T
separate (constant FS score inside the band) but the student can. Locks the
harness contract: feature layout, distillation, and that the override improves
end-to-end F1 while being a strict no-op when the band decision is empty.
"""
from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("sklearn")

# scripts/er_matcher on the path so `import band_student` (the package) resolves,
# mirroring test_train_helpers.py's self-managed sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from band_student import (  # noqa: E402
    BandStudent,
    band_features,
    distillation_fidelity,
    end_to_end_override,
    feature_names,
)

FIELDS = ["name", "dob"]


def _universe():
    """Build rows + a candidate universe: 100 band pairs (constant FS 0.6, 60/40
    true/false, separable on name) + confident matches (FS .95) + non-matches (.3)."""
    rows, band, gold, base = {}, [], {}, {}
    rid = 0

    def add(name_a, dob_a, name_b, dob_b, is_match, fs, band_pair):
        nonlocal rid
        a, b = rid, rid + 1
        rid += 2
        rows[a] = {"name": name_a, "dob": dob_a}
        rows[b] = {"name": name_b, "dob": dob_b}
        key = (a, b)
        gold[key] = 1 if is_match else 0
        base[key] = fs
        if band_pair:
            band.append(key)
        return key

    for i in range(60):  # band, true: identical name+dob, but FS is uncertain (0.6)
        add(f"person {i}", "1990-01-01", f"person {i}", "1990-01-01", True, 0.60, True)
    for i in range(40):  # band, false: dissimilar name, FS still 0.6
        add(f"aaaa {i}", "1990-01-01", f"zzzz {i}", "1975-12-31", False, 0.60, True)
    for i in range(50):  # confident matches (not overridden)
        add(f"m {i}", "2000-05-05", f"m {i}", "2000-05-05", True, 0.95, False)
    for i in range(50):  # confident non-matches
        add(f"p {i}", "1", f"q {i}", "2", False, 0.30, False)
    return rows, band, gold, base


def test_feature_layout():
    v = band_features({"name": "a", "dob": "x"}, {"name": "a", "dob": "y"}, FIELDS, 0.6)
    assert len(v) == len(FIELDS) * 4 + 1
    assert v[-1] == pytest.approx(0.6)
    assert feature_names(FIELDS)[-1] == "fs_score"
    assert feature_names(FIELDS)[:4] == ["name.jw", "name.token_sort", "name.exact", "name.both_present"]


def test_distillation_and_override():
    rows, band, gold, base = _universe()
    X = [band_features(rows[a], rows[b], FIELDS, base[(a, b)]) for a, b in band]
    y_gold = [gold[k] for k in band]
    # teacher labels: gold with a couple of flips (simulate an imperfect teacher)
    y_teacher = list(y_gold)
    y_teacher[0] = 1 - y_teacher[0]
    y_teacher[-1] = 1 - y_teacher[-1]

    student = BandStudent(fields=FIELDS).fit(X, y_teacher)
    pred = student.predict(X)

    fid = distillation_fidelity(pred, y_teacher, y_gold)
    assert fid.n == len(band)
    assert fid.student_vs_gold >= 0.9  # distilled student recovers the true separation

    band_decision = {k: int(p) for k, p in zip(band, pred)}
    baseline, override = end_to_end_override(gold=gold, base_score=base, band_decision=band_decision)
    assert override.f1 > baseline.f1 + 0.05  # the band override lifts end-to-end F1
    assert baseline.recall <= override.recall  # gain is recovered band matches


def test_override_empty_is_noop():
    _, _, gold, base = _universe()
    baseline, override = end_to_end_override(gold=gold, base_score=base, band_decision={})
    assert override.f1 == baseline.f1  # no band decision -> byte-identical to FS
