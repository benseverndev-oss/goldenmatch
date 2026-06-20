"""Sketch-then-verify throughput tier (#1083): banding, sketch-distance verify,
and the honest LSH-theoretic posture. Isolated from the accuracy scorer."""
from __future__ import annotations

import os
from goldenmatch.config.schemas import ThroughputConfig


class ThroughputNotApplicableError(Exception):
    """Raised when the throughput tier is requested but the data has no text
    column to sketch on. Explicit refuse - no silent fall-back to the accuracy
    tier (mirrors ControllerNotConfidentError)."""


def resolve_throughput_config(arg=None, config=None) -> ThroughputConfig | None:
    """Resolve throughput posture: kwarg -> env -> off.

    ``arg`` accepts True (enable w/ defaults), a float (recall_target), a
    ThroughputConfig, or None. If None and config.throughput is set, that wins.
    Env: GOLDENMATCH_THROUGHPUT (truthy), GOLDENMATCH_THROUGHPUT_RECALL,
    GOLDENMATCH_THROUGHPUT_SIMILARITY.
    """
    if isinstance(arg, ThroughputConfig):
        return arg
    if arg is True:
        return ThroughputConfig(enabled=True)
    if isinstance(arg, (int, float)) and not isinstance(arg, bool):
        return ThroughputConfig(enabled=True, recall_target=float(arg))
    if arg is False:
        return None
    if config is not None and getattr(config, "throughput", None) is not None:
        return config.throughput
    env = os.environ.get("GOLDENMATCH_THROUGHPUT")
    if env and env.strip().lower() in ("1", "true", "yes", "on"):
        recall = os.environ.get("GOLDENMATCH_THROUGHPUT_RECALL")
        sim = os.environ.get("GOLDENMATCH_THROUGHPUT_SIMILARITY")
        return ThroughputConfig(
            enabled=True,
            recall_target=float(recall) if recall else 0.95,
            similarity_threshold=float(sim) if sim else None,
        )
    return None


# ---------------------------------------------------------------------------
# Task 4: Banding selection + LSH S-curve
# ---------------------------------------------------------------------------

import math

DEFAULT_SIMILARITY: dict[str, float] = {"jaccard": 0.8, "cosine": 0.85}


def _band_match_prob(metric: str, s: float) -> float:
    """Per-band single-row collision base prob at similarity ``s``.

    Jaccard: a MinHash row matches with prob s. Cosine (SimHash): a single
    hyperplane bit matches with prob ``1 - arccos(s)/pi``.
    """
    if metric == "cosine":
        return 1.0 - math.acos(max(-1.0, min(1.0, s))) / math.pi
    return s


def expected_recall_lsh(metric: str, s: float, bands: int, rows: int) -> float:
    """LSH S-curve: probability a pair at similarity ``s`` shares >=1 band.

    ``1 - (1 - x**rows)**bands`` with x the per-row band-match prob for the
    metric. Ground-truth-free expected recall over pairs at similarity ``s``.
    """
    x = _band_match_prob(metric, s)
    return 1.0 - (1.0 - x**rows) ** bands


def select_banding(metric: str, signature_len: int, similarity: float,
                   recall_target: float) -> tuple[int, int]:
    """Choose (bands, rows) among divisor splits of ``signature_len``.

    Picks the fewest bands (best precision) whose expected recall still meets
    ``recall_target`` at ``similarity``; if none meets it, the max-recall split.
    Divisor invariant: bands * rows == signature_len.
    """
    splits = [(b, signature_len // b) for b in range(1, signature_len + 1)
              if signature_len % b == 0]
    scored = [(b, r, expected_recall_lsh(metric, similarity, b, r)) for b, r in splits]
    meeting = [c for c in scored if c[2] >= recall_target]
    if meeting:
        b, r, _ = min(meeting, key=lambda c: c[0])
    else:
        b, r, _ = max(scored, key=lambda c: c[2])
    return b, r


# ---------------------------------------------------------------------------
# Task 5: ThroughputPosture + build_posture
# ---------------------------------------------------------------------------

from dataclasses import dataclass


@dataclass(frozen=True)
class ThroughputPosture:
    recall_target: float
    similarity_threshold: float
    metric: str
    bands: int
    rows_per_band: int
    expected_recall: float
    reduction_ratio: float
    candidate_pairs: int
    verified_pairs: int
    notes: str

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


def build_posture(*, metric: str, recall_target: float, similarity: float,
                  bands: int, rows: int, n_rows: int, candidate_pairs: int,
                  verified_pairs: int, semantic_fell_back: bool) -> ThroughputPosture:
    """Build a ThroughputPosture telemetry record for this sketch-verify run."""
    total = n_rows * (n_rows - 1) / 2 if n_rows > 1 else 1.0
    notes = (
        f"expected_recall is an LSH-theoretic estimate over pairs at/above "
        f"similarity {similarity} ({metric}); it is not a measured F1. Precision "
        f"is traded for throughput and is not directly measured here."
    )
    if semantic_fell_back:
        notes += " Semantic embedder unreachable; fell back to lexical lsh."
    if candidate_pairs / total > 0.5:
        notes += " WARNING: reduction_ratio > 0.5 - banding is near-degenerate."
    return ThroughputPosture(
        recall_target=recall_target, similarity_threshold=similarity, metric=metric,
        bands=bands, rows_per_band=rows,
        expected_recall=expected_recall_lsh(metric, similarity, bands, rows),
        reduction_ratio=candidate_pairs / total,
        candidate_pairs=candidate_pairs, verified_pairs=verified_pairs, notes=notes,
    )
