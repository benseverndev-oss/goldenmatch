"""The band-override student: a small GBDT over comparison features.

Trained OFFLINE (ours) on teacher (LLM) or gold labels; it OVERRIDES FS on the
uncertain band only. Small + nonlinear — it expresses the field-interaction
correlations FS's linear/conditional-independence model can't (spec 2026-07-31).
Portable-format export for the shipped artifact is Phase 2; here we keep the
sklearn estimator for the offline harness + gate runs.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from sklearn.ensemble import HistGradientBoostingClassifier


@dataclass
class BandStudent:
    """A trained band classifier. ``fields`` fixes the feature layout it expects."""

    fields: list[str]
    max_iter: int = 200
    max_depth: int = 4
    learning_rate: float = 0.1
    l2: float = 1.0
    seed: int = 0
    _clf: HistGradientBoostingClassifier | None = field(default=None, repr=False)

    def fit(self, X: Sequence[Sequence[float]], y: Sequence[int]) -> BandStudent:
        # Lazy import: the package must import without sklearn (offline dep) so
        # pytest collection never hard-fails where the harness isn't exercised.
        from sklearn.ensemble import HistGradientBoostingClassifier

        self._clf = HistGradientBoostingClassifier(
            max_iter=self.max_iter, max_depth=self.max_depth,
            learning_rate=self.learning_rate, l2_regularization=self.l2,
            random_state=self.seed,
        )
        self._clf.fit(np.asarray(X, dtype=np.float32), np.asarray(y))
        return self

    def predict(self, X: Sequence[Sequence[float]]) -> np.ndarray:
        """Binary match decisions (1=match) for the given feature rows."""
        return self._estimator().predict(np.asarray(X, dtype=np.float32))

    def predict_proba(self, X: Sequence[Sequence[float]]) -> np.ndarray:
        """P(match) — for calibration (gate #3) + threshold tuning."""
        return self._estimator().predict_proba(np.asarray(X, dtype=np.float32))[:, 1]

    def save(self, path: str) -> None:
        import joblib
        joblib.dump({"fields": self.fields, "clf": self._estimator()}, path)

    @classmethod
    def load(cls, path: str) -> BandStudent:
        import joblib
        blob = joblib.load(path)
        s = cls(fields=list(blob["fields"]))
        s._clf = blob["clf"]
        return s

    def _estimator(self) -> HistGradientBoostingClassifier:
        if self._clf is None:
            raise RuntimeError("BandStudent is not fitted; call fit() or load() first.")
        return self._clf
