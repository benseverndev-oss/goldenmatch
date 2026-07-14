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
        mk = _ne_mk(
            [NegativeEvidenceField(field="phone", scorer="ensemble", threshold=0.5)]
        )
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

    monkeypatch.setattr(_native_loader, "native_module", lambda: _Spy())

    mk = MatchkeyConfig(
        name="fs", type="probabilistic", fields=_base_fields(), link_threshold=0.0
    )
    df = _block_df()
    pairs = score_probabilistic_native(df, mk, _em(_BASE_WEIGHTS))
    assert pairs, "expected pairs at link_threshold=0.0"
    assert "kwargs" in captured, "spy never saw the kernel call"
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
        assert not _same_cluster(mapping, a, b), (
            f"homonym trap {(a, b)} merged on the native path"
        )

    # Byte-identical clustering vs the pure-Python (numpy) run.
    monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "0")
    assert _fs_native_eligible(mk) is False  # guard: really the numpy path now
    python_result = gm.dedupe_df(df, config=config)

    def _membership(res) -> set[frozenset]:
        clusters = res.clusters
        infos = clusters.values() if isinstance(clusters, dict) else clusters
        return {
            frozenset(c["members"] if isinstance(c, dict) else c) for c in infos
        }

    assert _membership(native_result) == _membership(python_result)
