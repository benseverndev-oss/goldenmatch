"""Guards: NE capability boundaries for the native/fused/fast paths.

Native (R2, FS_SUPPORTS_NE): the real kernel now scores NE, so the native
gate ACCEPTS NE-bearing matchkeys when every NE scorer is native AND the
module advertises ``FS_SUPPORTS_NE``; old wheels (the mocks below) lack the
const and decline. The fused FS gate (R4) tracks the same capability (plus a
derive_from decline of its own); the probabilistic-fast path still declines
NE unconditionally.

Mirrors the level_thresholds gate-test scaffolding in
tests/test_nlevel_banding.py -- synthetic native mocks + a real-kernel
skipif helper + router __name__ assertions -- for negative_evidence
instead of level_thresholds. Also pins EMResult.validate_for's NE-aware
extension (missing __ne__<field> key -> FSModelMismatchError naming the
field and both remedies: retrain / set penalty_bits).
"""
from __future__ import annotations

import os

import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
    NegativeEvidenceField,
)
from goldenmatch.core import fused_match
from goldenmatch.core.probabilistic import (
    EMResult,
    FSModelMismatchError,
    _fallback_result,
    _fs_native_eligible,
    probabilistic_block_scorer,
)
from goldenmatch.core.probabilistic_fast import _resolve_probabilistic_fast_path


def _mk(negative_evidence=None, **field_kw):
    return MatchkeyConfig(
        name="t", type="probabilistic",
        fields=[MatchkeyField(**field_kw)],
        negative_evidence=negative_evidence,
    )


def _mk_ne():
    return _mk(
        field="name", scorer="jaro_winkler", levels=3, partial_threshold=0.8,
        negative_evidence=[
            NegativeEvidenceField(field="phone", scorer="exact", threshold=1.0, penalty_bits=20.0),
        ],
    )


def _mk_plain():
    return _mk(field="name", scorer="jaro_winkler", levels=3, partial_threshold=0.8)


# ── 1. _fs_native_eligible: NE declines even with a fully-supporting mock ───


def _fake_native_module_supporting():
    """A kernel advertising every PRE-NE capability (mirrors
    test_nlevel_banding.py's supporting fake) -- i.e. an OLD WHEEL: it has the
    FS kernel + FS_SUPPORTS_LEVEL_THRESHOLDS but lacks FS_SUPPORTS_NE, so an
    NE-bearing matchkey must decline (NE never crosses an old wheel's FFI)."""
    class _Fake:
        FS_SUPPORTS_LEVEL_THRESHOLDS = True

        def score_block_pairs_fs(self, *a, **kw):  # pragma: no cover - not invoked
            raise NotImplementedError

    return _Fake()


def test_fs_native_eligible_declines_ne_with_supporting_mock(monkeypatch):
    from goldenmatch.core import probabilistic as p

    monkeypatch.setattr(p, "_fs_native_enabled", lambda: True)
    monkeypatch.setattr(
        "goldenmatch.core._native_loader.native_module", _fake_native_module_supporting
    )

    assert _fs_native_eligible(_mk_ne()) is False
    # Plain matchkey unaffected -- still eligible against the same mock.
    assert _fs_native_eligible(_mk_plain()) is True


# ── 2. Real-kernel variant (skipif native unavailable) ──────────────────────

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


@pytest.mark.skipif(not _native_fs_available(), reason="native FS kernel not built")
def test_fs_native_eligible_accepts_ne_real_kernel(monkeypatch):
    """Against the real in-tree/wheel kernel: an NE-bearing matchkey whose NE
    scorer is native IS eligible when the kernel advertises FS_SUPPORTS_NE
    (R2 gate widening); a kernel without the const declines (mock test above).
    Eligibility tracks the loaded kernel's capability, either way."""
    monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "1")
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")

    from goldenmatch.core import _native_loader
    supports_ne = getattr(_native_loader.native_module(), "FS_SUPPORTS_NE", False)
    assert _fs_native_eligible(_mk_ne()) is bool(supports_ne)
    assert _fs_native_eligible(_mk_plain()) is True


# ── 3. Router: probabilistic_block_scorer selects the non-native scorer ────


def test_router_selects_non_native_scorer_for_ne_with_native_mocked(monkeypatch):
    from goldenmatch.core import probabilistic as p

    monkeypatch.setattr(p, "_fs_native_enabled", lambda: True)
    monkeypatch.setattr(
        "goldenmatch.core._native_loader.native_module", _fake_native_module_supporting
    )

    mk = _mk_ne()
    em = _fallback_result(mk)
    scorer = probabilistic_block_scorer(mk, em)
    # jaro_winkler + exact (the NE scorer) are both vectorized_scorer_supported
    # -> falls through to the vectorized numpy path, NOT native.
    assert scorer.__name__ == "_scorer"

    # Plain matchkey, same mock: still routes native.
    mk_plain = _mk_plain()
    em_plain = _fallback_result(mk_plain)
    assert probabilistic_block_scorer(mk_plain, em_plain).__name__ == "_native"


def test_router_falls_to_scalar_for_ne_with_unsupported_ne_scorer(monkeypatch):
    """N3-reviewer nit: the use_vec gate must also check NE scorers, not just
    regular field scorers. An NE field with an unsupported (matrix-incapable)
    scorer -- record_embedding -- must force the scalar path even though the
    regular field's scorer IS vectorized_scorer_supported."""
    from goldenmatch.core import probabilistic as p

    monkeypatch.setattr(p, "_fs_native_enabled", lambda: False)  # isolate use_vec gate
    mk = _mk(
        field="name", scorer="jaro_winkler", levels=3, partial_threshold=0.8,
        negative_evidence=[
            NegativeEvidenceField(
                field="embedding_field", scorer="record_embedding", threshold=0.5, penalty_bits=10.0,
            ),
        ],
    )
    em = _fallback_result(mk)
    scorer = probabilistic_block_scorer(mk, em)
    assert scorer.__name__ == "_scalar"


def test_batched_scorer_declines_vec_for_ne_with_unsupported_ne_scorer(monkeypatch):
    """The batched sibling of the router test above:
    ``score_probabilistic_blocks_batched`` has its OWN ``use_vec`` computation
    (now the shared ``_fs_vectorized_supported``); pre-fix it checked only
    ``mk.fields``, so an NE field with a matrix-incapable scorer
    (record_embedding) wrongly took the SxS batch path and crashed in
    ``_field_score_matrix_dedup`` (ValueError: unknown fuzzy scorer) on a live
    pipeline.py path. Pins: (1) the batched vectorized unit scorer is never
    selected for such a matchkey, (2) no crash, (3) the scalar fallback scores
    correctly (NE suppresses the differing-value pair; same-value pair kept).

    A minimal score_pair-only plugin is registered under 'record_embedding'
    (restored in ``finally``) so the scalar fallback can resolve the scorer the
    way a live pipeline with the embedding plugin registered does -- without
    the spy in (1) that registration would let the pre-fix double-loop matrix
    fallback pass vacuously.
    """
    import polars as pl
    from goldenmatch.core import probabilistic as p
    from goldenmatch.core.blocker import build_blocks
    from goldenmatch.plugins.registry import PluginRegistry

    monkeypatch.setattr(p, "_fs_native_enabled", lambda: False)  # isolate use_vec gate
    monkeypatch.setenv("GOLDENMATCH_FS_WORKERS", "1")

    def _never_batched(*a, **kw):
        raise AssertionError(
            "score_probabilistic_vectorized_batch must not be selected for a "
            "matchkey whose NE scorer is not vectorized_scorer_supported"
        )

    monkeypatch.setattr(p, "score_probabilistic_vectorized_batch", _never_batched)

    class _ExactLikeScorer:
        name = "record_embedding"

        def score_pair(self, a, b):
            if a is None or b is None:
                return None
            return 1.0 if a == b else 0.0

    reg = PluginRegistry.instance()
    prior = reg.get_scorer("record_embedding")
    reg.register_scorer("record_embedding", _ExactLikeScorer())
    try:
        mk = _mk(
            field="name", scorer="exact", levels=2,
            negative_evidence=[
                NegativeEvidenceField(
                    field="emb", scorer="record_embedding", threshold=1.0, penalty_bits=20.0,
                ),
            ],
        )
        mk.link_threshold = 0.5
        em = EMResult(
            m_probs={"name": [0.1, 0.9]}, u_probs={"name": [0.9, 0.1]},
            match_weights={"name": [-3.0, 3.0]},
            converged=True, iterations=1, proportion_matched=0.1,
        )
        df = pl.DataFrame({
            "__row_id__": [0, 1, 2, 3],
            "name": ["alice"] * 4,
            "city": ["springfield"] * 4,
            # rows 0/1 share emb (NE never fires); rows 2/3 differ from
            # everyone (NE fires on every pair involving them).
            "emb": ["X", "X", "Y", "Z"],
        })
        blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["city"])])
        blocks = build_blocks(df.lazy(), blocking)
        pairs = p.score_probabilistic_blocks_batched(blocks, mk, em, set())
        kept = {(a, b) for a, b, _s in pairs}
        # weights: agree=+3; fired NE adds -20 -> normalized (3-20+23)/26 ~ 0.23
        # < 0.5 suppressed; un-fired pair normalizes to 1.0 -> kept.
        assert kept == {(0, 1)}
    finally:
        if prior is None:
            reg._scorers.pop("record_embedding", None)
        else:
            reg._scorers["record_embedding"] = prior


# ── 4. match_fused_fs_ready: NE tracks the loaded kernel's capability ────────


def _fused_config(negative_evidence=None):
    return GoldenMatchConfig(
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["blk"])]),
        matchkeys=[
            MatchkeyConfig(
                name="mk", type="probabilistic", link_threshold=0.5,
                fields=[MatchkeyField(field="name", scorer="jaro_winkler", levels=3, partial_threshold=0.8)],
                negative_evidence=negative_evidence,
            )
        ],
    )


def test_match_fused_fs_ready_tracks_ne_capability(monkeypatch):
    """R4 FLIP: this test used to pin the UNCONDITIONAL fused NE decline. The
    fused gate is now capability-tracking (same style as the R2 classic gate):
    an old wheel lacking ``FS_SUPPORTS_NE`` (the supporting-but-pre-NE mock)
    declines NE-bearing configs; a kernel advertising it accepts. Plain
    configs stay pure-config ready either way."""
    ne_config = _fused_config(negative_evidence=[
        NegativeEvidenceField(field="phone", scorer="exact", threshold=1.0, penalty_bits=20.0),
    ])
    # Old wheel: FS kernel + level_thresholds const, but no FS_SUPPORTS_NE.
    monkeypatch.setattr(
        "goldenmatch.core._native_loader.native_module", _fake_native_module_supporting
    )
    assert fused_match.match_fused_fs_ready(ne_config) is False
    assert fused_match.match_fused_fs_ready(_fused_config()) is True

    # NE-capable kernel: the same config is now ready.
    class _NeCapable:
        FS_SUPPORTS_NE = True

        def match_fused_fs(self, *a, **kw):  # pragma: no cover - not invoked
            raise NotImplementedError

    monkeypatch.setattr(
        "goldenmatch.core._native_loader.native_module", lambda: _NeCapable()
    )
    assert fused_match.match_fused_fs_ready(ne_config) is True


@pytest.mark.skipif(not _native_fs_available(), reason="native FS kernel not built")
def test_match_fused_fs_ready_ne_real_kernel():
    """Against the real kernel, fused NE readiness tracks FS_SUPPORTS_NE."""
    from goldenmatch.core import _native_loader

    supports_ne = getattr(_native_loader.native_module(), "FS_SUPPORTS_NE", False)
    ne_config = _fused_config(negative_evidence=[
        NegativeEvidenceField(field="phone", scorer="exact", threshold=1.0, penalty_bits=20.0),
    ])
    assert fused_match.match_fused_fs_ready(ne_config) is bool(supports_ne)
    assert fused_match.match_fused_fs_ready(_fused_config()) is True


# ── 5. _resolve_probabilistic_fast_path declines NE ─────────────────────────


def test_resolve_probabilistic_fast_path_declines_ne():
    import polars as pl
    from goldenmatch.core.matchkey import precompute_matchkey_transforms

    mk = _mk(
        field="first_name", scorer="jaro_winkler", levels=2, partial_threshold=0.8,
        negative_evidence=[
            NegativeEvidenceField(field="phone", scorer="exact", threshold=1.0, penalty_bits=20.0),
        ],
    )
    df = pl.DataFrame({"__row_id__": [0, 1], "first_name": ["alice", "alice"], "phone": ["555", "666"]})
    prepared = precompute_matchkey_transforms(df, [mk])
    em = EMResult(
        m_probs={"first_name": [0.1, 0.9]}, u_probs={"first_name": [0.9, 0.1]},
        match_weights={"first_name": [-3.0, 3.0]},
        converged=True, iterations=1, proportion_matched=0.1,
    )
    assert _resolve_probabilistic_fast_path(mk, prepared, em) is None

    # Plain (no NE) matchkey with the same shape stays eligible.
    mk_plain = _mk(field="first_name", scorer="jaro_winkler", levels=2, partial_threshold=0.8)
    prepared_plain = precompute_matchkey_transforms(df, [mk_plain])
    assert _resolve_probabilistic_fast_path(mk_plain, prepared_plain, em) is not None


# ── 6. validate_for: NE field key requirements ──────────────────────────────


def _mk_ne_phone(penalty_bits=None):
    return _mk(
        field="name", scorer="jaro_winkler", levels=2, partial_threshold=0.8,
        negative_evidence=[
            NegativeEvidenceField(field="phone", scorer="exact", threshold=1.0, penalty_bits=penalty_bits),
        ],
    )


def _em_no_ne():
    return EMResult(
        m_probs={"name": [0.1, 0.9]}, u_probs={"name": [0.9, 0.1]},
        match_weights={"name": [-3.0, 3.0]},
        converged=True, iterations=1, proportion_matched=0.1,
    )


def test_validate_for_missing_ne_key_without_penalty_bits_raises():
    em = _em_no_ne()  # no __ne__phone entry, no penalty_bits on the matchkey
    mk = _mk_ne_phone(penalty_bits=None)
    with pytest.raises(FSModelMismatchError, match="phone") as exc_info:
        em.validate_for(mk)
    msg = str(exc_info.value)
    assert "retrain" in msg.lower()
    assert "penalty_bits" in msg


def test_validate_for_missing_ne_key_with_penalty_bits_passes():
    em = _em_no_ne()  # still no __ne__phone entry
    mk = _mk_ne_phone(penalty_bits=20.0)  # fixed override skips EM -> no key needed
    em.validate_for(mk)  # must not raise


def test_validate_for_ne_key_present_passes():
    em = EMResult(
        m_probs={"name": [0.1, 0.9]}, u_probs={"name": [0.9, 0.1]},
        match_weights={"name": [-3.0, 3.0], "__ne__phone": [-5.0, 0.0]},
        converged=True, iterations=1, proportion_matched=0.1,
    )
    mk = _mk_ne_phone(penalty_bits=None)
    em.validate_for(mk)  # must not raise


def test_validate_for_ne_key_wrong_length_raises():
    em = EMResult(
        m_probs={"name": [0.1, 0.9]}, u_probs={"name": [0.9, 0.1]},
        match_weights={"name": [-3.0, 3.0], "__ne__phone": [-5.0, 0.0, 1.0]},
        converged=True, iterations=1, proportion_matched=0.1,
    )
    mk = _mk_ne_phone(penalty_bits=None)
    with pytest.raises(FSModelMismatchError, match="phone"):
        em.validate_for(mk)
