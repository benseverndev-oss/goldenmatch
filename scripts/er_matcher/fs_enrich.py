"""Post-blend FS enrichment: FS-score-driven soft confidence targets + hard-negative
mining. Pure and box-testable; the FS matcher is injected as scorer(a,b)->float."""


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
            return hi
        return mid_hi + (hi - mid_hi) * frac
    frac = min(s / tau, 1.0) if tau > 0.0 else 0.0
    if frac >= 1.0:
        return mid_lo
    return lo + (mid_lo - lo) * frac
