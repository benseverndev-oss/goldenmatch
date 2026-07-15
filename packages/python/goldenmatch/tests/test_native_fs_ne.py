"""Native FS negative-evidence tests: kernel-direct + Python gate/caller/parity.

Kernel-direct tests call goldenmatch._native.score_block_pairs_fs directly (no
Python scoring path) to pin the NE firing rule ported from ``_ne_fired``
(core/probabilistic.py:466): fires iff BOTH values present AND non-empty AND
similarity STRICTLY below the threshold; contributes exactly 0 otherwise.

The gate/caller/parity tests cover the R2 Python side: ``_fs_native_eligible``
widening (native NE scorers + ``FS_SUPPORTS_NE``; old wheels decline),
``score_probabilistic_native`` NE kwarg construction (never sent without NE),
native-vs-numpy parity on NE-bearing matchkeys, and the homonym E2E success
bar on the NATIVE path with byte-identical clustering to the numpy run.

Skipped when the native extension isn't built.
"""

from __future__ import annotations

import polars as pl
import pyarrow as pa
import pytest
from goldenmatch.config.schemas import (
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
    NegativeEvidenceField,
)
from goldenmatch.core import _native_loader
from goldenmatch.core.probabilistic import (
    EMResult,
    _fs_native_eligible,
    score_probabilistic_native,
    score_probabilistic_vectorized,
)

pytestmark = pytest.mark.skipif(
    not _native_loader.native_available(),
    reason="goldenmatch._native not built",
)

# _NATIVE_FS_SCORER_IDS (core/probabilistic.py): jaro_winkler=0, levenshtein=1,
# token_sort=2, exact=3.
_EXACT = 3


def _base_args():
    """3-row single block; one regular exact field with identical values.

    Every pair gets the full agreement weight 2.0 (weights [[-2.0, 2.0]],
    levels [2], partials [0.5]). calibrated=False, prior_w=0.0, threshold=0.0
    (emit everything). min/max weights are hand-set NE-aware:
    min = -2.0 + -4.0 = -6.0, max = 2.0, range = 8.0.
    """
    row_ids = [1, 2, 3]
    field_values = [["same", "same", "same"]]
    scorer_ids = [_EXACT]
    levels = [2]
    partials = [0.5]
    weights = [[-2.0, 2.0]]
    return (
        row_ids,
        [3],
        field_values,
        scorer_ids,
        levels,
        partials,
        weights,
        False,
        0.0,
        -6.0,
        8.0,
        0.0,
        [],
    )


def test_kernel_exports_fs_supports_ne():
    mod = _native_loader.native_module()
    assert getattr(mod, "FS_SUPPORTS_NE", False) is True


# ── #1803: zero-copy arrow FS entry + shared exclude handle ────────────────


def _arrow_args():
    """The _base_args fixture with row_ids/field columns as arrow arrays,
    in score_block_pairs_fs_arrow's argument order."""
    (row_ids, sizes, field_values, scorer_ids, levels, partials, weights,
     calibrated, prior_w, min_w, w_range, threshold, _excl) = _base_args()
    return (
        pa.array(row_ids, type=pa.int64()),
        [pa.array(v, type=pa.large_string()) for v in field_values],
        sizes, scorer_ids, levels, partials, weights,
        calibrated, prior_w, min_w, w_range, threshold,
    )


def test_kernel_exports_fs_arrow_and_exclude_consts():
    mod = _native_loader.native_module()
    assert getattr(mod, "FS_SUPPORTS_ARROW", False) is True
    assert getattr(mod, "FS_SUPPORTS_EXCLUDE_SET", False) is True


def test_fs_arrow_entry_matches_vec_entry():
    mod = _native_loader.native_module()
    vec = mod.score_block_pairs_fs(*_base_args())
    arrow = mod.score_block_pairs_fs_arrow(*_arrow_args())
    assert arrow == vec


def test_fs_arrow_entry_matches_vec_entry_ne():
    mod = _native_loader.native_module()
    ne_kw = dict(ne_scorer_ids=[_EXACT], ne_thresholds=[0.5], ne_weights=[-4.0])
    vec = mod.score_block_pairs_fs(
        *_base_args(), ne_values=[["X", "X", "Y"]], **ne_kw
    )
    arrow = mod.score_block_pairs_fs_arrow(
        *_arrow_args(),
        ne_arrays=[pa.array(["X", "X", "Y"], type=pa.large_string())],
        **ne_kw,
    )
    assert arrow == vec
    # Null / empty-string NE values contribute exactly 0 on both entries.
    vec2 = mod.score_block_pairs_fs(
        *_base_args(), ne_values=[[None, "X", ""]], **ne_kw
    )
    arrow2 = mod.score_block_pairs_fs_arrow(
        *_arrow_args(),
        ne_arrays=[pa.array([None, "X", ""], type=pa.large_string())],
        **ne_kw,
    )
    assert arrow2 == vec2


def test_fs_arrow_entry_matches_vec_entry_level_thresholds():
    mod = _native_loader.native_module()
    # 3-weight field with custom 2-threshold banding.
    (row_ids, sizes, _fv, scorer_ids, levels, partials, _w,
     calibrated, prior_w, _minw, _range, threshold, excl) = _base_args()
    field_values = [["smith", "smyth", "jones"]]
    weights = [[-2.0, 0.5, 2.0]]
    lt = [[0.7, 0.95]]
    vec = mod.score_block_pairs_fs(
        row_ids, sizes, field_values, [0], levels, partials, weights,
        calibrated, prior_w, -2.0, 4.0, threshold, excl,
        level_thresholds=lt,
    )
    arrow = mod.score_block_pairs_fs_arrow(
        pa.array(row_ids, type=pa.int64()),
        [pa.array(v, type=pa.large_string()) for v in field_values],
        sizes, [0], levels, partials, weights,
        calibrated, prior_w, -2.0, 4.0, threshold,
        level_thresholds=lt,
    )
    assert arrow == vec


def test_fs_exclude_handle_parity_both_entries():
    mod = _native_loader.native_module()
    handle = mod.build_exclude_set([(2, 1)])  # canonicalized to (1, 2)
    base = _base_args()
    vec_list = mod.score_block_pairs_fs(*base[:-1], [(1, 2)])
    vec_handle = mod.score_block_pairs_fs(*base, exclude_set=handle)
    arrow_handle = mod.score_block_pairs_fs_arrow(*_arrow_args(), exclude_set=handle)
    assert vec_handle == vec_list
    assert arrow_handle == vec_list
    assert (1, 2) not in {(a, b) for a, b, _ in vec_handle}


class _FakeNativeRecorder:
    """Duck-typed native module recording which FS entry was called."""

    def __init__(self, consts: dict, result=None):
        self._consts = consts
        self.calls: list[tuple[str, dict]] = []
        self._result = result if result is not None else []

    def __getattr__(self, name):
        if name in self._consts:
            return self._consts[name]
        raise AttributeError(name)

    def score_block_pairs_fs(self, *a, **kw):
        self.calls.append(("vec", kw))
        return list(self._result)

    def score_block_pairs_fs_arrow(self, *a, **kw):
        self.calls.append(("arrow", kw))
        return list(self._result)


def test_score_fs_native_frame_routes_to_arrow(monkeypatch):
    from goldenmatch.core import probabilistic as p

    fake = _FakeNativeRecorder({"FS_SUPPORTS_ARROW": True, "FS_SUPPORTS_EXCLUDE_SET": True})
    monkeypatch.setattr(_native_loader, "native_module", lambda: fake)
    df = _block_df()
    p._score_fs_native_frame(df, [df.height], _ne_mk([]), _em(_BASE_WEIGHTS), set())
    assert [c[0] for c in fake.calls] == ["arrow"]


def test_score_fs_native_frame_old_wheel_uses_vec(monkeypatch):
    from goldenmatch.core import probabilistic as p

    fake = _FakeNativeRecorder({})  # neither const -> legacy vec entry
    monkeypatch.setattr(_native_loader, "native_module", lambda: fake)
    df = _block_df()
    p._score_fs_native_frame(df, [df.height], _ne_mk([]), _em(_BASE_WEIGHTS), {(1, 2)})
    assert [c[0] for c in fake.calls] == ["vec"]
    assert "exclude_set" not in fake.calls[0][1]  # old wheel never sees the kwarg


def test_kernel_ne_fires_strictly_below_threshold():
    mod = _native_loader.native_module()
    # Rows 1/2 share the NE value (sim 1.0 >= 0.5 -> no fire); rows 1/3 and
    # 2/3 differ (sim 0.0 < 0.5 -> fires, adds -4.0).
    pairs = mod.score_block_pairs_fs(
        *_base_args(),
        ne_values=[["X", "X", "Y"]],
        ne_scorer_ids=[_EXACT],
        ne_thresholds=[0.5],
        ne_weights=[-4.0],
    )
    scores = {(a, b): s for a, b, s in pairs}
    # no-fire pair: (2.0 - (-6.0)) / 8.0 = 1.0
    assert scores[(1, 2)] == pytest.approx(1.0)
    # fired pairs: (2.0 - 4.0 - (-6.0)) / 8.0 = 0.5
    assert scores[(1, 3)] == pytest.approx(0.5)
    assert scores[(2, 3)] == pytest.approx(0.5)
    assert len(scores) == 3


def test_kernel_ne_null_and_empty_never_fire():
    mod = _native_loader.native_module()
    # None on one side (pairs with row 1) and "" on one side (pairs with
    # row 3) are inconclusive: NE must contribute exactly 0, so the result
    # matches a call WITHOUT the ne kwargs entirely.
    without_ne = mod.score_block_pairs_fs(*_base_args())
    with_ne = mod.score_block_pairs_fs(
        *_base_args(),
        ne_values=[[None, "X", ""]],
        ne_scorer_ids=[_EXACT],
        ne_thresholds=[0.5],
        ne_weights=[-4.0],
    )
    assert with_ne == without_ne


def test_kernel_ne_validation_errors():
    mod = _native_loader.native_module()

    # Partial kwarg group: ne_values without the other three.
    with pytest.raises(ValueError, match="score_block_pairs_fs"):
        mod.score_block_pairs_fs(*_base_args(), ne_values=[["X", "X", "Y"]])
    # Partial kwarg group: ne_weights without ne_values.
    with pytest.raises(ValueError, match="score_block_pairs_fs"):
        mod.score_block_pairs_fs(*_base_args(), ne_weights=[-4.0])

    # Mismatched lengths across the four (2 values vs 1 of the rest).
    with pytest.raises(ValueError, match="score_block_pairs_fs"):
        mod.score_block_pairs_fs(
            *_base_args(),
            ne_values=[["X", "X", "Y"], ["A", "B", "C"]],
            ne_scorer_ids=[_EXACT],
            ne_thresholds=[0.5],
            ne_weights=[-4.0],
        )

    # ne_values[k] row count != len(row_ids).
    with pytest.raises(ValueError, match="score_block_pairs_fs"):
        mod.score_block_pairs_fs(
            *_base_args(),
            ne_values=[["X", "X"]],
            ne_scorer_ids=[_EXACT],
            ne_thresholds=[0.5],
            ne_weights=[-4.0],
        )


def test_kernel_ne_at_threshold_does_not_fire():
    """Strict comparator pin: sim == threshold (1.0 exact-match, threshold 1.0)
    is ``1.0 < 1.0`` = False -> NE contributes 0, identical to the no-NE call."""
    mod = _native_loader.native_module()
    without_ne = mod.score_block_pairs_fs(*_base_args())
    with_ne = mod.score_block_pairs_fs(
        *_base_args(),
        ne_values=[["X", "X", "X"]],
        ne_scorer_ids=[_EXACT],
        ne_thresholds=[1.0],
        ne_weights=[-4.0],
    )
    assert with_ne == without_ne


# ---------------------------------------------------------------------------
# R2: Python gate widening + native caller + parity + native success bar
# ---------------------------------------------------------------------------


def _em(match_weights: dict[str, list[float]], proportion: float = 0.2) -> EMResult:
    """Hand-built EMResult (no training); m/u probs are shape-matched dummies."""
    dummy = {k: [0.5] * len(v) for k, v in match_weights.items()}
    return EMResult(
        m_probs=dummy,
        u_probs=dummy,
        match_weights=match_weights,
        converged=True,
        iterations=3,
        proportion_matched=proportion,
    )


def _base_fields() -> list[MatchkeyField]:
    return [
        MatchkeyField(field="name", scorer="jaro_winkler", levels=2, partial_threshold=0.8),
        MatchkeyField(field="city", scorer="exact", levels=2),
    ]


_BASE_WEIGHTS = {"name": [-2.0, 3.0], "city": [-1.5, 2.5]}

# Names/cities keep all pairwise similarities well away from the 0.8 partial
# threshold (identical, trailing-char corruption ~0.98, or fully distinct).
_NAMES = ["Jonathan", "Jonathanx", "Michaela", "Michaela", "Robertson", "Robertson"]
_CITIES = ["Boston", "Boston", "Denver", "Denver", "Austin", "Austin"]


def _block_df(**extra_cols) -> pl.DataFrame:
    return pl.DataFrame(
        {"__row_id__": list(range(6)), "name": _NAMES, "city": _CITIES, **extra_cols}
    )


def _ne_mk(ne_fields: list[NegativeEvidenceField], fields=None) -> MatchkeyConfig:
    return MatchkeyConfig(
        name="fs",
        type="probabilistic",
        fields=fields or _base_fields(),
        negative_evidence=ne_fields,
        link_threshold=0.0,  # emit every pair: parity compares the full sets
    )


def _phone_ne(**kw) -> NegativeEvidenceField:
    # threshold 0.5 with an exact scorer: sims are only 0.0/1.0, both strictly
    # away from the threshold (tolerance discipline).
    return NegativeEvidenceField(field="phone", scorer="exact", threshold=0.5, **kw)


class TestFsNativeEligibleNE:
    def test_fs_native_eligible_ne_supported(self, monkeypatch):
        monkeypatch.delenv("GOLDENMATCH_FS_NATIVE", raising=False)
        monkeypatch.delenv("GOLDENMATCH_NATIVE", raising=False)
        mk = _ne_mk([_phone_ne()])
        assert _fs_native_eligible(mk) is True

    def test_fs_native_eligible_ne_old_wheel_declines(self, monkeypatch):
        monkeypatch.delenv("GOLDENMATCH_FS_NATIVE", raising=False)
        monkeypatch.delenv("GOLDENMATCH_NATIVE", raising=False)

        class _OldWheel:
            """Has the FS kernel + level_thresholds capability, lacks FS_SUPPORTS_NE."""

            FS_SUPPORTS_LEVEL_THRESHOLDS = True

            @staticmethod
            def score_block_pairs_fs(*args, **kwargs):
                return []

        monkeypatch.setattr(_native_loader, "native_module", lambda: _OldWheel())

        ne_mk = _ne_mk([_phone_ne()])
        assert _fs_native_eligible(ne_mk) is False

        # The same stub with a no-NE matchkey is still eligible: the decline
        # above is NE-specific, not a broken stub.
        plain_mk = MatchkeyConfig(
            name="fs", type="probabilistic", fields=_base_fields(), link_threshold=0.0
        )
        assert _fs_native_eligible(plain_mk) is True

    def test_fs_native_eligible_ensemble_ne_declines(self, monkeypatch):
        monkeypatch.delenv("GOLDENMATCH_FS_NATIVE", raising=False)
        monkeypatch.delenv("GOLDENMATCH_NATIVE", raising=False)
        mk = _ne_mk([NegativeEvidenceField(field="phone", scorer="ensemble", threshold=0.5)])
        assert _fs_native_eligible(mk) is False


def test_native_kwargs_not_sent_without_ne(monkeypatch):
    """A no-NE matchkey must never put ne_* kwargs on the FFI call (the old-wheel
    discipline: an old wheel must never see the kwarg, even if the gate drifts)."""
    real = _native_loader.native_module()
    captured: dict = {}

    class _Spy:
        def __getattr__(self, name):
            return getattr(real, name)

        def score_block_pairs_fs(self, *args, **kwargs):
            captured["kwargs"] = dict(kwargs)
            return real.score_block_pairs_fs(*args, **kwargs)

        def score_block_pairs_fs_arrow(self, *args, **kwargs):
            # New wheels route here (#1803); same no-NE-kwargs discipline.
            captured["kwargs"] = dict(kwargs)
            return real.score_block_pairs_fs_arrow(*args, **kwargs)

    monkeypatch.setattr(_native_loader, "native_module", lambda: _Spy())

    mk = MatchkeyConfig(name="fs", type="probabilistic", fields=_base_fields(), link_threshold=0.0)
    df = _block_df()
    pairs = score_probabilistic_native(df, mk, _em(_BASE_WEIGHTS))
    assert pairs, "expected pairs at link_threshold=0.0"
    assert "kwargs" in captured, "spy never saw the kernel call"
    assert "ne_arrays" not in captured["kwargs"]
    assert "ne_values" not in captured["kwargs"]
    assert "ne_scorer_ids" not in captured["kwargs"]
    assert "ne_thresholds" not in captured["kwargs"]
    assert "ne_weights" not in captured["kwargs"]


def _parity_cases() -> dict[str, tuple[pl.DataFrame, MatchkeyConfig, EMResult]]:
    ne_w = dict(_BASE_WEIGHTS)
    ne_w["__ne__phone"] = [-4.0, 0.0]

    two_ne_w = dict(ne_w)
    two_ne_w["__ne__email"] = [-3.0, 0.0]

    lt_w = {"name": [-2.0, 1.0, 3.0], "city": [-1.5, 2.5], "__ne__phone": [-4.0, 0.0]}
    lt_fields = [
        MatchkeyField(
            field="name",
            scorer="jaro_winkler",
            levels=3,
            partial_threshold=0.8,
            level_thresholds=[0.9, 0.7],
        ),
        MatchkeyField(field="city", scorer="exact", levels=2),
    ]

    # Phones: rows 0/1 agree (dup, NE never fires); rows 2/3 differ (homonym
    # shape, NE fires); rows 4/5 agree.
    phones = ["5551111111", "5551111111", "5552222222", "5553333333", "5554444444", "5554444444"]
    phones_null = list(phones)
    phones_null[2] = None
    # "-" with transforms=["digits_only"] -> "" after transform: inconclusive.
    phones_dash = list(phones)
    phones_dash[3] = "-"
    emails = ["a@x.com", "a@x.com", "b@x.com", "c@x.com", "d@x.com", "d@x.com"]

    return {
        "em-learned": (_block_df(phone=phones), _ne_mk([_phone_ne()]), _em(ne_w)),
        "penalty-bits": (
            _block_df(phone=phones),
            _ne_mk([_phone_ne(penalty_bits=5.0)]),
            _em(_BASE_WEIGHTS),
        ),
        "null-ne-row": (_block_df(phone=phones_null), _ne_mk([_phone_ne()]), _em(ne_w)),
        "empty-after-transform": (
            _block_df(phone=phones_dash),
            _ne_mk([_phone_ne(transforms=["digits_only"])]),
            _em(ne_w),
        ),
        "ne-plus-level-thresholds": (
            _block_df(phone=phones),
            _ne_mk([_phone_ne()], fields=lt_fields),
            _em(lt_w),
        ),
        "two-ne-fields": (
            _block_df(phone=phones, email=emails),
            _ne_mk(
                [
                    _phone_ne(),
                    NegativeEvidenceField(field="email", scorer="exact", threshold=0.5),
                ]
            ),
            _em(two_ne_w),
        ),
    }


@pytest.mark.parametrize("case", sorted(_parity_cases()))
def test_native_numpy_parity_ne(case, monkeypatch):
    """Native kernel vs numpy vectorized path: identical pair sets + scores
    (both round to 4 decimals; fixture sims sit away from every threshold)."""
    monkeypatch.delenv("GOLDENMATCH_FS_NATIVE", raising=False)
    monkeypatch.delenv("GOLDENMATCH_NATIVE", raising=False)
    df, mk, em = _parity_cases()[case]
    assert _fs_native_eligible(mk) is True  # guard: really exercising the kernel

    native = sorted(score_probabilistic_native(df, mk, em))
    vec = sorted(score_probabilistic_vectorized(df, mk, em))
    assert [(a, b) for a, b, _ in native] == [(a, b) for a, b, _ in vec]
    for (na, nb, ns), (_, _, vs) in zip(native, vec):
        assert ns == pytest.approx(vs, abs=1e-9), f"pair ({na}, {nb}) score diverged"


def test_native_success_bar_homonym(monkeypatch):
    """The FS-NE homonym E2E bar on the NATIVE path: traps separate, true dups
    merge, and clustering is byte-identical to the numpy run of the same fixture."""
    import goldenmatch as gm

    from tests.test_fs_ne_e2e import (
        LINK_THRESHOLD,
        _blocking,
        _build_fixture,
        _fields,
        _id_to_cluster,
        _same_cluster,
    )

    monkeypatch.delenv("GOLDENMATCH_FS_NATIVE", raising=False)
    monkeypatch.delenv("GOLDENMATCH_NATIVE", raising=False)

    df, dup_pairs, homonym_pairs = _build_fixture()
    mk = MatchkeyConfig(
        name="fs",
        type="probabilistic",
        fields=_fields(),
        negative_evidence=[
            NegativeEvidenceField(field="phone", scorer="exact", threshold=1.0),
        ],
        link_threshold=LINK_THRESHOLD,
    )
    # In-test guard against a silent numpy fallback: the fixture's NE-bearing
    # matchkey must be native-eligible before the native run means anything.
    assert _fs_native_eligible(mk) is True

    config = GoldenMatchConfig(matchkeys=[mk], blocking=_blocking())
    native_result = gm.dedupe_df(df, config=config)
    mapping = _id_to_cluster(native_result)

    for a, b in dup_pairs:
        assert _same_cluster(mapping, a, b), (
            f"true duplicate pair {(a, b)} failed to merge on the native path"
        )
    for a, b in homonym_pairs:
        assert not _same_cluster(mapping, a, b), f"homonym trap {(a, b)} merged on the native path"

    # Byte-identical clustering vs the pure-Python (numpy) run.
    monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "0")
    assert _fs_native_eligible(mk) is False  # guard: really the numpy path now
    python_result = gm.dedupe_df(df, config=config)

    def _membership(res) -> set[frozenset]:
        clusters = res.clusters
        infos = clusters.values() if isinstance(clusters, dict) else clusters
        return {frozenset(c["members"] if isinstance(c, dict) else c) for c in infos}

    assert _membership(native_result) == _membership(python_result)


# ---------------------------------------------------------------------------
# R3: fused kernel (match_fused_fs) — NE + level_thresholds, kernel-direct
# ---------------------------------------------------------------------------


def _fused_fs_call(
    mod,
    keys: list[str],
    field_vals: list[list[str | None]],
    *,
    scorer_ids: list[int],
    levels: list[int],
    partials: list[float],
    weights: list[list[float]],
    min_w: float,
    w_range: float,
    threshold: float,
    **kwargs,
):
    """Kernel-direct match_fused_fs call, positional shape mirroring
    run_match_fused_fs_arrow (fused_match.py): row_ids int64, one large_string
    block-key column, large_string score columns. calibrated=False, prior_w=0."""
    n = len(keys)
    row_ids = pa.array(range(n), type=pa.int64())
    key_arrs = [pa.array(keys, type=pa.large_string())]
    score_arrs = [pa.array(v, type=pa.large_string()) for v in field_vals]
    return mod.match_fused_fs(
        row_ids,
        key_arrs,
        score_arrs,
        scorer_ids,
        levels,
        partials,
        weights,
        False,
        0.0,
        min_w,
        w_range,
        threshold,
        **kwargs,
    )


def _memberships(clusters) -> set[frozenset]:
    return {frozenset(c) for c in clusters}


def _multi_memberships(clusters) -> set[frozenset]:
    return {frozenset(c) for c in clusters if len(c) >= 2}


def test_fused_exports_level_thresholds_const():
    mod = _native_loader.native_module()
    assert getattr(mod, "FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS", False) is True


# Interleaved block keys: rows of the same key are NOT adjacent in input order,
# so the kernel's block gather REORDERS rows. Block "b" = rows {0, 2}, block
# "a" = rows {1, 3}.
_FUSED_KEYS = ["b", "a", "b", "a"]


def _fused_ne_base_kwargs() -> dict:
    """One exact regular field, all values identical (agreement weight 2.0 for
    every within-block pair). NE-aware normalization range hand-set:
    min = -2.0 + -4.0 = -6.0, max = 2.0, range = 8.0. No fire -> score 1.0;
    fired (-4.0) -> score 0.5. threshold 0.75 sits between the two."""
    return dict(
        scorer_ids=[_EXACT],
        levels=[2],
        partials=[0.5],
        weights=[[-2.0, 2.0]],
        min_w=-6.0,
        w_range=8.0,
        threshold=0.75,
    )


def test_fused_fs_ne_fires():
    """NE flips both within-block pairs below the threshold — and doubles as
    the gather-trap test: NE values are chosen per ORIGINAL row index
    (["P", "P", "Q", "Q"]) so that after the block gather (either block-order:
    [0,2,1,3] or [1,3,0,2]) an unpermuted-NE bug would read same-valued
    positions (no fire, pairs merge) instead of the true differing row values
    (fire, all singletons)."""
    mod = _native_loader.native_module()
    kw = _fused_ne_base_kwargs()
    field_vals = [["same", "same", "same", "same"]]

    without_ne = _fused_fs_call(mod, _FUSED_KEYS, field_vals, **kw)
    # No NE: both within-block pairs score 1.0 >= 0.75 -> {0,2} and {1,3} merge.
    assert _multi_memberships(without_ne) == {frozenset({0, 2}), frozenset({1, 3})}

    with_ne = _fused_fs_call(
        mod,
        _FUSED_KEYS,
        field_vals,
        **kw,
        ne_fields=[pa.array(["P", "P", "Q", "Q"], type=pa.large_string())],
        ne_scorer_ids=[_EXACT],
        ne_thresholds=[0.5],
        ne_weights=[-4.0],
    )
    # Correct NE indexing: row 0 ("P") vs row 2 ("Q") fires; row 1 ("P") vs
    # row 3 ("Q") fires -> both pairs drop to 0.5 < 0.75 -> all singletons.
    # An unpermuted-NE bug reads adjacent gathered positions (equal values,
    # no fire) and wrongly keeps both merges.
    assert _multi_memberships(with_ne) == set()
    assert _memberships(with_ne) == {
        frozenset({0}),
        frozenset({1}),
        frozenset({2}),
        frozenset({3}),
    }


def test_fused_fs_ne_null_and_empty_never_fire():
    mod = _native_loader.native_module()
    kw = _fused_ne_base_kwargs()
    field_vals = [["same", "same", "same", "same"]]
    without_ne = _fused_fs_call(mod, _FUSED_KEYS, field_vals, **kw)
    # None on one side / "" on one side: inconclusive, NE contributes 0.
    with_ne = _fused_fs_call(
        mod,
        _FUSED_KEYS,
        field_vals,
        **kw,
        ne_fields=[pa.array([None, "", "X", "Y"], type=pa.large_string())],
        ne_scorer_ids=[_EXACT],
        ne_thresholds=[0.5],
        ne_weights=[-4.0],
    )
    assert _memberships(with_ne) == _memberships(without_ne)


def test_fused_fs_level_thresholds_bands():
    """Custom banding [[0.9, 0.5]] + 3-entry weights, hand-computed membership.

    Single block, levenshtein sims: (0,1)=1.0, (0,2)=(1,2)=0.75, (2,3)=0.25,
    (0,3)=(1,3)=0.0. Weights [-2.0, 1.0, 3.0], linear range [-2.0, 3.0]:
    level 2 -> 1.0, level 1 -> 0.6, level 0 -> 0.0. threshold 0.55.

    Custom banding: 0.75 >= 0.5 -> level 1 (0.6 links) -> cluster {0, 1, 2}.
    Legacy 3-level banding (partial 0.8): 0.75 < 0.8 -> level 0 -> only (0,1)
    links -> cluster {0, 1}. A kernel that ignores the kwarg fails the first
    assertion; the second proves the difference is the kwarg."""
    mod = _native_loader.native_module()
    keys = ["k", "k", "k", "k"]
    field_vals = [["aaaa", "aaaa", "aaab", "bbbb"]]
    kw = dict(
        scorer_ids=[1],  # levenshtein
        levels=[3],
        partials=[0.8],
        weights=[[-2.0, 1.0, 3.0]],
        min_w=-2.0,
        w_range=5.0,
        threshold=0.55,
    )
    custom = _fused_fs_call(mod, keys, field_vals, **kw, level_thresholds=[[0.9, 0.5]])
    assert _multi_memberships(custom) == {frozenset({0, 1, 2})}

    legacy = _fused_fs_call(mod, keys, field_vals, **kw)
    assert _multi_memberships(legacy) == {frozenset({0, 1})}


def test_fused_fs_ne_validation_errors():
    mod = _native_loader.native_module()
    kw = _fused_ne_base_kwargs()
    field_vals = [["same", "same", "same", "same"]]
    ne_col = pa.array(["P", "P", "Q", "Q"], type=pa.large_string())

    # Partial NE kwarg group.
    with pytest.raises(ValueError, match="match_fused_fs"):
        _fused_fs_call(mod, _FUSED_KEYS, field_vals, **kw, ne_fields=[ne_col])

    # Mismatched lengths across the four (2 fields vs 1 of the rest).
    with pytest.raises(ValueError, match="match_fused_fs"):
        _fused_fs_call(
            mod,
            _FUSED_KEYS,
            field_vals,
            **kw,
            ne_fields=[ne_col, ne_col],
            ne_scorer_ids=[_EXACT],
            ne_thresholds=[0.5],
            ne_weights=[-4.0],
        )

    # ne_fields[k] length != row count.
    with pytest.raises(ValueError, match="match_fused_fs"):
        _fused_fs_call(
            mod,
            _FUSED_KEYS,
            field_vals,
            **kw,
            ne_fields=[pa.array(["P", "Q"], type=pa.large_string())],
            ne_scorer_ids=[_EXACT],
            ne_thresholds=[0.5],
            ne_weights=[-4.0],
        )

    # NE scorer id outside score_one's implemented 0..=3 range.
    with pytest.raises(ValueError, match="match_fused_fs"):
        _fused_fs_call(
            mod,
            _FUSED_KEYS,
            field_vals,
            **kw,
            ne_fields=[ne_col],
            ne_scorer_ids=[9],
            ne_thresholds=[0.5],
            ne_weights=[-4.0],
        )

    # level_thresholds length != field count.
    with pytest.raises(ValueError, match="match_fused_fs"):
        _fused_fs_call(
            mod,
            _FUSED_KEYS,
            field_vals,
            **kw,
            level_thresholds=[[0.9, 0.5], [0.9]],
        )

    # weights-vs-thresholds arity: 2 thresholds need 3 weights, got 2.
    with pytest.raises(ValueError, match="match_fused_fs"):
        _fused_fs_call(
            mod,
            _FUSED_KEYS,
            field_vals,
            **kw,
            level_thresholds=[[0.9, 0.5]],
        )


def test_kernel_ne_scorer_id_validated():
    """Ride-along: score_block_pairs_fs rejects NE scorer ids outside 0..=3."""
    mod = _native_loader.native_module()
    with pytest.raises(ValueError, match="score_block_pairs_fs"):
        mod.score_block_pairs_fs(
            *_base_args(),
            ne_values=[["X", "X", "Y"]],
            ne_scorer_ids=[9],
            ne_thresholds=[0.5],
            ne_weights=[-4.0],
        )
