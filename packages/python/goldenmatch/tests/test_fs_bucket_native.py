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


def test_fs_bucket_route_decision(monkeypatch):
    """#1803 item 3: bucket is the FS route by default -- NO native-kernel
    requirement, NO row cap. Exclusions: explicit scale backends, the
    escape hatch, non-field blocking strategies, active profile emitter."""
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

    # Escape hatch always wins.
    monkeypatch.setenv("GOLDENMATCH_FS_DEFAULT_BUCKET", "0")
    assert _fs_use_bucket_route(cfg, mk) is False
    monkeypatch.delenv("GOLDENMATCH_FS_DEFAULT_BUCKET", raising=False)

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
