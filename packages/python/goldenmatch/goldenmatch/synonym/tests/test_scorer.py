from __future__ import annotations

import json

import numpy as np
from rapidfuzz.distance import JaroWinkler

from goldenmatch.synonym.scorer import SynonymScorer
from goldenmatch.synonym.table import SynonymTable


def _table(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"aliases": {"ibuprofen": ["Advil"]}}), encoding="utf-8")
    return SynonymTable.from_json(p)


def test_table_hit_is_one(tmp_path):
    s = SynonymScorer(domain="drug", table=_table(tmp_path))
    assert s.score_pair("Advil", "ibuprofen") == 1.0


def test_falls_back_to_jw_when_no_table_no_model(tmp_path):
    s = SynonymScorer(domain="drug", table=_table(tmp_path))  # default stub model
    assert s.score_pair("Advil", "Advel") == float(JaroWinkler.similarity("Advil", "Advel"))


def test_model_used_when_present_table_still_wins(tmp_path):
    class M:
        def score(self, a, b):
            return 0.9

    s = SynonymScorer(domain="drug", table=_table(tmp_path), model=M())
    assert s.score_pair("Advil", "Tylenol") == 0.9  # no table hit -> model
    assert s.score_pair("Advil", "ibuprofen") == 1.0  # table equivalence beats model


def test_none_returns_none(tmp_path):
    s = SynonymScorer(table=_table(tmp_path))
    assert s.score_pair(None, "x") is None
    assert s.score_pair("x", None) is None


def test_score_matrix_symmetric_table_aware(tmp_path):
    s = SynonymScorer(domain="drug", table=_table(tmp_path))
    m = s.score_matrix(["Advil", "ibuprofen", "Tylenol"])
    assert m.shape == (3, 3)
    assert m.dtype == np.float32
    assert m[0, 0] == 1.0 and m[1, 1] == 1.0
    assert m[0, 1] == 1.0 and m[1, 0] == 1.0  # table equivalence
    assert np.allclose(m, m.T)  # symmetric
