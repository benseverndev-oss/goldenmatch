"""Post-blend FS enrichment: FS-score-driven soft confidence targets + hard-negative
mining. Pure and box-testable; the FS matcher is injected as scorer(a,b)->float."""

import hashlib


def soft_confidence(
    score: float,
    is_match: bool,
    *,
    tau: float = 0.5,
    hi: float = 0.97,
    lo: float = 0.03,
    mid_hi: float = 0.55,
    mid_lo: float = 0.45,
) -> float:
    """Map an FS match-probability to a P(match) training target, compressed toward
    0.5 near the decision threshold tau. Gold label picks the direction; the score's
    distance from tau picks how extreme. Never 0 or 1."""
    s = min(max(score, 0.0), 1.0)
    if is_match:
        frac = max((s - tau) / (1.0 - tau), 0.0) if tau < 1.0 else 0.0
        if frac >= 1.0:
            # Return the exact boundary constant: mid_hi + (hi-mid_hi)*1.0 usually
            # rounds back to hi, but that's a coincidence of these literals, not a
            # guarantee -- don't delete this in favor of the interpolation below.
            return hi
        return mid_hi + (hi - mid_hi) * frac
    frac = min(s / tau, 1.0) if tau > 0.0 else 0.0
    if frac >= 1.0:
        # Return the exact boundary constant; lo + (mid_lo-lo)*1.0 overshoots to
        # 0.45000000000000007 because the rounding error is baked into the
        # (mid_lo - lo) subtraction, before the multiply ever runs.
        return mid_lo
    return lo + (mid_lo - lo) * frac


def select_hard_negatives(
    scored_candidates: list[dict],
    *,
    tau: float = 0.5,
    delta: float = 0.1,
    cap: int,
) -> list[dict]:
    """Keep candidates whose FS score is within [tau-delta, tau+delta] AND whose gold
    label is non-match. Sort by closeness to tau (hardest first) for a deterministic
    cap. Gold label is the truth; FS only selects difficulty."""
    band = [
        c
        for c in scored_candidates
        if not c["gold_match"] and abs(c["score"] - tau) <= delta
    ]
    band.sort(key=lambda c: (abs(c["score"] - tau), c["a_id"], c["b_id"]))
    return band[:cap]


def cache_key(*, corpus_hash: str, scorer_cfg: str, tau: float, delta: float) -> str:
    """Stable digest of the enrichment inputs so a later build step can skip a
    repeat FS pass when none of the corpus/scorer/threshold config changed."""
    raw = f"{corpus_hash}|{scorer_cfg}|{tau:.4f}|{delta:.4f}"
    return hashlib.sha256(raw.encode()).hexdigest()


def enrich(
    pairs: list[dict],
    *,
    records: list[dict],
    scorer,
    candidates_fn,
    tau: float = 0.5,
    delta: float = 0.1,
    mine_cap: int,
) -> list[dict]:
    """Attach FS-score-driven soft confidence to every pair, and append near-threshold
    gold non-matches mined from candidates_fn(records). Pure given injected scorer +
    candidates_fn. Records must already be within a single split (leakage-free
    ordering)."""
    enriched = []
    for p in pairs:
        s = scorer(p["a"], p["b"])
        conf = soft_confidence(s, p["label"] == "match", tau=tau)
        enriched.append({**p, "confidence": round(conf, 4), "fs_score": round(s, 4)})

    scored_cands = [
        {
            **c,
            "score": scorer(c["a"], c["b"]),
            "a_id": c["eid_a"],
            "b_id": c["eid_b"],
        }
        for c in candidates_fn(records)
    ]
    for c in select_hard_negatives(scored_cands, tau=tau, delta=delta, cap=mine_cap):
        conf = soft_confidence(c["score"], False, tau=tau)
        enriched.append(
            {
                "a": c["a"],
                "b": c["b"],
                "label": "no_match",
                "eid_a": c["eid_a"],
                "eid_b": c["eid_b"],
                "confidence": round(conf, 4),
                "fs_score": round(c["score"], 4),
                "negative_kind": "fs_mined",
            }
        )
    return enriched
