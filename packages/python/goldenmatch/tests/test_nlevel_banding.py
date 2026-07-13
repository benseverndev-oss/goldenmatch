"""N-level banding tests: comparison_vector + _levels_from_similarity."""
import numpy as np
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.probabilistic import comparison_vector, _levels_from_similarity


def _mk(**field_kw):
    return MatchkeyConfig(name="t", type="probabilistic",
                          fields=[MatchkeyField(**field_kw)])


def test_custom_thresholds_scalar():
    mk = _mk(field="name", scorer="jaro_winkler", levels=4,
             level_thresholds=[1.0, 0.92, 0.88])
    # identical strings -> similarity 1.0 -> top level 3
    assert comparison_vector({"name": "smith"}, {"name": "smith"}, mk) == [3]
    # totally different -> level 0
    assert comparison_vector({"name": "smith"}, {"name": "qqqqq"}, mk) == [0]


def test_levels_from_similarity_custom():
    sim = np.array([1.0, 0.95, 0.90, 0.5, 0.88])
    lvl = _levels_from_similarity(sim, 4, 0.8, level_thresholds=[1.0, 0.92, 0.88])
    # counts of satisfied descending thresholds
    assert lvl.tolist() == [3, 2, 1, 0, 1]


def test_levels_from_similarity_legacy_unchanged():
    sim = np.array([1.0, 0.9, 0.5])
    assert _levels_from_similarity(sim, 3, 0.8).tolist() == [2, 1, 0]
    assert _levels_from_similarity(sim, 2, 0.8).tolist() == [1, 1, 0]


def test_levels_lower_bound():
    import pytest
    with pytest.raises(Exception):
        MatchkeyField(field="x", scorer="exact", levels=1)
