"""The trained `drug` SynonymModel (GS2).

Loads the committed logistic weights (`data/drug_synonym_model.json`, reproducible
from the public pairs via `train.py`) and scores a pair = sigmoid(features·w).
Honest by measurement: lifts morphological synonyms over Jaro-Winkler; scores
arbitrary brand<->generic LOW (no morphological signal). Graceful: missing weights
-> `score` returns None (the scorer falls back to JW).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .train import _sigmoid, pair_features

_MODEL_PATH = Path(__file__).resolve().parent / "data" / "drug_synonym_model.json"


class DrugSynonymModel:
    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path else _MODEL_PATH
        self._w: np.ndarray | None = None
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self._path.exists():
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            self._w = np.asarray(payload["weights"], dtype=float)

    def score(self, a: str, b: str) -> float | None:
        self._load()
        if self._w is None or not a or not b:
            return None
        return float(_sigmoid(pair_features(a, b) @ self._w))


def register_drug_model() -> None:
    """Register the trained drug model for the `drug` domain."""
    from .providers import register_synonym_model

    register_synonym_model("drug", DrugSynonymModel())
