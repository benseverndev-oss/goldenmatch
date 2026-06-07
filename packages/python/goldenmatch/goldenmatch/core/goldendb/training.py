"""Gradient-based training: learn per-field weights from labeled pairs.

**WORK IN PROGRESS** -- part of the experimental GoldenDB matrix-native backend.

This closes the loop the spec calls "gradient-based probabilistic ER": given labeled
pairs, build the per-field similarity matrix, fit a :class:`GA2MCombiner` by gradient
descent, and read off the learned non-negative field weights. Because the default
combine is a weighted average, those weights can be written straight back onto the
matchkey via :func:`apply_field_weights` -- the normal scorer then consumes the
trained weights with no bespoke code path.

Replaces both Fellegi-Sunter m/u estimation and the controller's sample-and-guess
weight search with one differentiable objective (binary cross-entropy).
"""

from __future__ import annotations

import numpy as np

from goldenmatch.config.schemas import MatchkeyConfig
from goldenmatch.core.goldendb._combine import GA2MCombiner


def build_training_matrix(
    df,
    mk: MatchkeyConfig,
    labeled_pairs: list[tuple[int, int, float]],
) -> tuple[np.ndarray, np.ndarray]:
    """Build ``(sims[P, K], labels[P])`` from labeled ``(row_id_a, row_id_b, label)``.

    Per-field similarity is the same kernel the scorer uses (char-ngram cosine for
    fuzzy fields, exact match for exact fields), zeroed where either side is null.
    Pairs whose row ids are absent from ``df`` are skipped.
    """
    from goldenmatch.core.goldendb.scorer import _prep_fields

    fields = list(mk.fields)
    prepped = _prep_fields(df, fields)
    row_ids = df["__row_id__"].to_list()
    pos = {rid: i for i, rid in enumerate(row_ids)}

    I, J, labels = [], [], []
    for a, b, label in labeled_pairs:
        if a in pos and b in pos:
            I.append(pos[a])
            J.append(pos[b])
            labels.append(float(label))
    if not I:
        return np.zeros((0, len(prepped))), np.zeros((0,))

    I_arr = np.array(I)
    J_arr = np.array(J)
    sims = np.zeros((len(I), len(prepped)), dtype=np.float64)
    for ki, fld in enumerate(prepped):
        validk = (~(fld.null[I_arr] | fld.null[J_arr])).astype(np.float64)
        if fld.is_exact:
            varr = np.array(fld.values, dtype=object)
            simk = (varr[I_arr] == varr[J_arr]).astype(np.float64)
        else:
            simk = np.clip(np.einsum("pd,pd->p", fld.emb[I_arr], fld.emb[J_arr]), 0.0, 1.0)
        sims[:, ki] = simk * validk
    return sims, np.array(labels)


def fit_field_weights(
    df,
    mk: MatchkeyConfig,
    labeled_pairs: list[tuple[int, int, float]],
    steps: int = 300,
    lr: float = 0.05,
    seed: int = 0,
) -> tuple[GA2MCombiner, dict[str, float]]:
    """Fit a :class:`GA2MCombiner` on labeled pairs; return it plus a
    ``{field_name: learned_weight}`` mapping (the non-negative ``softplus`` weights).
    """
    sims, labels = build_training_matrix(df, mk, labeled_pairs)
    if len(labels) == 0:
        raise ValueError("No labeled pairs matched df __row_id__; nothing to train on.")

    combiner = GA2MCombiner(n_fields=sims.shape[1], seed=seed)
    for _ in range(steps):
        combiner.train_step(sims, labels, lr=lr)

    weights = combiner.field_weights()
    names = [f.resolved_field for f in mk.fields]
    return combiner, {name: float(w) for name, w in zip(names, weights)}


def apply_field_weights(mk: MatchkeyConfig, weights: dict[str, float]) -> MatchkeyConfig:
    """Return a copy of ``mk`` with each field's ``weight`` replaced by the learned
    value from ``weights`` (keyed by field name). Fields absent from ``weights`` keep
    their existing weight. The normal weighted-average scorer then uses the trained
    weights directly.
    """
    new = mk.model_copy(deep=True)
    for f in new.fields:
        name = f.field
        if name in weights:
            f.weight = weights[name]
    return new
