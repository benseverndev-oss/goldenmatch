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
) -> np.ndarray:
    """Per-field jaro-winkler agreement for each pair -> (n_pairs, n_fields) in
    [0, 1]. These are the HUMAN primitives ("do the surnames agree?"). Missing on
    either side -> 0.0 (no positive agreement evidence). Pure; needs jellyfish."""
    import jellyfish

    out = np.zeros((len(pairs), len(fields)), dtype=np.float64)
    for i, (a, b, _t) in enumerate(pairs):
        ra, rb = rows[a], rows[b]
        for j, f in enumerate(fields):
            va, vb = str(ra.get(f, "") or "").strip(), str(rb.get(f, "") or "").strip()
            if not va or not vb:
                out[i, j] = 0.0
            elif va == vb:
                out[i, j] = 1.0
            else:
                out[i, j] = float(jellyfish.jaro_winkler_similarity(va, vb))
    return out


def attribute_direction(
    projections: np.ndarray, field_feats: np.ndarray, field_names: list[str]
) -> dict[str, Any]:
    """Regress the direction-PROJECTION onto standardized field-agreement signals.

    Returns per-field standardized coefficients (the field's contribution to the
    match primitive), their ranking, and R^2 (how much of the projection the human
    field story explains -- the faithfulness of the abstraction). Standardizing
    both sides makes coefficients comparable across fields with different spreads.
    """
    if field_feats.shape[0] != projections.shape[0]:
        raise ValueError("projections and field_feats must have the same #rows")
    if field_feats.shape[1] != len(field_names):
        raise ValueError("field_feats columns must match field_names")
    from sklearn.linear_model import LinearRegression

    xs = field_feats.std(0)
    Xz = (field_feats - field_feats.mean(0)) / np.where(xs < 1e-9, 1.0, xs)
    ys = projections.std()
    yz = (projections - projections.mean()) / (ys if ys > 1e-9 else 1.0)

    reg = LinearRegression().fit(Xz, yz)
    r2 = float(reg.score(Xz, yz))
    coefs = {field_names[j]: float(reg.coef_[j]) for j in range(len(field_names))}
    ranking = sorted(coefs.items(), key=lambda kv: -abs(kv[1]))
    return {
        "r2": r2,
        "coefficients": coefs,
        "ranking": [{"field": f, "coef": c} for f, c in ranking],
        "top_field": ranking[0][0] if ranking else None,
    }


def richer_field_features(
    rows: dict[int, dict[str, Any]],
    pairs: list[tuple[int, int, int]],
    fields: list[str],
) -> tuple[np.ndarray, list[str]]:
    """Expanded per-field features -> ((n_pairs, 6*n_fields), names).

    Six signals per field, all cheap and human-nameable: jaro-winkler
    ``agreement``, ``exact`` (identical), ``missing`` (absent on either side),
    ``conflict`` (present on both sides but agreement <= 0.6), ``len_ratio``
    (shorter/longer, 0 if missing), and ``edit_norm`` (normalized Levenshtein
    distance). The extra signals let a fit distinguish cases the single
    agreement scalar conflates -- notably *missing* (no evidence) from
    *conflicting* (negative evidence), which both sit near agreement 0.

    Pure; needs jellyfish. Used to measure how much faithfulness headroom the
    simple 1-signal-per-field basis leaves on the table.
    """
    import jellyfish

    names = [f"{f}__{s}" for f in fields
             for s in ("agreement", "exact", "missing", "conflict", "len_ratio", "edit_norm")]
    out = np.zeros((len(pairs), 6 * len(fields)), dtype=np.float64)
    for i, (a, b, _t) in enumerate(pairs):
        ra, rb = rows[a], rows[b]
        for j, f in enumerate(fields):
            va, vb = str(ra.get(f, "") or "").strip(), str(rb.get(f, "") or "").strip()
            base = 6 * j
            if not va or not vb:
                out[i, base + 2] = 1.0  # missing; the rest stay 0
                continue
            if va == vb:
                agr, edit = 1.0, 0.0
            else:
                agr = float(jellyfish.jaro_winkler_similarity(va, vb))
                edit = jellyfish.levenshtein_distance(va, vb) / max(len(va), len(vb))
            out[i, base + 0] = agr
            out[i, base + 1] = 1.0 if va == vb else 0.0
            out[i, base + 3] = 1.0 if agr <= 0.6 else 0.0
            out[i, base + 4] = min(len(va), len(vb)) / max(len(va), len(vb))
            out[i, base + 5] = float(edit)
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


def affine_r2(
    train_score: np.ndarray, train_y: np.ndarray,
    test_score: np.ndarray, test_y: np.ndarray,
) -> dict[str, float]:
    """R^2 of a FROZEN 1-D score against ``y``, fitting only intercept + scale.

    Two free parameters (a monotone affine link), fit on train and evaluated on
    test -- so the reported number credits the fixed weights, not a refit. This
    is the honest "what does the shipped explainer actually explain?" measure,
    as opposed to refitting the weights (which yields a ceiling).
    """
    if train_score.shape[0] != train_y.shape[0] or test_score.shape[0] != test_y.shape[0]:
        raise ValueError("scores and targets must align")
    xc = train_score - train_score.mean()
    var = float((xc * xc).sum())
    slope = float((xc * (train_y - train_y.mean())).sum() / var) if var > 1e-12 else 0.0
    intercept = float(train_y.mean() - slope * train_score.mean())

    def _r2(s: np.ndarray, y: np.ndarray) -> float:
        ss_res = float(((y - (slope * s + intercept)) ** 2).sum())
        ss_tot = float(((y - y.mean()) ** 2).sum())
        return 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0

    return {
        "r2_test": _r2(test_score, test_y), "r2_train": _r2(train_score, train_y),
        "slope": slope, "intercept": intercept,
    }


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
