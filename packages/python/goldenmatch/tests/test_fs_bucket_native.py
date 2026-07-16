"""Memory-bounded + fast Fellegi-Sunter scoring via the batched native bucket
worker (issue #1792).

Contracts under test:

1. ``score_probabilistic_bucket_native`` over a multi-block, block-sorted bucket
   is BYTE-IDENTICAL to concatenating ``score_probabilistic_native`` over each
   block slice (same block order, same 4dp rounding) — the kernel isolates
   blocks by the sizes list, so this holds by construction; we assert it.
2. ``score_buckets``'s ``_score_one_bucket`` FS-native path emits the SAME pair
   set as the per-block ``prob_scorer`` loop (``GOLDENMATCH_FS_BUCKET_NATIVE=0``),
   including an oversized block that both paths skip.
3. Routing: ``_fs_default_bucket`` sends an FS matchkey with ``backend=None`` to
   ``score_buckets`` when native FS is available, and the legacy per-block
   batched path when ``GOLDENMATCH_FS_DEFAULT_BUCKET=0``.

Tests that need the native kernel skip when it is not built/enabled.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.probabilistic import (
    _fs_native_enabled,
    score_probabilistic_bucket_native,
    score_probabilistic_native,
    train_em,
)

native_required = pytest.mark.skipif(
    not _fs_native_enabled(),
    reason="native FS kernel not built/enabled (GOLDENMATCH_FS_NATIVE + built _native)",
)


def _mk(**kw) -> MatchkeyConfig:
    defaults = dict(
        name="fs",
        type="probabilistic",
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3, partial_threshold=0.8),
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=2, partial_threshold=0.85),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ],
    )
    defaults.update(kw)
    return MatchkeyConfig(**defaults)


def _multiblock_df() -> pl.DataFrame:
    """Four blocks by ``zip``: sizes 3, 2, 4, and a singleton (1)."""
    return pl.DataFrame(
        {
            "__row_id__": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            "first_name": [
                "John", "Jon", "Jonn",  # zip 90210
                "Jane", "Janet",         # zip 10001
                "Bob", "Rob", "Bobby", "Robert",  # zip 60601
                "Zoe",                   # zip 77777 (singleton)
            ],
            "last_name": [
                "Smith", "Smith", "Smyth",
                "Doe", "Doe",
                "Jones", "Jones", "Jones", "Jones",
                "Xu",
            ],
            "zip": [
                "90210", "90210", "90210",
                "10001", "10001",
                "60601", "60601", "60601", "60601",
                "77777",
            ],
        }
    )


def _pairset(pairs) -> dict[tuple[int, int], float]:
    return {(min(a, b), max(a, b)): round(s, 4) for a, b, s in pairs}


def _block_sorted(df: pl.DataFrame) -> tuple[pl.DataFrame, list[int]]:
    """Sort ``df`` by a ``__block_key__`` = ``zip`` and return (sorted_df,
    run-length size_list) — the exact shape ``_score_one_bucket`` builds."""
    sdf = df.with_columns(pl.col("zip").alias("__block_key__")).sort("__block_key__")
    sizes = (
        sdf.group_by("__block_key__", maintain_order=True)
        .len()
        .get_column("len")
        .to_list()
    )
    return sdf, sizes


# ── 1. batched bucket-native == per-block native ─────────────────────────────


@native_required
def test_bucket_native_equals_concat_per_block():
    df = _multiblock_df()
    mk = _mk()
    em = train_em(df, mk, n_sample_pairs=200)

    sdf, sizes = _block_sorted(df)

    batched = score_probabilistic_bucket_native(sdf, sizes, mk, em)

    per_block: list[tuple[int, int, float]] = []
    offset = 0
    for s in sizes:
        block = sdf.slice(offset, s)
        per_block.extend(score_probabilistic_native(block, mk, em))
        offset += s

    assert _pairset(batched) == _pairset(per_block)


@native_required
def test_bucket_native_honors_exclude():
    df = _multiblock_df()
    mk = _mk()
    em = train_em(df, mk, n_sample_pairs=200)
    sdf, sizes = _block_sorted(df)

    # Exclude one within-block pair; both paths must drop it identically.
    exclude = {(1, 2)}
    batched = score_probabilistic_bucket_native(sdf, sizes, mk, em, exclude)

    per_block: list[tuple[int, int, float]] = []
    offset = 0
    for s in sizes:
        block = sdf.slice(offset, s)
        per_block.extend(score_probabilistic_native(block, mk, em, exclude))
        offset += s

    assert _pairset(batched) == _pairset(per_block)
    assert (1, 2) not in _pairset(batched)


# ── 2. _score_one_bucket FS-native path == per-block loop ─────────────────────


def _oversized_df() -> pl.DataFrame:
    """Blocks by ``grp``: A=5 (oversized, skipped at max_block_size=3),
    B=3 (kept), C=2 (kept), D=1 (singleton)."""
    rows = []
    rid = 1
    # A: 5 similar rows -> oversized
    for nm in ["Aaron", "Aron", "Aaronn", "Aaronx", "Aronn"]:
        rows.append({"__row_id__": rid, "first_name": nm, "last_name": "Alpha", "zip": "A"})
        rid += 1
    # B: 3 rows (2 near-dupes + 1 distinct)
    for nm, ln in [("Brenda", "Beta"), ("Brendaa", "Beta"), ("Zed", "Omega")]:
        rows.append({"__row_id__": rid, "first_name": nm, "last_name": ln, "zip": "B"})
        rid += 1
    # C: 2 near-dupes
    for nm in ["Carl", "Karl"]:
        rows.append({"__row_id__": rid, "first_name": nm, "last_name": "Gamma", "zip": "C"})
        rid += 1
    # D: singleton
    rows.append({"__row_id__": rid, "first_name": "Solo", "last_name": "Delta", "zip": "D"})
    return pl.DataFrame(rows)


@native_required
def test_score_one_bucket_fs_native_matches_per_block(monkeypatch):
    df = _oversized_df()
    mk = _mk()
    blocking = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["zip"])],
        max_block_size=3,
        skip_oversized=True,
    )
    em = train_em(df, mk, n_sample_pairs=200)

    from goldenmatch.backends.score_buckets import score_buckets

    monkeypatch.setenv("GOLDENMATCH_FS_BUCKET_NATIVE", "1")
    got_native = score_buckets(df, blocking, mk, set(), em_result=em)

    monkeypatch.setenv("GOLDENMATCH_FS_BUCKET_NATIVE", "0")
    got_perblock = score_buckets(df, blocking, mk, set(), em_result=em)

    assert _pairset(got_native) == _pairset(got_perblock)
    # The oversized block A (rows 1..5) must contribute NO pairs on either path.
    for a, b in _pairset(got_native):
        assert not (a <= 5 and b <= 5)


@native_required
def test_fs_bucket_native_env_off_reproduces_per_block_byte_for_byte(monkeypatch):
    """GOLDENMATCH_FS_BUCKET_NATIVE=0 forces the per-block prob_scorer loop; its
    output must be byte-for-byte the per-block native scorer over the same
    blocks (scores included, not just the pair set)."""
    df = _multiblock_df()
    mk = _mk()
    blocking = BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["zip"])])
    em = train_em(df, mk, n_sample_pairs=200)

    from goldenmatch.backends.score_buckets import score_buckets

    monkeypatch.setenv("GOLDENMATCH_FS_BUCKET_NATIVE", "0")
    via_bucket = score_buckets(df, blocking, mk, set(), em_result=em)

    # Reference: per-block native over the same block partition.
    sdf, sizes = _block_sorted(df)
    ref: list[tuple[int, int, float]] = []
    offset = 0
    for s in sizes:
        ref.extend(score_probabilistic_native(sdf.slice(offset, s), mk, em))
        offset += s

    assert _pairset(via_bucket) == _pairset(ref)


# ── 3. Routing ───────────────────────────────────────────────────────────────


def _prob_config(backend=None) -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[_mk()],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["zip"])]),
        backend=backend,
    )


def test_fs_bucket_route_decision(monkeypatch, caplog):
    """#1803 item 3: bucket is the FS route by default -- NO native-kernel
    requirement, NO row cap. Exclusions: explicit scale backends, the
    escape hatch, non-field blocking strategies, active profile emitter."""
    import logging

    from goldenmatch.core.pipeline import _fs_use_bucket_route

    mk = _mk()
    cfg = _prob_config(backend=None)

    monkeypatch.delenv("GOLDENMATCH_FS_DEFAULT_BUCKET", raising=False)
    # Default route regardless of the native kernel (the batched fallback
    # needs eager build_blocks -- the #1798 OOM path; non-native bucket is
    # still frame-memory-bounded).
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    assert _fs_use_bucket_route(cfg, mk) is True
    monkeypatch.delenv("GOLDENMATCH_NATIVE", raising=False)
    assert _fs_use_bucket_route(cfg, mk) is True

    # polars-direct is the planner's in-band choice -> same default as None
    # (parity with _use_bucket_scorer's band semantics).
    assert _fs_use_bucket_route(_prob_config(backend="polars-direct"), mk) is True
    # Explicit bucket honored, explicit scale backends keep their routing.
    assert _fs_use_bucket_route(_prob_config(backend="bucket"), mk) is True
    for be in ("ray", "duckdb", "datafusion"):
        assert _fs_use_bucket_route(_prob_config(backend=be), mk) is False

    # Escape hatch always wins -- and warns (the batched fallback is the
    # memory-unbounded #1798 path; opting out should be loud).
    monkeypatch.setenv("GOLDENMATCH_FS_DEFAULT_BUCKET", "0")
    with caplog.at_level(logging.WARNING, logger="goldenmatch.core.pipeline"):
        assert _fs_use_bucket_route(cfg, mk) is False
    assert any(
        "GOLDENMATCH_FS_DEFAULT_BUCKET=0" in r.getMessage() for r in caplog.records
    )
    monkeypatch.delenv("GOLDENMATCH_FS_DEFAULT_BUCKET", raising=False)

    # The columnar opt-in does NOT demote FS: the columnar branch is
    # structurally weighted-only (_is_columnar_eligible), so a probabilistic
    # matchkey keeps the bucket route even with the experiment enabled.
    monkeypatch.setenv("GOLDENMATCH_COLUMNAR_PIPELINE", "1")
    assert _fs_use_bucket_route(cfg, mk) is True
    monkeypatch.delenv("GOLDENMATCH_COLUMNAR_PIPELINE", raising=False)

    # Blocking strategies bucket can't replicate stay legacy.
    lsh_cfg = _prob_config(backend=None)
    lsh_cfg.blocking.strategy = "lsh"
    assert _fs_use_bucket_route(lsh_cfg, mk) is False

    # Controller-profiled sample runs keep legacy block-size signals.
    from goldenmatch.core.profile_emitter import profile_capture

    with profile_capture():
        assert _fs_use_bucket_route(cfg, mk) is False


def test_fs_bucket_route_nonnative_e2e(monkeypatch):
    """A backend=None FS dedupe with the native kernel OFF must still score
    via score_buckets (pre-#1803 it fell back to the batched legacy path)."""
    import goldenmatch as gm
    import goldenmatch.backends.score_buckets as sb_mod

    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    monkeypatch.setenv("GOLDENMATCH_BUCKET_DEFAULT", "0")  # isolate the FS route
    monkeypatch.delenv("GOLDENMATCH_FS_DEFAULT_BUCKET", raising=False)

    calls = {"bucket": 0}
    real_sb = sb_mod.score_buckets

    def spy_sb(*a, **k):
        calls["bucket"] += 1
        return real_sb(*a, **k)

    monkeypatch.setattr(sb_mod, "score_buckets", spy_sb)
    df = _multiblock_df().drop("__row_id__")
    gm.dedupe_df(df, config=_prob_config(backend=None))
    assert calls["bucket"] >= 1


def _splittable_oversized_df() -> pl.DataFrame:
    """One oversized block by ``zip`` (6 rows > max_block_size=3) that
    auto-split can recover, plus a small control block. First names REPEAT
    across last names so whole-block scoring emits cross-sub-block pairs
    that split scoring cannot -- the discriminator between "scored whole"
    (the #1826 dense-NxN behavior) and "auto-split" (#1790 parity)."""
    rows = []
    rid = 1
    for ln in ("Beta", "Gamma"):
        for nm in ("Brenda", "Brendaa", "Brendax"):
            rows.append(
                {"__row_id__": rid, "first_name": nm, "last_name": ln, "zip": "A"}
            )
            rid += 1
    for nm in ["Carl", "Karl"]:
        rows.append({"__row_id__": rid, "first_name": nm, "last_name": "Ctrl", "zip": "C"})
        rid += 1
    return pl.DataFrame(rows)


class TestBucketOversizedAutoSplit:
    """#1803 item 6 / #1826: oversized blocks on the bucket lane must be
    auto-split (parity with build_blocks' #1790 default-path recovery)
    instead of scored whole (vectorized dense NxN = the 1.1 TiB alloc) or
    silently kept."""

    def _blocking(self, skip_oversized=False):
        return BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["zip"])],
            max_block_size=3,
            skip_oversized=skip_oversized,
        )

    def _fixture(self):
        df = _splittable_oversized_df()
        mk = _mk()
        em = train_em(df, mk, n_sample_pairs=200)
        return df, mk, em

    def _run(self, monkeypatch, df, mk, em, native: bool):
        from goldenmatch.backends.score_buckets import score_buckets

        monkeypatch.setenv("GOLDENMATCH_FS_BUCKET_NATIVE", "1" if native else "0")
        return _pairset(
            score_buckets(df, self._blocking(), mk, set(), em_result=em)
        )

    def _expected(self, df, mk, em):
        """The #1790 contract: score _auto_split_block's sub-blocks of the
        oversized block + the control block, nothing else."""
        from goldenmatch.core.blocker import _auto_split_block
        from goldenmatch.core.probabilistic import probabilistic_block_scorer

        scorer = probabilistic_block_scorer(mk, em)
        pairs: list[tuple[int, int, float]] = []
        big = df.filter(pl.col("zip") == "A")
        for b in _auto_split_block(big, 3, "A"):
            pairs.extend(scorer(b.materialize().native, set()))
        pairs.extend(scorer(df.filter(pl.col("zip") == "C"), set()))
        expected = _pairset(pairs)
        # Discriminator sanity: whole-block scoring emits at least one pair
        # the split output lacks (else this test cannot detect scored-whole).
        whole = _pairset(scorer(big, set()))
        assert set(whole) - set(expected), "fixture no longer discriminates"
        return expected

    @native_required
    def test_native_lane_splits_oversized(self, monkeypatch):
        df, mk, em = self._fixture()
        assert self._run(monkeypatch, df, mk, em, native=True) == self._expected(
            df, mk, em
        )

    def test_perblock_lane_splits_oversized(self, monkeypatch):
        df, mk, em = self._fixture()
        assert self._run(monkeypatch, df, mk, em, native=False) == self._expected(
            df, mk, em
        )

    @native_required
    def test_native_and_perblock_lanes_agree(self, monkeypatch):
        df, mk, em = self._fixture()
        assert self._run(monkeypatch, df, mk, em, native=True) == self._run(
            monkeypatch, df, mk, em, native=False
        )

    def test_bucket_route_matches_legacy_route_e2e(self, monkeypatch):
        # Bucket (default) vs legacy batched (build_blocks auto-splits per
        # #1790): identical multi-member clusters on the oversized fixture.
        import goldenmatch as gm

        def _parts(res):
            return sorted(
                tuple(sorted(c["members"]))
                for c in res.clusters.values() if len(c.get("members", [])) > 1
            )

        df = _splittable_oversized_df().drop("__row_id__")
        cfg = GoldenMatchConfig(
            matchkeys=[_mk(model_path=None)],
            blocking=self._blocking(),
        )
        monkeypatch.delenv("GOLDENMATCH_FS_DEFAULT_BUCKET", raising=False)
        bucket = gm.dedupe_df(df, config=cfg)
        monkeypatch.setenv("GOLDENMATCH_FS_DEFAULT_BUCKET", "0")
        legacy = gm.dedupe_df(df, config=cfg)
        assert _parts(bucket) == _parts(legacy)


def test_skip_oversized_true_still_skips_splittable_blocks(monkeypatch):
    """Regression for the #1829 autoconfig OOM: with skip_oversized=True the
    bucket lane must keep its historical SKIP even when the oversized block
    IS splittable -- autoconfig's probe passes are calibrated against the
    skip, and splitting a degenerate constant-key probe block detonated 18M
    pairs in autoconfig verify. Auto-split engages only on the DEFAULT
    skip_oversized=False path (#1826)."""
    from goldenmatch.backends.score_buckets import score_buckets
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig

    df = _splittable_oversized_df()
    mk = _mk()
    em = train_em(df, mk, n_sample_pairs=200)
    blocking = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["zip"])],
        max_block_size=3,
        skip_oversized=True,
    )
    for native in ("1", "0"):
        monkeypatch.setenv("GOLDENMATCH_FS_BUCKET_NATIVE", native)
        pairs = _pairset(score_buckets(df, blocking, mk, set(), em_result=em))
        # Only the control block (rows 7, 8) may emit; the splittable
        # oversized zip=A block (rows 1-6) is skipped whole.
        assert set(pairs) == {(7, 8)}, f"native={native}: {sorted(pairs)}"


def test_vectorized_dense_alloc_guard(monkeypatch):
    """#1826: score_probabilistic_vectorized must refuse a dense NxN it cannot
    afford with an actionable error instead of an allocator OOM. Cap is
    env-tunable; 0 disables."""
    from goldenmatch.core.probabilistic import score_probabilistic_vectorized

    df = _multiblock_df()
    mk = _mk()
    em = train_em(df, mk, n_sample_pairs=200)

    monkeypatch.setenv("GOLDENMATCH_FS_VEC_MAX_ELEMS", "10")  # 10 rows -> 100 > 10
    with pytest.raises(ValueError, match="dense"):
        score_probabilistic_vectorized(df, mk, em)

    monkeypatch.setenv("GOLDENMATCH_FS_VEC_MAX_ELEMS", "0")  # disabled
    assert isinstance(score_probabilistic_vectorized(df, mk, em), list)


@native_required
def test_score_buckets_exclude_handle_parity(monkeypatch):
    """#1803 item 1: score_buckets with a non-empty exclude set must produce
    identical FS pairs on the batched-native path (which now builds the shared
    ExcludeSet handle once at entry) and the per-block path
    (GOLDENMATCH_FS_BUCKET_NATIVE=0), and both must drop the excluded pair."""
    from goldenmatch.backends.score_buckets import score_buckets
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig

    df = _multiblock_df()
    mk = _mk()
    em = train_em(df, mk, n_sample_pairs=200)
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["grp"])])

    def _run():
        matched = {(1, 2)}  # pre-matched pair: must be excluded from FS emit
        pairs = score_buckets(df, blocking, mk, matched, em_result=em)
        return _pairset(pairs)

    monkeypatch.delenv("GOLDENMATCH_FS_BUCKET_NATIVE", raising=False)
    native_batched = _run()
    monkeypatch.setenv("GOLDENMATCH_FS_BUCKET_NATIVE", "0")
    per_block = _run()

    assert native_batched == per_block
    assert (1, 2) not in native_batched


@native_required
def test_routing_backend_none_uses_bucket_when_native(monkeypatch):
    """With _use_bucket_scorer killed (GOLDENMATCH_BUCKET_DEFAULT=0), a
    backend=None FS matchkey still reaches score_buckets purely via
    _fs_default_bucket when native FS is on."""
    import goldenmatch as gm
    import goldenmatch.backends.score_buckets as sb_mod
    import goldenmatch.core.probabilistic as prob_mod

    monkeypatch.setenv("GOLDENMATCH_BUCKET_DEFAULT", "0")
    monkeypatch.delenv("GOLDENMATCH_FS_DEFAULT_BUCKET", raising=False)

    calls = {"bucket": 0, "batched": 0}
    real_sb = sb_mod.score_buckets
    real_batched = prob_mod.score_probabilistic_blocks_batched

    def spy_sb(*a, **k):
        calls["bucket"] += 1
        return real_sb(*a, **k)

    def spy_batched(*a, **k):
        calls["batched"] += 1
        return real_batched(*a, **k)

    monkeypatch.setattr(sb_mod, "score_buckets", spy_sb)
    monkeypatch.setattr(prob_mod, "score_probabilistic_blocks_batched", spy_batched)

    df = _multiblock_df().drop("__row_id__")
    gm.dedupe_df(df, config=_prob_config(backend=None))

    assert calls["bucket"] >= 1
    assert calls["batched"] == 0


def test_routing_fs_default_off_uses_batched(monkeypatch):
    """GOLDENMATCH_FS_DEFAULT_BUCKET=0 (+ _use_bucket_scorer killed) forces the
    legacy per-block batched path. No native kernel required."""
    import goldenmatch as gm
    import goldenmatch.backends.score_buckets as sb_mod
    import goldenmatch.core.probabilistic as prob_mod

    monkeypatch.setenv("GOLDENMATCH_BUCKET_DEFAULT", "0")
    monkeypatch.setenv("GOLDENMATCH_FS_DEFAULT_BUCKET", "0")

    calls = {"bucket": 0, "batched": 0}
    real_sb = sb_mod.score_buckets
    real_batched = prob_mod.score_probabilistic_blocks_batched

    def spy_sb(*a, **k):
        calls["bucket"] += 1
        return real_sb(*a, **k)

    def spy_batched(*a, **k):
        calls["batched"] += 1
        return real_batched(*a, **k)

    monkeypatch.setattr(sb_mod, "score_buckets", spy_sb)
    monkeypatch.setattr(prob_mod, "score_probabilistic_blocks_batched", spy_batched)

    df = _multiblock_df().drop("__row_id__")
    gm.dedupe_df(df, config=_prob_config(backend=None))

    assert calls["batched"] >= 1
    assert calls["bucket"] == 0


# ── Strategy-generated FS candidates: external-blocks scorer ─────────────────


def test_fs_external_blocks_route_decision(monkeypatch):
    """The non-bucket FS refinement: strategy-generated candidates go to the
    memory-bounded external-blocks scorer; the FS_DEFAULT_BUCKET=0 hatch,
    explicit scale backends, and active-emitter probe runs keep legacy
    batched."""
    from goldenmatch.core.pipeline import _fs_external_blocks_route

    monkeypatch.delenv("GOLDENMATCH_FS_DEFAULT_BUCKET", raising=False)

    # static / multi_pass are the bucket route's job, not this one's.
    assert _fs_external_blocks_route(_prob_config(backend=None)) is False

    lsh_cfg = _prob_config(backend=None)
    lsh_cfg.blocking.strategy = "lsh"
    assert _fs_external_blocks_route(lsh_cfg) is True

    # Hatch means "legacy batched" literally.
    monkeypatch.setenv("GOLDENMATCH_FS_DEFAULT_BUCKET", "0")
    assert _fs_external_blocks_route(lsh_cfg) is False
    monkeypatch.delenv("GOLDENMATCH_FS_DEFAULT_BUCKET", raising=False)

    # Explicit scale backends keep their own routing.
    ray_cfg = _prob_config(backend="ray")
    ray_cfg.blocking.strategy = "lsh"
    assert _fs_external_blocks_route(ray_cfg) is False

    # Emitter probe runs are calibrated against the legacy path (#1829).
    from goldenmatch.core.profile_emitter import profile_capture

    with profile_capture():
        assert _fs_external_blocks_route(lsh_cfg) is False


def _external_blocks(df: pl.DataFrame) -> list:
    """Slice df into per-zip BlockResults tagged with a non-static strategy
    (the shape lsh/canopy/sorted_neighborhood hand to the FS scorer)."""
    from goldenmatch.core.blocker import BlockResult

    return [
        BlockResult(
            block_key=str(z),
            df=df.filter(pl.col("zip") == z).lazy(),
            strategy="lsh",
        )
        for z in df["zip"].unique(maintain_order=True).to_list()
    ]


@pytest.mark.parametrize("native", ["1", "0"])
def test_fs_external_blocks_parity_with_batched(monkeypatch, native):
    """Same blocks in -> same pair set + scores as the batched legacy scorer
    (the path these strategies used before), on both scorer lanes."""
    from goldenmatch.backends.score_buckets import (
        score_probabilistic_external_blocks,
    )
    from goldenmatch.core.probabilistic import (
        score_probabilistic_blocks_batched,
    )

    monkeypatch.setenv("GOLDENMATCH_FS_BUCKET_NATIVE", native)
    df = _multiblock_df()
    mk = _mk()
    em = train_em(df, mk, n_sample_pairs=200)
    blocking = BlockingConfig(
        strategy="static", keys=[BlockingKeyConfig(fields=["zip"])]
    )

    got = score_probabilistic_external_blocks(
        _external_blocks(df), blocking, mk, set(), em
    )
    ref = score_probabilistic_blocks_batched(_external_blocks(df), mk, em, set())
    assert _pairset(got) == _pairset(ref)


@pytest.mark.parametrize("native", ["1", "0"])
def test_fs_external_blocks_oversized_semantics(monkeypatch, native):
    """Oversized external blocks follow the bucket lane's semantics: SKIP on
    skip_oversized=True; auto-split (never scored whole when splittable) on
    the default skip_oversized=False."""
    from goldenmatch.backends.score_buckets import (
        score_probabilistic_external_blocks,
    )
    from goldenmatch.core.blocker import _auto_split_block
    from goldenmatch.core.probabilistic import probabilistic_block_scorer

    monkeypatch.setenv("GOLDENMATCH_FS_BUCKET_NATIVE", native)
    df = _splittable_oversized_df()
    mk = _mk()
    em = train_em(df, mk, n_sample_pairs=200)

    def blocking(skip):
        return BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["zip"])],
            max_block_size=3,
            skip_oversized=skip,
        )

    blocks = _external_blocks(df)  # zip A oversized (6 > 3), zip C control

    # skip_oversized=True: oversized block contributes nothing; control kept.
    skipped = _pairset(
        score_probabilistic_external_blocks(blocks, blocking(True), mk, set(), em)
    )
    assert all(a > 6 or b > 6 for a, b in skipped)

    # Default: auto-split. Expected = split sub-blocks + control block.
    scorer = probabilistic_block_scorer(mk, em)
    expected: list[tuple[int, int, float]] = []
    big = df.filter(pl.col("zip") == "A")
    for b in _auto_split_block(big, 3, "A"):
        expected.extend(scorer(b.materialize().native, set()))
    expected.extend(scorer(df.filter(pl.col("zip") == "C"), set()))
    whole = _pairset(scorer(big, set()))
    assert set(whole) - set(_pairset(expected)), "fixture no longer discriminates"

    got = _pairset(
        score_probabilistic_external_blocks(blocks, blocking(False), mk, set(), em)
    )
    assert got == _pairset(expected)


def test_fs_external_blocks_dedupe_routing(monkeypatch):
    """E2E: a sorted_neighborhood FS dedupe routes through the external-blocks
    scorer (not score_buckets, not the batched scorer)."""
    import goldenmatch as gm
    import goldenmatch.backends.score_buckets as sb_mod
    import goldenmatch.core.probabilistic as prob_mod
    from goldenmatch.config.schemas import SortKeyField

    monkeypatch.delenv("GOLDENMATCH_FS_DEFAULT_BUCKET", raising=False)

    calls = {"external": 0, "bucket": 0, "batched": 0}
    real_ext = sb_mod.score_probabilistic_external_blocks
    real_sb = sb_mod.score_buckets
    real_batched = prob_mod.score_probabilistic_blocks_batched

    def spy_ext(*a, **k):
        calls["external"] += 1
        return real_ext(*a, **k)

    def spy_sb(*a, **k):
        calls["bucket"] += 1
        return real_sb(*a, **k)

    def spy_batched(*a, **k):
        calls["batched"] += 1
        return real_batched(*a, **k)

    monkeypatch.setattr(
        sb_mod, "score_probabilistic_external_blocks", spy_ext
    )
    monkeypatch.setattr(sb_mod, "score_buckets", spy_sb)
    monkeypatch.setattr(
        prob_mod, "score_probabilistic_blocks_batched", spy_batched
    )

    cfg = _prob_config(backend=None)
    cfg.blocking = BlockingConfig(
        strategy="sorted_neighborhood",
        sort_key=[SortKeyField(column="last_name")],
        window_size=4,
    )
    df = _multiblock_df().drop("__row_id__")
    gm.dedupe_df(df, config=cfg)

    assert calls["external"] >= 1
    assert calls["bucket"] == 0
    assert calls["batched"] == 0
