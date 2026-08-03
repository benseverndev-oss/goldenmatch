#!/usr/bin/env python
"""Layer 2: translate the LOCKED Layer-1 structures into human-readable form.

Layer 1 proved the same-entity decision is a low-dimensional linear direction in
the residual stream that CAUSALLY controls the verdict (steering it 0->1). Layer 2
does the *translation the framework reserves for a locked Layer 1*: it abstracts
that proven direction into human-readable field signals -- WITHOUT inventing a new
linguistic story, only decomposing the structure Layer 1 already validated.

Two abstractions, both grounded in the proven geometry:

  1. FIELD DECOMPOSITION of the match direction. For each record pair, compute
     per-field human-readable AGREEMENT signals (jaro-winkler similarity of each
     field's two values -- "do the surnames agree? the DOBs?") and regress the
     pair's PROJECTION ONTO THE CAUSAL MATCH DIRECTION against them. The
     standardized coefficients say which human field signals COMPOSE the primitive;
     the R^2 says HOW MUCH of the primitive the field story captures (an honest
     faithfulness number -- residual = interactions/context the fields miss).

  2. SAE FEATURE LABELS. For each top Layer-1 SAE feature, the field-agreement
     signal its activation correlates with most -- a human label for that basis
     direction (e.g. "fires on DOB disagreement").

The pure helpers here (``field_agreements`` + ``attribute_direction`` +
``label_sae_features``) are unit-tested model-free; the projections/activations are
produced on GPU by ``modal_interp.py::layer2_abstraction`` at the causal layer 14.
"""
from __future__ import annotations

from typing import Any

import numpy as np


def field_agreements(
    rows: dict[int, dict[str, Any]],
    pairs: list[tuple[int, int, int]],
    fields: list[str],
    agreement: Any = None,
) -> np.ndarray:
    """Per-field agreement for each pair -> (n_pairs, n_fields) in [0, 1].

    These are the HUMAN primitives ("do the surnames agree?"). Missing on either
    side -> 0.0 (no positive agreement evidence).

    ``agreement`` overrides the pairwise metric with a callable ``(va, vb) ->
    float | None``. Callers that measure the SHIPPED explainer must pass
    ``goldenmatch.core.er_matcher.explainer.field_agreement`` so the measured
    basis and the product's basis cannot drift apart; the built-in default is
    plain jaro-winkler and exists so this module stays testable standalone.
    Pure; needs jellyfish.
    """
    import jellyfish

    out = np.zeros((len(pairs), len(fields)), dtype=np.float64)
    for i, (a, b, _t) in enumerate(pairs):
        ra, rb = rows[a], rows[b]
        for j, f in enumerate(fields):
            va, vb = str(ra.get(f, "") or "").strip(), str(rb.get(f, "") or "").strip()
            if agreement is not None:
                out[i, j] = float(agreement(va, vb) or 0.0)
            elif not va or not vb:
                out[i, j] = 0.0
            elif va == vb:
                out[i, j] = 1.0
            else:
                out[i, j] = float(jellyfish.jaro_winkler_similarity(va, vb))
    return out


def attribute_direction(
    projections: np.ndarray,
    field_feats: np.ndarray,
    field_names: list[str],
    *,
    l1_alpha: float | None = None,
) -> dict[str, Any]:
    """Regress the direction-PROJECTION onto standardized field-agreement signals.

    Returns per-field standardized coefficients (the field's contribution to the
    match primitive), their ranking, and R^2 (how much of the projection the human
    field story explains -- the faithfulness of the abstraction). Standardizing
    both sides makes coefficients comparable across fields with different spreads.

    ``l1_alpha`` switches the fit to Lasso. On a wide, correlated basis an
    ordinary least-squares fit spreads weight across redundant signals and the
    per-coefficient story stops being readable long before the fit stops being
    accurate; L1 drops the redundant ones. Also reports ``n_nonzero`` so the
    sparsity is visible alongside the R^2 it costs.
    """
    if field_feats.shape[0] != projections.shape[0]:
        raise ValueError("projections and field_feats must have the same #rows")
    if field_feats.shape[1] != len(field_names):
        raise ValueError("field_feats columns must match field_names")
    from sklearn.linear_model import Lasso, LinearRegression

    xs = field_feats.std(0)
    Xz = (field_feats - field_feats.mean(0)) / np.where(xs < 1e-9, 1.0, xs)
    ys = projections.std()
    yz = (projections - projections.mean()) / (ys if ys > 1e-9 else 1.0)

    if l1_alpha is not None:
        reg = Lasso(alpha=float(l1_alpha), max_iter=20000).fit(Xz, yz)
    else:
        reg = LinearRegression().fit(Xz, yz)
    r2 = float(reg.score(Xz, yz))
    coefs = {field_names[j]: float(reg.coef_[j]) for j in range(len(field_names))}
    ranking = sorted(coefs.items(), key=lambda kv: -abs(kv[1]))
    return {
        "r2": r2,
        "coefficients": coefs,
        "ranking": [{"field": f, "coef": c} for f, c in ranking],
        "top_field": ranking[0][0] if ranking else None,
        "l1_alpha": l1_alpha,
        "n_nonzero": int(sum(1 for c in coefs.values() if abs(c) > 1e-9)),
    }


def attribute_direction_grouped(
    projections: np.ndarray,
    feats: np.ndarray,
    signal_names: list[str],
    fields: list[str],
) -> dict[str, Any]:
    """Two-stage GROUPED fit: within-field pattern, then per-field importance.

    The flat 36-signal fits score well but their per-field rollup is an accident of
    which collinear signal happened to absorb the weight. This fits the structure
    explicitly instead:

      1. per field, regress the projection on that field's own signals -> a
         within-field pattern ``beta_f``, L1-normalized to sum |beta| = 1;
      2. regress the projection on the 6 resulting composites -> ``a_f``, which is
         the field's importance *by construction*, directly comparable to the
         6-field table.

    Because the normalization fixes each field's internal scale, the per-field
    rollup of the effective per-signal weights equals ``|a_f|`` exactly -- so the
    ranking is designed rather than inferred, while each field still gets the full
    six-signal decomposition. Returns the effective per-signal weights too, so the
    result drops into the same frozen-weight scoring path as the other bases.
    """
    if feats.shape[1] != len(signal_names):
        raise ValueError("feats columns must match signal_names")
    from sklearn.linear_model import LinearRegression

    xs = feats.std(0)
    Xz = (feats - feats.mean(0)) / np.where(xs < 1e-9, 1.0, xs)
    ys = projections.std()
    yz = (projections - projections.mean()) / (ys if ys > 1e-9 else 1.0)

    cols = {f: [j for j, n in enumerate(signal_names) if n.split("__")[0] == f]
            for f in fields}
    patterns: dict[str, dict[str, float]] = {}
    composites = np.zeros((Xz.shape[0], len(fields)), dtype=np.float64)
    for i, f in enumerate(fields):
        idx = cols[f]
        if not idx:
            continue
        beta = LinearRegression().fit(Xz[:, idx], yz).coef_
        mass = float(np.abs(beta).sum())
        beta = beta / mass if mass > 1e-12 else beta
        patterns[f] = {signal_names[j]: float(beta[k]) for k, j in enumerate(idx)}
        composites[:, i] = Xz[:, idx] @ beta

    cs = composites.std(0)
    Cz = (composites - composites.mean(0)) / np.where(cs < 1e-9, 1.0, cs)
    outer = LinearRegression().fit(Cz, yz)
    a = {f: float(outer.coef_[i]) for i, f in enumerate(fields)}

    effective = {
        n: a[f] * patterns[f][n]
        for f in fields if f in patterns
        for n in patterns[f]
    }
    ranking = sorted(a.items(), key=lambda kv: -abs(kv[1]))
    return {
        "r2": float(outer.score(Cz, yz)),
        "field_importance": a,
        "within_field_patterns": patterns,
        "coefficients": effective,
        "ranking": [{"field": f, "coef": c} for f, c in ranking],
        "top_field": ranking[0][0] if ranking else None,
        "n_nonzero": int(sum(1 for v in effective.values() if abs(v) > 1e-9)),
    }


def field_rollup(coefficients: dict[str, float]) -> list[tuple[str, float]]:
    """Sum |coef| per field over ``field__signal`` keys -> ranked (field, mass).

    The readability test for a wide basis: if this ordering disagrees with what
    ablation says the model needs, the coefficients are not a usable human story
    however well they fit.
    """
    roll: dict[str, float] = {}
    for k, v in coefficients.items():
        roll[k.split("__")[0]] = roll.get(k.split("__")[0], 0.0) + abs(float(v))
    return sorted(roll.items(), key=lambda kv: -kv[1])


def record_disjoint_split(
    pairs: list[tuple[int, int, int]], *, seed: int = 0, test_frac: float = 0.5
) -> tuple[list[int], list[int]]:
    """Split pair INDICES so train and test share no underlying RECORD.

    Weaker than a cluster-disjoint split and kept only to measure the difference:
    two records of the SAME entity can land on opposite sides, so a fit can learn
    that entity's agreement->P(match) mapping in train and be scored on it in
    test. Use ``split="cluster"`` in ``faithfulness_eval`` for the honest number;
    this exists to quantify how much the weaker split inflates it.

    Assigns each record id to a side, then keeps only pairs whose both endpoints
    landed on the same side. Returns ``(train_idx, test_idx)``. Deterministic.
    """
    import random

    rng = random.Random(seed)
    side: dict[int, int] = {}
    for a, b, _t in pairs:
        for r in (a, b):
            if r not in side:
                side[r] = 1 if rng.random() < test_frac else 0
    train_idx, test_idx = [], []
    for i, (a, b, _t) in enumerate(pairs):
        if side[a] == side[b]:
            (test_idx if side[a] else train_idx).append(i)
    return train_idx, test_idx


# Fallback copies of the SHIPPED basis, used only when this module is exercised
# standalone (its unit tests run without the goldenmatch package importable). The
# production harness passes the real functions in; `test_basis_parity.py` in the
# package suite asserts these two implementations agree, so the duplication is
# gated rather than trusted.
_LOCAL_SIGNAL_NAMES = ("agreement", "exact", "missing", "conflict", "len_ratio", "edit_norm")
_LOCAL_CONFLICT_THRESHOLD = 0.60


def _local_signal_vector(va: Any, vb: Any) -> dict[str, float]:
    """Standalone mirror of ``explainer.field_signal_vector``. Keep in lockstep."""
    import jellyfish

    sa = "" if va is None else str(va).strip()
    sb = "" if vb is None else str(vb).strip()
    out: dict[str, float] = dict.fromkeys(_LOCAL_SIGNAL_NAMES, 0.0)
    if not sa or not sb:
        out["missing"] = 1.0
        return out
    agr = 1.0 if sa == sb else float(jellyfish.jaro_winkler_similarity(sa, sb))
    out["agreement"] = agr
    out["exact"] = 1.0 if sa == sb else 0.0
    out["conflict"] = 1.0 if agr <= _LOCAL_CONFLICT_THRESHOLD else 0.0
    out["len_ratio"] = min(len(sa), len(sb)) / max(len(sa), len(sb))
    out["edit_norm"] = (
        0.0 if sa == sb
        else jellyfish.levenshtein_distance(sa, sb) / max(len(sa), len(sb))
    )
    return out


def richer_field_features(
    rows: dict[int, dict[str, Any]],
    pairs: list[tuple[int, int, int]],
    fields: list[str],
    signal_fn: Any = None,
    signal_names: tuple[str, ...] | None = None,
) -> tuple[np.ndarray, list[str]]:
    """Expanded per-field features -> ((n_pairs, k*n_fields), names).

    Six signals per field, all cheap and human-nameable: ``agreement``, ``exact``
    (identical), ``missing`` (absent on either side), ``conflict`` (present on
    both sides but agreement below the shipped conflict threshold), ``len_ratio``
    (shorter/longer), and ``edit_norm`` (normalized Levenshtein). The extra
    signals let a fit distinguish cases the single agreement scalar conflates --
    notably *missing* (no evidence) from *conflicting* (negative evidence), which
    both sit near agreement 0.

    ``signal_fn``/``signal_names`` inject the SHIPPED decomposition
    (``explainer.field_signal_vector`` / ``FIELD_SIGNAL_NAMES``). The production
    harness always passes them, so what we measure is what we ship; the built-in
    fallback exists only so this module stays testable standalone.

    Pure; needs jellyfish.
    """
    fn = signal_fn or _local_signal_vector
    sig_names = tuple(signal_names or _LOCAL_SIGNAL_NAMES)
    k = len(sig_names)
    names = [f"{f}__{s}" for f in fields for s in sig_names]
    out = np.zeros((len(pairs), k * len(fields)), dtype=np.float64)
    for i, (a, b, _t) in enumerate(pairs):
        ra, rb = rows[a], rows[b]
        for j, f in enumerate(fields):
            vec = fn(ra.get(f, ""), rb.get(f, ""))
            base = k * j
            for m, s in enumerate(sig_names):
                out[i, base + m] = float(vec[s])
    return out, names


def fixed_weight_score(
    field_feats: np.ndarray, field_names: list[str], weights: dict[str, float]
) -> np.ndarray:
    """Weighted sum of per-field agreements under FIXED importance ``weights``.

    This is the score the *shipped* explainer's story implies: the learned
    per-field weights are frozen (not refit), so the only freedom left is the
    scale/offset of the link to the model's probability. Fields absent from
    ``weights`` contribute 0.
    """
    if field_feats.shape[1] != len(field_names):
        raise ValueError("field_feats columns must match field_names")
    w = np.array([float(weights.get(f, 0.0)) for f in field_names], dtype=np.float64)
    return field_feats @ w


def logit(p: np.ndarray, eps: float = 1e-3) -> np.ndarray:
    """log(p/(1-p)) with ``p`` clipped to [eps, 1-eps] so saturated probabilities
    stay finite. The model's P(match) is near-bimodal (masses at ~0 and ~1), so
    without clipping the transform blows up on the most common values."""
    q = np.clip(np.asarray(p, dtype=np.float64), eps, 1.0 - eps)
    return np.log(q / (1.0 - q))


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(np.asarray(z, dtype=np.float64), -60.0, 60.0)))


def prob_space_r2(pred_logit: np.ndarray, y_true: np.ndarray) -> float:
    """R^2 of logit-space predictions, scored back in PROBABILITY space.

    A logit-link fit predicts log-odds; squashing through the sigmoid before
    scoring keeps the number on the same scale as a linear-link R^2, so the two
    links are directly comparable instead of being two different metrics.
    """
    yhat = _sigmoid(pred_logit)
    y = np.asarray(y_true, dtype=np.float64)
    ss_res = float(((y - yhat) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0


def affine_r2(
    train_score: np.ndarray, train_y: np.ndarray,
    test_score: np.ndarray, test_y: np.ndarray,
    *, link: str = "linear", eps: float = 1e-3,
) -> dict[str, Any]:
    """R^2 of a FROZEN 1-D score against ``y``, fitting only intercept + scale.

    Two free parameters (a monotone link), fit on train and evaluated on test --
    so the reported number credits the fixed weights, not a refit. This is the
    honest "what does the shipped explainer actually explain?" measure, as
    opposed to refitting the weights (which yields a ceiling).

    ``link="linear"`` regresses the score straight onto ``y``. ``link="logit"``
    regresses it onto ``logit(y)`` and scores the squashed prediction back in
    probability space -- the fairer form when ``y`` is a saturated probability,
    and still directly comparable to the linear number.
    """
    if train_score.shape[0] != train_y.shape[0] or test_score.shape[0] != test_y.shape[0]:
        raise ValueError("scores and targets must align")
    if link not in ("linear", "logit"):
        raise ValueError(f"link must be 'linear' or 'logit', got {link!r}")

    target = logit(train_y, eps) if link == "logit" else np.asarray(train_y, np.float64)
    xc = train_score - train_score.mean()
    var = float((xc * xc).sum())
    slope = float((xc * (target - target.mean())).sum() / var) if var > 1e-12 else 0.0
    intercept = float(target.mean() - slope * train_score.mean())

    def _r2(s: np.ndarray, y: np.ndarray) -> float:
        pred = slope * s + intercept
        if link == "logit":
            return prob_space_r2(pred, y)
        ss_res = float(((y - pred) ** 2).sum())
        ss_tot = float(((y - y.mean()) ** 2).sum())
        return 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0

    return {
        "r2_test": _r2(test_score, test_y), "r2_train": _r2(train_score, train_y),
        "slope": slope, "intercept": intercept, "link": link,
    }


def _rank(x: np.ndarray) -> np.ndarray:
    """Average-tied ranks of ``x`` (1-based), for a Spearman correlation."""
    order = np.argsort(x, kind="stable")
    ranks = np.empty(len(x), dtype=np.float64)
    ranks[order] = np.arange(1, len(x) + 1, dtype=np.float64)
    # average ties so a flat weight table doesn't fake a perfect correlation
    for v in np.unique(x):
        m = x == v
        if m.sum() > 1:
            ranks[m] = ranks[m].mean()
    return ranks


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Spearman rank correlation, numpy-only (no scipy in the Modal image)."""
    ra, rb = _rank(np.asarray(a, np.float64)), _rank(np.asarray(b, np.float64))
    ca, cb = ra - ra.mean(), rb - rb.mean()
    den = float(np.sqrt((ca * ca).sum() * (cb * cb).sum()))
    return float((ca * cb).sum() / den) if den > 1e-12 else 0.0


def attribution_summary(
    base_p: np.ndarray,
    occluded_p: np.ndarray,
    field_names: list[str],
    *,
    weights: dict[str, float] | None = None,
    threshold: float = 0.5,
) -> dict[str, Any]:
    """Summarize per-field CAUSAL attribution from an occlusion sweep.

    ``occluded_p[i, j]`` is P(match) for pair ``i`` with field ``j`` removed from
    both records. Unlike an R^2, this is a direct interventional claim: how far
    the model's own verdict moves when the evidence is taken away.

    Reports, per field: mean signed delta (``base - occluded``; positive means the
    field was pushing TOWARD match), mean absolute delta (raw causal weight), and
    **flip rate** -- the fraction of pairs whose verdict actually crosses
    ``threshold`` when the field is removed. Flip rate is the number a compliance
    reader wants: "removing birth_place changes the decision in N% of cases."

    When ``weights`` is supplied, also returns the Spearman correlation between
    the causal ranking and the explainer's learned weights -- an INDEPENDENT check
    that the shipped weights rank fields the way ablation says they matter.
    """
    base_p = np.asarray(base_p, dtype=np.float64)
    occluded_p = np.asarray(occluded_p, dtype=np.float64)
    if occluded_p.ndim != 2 or occluded_p.shape[0] != base_p.shape[0]:
        raise ValueError("occluded_p must be (n_pairs, n_fields) aligned with base_p")
    if occluded_p.shape[1] != len(field_names):
        raise ValueError("occluded_p columns must match field_names")

    delta = base_p[:, None] - occluded_p
    base_verdict = base_p >= threshold
    flips = (occluded_p >= threshold) != base_verdict[:, None]

    per_field = []
    for j, f in enumerate(field_names):
        per_field.append({
            "field": f,
            "mean_delta": float(delta[:, j].mean()),
            "mean_abs_delta": float(np.abs(delta[:, j]).mean()),
            "max_abs_delta": float(np.abs(delta[:, j]).max()),
            "flip_rate": float(flips[:, j].mean()),
        })
    ranking = sorted(per_field, key=lambda e: -e["mean_abs_delta"])

    out: dict[str, Any] = {
        "n_pairs": int(base_p.shape[0]),
        "per_field": per_field,
        "ranking": [e["field"] for e in ranking],
        "any_flip_rate": float(flips.any(axis=1).mean()),
    }
    if weights is not None:
        causal = np.array([e["mean_abs_delta"] for e in per_field])
        learned = np.array([float(weights.get(f, 0.0)) for f in field_names])
        out["spearman_vs_learned_weights"] = spearman(causal, learned)
    return out


def label_sae_features(
    feature_acts: np.ndarray, field_feats: np.ndarray, field_names: list[str]
) -> list[dict[str, Any]]:
    """For each SAE feature (column of ``feature_acts``), the field-agreement signal
    it correlates with most strongly -> a human label for that basis direction.
    Returns one entry per feature: best field, that correlation, and the full
    per-field correlation vector."""
    if feature_acts.shape[0] != field_feats.shape[0]:
        raise ValueError("feature_acts and field_feats must have the same #rows")
    labels = []
    fc = field_feats - field_feats.mean(0)
    fden = np.sqrt((fc**2).sum(0))
    for k in range(feature_acts.shape[1]):
        z = feature_acts[:, k]
        zc = z - z.mean()
        zden = np.sqrt((zc**2).sum())
        if zden < 1e-9:
            labels.append({"feature_col": k, "top_field": None, "corr": 0.0, "by_field": {}})
            continue
        corrs = {}
        for j, f in enumerate(field_names):
            d = zden * fden[j]
            corrs[f] = float((zc * fc[:, j]).sum() / d) if d > 1e-9 else 0.0
        best = max(corrs.items(), key=lambda kv: abs(kv[1]))
        labels.append({
            "feature_col": k, "top_field": best[0], "corr": best[1], "by_field": corrs,
        })
    return labels
