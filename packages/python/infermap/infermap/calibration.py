"""Confidence calibration for infermap.

Transforms raw mapping confidences into calibrated probabilities so that
``confidence == p(correct)`` in aggregate. Used post-assignment only — the
calibrator does not influence which mappings the engine picks, only the
scores attached to them. This keeps F1/top-1/MRR bit-identical while moving
ECE.

Calibrators are fitted offline from ``(raw_score, was_correct)`` pairs
collected by running the engine on labeled cases. See
``benchmark/runners/python/infermap_bench/calibrate.py`` for the fitting
CLI.

Serialization uses JSON via ``to_dict``/``from_dict`` with a ``kind``
discriminator — no opaque binary formats (for security and portability).
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import numpy as np


class Calibrator(ABC):
    """Abstract base: fit on labeled pairs, transform raw scores."""

    kind: str = "abstract"

    @abstractmethod
    def fit(self, scores: np.ndarray, correct: np.ndarray) -> None: ...

    @abstractmethod
    def transform(self, scores: np.ndarray) -> np.ndarray: ...

    @abstractmethod
    def to_dict(self) -> dict[str, Any]: ...

    @classmethod
    @abstractmethod
    def from_dict(cls, d: dict[str, Any]) -> "Calibrator": ...


class IdentityCalibrator(Calibrator):
    """Passthrough — useful as a default and in tests."""

    kind = "identity"

    def fit(self, scores: np.ndarray, correct: np.ndarray) -> None:
        return None

    def transform(self, scores: np.ndarray) -> np.ndarray:
        return np.asarray(scores, dtype=float).copy()

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "IdentityCalibrator":
        return cls()


class IsotonicCalibrator(Calibrator):
    """Monotonic non-parametric calibration via pool-adjacent-violators.

    Stores ``x`` (breakpoint scores) and ``y`` (monotonic calibrated values),
    applied at inference via piecewise-linear interpolation
    (``numpy.interp``), clamped to ``[min(x), max(x)]``.
    """

    kind = "isotonic"

    def __init__(self, x: np.ndarray | None = None, y: np.ndarray | None = None):
        self.x = np.asarray(x, dtype=float) if x is not None else np.array([0.0, 1.0])
        self.y = np.asarray(y, dtype=float) if y is not None else np.array([0.0, 1.0])

    def fit(self, scores: np.ndarray, correct: np.ndarray) -> None:
        scores = np.asarray(scores, dtype=float)
        correct = np.asarray(correct, dtype=float)
        if len(scores) == 0:
            return
        # Sort by score ascending (stable for determinism)
        order = np.argsort(scores, kind="stable")
        xs = scores[order]
        ys = correct[order]
        # Pool-adjacent-violators: maintain stacks of (value, weight).
        vals: list[float] = []
        wts: list[float] = []
        for y in ys:
            vals.append(float(y))
            wts.append(1.0)
            while len(vals) >= 2 and vals[-2] > vals[-1]:
                v2 = vals.pop()
                w2 = wts.pop()
                v1 = vals.pop()
                w1 = wts.pop()
                vals.append((v1 * w1 + v2 * w2) / (w1 + w2))
                wts.append(w1 + w2)
        # Expand pooled values back to per-point fitted ys
        fitted = np.empty_like(ys)
        idx = 0
        for v, w in zip(vals, wts):
            k = int(round(w))
            fitted[idx : idx + k] = v
            idx += k
        # Deduplicate by xs: for identical x, average the fitted values
        uniq_x, inv = np.unique(xs, return_inverse=True)
        uniq_y = np.zeros_like(uniq_x)
        counts = np.zeros_like(uniq_x)
        for i, u in enumerate(inv):
            uniq_y[u] += fitted[i]
            counts[u] += 1
        uniq_y = uniq_y / np.maximum(counts, 1)
        # Enforce monotonicity after dedup (defensive).
        uniq_y = np.maximum.accumulate(uniq_y)
        self.x = uniq_x.astype(float)
        self.y = np.clip(uniq_y, 0.0, 1.0).astype(float)

    def transform(self, scores: np.ndarray) -> np.ndarray:
        scores = np.asarray(scores, dtype=float)
        if len(self.x) == 0:
            return scores.copy()
        return np.interp(scores, self.x, self.y, left=self.y[0], right=self.y[-1])

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "x": self.x.tolist(), "y": self.y.tolist()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "IsotonicCalibrator":
        return cls(x=np.array(d["x"], dtype=float), y=np.array(d["y"], dtype=float))


class PlattCalibrator(Calibrator):
    """Sigmoid calibration: ``p = 1 / (1 + exp(-(a*x + b)))``.

    Fit by minimizing binary cross-entropy via ``scipy.optimize.minimize``.
    """

    kind = "platt"

    def __init__(self, a: float = 1.0, b: float = 0.0):
        self.a = float(a)
        self.b = float(b)

    def fit(self, scores: np.ndarray, correct: np.ndarray) -> None:
        from scipy.optimize import minimize

        scores = np.asarray(scores, dtype=float)
        correct = np.asarray(correct, dtype=float)
        if len(scores) == 0:
            return

        def nll(params: np.ndarray) -> float:
            a, b = params
            z = a * scores + b
            logp = -np.logaddexp(0.0, -z)  # log sigmoid(z)
            log1mp = -np.logaddexp(0.0, z)  # log (1-sigmoid(z))
            return -float(np.sum(correct * logp + (1.0 - correct) * log1mp))

        res = minimize(nll, x0=np.array([1.0, 0.0]), method="Nelder-Mead")
        self.a = float(res.x[0])
        self.b = float(res.x[1])

    def transform(self, scores: np.ndarray) -> np.ndarray:
        scores = np.asarray(scores, dtype=float)
        z = self.a * scores + self.b
        return np.where(z >= 0, 1.0 / (1.0 + np.exp(-z)), np.exp(z) / (1.0 + np.exp(z)))

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "a": self.a, "b": self.b}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PlattCalibrator":
        return cls(a=float(d["a"]), b=float(d["b"]))


_REGISTRY: dict[str, type[Calibrator]] = {
    "identity": IdentityCalibrator,
    "isotonic": IsotonicCalibrator,
    "platt": PlattCalibrator,
}


def calibrator_from_dict(d: dict[str, Any]) -> Calibrator:
    kind = d.get("kind")
    if kind not in _REGISTRY:
        raise ValueError(f"Unknown calibrator kind: {kind!r}")
    return _REGISTRY[kind].from_dict(d)


def save_calibrator(cal: Calibrator, path: str | Path) -> None:
    Path(path).write_text(json.dumps(cal.to_dict(), indent=2), encoding="utf-8")


def load_calibrator(path: str | Path) -> Calibrator:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return calibrator_from_dict(d)


# TODO(ts-port): TypeScript parity. Port IdentityCalibrator, IsotonicCalibrator,
# and PlattCalibrator to infermap-js with the same JSON schema
# ({kind, x, y} / {kind, a, b}) so Python-fitted calibrators can be loaded by
# the JS engine. PAV is ~40 LOC with no heavy deps; Platt needs a small MLE
# (hand-rolled Nelder-Mead or gradient descent) since scipy isn't available in
# JS. Tracked as a follow-up.
