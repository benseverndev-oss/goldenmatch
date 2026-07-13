"""N-level banding tests: comparison_vector + _levels_from_similarity."""
import os

import numpy as np
import pydantic
import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.probabilistic import _levels_from_similarity, comparison_vector


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


# --- Native FS kernel routing for level_thresholds ---------------------------
#
# The Rust kernel assigns comparison levels itself. `score_probabilistic_native`
# passes `levels` (a level count) + `partial_threshold` per field, and -- since
# goldenmatch-native 0.1.14 -- an optional per-field `level_thresholds` list
# that `score.rs fs_level_from_sim` bands with the exact
# `_levels_from_similarity` custom semantics (level = count of satisfied
# descending thresholds, `>=` inclusive). Support is advertised by the module
# const `FS_SUPPORTS_LEVEL_THRESHOLDS`; `_fs_native_eligible` declines a
# level_thresholds matchkey only when the loaded kernel does NOT advertise it
# (a pre-0.1.14 wheel), keeping the pure-Python fallback for stale
# `pip install goldenmatch[native]` environments while routing everything else
# natively. The vectorized/scalar paths still thread `f.level_thresholds` into
# `_levels_from_similarity` / `comparison_vector` directly.


def _fake_native_module_old_wheel():
    """A kernel WITHOUT FS_SUPPORTS_LEVEL_THRESHOLDS (pre-0.1.14 wheel)."""
    class _Fake:
        def score_block_pairs_fs(self, *a, **kw):  # pragma: no cover - not invoked
            raise NotImplementedError

    return _Fake()


def _fake_native_module_supporting():
    """A kernel advertising level_thresholds support (goldenmatch-native >= 0.1.14)."""
    class _Fake:
        FS_SUPPORTS_LEVEL_THRESHOLDS = True

        def score_block_pairs_fs(self, *a, **kw):  # pragma: no cover - not invoked
            raise NotImplementedError

    return _Fake()


def _mk_custom():
    return _mk(field="name", scorer="jaro_winkler", levels=4,
               level_thresholds=[1.0, 0.92, 0.88])


def test_level_thresholds_not_eligible_on_old_wheel_synthetic(monkeypatch):
    """Old-wheel behavior pinned: a kernel that does NOT advertise
    FS_SUPPORTS_LEVEL_THRESHOLDS must decline level_thresholds matchkeys
    (plain matchkeys stay eligible). Runs in every environment.
    """
    from goldenmatch.core import probabilistic as p

    monkeypatch.setattr(p, "_fs_native_enabled", lambda: True)
    monkeypatch.setattr(
        "goldenmatch.core._native_loader.native_module", _fake_native_module_old_wheel
    )

    mk_plain = _mk(field="name", scorer="jaro_winkler", levels=3, partial_threshold=0.8)
    assert p._fs_native_eligible(mk_plain) is True
    assert p._fs_native_eligible(_mk_custom()) is False


def test_level_thresholds_eligible_on_supporting_kernel_synthetic(monkeypatch):
    """A kernel advertising FS_SUPPORTS_LEVEL_THRESHOLDS accepts
    level_thresholds matchkeys (and plain matchkeys stay eligible too)."""
    from goldenmatch.core import probabilistic as p

    monkeypatch.setattr(p, "_fs_native_enabled", lambda: True)
    monkeypatch.setattr(
        "goldenmatch.core._native_loader.native_module", _fake_native_module_supporting
    )

    mk_plain = _mk(field="name", scorer="jaro_winkler", levels=3, partial_threshold=0.8)
    assert p._fs_native_eligible(mk_plain) is True
    assert p._fs_native_eligible(_mk_custom()) is True


def test_level_thresholds_router_selects_native_when_supported(monkeypatch):
    """With a supporting kernel, probabilistic_block_scorer hands a
    level_thresholds matchkey to the native closure (``_native``)."""
    from goldenmatch.core import probabilistic as p
    from goldenmatch.core.probabilistic import _fallback_result

    monkeypatch.setattr(p, "_fs_native_enabled", lambda: True)
    monkeypatch.setattr(
        "goldenmatch.core._native_loader.native_module", _fake_native_module_supporting
    )

    mk_custom = _mk_custom()
    em = _fallback_result(mk_custom)
    scorer = p.probabilistic_block_scorer(mk_custom, em)
    assert scorer.__name__ == "_native"


def test_level_thresholds_router_falls_back_on_old_wheel(monkeypatch):
    """With a NON-supporting kernel, the router must not hand a
    level_thresholds matchkey to the native closure. It falls through to the
    vectorized numpy scorer (jaro_winkler is vectorized_scorer_supported),
    named ``_scorer`` -- see probabilistic_block_scorer's ``_native`` /
    ``_scorer`` / ``_scalar`` closures.
    """
    from goldenmatch.core import probabilistic as p
    from goldenmatch.core.probabilistic import _fallback_result

    monkeypatch.setattr(p, "_fs_native_enabled", lambda: True)
    monkeypatch.setattr(
        "goldenmatch.core._native_loader.native_module", _fake_native_module_old_wheel
    )

    mk_custom = _mk_custom()
    em = _fallback_result(mk_custom)
    scorer = p.probabilistic_block_scorer(mk_custom, em)
    assert scorer.__name__ == "_scorer"


# native_available() is importability only -- it does NOT read GOLDENMATCH_NATIVE.
# Honor an explicit forced-off run (pure-Python lane) by skipping the
# real-kernel tests below instead of failing their native assertions.
_NATIVE_FORCED_OFF = os.environ.get("GOLDENMATCH_NATIVE", "").strip().lower() in (
    "0", "false", "no", "off", "disabled"
)


def _native_fs_available():
    if _NATIVE_FORCED_OFF:
        return False
    try:
        from goldenmatch.core import _native_loader
        return _native_loader.native_available() and hasattr(
            _native_loader.native_module(), "score_block_pairs_fs"
        )
    except Exception:
        return False


def _native_fs_supports_level_thresholds():
    try:
        from goldenmatch.core import _native_loader
        return bool(getattr(
            _native_loader.native_module(), "FS_SUPPORTS_LEVEL_THRESHOLDS", False
        ))
    except Exception:
        return False


@pytest.mark.skipif(not _native_fs_available(), reason="native FS kernel not built")
@pytest.mark.skipif(
    not _native_fs_supports_level_thresholds(),
    reason="native FS kernel predates level_thresholds support (< 0.1.14)",
)
def test_level_thresholds_native_eligible_real_kernel(monkeypatch):
    """Against the real native build (>= 0.1.14): level_thresholds matchkeys
    ARE native-eligible now that the kernel advertises the capability."""
    from goldenmatch.core import probabilistic as p

    monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "1")
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")

    mk_plain = _mk(field="name", scorer="jaro_winkler", levels=3, partial_threshold=0.8)
    assert p._fs_native_eligible(mk_plain) is True
    assert p._fs_native_eligible(_mk_custom()) is True


@pytest.mark.skipif(not _native_fs_available(), reason="native FS kernel not built")
@pytest.mark.skipif(
    not _native_fs_supports_level_thresholds(),
    reason="native FS kernel predates level_thresholds support (< 0.1.14)",
)
class TestNativeNLevelParity:
    """Real-kernel parity: custom level_thresholds banding native vs numpy.

    Comparison idiom mirrors TestNativeFSParity (test_probabilistic.py):
    scores depend only on the LEVEL a similarity bands into, so as long as no
    pair sits on a threshold boundary, native == numpy exactly (both round
    to 4 decimals).
    """

    def _df(self):
        import polars as pl
        # Engineered bands for jaro_winkler with [1.0, 0.92, 0.88]:
        #   smith/smith          -> 1.0    -> level 3
        #   martha/marhta        -> ~0.961 -> level 2
        #   jellyfish/smellyfish -> ~0.896 -> level 1
        #   cross-block pairs    -> low    -> level 0
        return pl.DataFrame({
            "__row_id__": [0, 1, 2, 3, 4, 5],
            "name": ["smith", "smith", "martha", "marhta",
                     "jellyfish", "smellyfish"],
            "city": ["aa", "aa", "bb", "bb", "cc", "zz"],
        })

    def _mk_mixed(self):
        # MIXED matchkey: one field WITH custom thresholds, one WITHOUT --
        # exercises the per-field Option across the FFI.
        return MatchkeyConfig(
            name="nl", type="probabilistic", link_threshold=0.05,
            fields=[
                MatchkeyField(field="name", scorer="jaro_winkler", levels=4,
                              level_thresholds=[1.0, 0.92, 0.88]),
                MatchkeyField(field="city", scorer="exact", levels=2),
            ],
        )

    def test_native_matches_numpy_mixed_matchkey(self, monkeypatch):
        from goldenmatch.core import probabilistic as p
        monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "1")
        df, mk = self._df(), self._mk_mixed()
        em = p._fallback_result(mk)
        assert p._fs_native_eligible(mk) is True
        native_pairs = sorted(p.score_probabilistic_native(df, mk, em, set()))
        numpy_pairs = sorted(p.score_probabilistic_vectorized(df, mk, em, set()))
        assert native_pairs == numpy_pairs
        assert native_pairs  # the engineered bands must actually score pairs
        # Multiple distinct bands hit -> more than one distinct score.
        assert len({s for _a, _b, s in native_pairs}) > 1

    def test_native_matches_numpy_all_custom(self, monkeypatch):
        from goldenmatch.core import probabilistic as p
        monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "1")
        df = self._df()
        mk = MatchkeyConfig(
            name="nl2", type="probabilistic", link_threshold=0.05,
            fields=[
                MatchkeyField(field="name", scorer="jaro_winkler", levels=4,
                              level_thresholds=[1.0, 0.92, 0.88]),
                MatchkeyField(field="city", scorer="jaro_winkler", levels=3,
                              level_thresholds=[1.0, 0.75]),
            ],
        )
        em = p._fallback_result(mk)
        native_pairs = sorted(p.score_probabilistic_native(df, mk, em, set()))
        numpy_pairs = sorted(p.score_probabilistic_vectorized(df, mk, em, set()))
        assert native_pairs == numpy_pairs
        assert native_pairs

    def test_block_scorer_selects_native_for_level_thresholds(self, monkeypatch):
        from goldenmatch.core import probabilistic as p
        monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "1")
        mk = self._mk_mixed()
        em = p._fallback_result(mk)
        scorer = p.probabilistic_block_scorer(mk, em)
        assert scorer.__name__ == "_native"

    # -- kernel-level ValueError invariants (called directly across the FFI) --

    @staticmethod
    def _kernel_args(match_weights, level_thresholds):
        # (row_ids, block_sizes, field_values, scorer_ids, levels,
        #  partial_thresholds, match_weights, calibrated, prior_w, min_weight,
        #  weight_range, threshold, exclude, level_thresholds)
        return ([0, 1], [2], [["abc", "abd"]], [0], [4], [0.8], match_weights,
                False, 0.0, 0.0, 1.0, 0.0, [], level_thresholds)

    def test_kernel_rejects_thresholds_field_count_mismatch(self):
        from goldenmatch.core._native_loader import native_module
        with pytest.raises(ValueError,
                           match="level_thresholds length 2 != field count 1"):
            native_module().score_block_pairs_fs(
                *self._kernel_args([[0.0, 1.0, 2.0, 3.0]],
                                   [[1.0], [0.9]])
            )

    def test_kernel_rejects_weight_threshold_length_mismatch(self):
        from goldenmatch.core._native_loader import native_module
        # 2 thresholds need 3 weights; 4 given.
        with pytest.raises(ValueError,
                           match=r"need thresholds \+ 1 weights"):
            native_module().score_block_pairs_fs(
                *self._kernel_args([[0.0, 1.0, 2.0, 3.0]],
                                   [[1.0, 0.9]])
            )
