"""Gate metrics for the band-override student.

- ``distillation_fidelity``: student-vs-teacher agreement + student-vs-gold accuracy
  (the deployable number; teacher-vs-gold is the ceiling context).
- ``end_to_end_override``: best-threshold pair-F1 over a FIXED candidate universe,
  with band pairs' FS decision replaced by the student's. Reproduces the
  historical_50k +3.1/+4.8 result. FS everywhere else -> the delta isolates the
  band override.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class FidelityResult:
    student_vs_gold: float
    student_vs_teacher: float
    teacher_vs_gold: float
    n: int


def distillation_fidelity(student_pred, teacher_labels, gold_labels) -> FidelityResult:
    sp = np.asarray(student_pred)
    tl = np.asarray(teacher_labels)
    gl = np.asarray(gold_labels)
    return FidelityResult(
        student_vs_gold=float((sp == gl).mean()),
        student_vs_teacher=float((sp == tl).mean()),
        teacher_vs_gold=float((tl == gl).mean()),
        n=int(sp.shape[0]),
    )


@dataclass(frozen=True)
class OverrideResult:
    threshold: float
    precision: float
    recall: float
    f1: float


def _best_f1(decision_for, universe_keys, gold, base_score, band_decision) -> OverrideResult:
    """Sweep the non-band FS threshold; band keys take the student decision."""
    P_total = int(sum(gold.values()))
    best = OverrideResult(0.0, 0.0, 0.0, 0.0)
    for ti in range(30, 96):
        T = ti / 100.0
        tp = fp = 0
        for k in universe_keys:
            if k in band_decision:
                pred = band_decision[k]
            else:
                pred = 1 if base_score[k] >= T else 0
            if pred:
                if gold[k]:
                    tp += 1
                else:
                    fp += 1
        rec = tp / P_total if P_total else 0.0
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        if f1 > best.f1:
            best = OverrideResult(T, prec, rec, f1)
    return best


def end_to_end_override(
    *,
    gold: Mapping,
    base_score: Mapping,
    band_decision: Mapping,
) -> tuple[OverrideResult, OverrideResult]:
    """Return (baseline, override) best-F1 over the same fixed universe.

    ``gold``/``base_score`` are keyed by canonical pair over the full candidate
    universe; ``band_decision`` maps the band subset -> the student's 0/1 call.
    Baseline = FS everywhere (empty band_decision); override = student on the band.
    """
    keys = list(gold)
    baseline = _best_f1(None, keys, gold, base_score, {})
    override = _best_f1(None, keys, gold, base_score, dict(band_decision))
    return baseline, override
