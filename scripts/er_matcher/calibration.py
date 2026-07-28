"""Pure temperature-scaling calibration for the ER-matcher eval.

Post-hoc calibration: given per-pair logits `z` (where P(match) = sigmoid(z))
and binary match labels, fit a scalar temperature T minimizing negative
log-likelihood, then apply sigmoid(z / T). Pure stdlib (`math`), box-safe --
no torch/scipy/numpy/network.
"""
from __future__ import annotations

import math

_EPS = 1e-7


def sigmoid(z: float) -> float:
    """Numerically stable logistic sigmoid."""
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def logit(p: float) -> float:
    """Inverse sigmoid, with p clamped to [1e-7, 1 - 1e-7] to avoid +/-inf."""
    p = min(max(p, _EPS), 1.0 - _EPS)
    return math.log(p / (1.0 - p))


def _nll(logits: list[float], labels: list[bool], T: float) -> float:
    total = 0.0
    for z, y in zip(logits, labels):
        p = sigmoid(z / T)
        p = min(max(p, _EPS), 1.0 - _EPS)
        if y:
            total -= math.log(p)
        else:
            total -= math.log(1.0 - p)
    return total


def fit_temperature(
    logits: list[float],
    labels: list[bool],
    *,
    lo: float = 0.05,
    hi: float = 10.0,
    iters: int = 60,
) -> float:
    """Fit a scalar temperature T minimizing NLL via bounded golden-section search.

    Tie-breaks toward T=1.0 when the objective is near-flat (e.g. all-zero
    logits make sigmoid(0/T) == 0.5 for every T, an underdetermined case).
    """
    invphi = (math.sqrt(5.0) - 1.0) / 2.0  # 1/phi
    a, b = lo, hi
    c = b - invphi * (b - a)
    d = a + invphi * (b - a)
    fc = _nll(logits, labels, c)
    fd = _nll(logits, labels, d)
    for _ in range(iters):
        if b - a < 1e-9:
            break
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - invphi * (b - a)
            fc = _nll(logits, labels, c)
        else:
            a, c, fc = c, d, fd
            d = a + invphi * (b - a)
            fd = _nll(logits, labels, d)

    T_best = (a + b) / 2.0
    nll_best = _nll(logits, labels, T_best)
    nll_one = _nll(logits, labels, 1.0)

    # Tie-break toward T=1.0 on a near-flat / underdetermined objective.
    if abs(nll_best - nll_one) < 1e-6 * max(1.0, abs(nll_one)):
        return 1.0

    return T_best


def apply_temperature(z: float, *, T: float) -> float:
    """Apply a fitted temperature to a single logit: sigmoid(z / T)."""
    return sigmoid(z / T)


def ece_from_probs(probs: list[float], labels: list[bool], *, bins: int = 10) -> float:
    """Binned expected calibration error of P(positive) vs the positive label.

    Standard ECE: partition [0, 1] into equal-width bins by predicted
    probability, and for each non-empty bin compute |mean_accuracy -
    mean_confidence|, weighted by the bin's share of the total sample count.
    """
    n = len(probs)
    if n == 0:
        return 0.0

    bin_conf_sum = [0.0] * bins
    bin_acc_sum = [0.0] * bins
    bin_count = [0] * bins

    for p, y in zip(probs, labels):
        idx = min(int(p * bins), bins - 1)
        bin_conf_sum[idx] += p
        bin_acc_sum[idx] += 1.0 if y else 0.0
        bin_count[idx] += 1

    ece = 0.0
    for i in range(bins):
        c = bin_count[i]
        if c == 0:
            continue
        mean_conf = bin_conf_sum[i] / c
        mean_acc = bin_acc_sum[i] / c
        ece += (c / n) * abs(mean_acc - mean_conf)

    return ece
