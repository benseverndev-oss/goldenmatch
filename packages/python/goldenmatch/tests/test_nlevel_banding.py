"""N-level banding tests: comparison_vector + _levels_from_similarity."""
import numpy as np
import pydantic
import pytest
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
    with pytest.raises(pydantic.ValidationError, match="levels"):
        MatchkeyField(field="x", scorer="exact", levels=1)


def test_custom_thresholds_scalar_midband():
    # exact-boundary similarity in a middle band, scalar path
    mk = _mk(field="name", scorer="exact", levels=3, level_thresholds=[1.0, 0.5])
    # exact scorer: same value -> 1.0 -> level 2; different -> 0.0 -> level 0
    assert comparison_vector({"name": "a"}, {"name": "a"}, mk) == [2]
    assert comparison_vector({"name": "a"}, {"name": "b"}, mk) == [0]


def test_fallback_and_neutral_u_nlevel():
    from goldenmatch.core.probabilistic import _fallback_result
    mk = _mk(field="name", scorer="jaro_winkler", levels=5,
             level_thresholds=[1.0, 0.95, 0.9, 0.85])
    r = _fallback_result(mk)
    assert len(r.m_probs["name"]) == 5
    assert len(r.u_probs["name"]) == 5
    assert abs(sum(r.m_probs["name"]) - 1.0) < 1e-9


def test_fallback_2_and_3_level_literals_unchanged():
    # Back-compat guarantee from the spec: existing 2/3-level behavior untouched.
    from goldenmatch.core.probabilistic import _fallback_result
    r2 = _fallback_result(_mk(field="x", scorer="exact", levels=2))
    assert r2.m_probs["x"] == [0.1, 0.9]
    r3 = _mk(field="x", scorer="jaro_winkler", levels=3)
    r3 = _fallback_result(r3)
    assert r3.m_probs["x"] == [0.05, 0.15, 0.80]
