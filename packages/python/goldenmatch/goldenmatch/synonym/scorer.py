"""SynonymScorer: a pluggable, model-capable synonym ScorerPlugin.

Scoring precedence per pair: known table equivalence -> 1.0; else the domain's
trained SynonymModel (if it has an opinion); else Jaro-Winkler. JW is computed via
`rapidfuzz` DIRECTLY (NOT `core.scorer`) so this module stays import-light and free
of a circular import (it's imported at goldenmatch package init to register).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from rapidfuzz.distance import JaroWinkler
from rapidfuzz.process import cdist

from .model import SynonymModel
from .providers import resolve_synonym_model
from .table import SynonymTable

_DATA_DIR = Path(__file__).resolve().parent / "data"


class SynonymScorer:
    name = "synonym"

    def __init__(
        self,
        domain: str = "generic",
        table: SynonymTable | None = None,
        model: SynonymModel | None = None,
    ):
        self.domain = domain
        # Injected table wins; else auto-load the per-domain knowledge base
        # data/<domain>_synonyms.json (missing -> empty, graceful). This is the
        # deterministic production path for arbitrary synonyms (a curated lookup,
        # like the refdata alias tables) the trained morphological model can't reach.
        if table is not None:
            self._table = table
        else:
            self._table = SynonymTable.from_json(_DATA_DIR / f"{domain}_synonyms.json")
        # Injected model wins; otherwise resolve LAZILY per call so a model
        # registered AFTER this scorer was registered (at import) is still used.
        self._model = model

    def _get_model(self) -> SynonymModel:
        return self._model if self._model is not None else resolve_synonym_model(self.domain)

    def score_pair(self, val_a: str | None, val_b: str | None) -> float | None:
        if val_a is None or val_b is None:
            return None
        if self._table.are_equivalent(val_a, val_b):
            return 1.0
        m = self._get_model().score(val_a, val_b)
        if m is not None:
            return float(m)
        return float(JaroWinkler.similarity(val_a, val_b))

    def score_matrix(self, values: list[str | None]) -> np.ndarray:
        n = len(values)
        clean = [v if v is not None else "" for v in values]
        # Vectorized JW base (float32), mirroring refdata/scorer.py's cdist path.
        mat = np.asarray(
            cdist(clean, clean, scorer=JaroWinkler.similarity), dtype=np.float32
        )
        model = self._get_model()
        for i in range(n):
            mat[i, i] = np.float32(1.0)
            vi = values[i]
            for j in range(i + 1, n):
                vj = values[j]
                if vi is None or vj is None:
                    continue
                if self._table.are_equivalent(vi, vj):
                    s: float | None = 1.0
                else:
                    s = model.score(vi, vj)
                if s is not None:
                    mat[i, j] = mat[j, i] = np.float32(s)
        return mat
