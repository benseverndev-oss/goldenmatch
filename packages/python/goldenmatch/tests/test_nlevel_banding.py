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
    # Real jaro_winkler similarity landing in a MIDDLE band, scalar path.
    # score_field('martha', 'marhta', 'jaro_winkler') measures ~0.9611
    # (see goldenmatch.core.scorer.score_field). With level_thresholds=
    # [1.0, 0.9] on a 3-level field: 0.9611 >= 0.9 but < 1.0 -> 1 threshold
    # satisfied -> level 1 (the middle band, not top/bottom).
    mk = _mk(field="name", scorer="jaro_winkler", levels=3,
             level_thresholds=[1.0, 0.9])
    assert comparison_vector({"name": "martha"}, {"name": "marhta"}, mk) == [1]
    # exact match still lands top level 2; totally different lands level 0.
    assert comparison_vector({"name": "martha"}, {"name": "martha"}, mk) == [2]
    assert comparison_vector({"name": "martha"}, {"name": "zzzzzz"}, mk) == [0]


def test_fallback_nlevel():
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
    assert r2.u_probs["x"] == [0.9, 0.1]
    mk3 = _mk(field="x", scorer="jaro_winkler", levels=3)
    r3 = _fallback_result(mk3)
    assert r3.m_probs["x"] == [0.05, 0.15, 0.80]
    assert r3.u_probs["x"] == [0.80, 0.15, 0.05]


# --- Native FS kernel guard for level_thresholds -----------------------------
#
# Investigation verdict (Task 4): the Rust kernel assigns comparison levels
# itself, NOT Python. `score_probabilistic_native` (goldenmatch/core/
# probabilistic.py ~1956-1968) builds `levels = [int(f.levels) for f in
# mk.fields]` and `partials = [float(f.partial_threshold) for f in mk.fields]`
# and passes only those two (a level *count* + a single partial threshold) to
# `native_module().score_block_pairs_fs(...)` -- `f.level_thresholds` is never
# read or passed across the FFI boundary anywhere in that function. The kernel
# therefore bands raw rapidfuzz-rs similarities into levels using its own
# hard-coded default banding (2/3-level partial_threshold rule + even-spaced
# N-level; see score.rs fs_level_from_sim), with no notion of an arbitrary
# N-level CUSTOM threshold list -- custom level_thresholds lists never cross
# the FFI. This differs from the vectorized/scalar paths, where
# `f.level_thresholds` is threaded straight into `_levels_from_similarity`
# (probabilistic.py lines 1579, 1691) and `comparison_vector` (line 325-330).
# Since the kernel can't reproduce N-level custom banding, `_fs_native_eligible`
# must decline (fall back to numpy/scalar) whenever any field sets
# `level_thresholds`.


def _fake_native_module():
    class _Fake:
        def score_block_pairs_fs(self, *a, **kw):  # pragma: no cover - not invoked
            raise NotImplementedError

    return _Fake()


def test_level_thresholds_not_native_eligible_synthetic(monkeypatch):
    """Guard logic in isolation: force "native enabled" without a real build.

    Runs in every environment (this worktree has no native module), unlike the
    skipif-guarded real-kernel test below.
    """
    from goldenmatch.core import probabilistic as p

    monkeypatch.setattr(p, "_fs_native_enabled", lambda: True)
    monkeypatch.setattr(
        "goldenmatch.core._native_loader.native_module", _fake_native_module
    )

    mk_plain = _mk(field="name", scorer="jaro_winkler", levels=3, partial_threshold=0.8)
    assert p._fs_native_eligible(mk_plain) is True

    mk_custom = _mk(field="name", scorer="jaro_winkler", levels=4,
                    level_thresholds=[1.0, 0.92, 0.88])
    assert p._fs_native_eligible(mk_custom) is False


def test_level_thresholds_router_selects_non_native_scorer(monkeypatch):
    """Router-level guard: probabilistic_block_scorer must not hand a
    level_thresholds matchkey to the native closure, even with native mocked
    "available". It should fall through to the vectorized numpy scorer
    (jaro_winkler is vectorized_scorer_supported), named ``_scorer`` --
    see probabilistic_block_scorer's ``_native`` / ``_scorer`` / ``_scalar``
    closures.
    """
    from goldenmatch.core.probabilistic import _fallback_result
    from goldenmatch.core import probabilistic as p

    monkeypatch.setattr(p, "_fs_native_enabled", lambda: True)
    monkeypatch.setattr(
        "goldenmatch.core._native_loader.native_module", _fake_native_module
    )

    mk_custom = _mk(field="name", scorer="jaro_winkler", levels=4,
                    level_thresholds=[1.0, 0.92, 0.88])
    em = _fallback_result(mk_custom)
    scorer = p.probabilistic_block_scorer(mk_custom, em)
    assert scorer.__name__ == "_scorer"


def _native_fs_available():
    try:
        from goldenmatch.core import _native_loader
        return _native_loader.native_available() and hasattr(
            _native_loader.native_module(), "score_block_pairs_fs"
        )
    except Exception:
        return False


@pytest.mark.skipif(not _native_fs_available(), reason="native FS kernel not built")
def test_level_thresholds_not_native_eligible_real_kernel(monkeypatch):
    """Same assertion against the real native build, when one is present (CI)."""
    from goldenmatch.core import probabilistic as p

    monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "1")
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")

    mk_plain = _mk(field="name", scorer="jaro_winkler", levels=3, partial_threshold=0.8)
    assert p._fs_native_eligible(mk_plain) is True

    mk_custom = _mk(field="name", scorer="jaro_winkler", levels=4,
                    level_thresholds=[1.0, 0.92, 0.88])
    assert p._fs_native_eligible(mk_custom) is False
