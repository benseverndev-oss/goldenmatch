"""Evaluation engine -- precision, recall, F1 from ground truth pairs."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations


@dataclass
class EvalResult:
    """Evaluation metrics container."""
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def summary(self) -> dict:
        return {
            "tp": self.tp, "fp": self.fp, "fn": self.fn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "predicted_pairs": self.tp + self.fp,
            "ground_truth_pairs": self.tp + self.fn,
        }


def evaluate_pairs(
    predicted: list[tuple[int, int, float]],
    ground_truth: set[tuple],
) -> EvalResult:
    """Evaluate predicted pairs against ground truth.

    Ground truth pairs are matched symmetrically: (a,b) matches (b,a).
    """
    # Normalize ground truth to canonical form (min, max)
    gt_canonical = set()
    for pair in ground_truth:
        a, b = pair[0], pair[1]
        gt_canonical.add((min(a, b), max(a, b)))

    tp = fp = 0
    seen = set()
    for a, b, _score in predicted:
        canon = (min(a, b), max(a, b))
        if canon in seen:
            continue
        seen.add(canon)
        if canon in gt_canonical:
            tp += 1
        else:
            fp += 1
    fn = len(gt_canonical) - tp
    return EvalResult(tp=tp, fp=fp, fn=fn)


def evaluate_clusters(
    clusters: dict[int, dict],
    ground_truth: set[tuple],
) -> EvalResult:
    """Evaluate clusters by expanding to pairwise comparisons."""
    predicted = []
    for cid, info in clusters.items():
        members = info.get("members", [])
        if len(members) < 2:
            continue
        for a, b in combinations(sorted(members), 2):
            predicted.append((a, b, 1.0))
    return evaluate_pairs(predicted, ground_truth)


def load_ground_truth_csv(path: str, col_a: str = "id_a", col_b: str = "id_b") -> set[tuple]:
    """Load ground truth pairs from CSV.

    Supports both ID-based (integer) and string-based pair columns.
    """
    import polars as pl
    df = pl.read_csv(path)
    if col_a not in df.columns or col_b not in df.columns:
        # Try common alternative column names
        for alt_a, alt_b in [("idA", "idB"), ("id1", "id2"), ("left_id", "right_id")]:
            if alt_a in df.columns and alt_b in df.columns:
                col_a, col_b = alt_a, alt_b
                break
        else:
            raise ValueError(
                f"Ground truth CSV must have columns '{col_a}' and '{col_b}'. "
                f"Found: {df.columns}"
            )
    pairs = set()
    for row in df.select(col_a, col_b).to_dicts():
        a, b = row[col_a], row[col_b]
        # Try integer conversion (row IDs are ints in GoldenMatch)
        try:
            a = int(a)
        except (ValueError, TypeError):
            a = str(a).strip()
        try:
            b = int(b)
        except (ValueError, TypeError):
            b = str(b).strip()
        pairs.add((a, b))
    return pairs


# ── Threshold sweep & accuracy analysis from labels (Splink-parity Phase 4) ──


def _canonical_gt(ground_truth: set[tuple]) -> set[tuple]:
    return {(min(p[0], p[1]), max(p[0], p[1])) for p in ground_truth}


def _dedup_pairs_max(scored_pairs: list[tuple[int, int, float]]) -> dict[tuple[int, int], float]:
    """Canonicalize (min,max) and keep the max score per pair."""
    best: dict[tuple[int, int], float] = {}
    for a, b, s in scored_pairs:
        k = (min(a, b), max(a, b))
        if k not in best or s > best[k]:
            best[k] = s
    return best


# Readable anchors for [0,1] (posterior) scores, so the steward sees the
# probabilistic operating points even when scores cluster near 0/1.
_PROB_ANCHORS = (0.5, 0.7, 0.8, 0.9, 0.95, 0.99, 0.999)


def _default_thresholds(scores: list[float], n: int = 15) -> list[float]:
    """A sweep grid spanning the observed score distribution.

    Distribution quantiles (so the recommended cut is reachable even when
    scores cluster) UNION the standard probability anchors that fall within the
    score range (so a calibrated [0,1] score gets readable operating points).
    """
    if not scores:
        return []
    xs = sorted(scores)
    m = len(xs)
    lo, hi = xs[0], xs[-1]
    qs = {round(xs[min(m - 1, int(k / n * m))], 4) for k in range(n)}
    qs.add(round(hi, 4))
    qs.update(a for a in _PROB_ANCHORS if lo <= a <= hi)
    return sorted(qs)


def threshold_sweep(
    scored_pairs: list[tuple[int, int, float]],
    ground_truth: set[tuple],
    thresholds: list[float] | None = None,
) -> list[dict]:
    """Precision/recall/F1 at each candidate link threshold.

    For each threshold ``t``, pairs scoring ``>= t`` are predicted matches.
    Returns one dict per threshold (ascending), each with tp/fp/fn +
    precision/recall/f1 + predicted_pairs. The score IS the FS posterior (or
    linear) score, so this is the operating-point curve a steward picks a cut
    from. Computed with a single descending sweep (O(P log P + P + T)).
    """
    gt = _canonical_gt(ground_truth)
    total_gt = len(gt)
    best = _dedup_pairs_max(scored_pairs)
    items = sorted(best.items(), key=lambda kv: kv[1], reverse=True)  # high score first
    if thresholds is None:
        thresholds = _default_thresholds([s for _, s in items])

    rows: list[dict] = []
    idx = tp = fp = 0
    for t in sorted(set(thresholds), reverse=True):
        while idx < len(items) and items[idx][1] >= t:
            tp += 1 if items[idx][0] in gt else 0
            fp += 0 if items[idx][0] in gt else 1
            idx += 1
        fn = total_gt - tp
        p = tp / (tp + fp) if (tp + fp) else 0.0
        r = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        rows.append({
            "threshold": round(t, 4), "tp": tp, "fp": fp, "fn": fn,
            "precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4),
            "predicted_pairs": tp + fp,
        })
    rows.sort(key=lambda row: row["threshold"])
    return rows


def recommend_threshold(
    scored_pairs: list[tuple[int, int, float]],
    ground_truth: set[tuple],
    thresholds: list[float] | None = None,
) -> dict:
    """The sweep row that maximizes F1 (tie-break: higher threshold).

    Returns the full metrics dict for the recommended cut, plus the whole
    ``sweep`` under key ``"sweep"``. Empty-input safe (returns zeros).
    """
    sweep = threshold_sweep(scored_pairs, ground_truth, thresholds)
    if not sweep:
        return {"threshold": 0.0, "precision": 0.0, "recall": 0.0, "f1": 0.0,
                "tp": 0, "fp": 0, "fn": len(_canonical_gt(ground_truth)),
                "predicted_pairs": 0, "sweep": []}
    best = max(sweep, key=lambda row: (row["f1"], row["threshold"]))
    return {**best, "sweep": sweep}


def probability_two_random_records_match(em_result) -> float:
    """Splink's ``probability_two_random_records_match`` for an FS model.

    The EM-estimated prior that a sampled (within-block) pair is a match —
    ``em_result.proportion_matched`` (λ), the prior the posterior calibration
    restores. Note GoldenMatch estimates λ over BLOCKED pairs, so it is the
    within-block match rate, higher than a whole-dataset random-pair prior.
    """
    return float(getattr(em_result, "proportion_matched", 0.0))


def fs_model_report(em_result, mk) -> dict:
    """Per-comparison m/u/match-weight table for a trained FS model.

    The data behind Splink's m/u + match-weight charts: for each field and
    comparison level, P(level|match) (m), P(level|non-match) (u), and the
    log2(m/u) match weight in bits. Plus the prior (in bits and probability)
    and EM convergence. Renderable as a table or fed to a charting layer.
    """
    import math

    prior_lambda = probability_two_random_records_match(em_result)
    lam = min(max(prior_lambda, 1e-9), 1.0 - 1e-9)
    fields = []
    for f in mk.fields:
        weights = em_result.match_weights.get(f.field, [])
        m_list = em_result.m_probs.get(f.field, [])
        u_list = em_result.u_probs.get(f.field, [])
        levels = []
        for lvl in range(int(f.levels)):
            levels.append({
                "level": lvl,
                "m": round(float(m_list[lvl]), 6) if lvl < len(m_list) else None,
                "u": round(float(u_list[lvl]), 6) if lvl < len(u_list) else None,
                "weight_bits": round(float(weights[lvl]), 4) if lvl < len(weights) else None,
            })
        fields.append({"field": f.field, "scorer": getattr(f, "scorer", None),
                       "n_levels": int(f.levels), "levels": levels})
    return {
        "proportion_matched": round(prior_lambda, 6),
        "prior_bits": round(math.log2(lam / (1.0 - lam)), 4),
        "converged": bool(getattr(em_result, "converged", False)),
        "iterations": int(getattr(em_result, "iterations", 0)),
        "fields": fields,
    }
