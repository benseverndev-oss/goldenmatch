"""Increment 3: the fused Arrow-native match entry (goldenmatch.core.fused_match).

Gate tests for `match_fused_ready` (covered boundary) + a parity test of
`run_match_fused_arrow` against an INDEPENDENT brute-force oracle (block by key
with the same null/sentinel drop, score with jaro_winkler, union-find) -- so the
entry's marshaling (scorer id, weight, threshold, column selection, block-key
semantics) is proven correct end to end, not just kernel-vs-kernel.
"""

from __future__ import annotations

from collections import defaultdict

import pyarrow as pa
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
from goldenmatch.core._native_loader import native_module

_HAS_FUSED = fused_match._match_fused_symbol() is not None


def _covered_config(threshold: float = 0.85) -> GoldenMatchConfig:
    return GoldenMatchConfig(
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["blk"])]),
        matchkeys=[
            MatchkeyConfig(
                name="mk",
                type="weighted",
                fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
                threshold=threshold,
            )
        ],
    )


# ---- gate ---------------------------------------------------------------

def test_ready_true_on_covered_config():
    assert fused_match.match_fused_ready(_covered_config()) is True


def test_ready_true_with_key_transform():
    # Transforms are covered — derived host-side via the pipeline reference.
    c = _covered_config()
    c.blocking.keys[0].transforms = ["lowercase", "soundex"]
    assert fused_match.match_fused_ready(c) is True


def test_ready_true_with_field_transform():
    c = _covered_config()
    c.matchkeys[0].fields[0].transforms = ["lowercase", "strip"]
    assert fused_match.match_fused_ready(c) is True


def test_ready_false_on_uncovered_scorer():
    c = _covered_config()
    c.matchkeys[0].fields[0].scorer = "soundex_match"
    assert fused_match.match_fused_ready(c) is False


def test_ready_false_on_multi_pass_blocking():
    c = _covered_config()
    c.blocking.strategy = "multi_pass"
    assert fused_match.match_fused_ready(c) is False


def test_ready_false_on_two_blocking_keys():
    c = _covered_config()
    c.blocking.keys.append(BlockingKeyConfig(fields=["name"]))
    assert fused_match.match_fused_ready(c) is False


def test_ready_false_on_missing_threshold():
    c = _covered_config()
    c.matchkeys[0].threshold = None
    assert fused_match.match_fused_ready(c) is False


# ---- parity vs an independent brute oracle -----------------------------

def _brute_clusters(keys, names, threshold, key_transforms=(), score_transforms=()):
    """Independent oracle. Applies the SAME transforms via `apply_transforms`
    (the per-value reference `_build_block_key_expr`/`_get_transformed_values`
    fall back to), then blocks + scores + union-finds."""
    from goldenmatch.utils.transforms import apply_transforms

    jw = native_module().jaro_winkler_similarity

    def _xf(v, chain):
        return apply_transforms(v, list(chain)) if chain else v

    keys = [_xf(k, key_transforms) for k in keys]
    names = [_xf(nm, score_transforms) for nm in names]

    blocks: dict[str, list[int]] = defaultdict(list)
    for i, k in enumerate(keys):
        if k is None:
            continue
        if str(k).strip().lower() in ("nan", "null", "none"):
            continue
        blocks[str(k)].append(i)

    parent = list(range(len(keys)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for members in blocks.values():
        for ai in range(len(members)):
            for bi in range(ai + 1, len(members)):
                a, b = members[ai], members[bi]
                if names[a] is None or names[b] is None:
                    continue
                if jw(names[a], names[b]) >= threshold:
                    ra, rb = find(a), find(b)
                    if ra != rb:
                        parent[ra] = rb

    comps: dict[int, list[int]] = defaultdict(list)
    for i in range(len(keys)):
        comps[find(i)].append(i)
    return {frozenset(v) for v in comps.values() if len(v) >= 2}


def _table_to_clusters(tbl):
    comps: dict[int, list[int]] = defaultdict(list)
    for r, c in zip(tbl.column("__row_id__").to_pylist(), tbl.column("__cluster_id__").to_pylist()):
        comps[c].append(r)
    return {frozenset(v) for v in comps.values() if len(v) >= 2}


@pytest.mark.skipif(not _HAS_FUSED, reason="goldenmatch-native match_fused not built")
def test_run_match_fused_arrow_matches_brute_oracle():
    # Blocks form on `blk`; near-duplicate names cross the jaro_winkler threshold.
    keys = ["a", "a", "a", "b", "b", "c", None, "NULL", "nan", "d", "d"]
    names = [
        "jonathan", "jonathon", "michael",   # blk a: jonathan~jonathon merge, michael alone
        "sarah", "sarah",                    # blk b: exact merge
        "lone",                              # blk c: singleton
        "dropme1", "dropme2", "dropme3",     # null / NULL / nan keys dropped
        "kevin", "kevni",                    # blk d: near-dup merge
    ]
    config = _covered_config(threshold=0.85)
    columns = {"blk": pa.array(keys), "name": pa.array(names)}

    tbl = fused_match.run_match_fused_arrow(columns, config)
    assert tbl is not None
    got = _table_to_clusters(tbl)
    want = _brute_clusters(keys, names, 0.85)
    assert got == want


@pytest.mark.skipif(not _HAS_FUSED, reason="goldenmatch-native match_fused not built")
def test_run_match_fused_arrow_matches_brute_oracle_with_transforms():
    # Block key normalized by lowercase+strip (case/whitespace noise collapses into
    # one block); score field normalized the same. Proves the host-side transform
    # derivation is byte-faithful to the pipeline reference.
    keys = [" Smith ", "smith", "SMITH", "jones", "Jones ", "lee"]
    names = ["Jonathan", "jonathon ", " JONATHAN", "sarah", "SARAH", "solo"]
    config = _covered_config(threshold=0.85)
    config.blocking.keys[0].transforms = ["lowercase", "strip"]
    config.matchkeys[0].fields[0].transforms = ["lowercase", "strip"]
    columns = {"blk": pa.array(keys), "name": pa.array(names)}

    tbl = fused_match.run_match_fused_arrow(columns, config)
    assert tbl is not None
    got = _table_to_clusters(tbl)
    want = _brute_clusters(
        keys, names, 0.85, key_transforms=["lowercase", "strip"], score_transforms=["lowercase", "strip"]
    )
    assert got == want


@pytest.mark.skipif(not _HAS_FUSED, reason="goldenmatch-native match_fused not built")
def test_run_match_fused_arrow_soundex_block_key():
    # soundex block key exercises the map_elements(apply_transforms) fallback path
    # end to end (the common auto-config blocking transform).
    keys = ["Smith", "Smyth", "Smithe", "Jones", "Jonez", "Zzzz"]
    names = ["robert", "robbert", "roberto", "alice", "alicia", "solo"]
    config = _covered_config(threshold=0.80)
    config.blocking.keys[0].transforms = ["lowercase", "soundex"]
    columns = {"blk": pa.array(keys), "name": pa.array(names)}

    tbl = fused_match.run_match_fused_arrow(columns, config)
    assert tbl is not None
    got = _table_to_clusters(tbl)
    want = _brute_clusters(keys, names, 0.80, key_transforms=["lowercase", "soundex"])
    assert got == want


def test_run_match_fused_arrow_declines_uncovered():
    c = _covered_config()
    c.matchkeys[0].fields[0].scorer = "soundex_match"
    assert fused_match.run_match_fused_arrow({"blk": pa.array(["a"]), "name": pa.array(["x"])}, c) is None


# ---- Fellegi-Sunter (probabilistic) fused path -------------------------

def _probabilistic_config():
    return GoldenMatchConfig(
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["blk"])]),
        matchkeys=[
            MatchkeyConfig(
                name="mk",
                type="probabilistic",
                link_threshold=0.5,
                fields=[
                    MatchkeyField(field="name", scorer="jaro_winkler", levels=3, partial_threshold=0.8)
                ],
            )
        ],
    )


def _em():
    from goldenmatch.core.probabilistic import EMResult

    # match_weights per level 0/1/2 (log2(m/u)); the only field that matters here.
    return EMResult(
        m_probs={"name": [0.1, 0.3, 0.6]},
        u_probs={"name": [0.7, 0.2, 0.1]},
        match_weights={"name": [-2.0, 0.585, 2.585]},
        converged=True,
        iterations=1,
        proportion_matched=0.1,
    )


def test_fs_ready_true_on_probabilistic_false_on_weighted():
    assert fused_match.match_fused_fs_ready(_probabilistic_config()) is True
    assert fused_match.match_fused_fs_ready(_covered_config()) is False  # weighted


def test_fs_ready_level_thresholds_tracks_kernel_capability(monkeypatch):
    # R4 FLIP: level_thresholds was an unconditional decline (the fused kernel
    # could only band with the hard-coded default banding); it is now a
    # per-feature capability gate on FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS --
    # an old wheel (stub below, lacking the const) still declines so custom
    # lists never cross its FFI; a supporting kernel accepts.
    config = _lt_config()
    monkeypatch.setattr(
        "goldenmatch.core._native_loader.native_module",
        lambda: _stub_kernel(),  # old wheel: no FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS
    )
    assert fused_match.match_fused_fs_ready(config) is False
    monkeypatch.setattr(
        "goldenmatch.core._native_loader.native_module",
        lambda: _stub_kernel(
            FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS=True,
            FS_SUPPORTS_MISSING_NEUTRAL=True,
        ),
    )
    assert fused_match.match_fused_fs_ready(config) is True
    # A plain matchkey is pure-config either way.
    assert fused_match.match_fused_fs_ready(_probabilistic_config()) is True


@pytest.mark.skipif(not _HAS_FUSED, reason="goldenmatch-native match_fused_fs not built")
def test_match_fused_fs_matches_pipeline_fs_block_scorer():
    import polars as pl

    config = _probabilistic_config()
    em = _em()
    keys = ["a", "a", "a", "b", "b", "c"]
    names = ["jonathan", "jonathon", "michael", "sarah", "sara", "solo"]
    df = (
        pl.DataFrame({"blk": keys, "name": names})
        .with_row_index("__row_id__")
        .with_columns(pl.col("__row_id__").cast(pl.Int64))
    )

    # fused FS
    columns = {"blk": pa.array(keys), "name": pa.array(names)}
    tbl = fused_match.run_match_fused_fs_arrow(columns, config, em)
    assert tbl is not None
    got = _table_to_clusters(tbl)

    # reference: the pipeline FS block path (same em -> same kernel FS math)
    assert got == _classic_fs_clusters(df, config, em)


# ---- Fellegi-Sunter fused path: NE + level_thresholds capability (R4) ----

def _stub_kernel(**consts):
    """A fake native module carrying the fused FS symbol + the given
    capability consts -- i.e. some published wheel vintage. No consts =
    an old wheel that predates both the NE and the fused-level_thresholds
    ports."""
    class _Stub:
        def match_fused_fs(self, *a, **kw):  # pragma: no cover - not invoked
            raise NotImplementedError

        def score_block_pairs_fs(self, *a, **kw):  # pragma: no cover
            raise NotImplementedError

    stub = _Stub()
    for k, v in consts.items():
        setattr(stub, k, v)
    return stub


def _supporting_stub():
    return _stub_kernel(
        FS_SUPPORTS_NE=True,
        FS_SUPPORTS_LEVEL_THRESHOLDS=True,
        FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS=True,
        FS_SUPPORTS_MISSING_NEUTRAL=True,
    )


def _ne(**kw):
    base = dict(field="phone", scorer="exact", threshold=1.0, penalty_bits=20.0)
    base.update(kw)
    return NegativeEvidenceField(**base)


def _ne_config(*ne_fields):
    config = _probabilistic_config()
    config.matchkeys[0].negative_evidence = list(ne_fields) or [_ne()]
    return config


def _lt_config(ne=None):
    config = _probabilistic_config()
    f = config.matchkeys[0].fields[0]
    f.levels = 4
    f.level_thresholds = [1.0, 0.92, 0.88]
    if ne is not None:
        config.matchkeys[0].negative_evidence = [ne]
    return config


def _real_kernel():
    try:
        return native_module()
    except Exception:
        return None


_HAS_FUSED_NE = bool(
    _HAS_FUSED and getattr(_real_kernel(), "FS_SUPPORTS_NE", False)
)
_HAS_FUSED_LT = bool(
    _HAS_FUSED
    and getattr(_real_kernel(), "FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS", False)
)


@pytest.mark.skipif(not _HAS_FUSED_NE, reason="kernel lacks fused FS_SUPPORTS_NE")
def test_fs_ready_ne_true_on_real_kernel():
    assert fused_match.match_fused_fs_ready(_ne_config()) is True


@pytest.mark.skipif(not _HAS_FUSED_LT, reason="kernel lacks FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS")
def test_fs_ready_level_thresholds_true_on_real_kernel():
    assert fused_match.match_fused_fs_ready(_lt_config()) is True


def test_fs_ready_ne_declines_on_old_wheel(monkeypatch):
    # Old wheel: has the fused FS kernel but no FS_SUPPORTS_NE -> NE-bearing
    # configs decline. The same wheel also predates missing-as-unobserved, so
    # even a plain FS config must decline rather than silently mis-score nulls.
    monkeypatch.setattr(
        "goldenmatch.core._native_loader.native_module", lambda: _stub_kernel()
    )
    assert fused_match.match_fused_fs_ready(_ne_config()) is False
    assert fused_match.match_fused_fs_ready(_probabilistic_config()) is False


def test_fs_ready_declines_derive_from_ne_even_when_kernel_supports_ne(monkeypatch):
    # derive_from-synthesized NE columns never exist in the raw `columns`
    # mapping run_match_fused_fs_arrow receives (it never runs
    # precompute_matchkey_transforms), so NE would silently never fire ->
    # decline EVEN with a fully-supporting kernel.
    monkeypatch.setattr(
        "goldenmatch.core._native_loader.native_module", _supporting_stub
    )
    cfg = _ne_config(
        _ne(field="full_name", scorer="token_sort", threshold=0.6,
            derive_from=["first_name", "last_name"])
    )
    assert fused_match.match_fused_fs_ready(cfg) is False

    # The spec's asymmetry: the CLASSIC native gate does NOT decline the same
    # matchkey -- derive_from columns are synthesized upstream
    # (precompute_matchkey_transforms) before its block frames are scored.
    from goldenmatch.core import probabilistic as p

    monkeypatch.setattr(p, "_fs_native_enabled", lambda: True)
    assert p._fs_native_eligible(cfg.matchkeys[0]) is True


def test_fs_ready_declines_ensemble_ne_scorer(monkeypatch):
    monkeypatch.setattr(
        "goldenmatch.core._native_loader.native_module", _supporting_stub
    )
    cfg = _ne_config(_ne(scorer="ensemble", threshold=0.5))
    assert fused_match.match_fused_fs_ready(cfg) is False


def test_fs_ready_per_feature_capability_independence(monkeypatch):
    # NE-only wheel: NE configs ready, level_thresholds configs declined.
    monkeypatch.setattr(
        "goldenmatch.core._native_loader.native_module",
        lambda: _stub_kernel(
            FS_SUPPORTS_NE=True, FS_SUPPORTS_MISSING_NEUTRAL=True,
        ),
    )
    assert fused_match.match_fused_fs_ready(_ne_config()) is True
    assert fused_match.match_fused_fs_ready(_lt_config()) is False
    # level_thresholds-only wheel: the reverse.
    monkeypatch.setattr(
        "goldenmatch.core._native_loader.native_module",
        lambda: _stub_kernel(
            FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS=True,
            FS_SUPPORTS_MISSING_NEUTRAL=True,
        ),
    )
    assert fused_match.match_fused_fs_ready(_ne_config()) is False
    assert fused_match.match_fused_fs_ready(_lt_config()) is True


def test_fs_ready_all_configs_require_missing_semantics_capability(monkeypatch):
    # Missing-as-unobserved affects every regular FS field, so every config
    # probes the native module and declines gracefully when loading fails.
    def _boom():
        raise RuntimeError("unavailable")

    monkeypatch.setattr("goldenmatch.core._native_loader.native_module", _boom)
    assert fused_match.match_fused_fs_ready(_probabilistic_config()) is False
    assert fused_match.match_fused_fs_ready(_ne_config()) is False
    assert fused_match.match_fused_fs_ready(_lt_config()) is False


def test_fused_weight_range_uses_fs_weight_range(monkeypatch):
    # The hand-rolled min/max weight sums ignored __ne__ entries and would
    # mis-normalize every fused NE score; the caller must resolve the weight
    # envelope through the centralized fs_weight_range.
    from goldenmatch.core import probabilistic as p

    class _Sentinel(Exception):
        pass

    def _tripwire(em, mk):
        raise _Sentinel

    monkeypatch.setattr(p, "fs_weight_range", _tripwire)
    monkeypatch.setattr(
        fused_match, "_match_fused_fs_symbol", lambda: lambda *a, **kw: []
    )
    columns = {"blk": pa.array(["a", "a"]), "name": pa.array(["x", "y"])}
    with pytest.raises(_Sentinel):
        fused_match.run_match_fused_fs_arrow(columns, _probabilistic_config(), _em())


def _classic_fs_clusters(df, config, em):
    """Reference clusters via the classic pipeline: build_blocks + the routed
    probabilistic block scorer + the same union-find the fused kernel applies
    (shared by all three fused-FS parity tests)."""
    from goldenmatch.core.blocker import build_blocks
    from goldenmatch.core.probabilistic import probabilistic_block_scorer

    scorer = probabilistic_block_scorer(config.get_matchkeys()[0], em)
    pairs = []
    for br in build_blocks(df.lazy(), config.blocking):
        g = br.materialize().native if hasattr(br.df, "collect") else br.df
        pairs += scorer(g)
    parent = list(range(df.height))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, _s in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    comps = defaultdict(list)
    for i in range(df.height):
        comps[find(i)].append(i)
    return {frozenset(v) for v in comps.values() if len(v) >= 2}


@pytest.mark.skipif(not _HAS_FUSED_NE, reason="kernel lacks fused FS_SUPPORTS_NE")
def test_fused_fs_ne_parity():
    # NE (penalty_bits) parity vs the classic pipeline: same data, same EM,
    # identical cluster membership. Similarities sit away from every banding /
    # NE threshold (the documented boundary-tolerance class).
    import polars as pl

    config = _ne_config()  # phone exact-NE, penalty_bits=20
    em = _em()
    keys = ["a", "a", "a", "b", "b", "c"]
    names = ["jonathan", "jonathon", "michael", "sarah", "sara", "solo"]
    # jonathan/jonathon share a phone (NE never fires -> merge survives);
    # sarah/sara disagree (NE fires: 2.585 - 20 normalizes < 0.5 -> suppressed).
    phones = ["555", "555", "999", "111", "222", "000"]
    df = (
        pl.DataFrame({"blk": keys, "name": names, "phone": phones})
        .with_row_index("__row_id__")
        .with_columns(pl.col("__row_id__").cast(pl.Int64))
    )
    columns = {
        "blk": pa.array(keys), "name": pa.array(names), "phone": pa.array(phones)
    }

    tbl = fused_match.run_match_fused_fs_arrow(columns, config, em)
    assert tbl is not None
    got = _table_to_clusters(tbl)
    assert got == _classic_fs_clusters(df, config, em)
    # NE actually changed the outcome on BOTH paths (guards vacuous parity).
    assert frozenset({0, 1}) in got
    assert frozenset({3, 4}) not in got


@pytest.mark.skipif(
    not (_HAS_FUSED_NE and _HAS_FUSED_LT),
    reason="kernel lacks fused NE + level_thresholds",
)
def test_fused_fs_ne_with_level_thresholds_parity():
    # Combined coverage: custom level_thresholds banding + an EM-learned NE
    # weight (penalty_bits=None -> __ne__phone fired weight), fused vs classic.
    import polars as pl
    from goldenmatch.core.probabilistic import EMResult

    config = _lt_config(ne=_ne(penalty_bits=None))
    em = EMResult(
        m_probs={"name": [0.05, 0.1, 0.25, 0.6], "__ne__phone": [0.0625, 0.9375]},
        u_probs={"name": [0.6, 0.25, 0.1, 0.05], "__ne__phone": [0.5, 0.5]},
        match_weights={"name": [-2.0, 0.5, 1.5, 2.585], "__ne__phone": [-8.0, 0.0]},
        converged=True,
        iterations=1,
        proportion_matched=0.1,
    )
    keys = ["a", "a", "a", "b", "b", "c"]
    names = ["jonathan", "jonathon", "michael", "sarah", "sara", "solo"]
    phones = ["555", "555", "999", "111", "222", "000"]
    df = (
        pl.DataFrame({"blk": keys, "name": names, "phone": phones})
        .with_row_index("__row_id__")
        .with_columns(pl.col("__row_id__").cast(pl.Int64))
    )
    columns = {
        "blk": pa.array(keys), "name": pa.array(names), "phone": pa.array(phones)
    }

    tbl = fused_match.run_match_fused_fs_arrow(columns, config, em)
    assert tbl is not None
    got = _table_to_clusters(tbl)
    assert got == _classic_fs_clusters(df, config, em)
    assert frozenset({0, 1}) in got  # banded level-2 agreement, NE silent
    assert frozenset({3, 4}) not in got  # EM-learned NE fired -> suppressed


# ---- multi-pass blocking fused path ------------------------------------

def _multipass_config():
    return GoldenMatchConfig(
        blocking=BlockingConfig(
            strategy="multi_pass",
            keys=[BlockingKeyConfig(fields=["blk1"])],
            passes=[BlockingKeyConfig(fields=["blk1"]), BlockingKeyConfig(fields=["blk2"])],
        ),
        matchkeys=[
            MatchkeyConfig(
                name="mk",
                type="weighted",
                threshold=0.85,
                fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
            )
        ],
    )


def test_multipass_ready_true_false():
    assert fused_match.match_fused_multipass_ready(_multipass_config()) is True
    assert fused_match.match_fused_multipass_ready(_covered_config()) is False  # static


@pytest.mark.skipif(not _HAS_FUSED, reason="goldenmatch-native match_fused not built")
def test_multipass_merges_across_passes():
    # pass1 blocks on blk1: {0,1}(a), {4,5}(z); pass2 on blk2: {0,2}(p), {3,4}(r).
    # All names identical -> every intra-block pair merges; the union chains
    # 0-1-2 (0-1 via pass1, 0-2 via pass2) and 3-4-5 (3-4 pass2, 4-5 pass1).
    blk1 = ["a", "a", "x", "y", "z", "z"]
    blk2 = ["p", "q", "p", "r", "r", "s"]
    name = ["john"] * 6
    config = _multipass_config()
    columns = {"blk1": pa.array(blk1), "blk2": pa.array(blk2), "name": pa.array(name)}
    tbl = fused_match.run_match_fused_multipass_arrow(columns, config)
    assert tbl is not None
    got = _table_to_clusters(tbl)
    assert got == {frozenset({0, 1, 2}), frozenset({3, 4, 5})}


@pytest.mark.skipif(not _HAS_FUSED, reason="goldenmatch-native match_fused not built")
def test_multipass_equals_single_pass_when_one_pass():
    # A one-pass multi_pass config must equal the single-key fused result.
    blk = ["a", "a", "a", "b", "b", "c"]
    name = ["jonathan", "jonathon", "michael", "sarah", "sarah", "lone"]
    columns = {"blk": pa.array(blk), "name": pa.array(name)}

    single = _covered_config(threshold=0.85)
    single.blocking.keys = [BlockingKeyConfig(fields=["blk"])]
    mp = GoldenMatchConfig(
        blocking=BlockingConfig(
            strategy="multi_pass",
            keys=[BlockingKeyConfig(fields=["blk"])],
            passes=[BlockingKeyConfig(fields=["blk"])],
        ),
        matchkeys=single.matchkeys,
    )
    got_single = _table_to_clusters(fused_match.run_match_fused_arrow(columns, single))
    got_mp = _table_to_clusters(fused_match.run_match_fused_multipass_arrow(columns, mp))
    assert got_mp == got_single


# ---- W2a: arrow-backend prep (polars-free covered spine) -----------------
#
# Twins of the three brute-oracle tests under GOLDENMATCH_FRAME=arrow: the
# fused prep derives the key/score columns via ArrowFrame + arrow_derive
# instead of the Polars expressions; the oracle and expectations are
# UNCHANGED, so any derivation drift fails here exactly like it would on the
# polars backend.

@pytest.mark.skipif(not _HAS_FUSED, reason="goldenmatch-native match_fused not built")
def test_fused_arrow_backend_matches_brute_oracle(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FRAME", "arrow")
    keys = ["a", "a", "a", "b", "b", "c", None, "NULL", "nan", "d", "d"]
    names = [
        "jonathan", "jonathon", "michael",
        "sarah", "sarah",
        "lone",
        "dropme1", "dropme2", "dropme3",
        "kevin", "kevni",
    ]
    config = _covered_config(threshold=0.85)
    columns = {"blk": pa.array(keys), "name": pa.array(names)}

    tbl = fused_match.run_match_fused_arrow(columns, config)
    assert tbl is not None
    assert _table_to_clusters(tbl) == _brute_clusters(keys, names, 0.85)


@pytest.mark.skipif(not _HAS_FUSED, reason="goldenmatch-native match_fused not built")
def test_fused_arrow_backend_matches_brute_oracle_with_transforms(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FRAME", "arrow")
    keys = [" Smith ", "smith", "SMITH", "jones", "Jones ", "lee"]
    names = ["Jonathan", "jonathon ", " JONATHAN", "sarah", "SARAH", "solo"]
    config = _covered_config(threshold=0.85)
    config.blocking.keys[0].transforms = ["lowercase", "strip"]
    config.matchkeys[0].fields[0].transforms = ["lowercase", "strip"]
    columns = {"blk": pa.array(keys), "name": pa.array(names)}

    tbl = fused_match.run_match_fused_arrow(columns, config)
    assert tbl is not None
    assert _table_to_clusters(tbl) == _brute_clusters(
        keys, names, 0.85,
        key_transforms=["lowercase", "strip"], score_transforms=["lowercase", "strip"],
    )


@pytest.mark.skipif(not _HAS_FUSED, reason="goldenmatch-native match_fused not built")
def test_fused_arrow_backend_soundex_block_key(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FRAME", "arrow")
    keys = ["Smith", "Smyth", "Smithe", "Jones", "Jonez", "Zzzz"]
    names = ["robert", "robbert", "roberto", "alice", "alicia", "solo"]
    config = _covered_config(threshold=0.80)
    config.blocking.keys[0].transforms = ["lowercase", "soundex"]
    columns = {"blk": pa.array(keys), "name": pa.array(names)}

    tbl = fused_match.run_match_fused_arrow(columns, config)
    assert tbl is not None
    assert _table_to_clusters(tbl) == _brute_clusters(
        keys, names, 0.80, key_transforms=["lowercase", "soundex"]
    )


@pytest.mark.skipif(not _HAS_FUSED, reason="goldenmatch-native match_fused not built")
def test_fused_arrow_backend_polars_free_tripwire():
    # The W2a spine proof: under GOLDENMATCH_FRAME=arrow, pyarrow-in ->
    # match_fused -> clusters-out must not import polars AT ALL. The
    # _LazyPolars proxy guarantees any real pl.* touch lands in sys.modules,
    # so the check can genuinely fail. Scope: the fused-prep call itself (the
    # pipeline CALLER still materializes via Polars until W2c/W2d).
    import subprocess
    import sys

    code = (
        "import sys\n"
        "import pyarrow as pa\n"
        "from goldenmatch.config.schemas import (\n"
        "    BlockingConfig, BlockingKeyConfig, GoldenMatchConfig,\n"
        "    MatchkeyConfig, MatchkeyField,\n"
        ")\n"
        "from goldenmatch.core import fused_match\n"
        "config = GoldenMatchConfig(\n"
        "    blocking=BlockingConfig(strategy='static',\n"
        "        keys=[BlockingKeyConfig(fields=['blk'], transforms=['lowercase', 'soundex'])]),\n"
        "    matchkeys=[MatchkeyConfig(name='mk', type='weighted', threshold=0.85,\n"
        "        fields=[MatchkeyField(field='name', scorer='jaro_winkler', weight=1.0,\n"
        "                              transforms=['lowercase', 'strip'])])],\n"
        ")\n"
        "columns = {'blk': pa.array(['Smith', 'Smyth', 'Jones']),\n"
        "           'name': pa.array(['Robert ', 'robert', 'alice'])}\n"
        "tbl = fused_match.run_match_fused_arrow(columns, config)\n"
        "assert tbl is not None, 'kernel declined; tripwire needs the fused symbol'\n"
        "assert tbl.num_rows == 3\n"
        "assert 'polars' not in sys.modules, 'fused arrow prep imported polars'\n"
    )
    env = {"GOLDENMATCH_FRAME": "arrow"}
    import os
    for k in (
        "PATH", "SYSTEMROOT", "SYSTEMDRIVE", "PYTHONPATH", "VIRTUAL_ENV",
        "PYTHONIOENCODING", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME",
        "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR",
    ):
        if k in os.environ:
            env[k] = os.environ[k]
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=180, env=env
    )
    assert proc.returncode == 0, proc.stderr


def test_fused_arrow_backend_declines_uncovered(monkeypatch):
    # The decline contract is backend-independent.
    monkeypatch.setenv("GOLDENMATCH_FRAME", "arrow")
    c = _covered_config()
    c.matchkeys[0].fields[0].scorer = "soundex_match"
    assert fused_match.run_match_fused_arrow({"blk": pa.array(["a"]), "name": pa.array(["x"])}, c) is None


# ---- Fellegi-Sunter fused path: MULTI-PASS blocking (#1804 item 2) --------
#
# Expands the fused FS coverage from single-static-key to the compound-union
# blocking shape that OOM'd in #1798. No new native code: each pass runs the
# SAME single-key FS kernel; per-pass clusters are union-find-merged host-side
# (byte-parity with the classic multi-pass FS pipeline).

def _fs_multipass_config():
    return GoldenMatchConfig(
        blocking=BlockingConfig(
            strategy="multi_pass",
            keys=[BlockingKeyConfig(fields=["blk1"])],
            passes=[
                BlockingKeyConfig(fields=["blk1"]),
                BlockingKeyConfig(fields=["blk2"]),
            ],
        ),
        matchkeys=[
            MatchkeyConfig(
                name="mk", type="probabilistic", link_threshold=0.5,
                fields=[MatchkeyField(
                    field="name", scorer="jaro_winkler", levels=3,
                    partial_threshold=0.8,
                )],
            )
        ],
    )


def test_fs_multipass_ready_true_false():
    assert fused_match.match_fused_fs_multipass_ready(_fs_multipass_config()) is True
    # static single-key FS config -> the single-key gate, not the multipass one.
    assert fused_match.match_fused_fs_multipass_ready(_probabilistic_config()) is False
    # weighted multipass -> the weighted twin, not this one (wrong matchkey type).
    assert fused_match.match_fused_fs_multipass_ready(_multipass_config()) is False


def test_fs_multipass_ready_declines_uncovered_matchkey():
    # A non-FS-native scorer fails the shared _fused_fs_matchkey_covered check
    # even with a valid multi_pass blocking shape.
    cfg = _fs_multipass_config()
    cfg.matchkeys[0].fields[0].scorer = "soundex_match"
    assert fused_match.match_fused_fs_multipass_ready(cfg) is False
    # empty passes -> declined.
    cfg2 = _fs_multipass_config()
    cfg2.blocking.passes = []
    assert fused_match.match_fused_fs_multipass_ready(cfg2) is False


@pytest.mark.skipif(not _HAS_FUSED, reason="goldenmatch-native match_fused_fs not built")
def test_fs_multipass_merges_across_passes():
    import polars as pl

    # pass1 blocks on blk1: {0,1}, {4,5}; pass2 on blk2: {0,2}, {3,4}. Identical
    # names -> every intra-block pair scores above link_threshold; the union
    # chains 0-1-2 (0-1 pass1, 0-2 pass2) and 3-4-5 (3-4 pass2, 4-5 pass1).
    blk1 = ["a", "a", "x", "y", "z", "z"]
    blk2 = ["p", "q", "p", "r", "r", "s"]
    name = ["jonathan"] * 6
    config = _fs_multipass_config()
    em = _em()
    columns = {"blk1": pa.array(blk1), "blk2": pa.array(blk2), "name": pa.array(name)}
    tbl = fused_match.run_match_fused_fs_multipass_arrow(columns, config, em)
    assert tbl is not None
    got = _table_to_clusters(tbl)
    assert got == {frozenset({0, 1, 2}), frozenset({3, 4, 5})}

    # Byte-parity with the classic multi-pass FS pipeline on the same data + EM.
    df = (
        pl.DataFrame({"blk1": blk1, "blk2": blk2, "name": name})
        .with_row_index("__row_id__")
        .with_columns(pl.col("__row_id__").cast(pl.Int64))
    )
    assert got == _classic_fs_clusters(df, config, em)


@pytest.mark.skipif(not _HAS_FUSED, reason="goldenmatch-native match_fused_fs not built")
def test_fs_multipass_parity_partial_matches():
    import polars as pl

    # Realistic shape: near-dup names that only partially merge, spread across
    # two orthogonal blocking keys -- exercises the union of link-pairs, not just
    # all-identical blocks.
    blk1 = ["a", "a", "a", "b", "b", "c"]
    blk2 = ["p", "q", "p", "q", "q", "p"]
    name = ["jonathan", "jonathon", "michael", "sarah", "sara", "solo"]
    config = _fs_multipass_config()
    em = _em()
    columns = {"blk1": pa.array(blk1), "blk2": pa.array(blk2), "name": pa.array(name)}
    tbl = fused_match.run_match_fused_fs_multipass_arrow(columns, config, em)
    assert tbl is not None
    got = _table_to_clusters(tbl)

    df = (
        pl.DataFrame({"blk1": blk1, "blk2": blk2, "name": name})
        .with_row_index("__row_id__")
        .with_columns(pl.col("__row_id__").cast(pl.Int64))
    )
    assert got == _classic_fs_clusters(df, config, em)


@pytest.mark.skipif(not _HAS_FUSED, reason="goldenmatch-native match_fused_fs not built")
def test_fs_multipass_equals_single_pass_when_one_pass():
    # A one-pass multi_pass FS config must equal the single-key fused FS result.
    blk = ["a", "a", "a", "b", "b", "c"]
    name = ["jonathan", "jonathon", "michael", "sarah", "sara", "solo"]
    em = _em()
    columns = {"blk": pa.array(blk), "name": pa.array(name)}

    single = _probabilistic_config()
    mp = GoldenMatchConfig(
        blocking=BlockingConfig(
            strategy="multi_pass",
            keys=[BlockingKeyConfig(fields=["blk"])],
            passes=[BlockingKeyConfig(fields=["blk"])],
        ),
        matchkeys=single.matchkeys,
    )
    got_single = _table_to_clusters(
        fused_match.run_match_fused_fs_arrow(columns, single, em)
    )
    got_mp = _table_to_clusters(
        fused_match.run_match_fused_fs_multipass_arrow(columns, mp, em)
    )
    assert got_mp == got_single


def test_fs_multipass_declines_uncovered_returns_none():
    # Runner returns None (not raise) on an uncovered config -> caller falls back.
    cfg = _fs_multipass_config()
    cfg.matchkeys[0].fields[0].scorer = "soundex_match"
    out = fused_match.run_match_fused_fs_multipass_arrow(
        {"blk1": pa.array(["a"]), "blk2": pa.array(["p"]), "name": pa.array(["x"])},
        cfg, _em(),
    )
    assert out is None
