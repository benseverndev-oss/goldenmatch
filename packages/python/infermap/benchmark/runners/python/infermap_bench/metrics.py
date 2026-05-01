"""Metric computation for the benchmark runner.

All four metrics are pure functions over a `MetricInput` — no I/O, no state.
See docs/design/2026-04-08-infermap-accuracy-benchmark-design.md §9 for the
full aggregation convention (F1 micro, top-1/MRR macro, ECE population).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Prediction:
    confidence: float
    correct: bool


@dataclass(frozen=True)
class MetricInput:
    source_fields: list[str]
    target_fields: list[str]
    expected_mappings: list[dict[str, str]]
    expected_unmapped_source: list[str]
    expected_unmapped_target: list[str]
    actual_mappings: list[dict[str, object]]
    score_matrix: dict[str, dict[str, float]]
    min_confidence: float


def top1_accuracy(inp: MetricInput) -> float:
    """Per source field: did we predict the expected target (or correctly predict unmapped)?"""
    expected: dict[str, str | None] = {m["source"]: m["target"] for m in inp.expected_mappings}
    for unmapped in inp.expected_unmapped_source:
        expected[unmapped] = None

    predicted: dict[str, str] = {
        str(m["source"]): str(m["target"]) for m in inp.actual_mappings
    }

    total = len(inp.source_fields)
    if total == 0:
        return 0.0
    correct = 0
    for src in inp.source_fields:
        pred = predicted.get(src)  # None if source wasn't mapped
        if pred == expected.get(src):
            correct += 1
    return correct / total


def f1_per_case(inp: MetricInput) -> tuple[int, int, int]:
    """Return (tp, fp, fn) for one case. Micro F1 aggregates across cases."""
    expected_set = frozenset((m["source"], m["target"]) for m in inp.expected_mappings)
    predicted_set = frozenset(
        (str(m["source"]), str(m["target"])) for m in inp.actual_mappings
    )
    tp = len(expected_set & predicted_set)
    fp = len(predicted_set - expected_set)
    fn = len(expected_set - predicted_set)
    return (tp, fp, fn)


def micro_f1(per_case_counts: Iterable[tuple[int, int, int]]) -> float:
    """Micro-averaged F1 across an iterable of (tp, fp, fn) triples."""
    total_tp = 0
    total_fp = 0
    total_fn = 0
    for tp, fp, fn in per_case_counts:
        total_tp += tp
        total_fp += fp
        total_fn += fn
    if total_tp == 0 and total_fp == 0 and total_fn == 0:
        return 1.0  # perfect negative: nothing expected, nothing predicted
    if total_tp + total_fp == 0 or total_tp + total_fn == 0:
        return 0.0
    precision = total_tp / (total_tp + total_fp)
    recall = total_tp / (total_tp + total_fn)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def mean_reciprocal_rank(inp: MetricInput) -> float:
    """MRR over expected mappings. Unmapped sources are not ranked."""
    expected: dict[str, str] = {m["source"]: m["target"] for m in inp.expected_mappings}
    if not expected:
        return 1.0  # no ranking to do — trivially perfect

    reciprocal_ranks: list[float] = []
    for src, correct_tgt in expected.items():
        row = inp.score_matrix.get(src, {})
        if not row:
            reciprocal_ranks.append(0.0)
            continue
        # Sort by score descending, tie-break by target name ascending (deterministic)
        ranked = sorted(row.items(), key=lambda kv: (-kv[1], kv[0]))
        rank = next(
            (i + 1 for i, (tgt, _) in enumerate(ranked) if tgt == correct_tgt),
            None,
        )
        if rank is None:
            reciprocal_ranks.append(0.0)
        else:
            reciprocal_ranks.append(1.0 / rank)

    return sum(reciprocal_ranks) / len(reciprocal_ranks)


def expected_calibration_error(
    predictions: list[Prediction],
    num_bins: int = 10,
) -> float:
    """ECE over a flat list of (confidence, correct) predictions."""
    if not predictions:
        return 0.0
    bins: list[list[Prediction]] = [[] for _ in range(num_bins)]
    for p in predictions:
        idx = min(int(p.confidence * num_bins), num_bins - 1)
        bins[idx].append(p)

    total = len(predictions)
    ece = 0.0
    for bin_ in bins:
        if not bin_:
            continue
        bin_conf = sum(p.confidence for p in bin_) / len(bin_)
        bin_acc = sum(1 for p in bin_ if p.correct) / len(bin_)
        bin_weight = len(bin_) / total
        ece += bin_weight * abs(bin_conf - bin_acc)
    return ece


def macro_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def extract_predictions(inp: MetricInput) -> list[Prediction]:
    """Build the ECE-input list for one case: (confidence, was_correct) per actual mapping."""
    expected_set = frozenset((m["source"], m["target"]) for m in inp.expected_mappings)
    out: list[Prediction] = []
    for m in inp.actual_mappings:
        conf = float(m["confidence"])  # type: ignore[arg-type]
        key = (str(m["source"]), str(m["target"]))
        out.append(Prediction(confidence=conf, correct=key in expected_set))
    return out
