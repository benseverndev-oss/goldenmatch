"""Bucket is the DEFAULT fuzzy scorer (perf/gm-bucket-default).

`_use_bucket_scorer` routes an UNSET / planner-`polars-direct` backend to the
bucket scorer within a memory-safe row band (5-7x faster than the legacy
per-block path, byte-identical clusters), while:
  - honoring an explicit backend (bucket always; ray/duckdb/datafusion/chunked
    keep their own routing);
  - deferring to the explicit `GOLDENMATCH_COLUMNAR_PIPELINE` opt-in;
  - keeping the LEGACY path during controller profiling (so auto-config's
    block-size signals are unchanged);
  - a `GOLDENMATCH_BUCKET_DEFAULT=0` kill-switch;
  - staying legacy above the row cap.
"""
from __future__ import annotations

import pyarrow as pa
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.pipeline import _BUCKET_DEFAULT_MAX_ROWS, _use_bucket_scorer


@pytest.fixture(autouse=True)
def _isolate_routing_globals(monkeypatch):
    """Pin the process-global routing state _use_bucket_scorer reads.

    _use_bucket_scorer consults three ambient globals -- the
    GOLDENMATCH_COLUMNAR_PIPELINE and GOLDENMATCH_BUCKET_DEFAULT env vars and the
    profile-emitter stack (has_active_emitter). A co-located test in the same
    xdist worker that leaves any of them set flips the routing decision, so these
    tests (which assert the decision for specific inputs) were shard-membership
    dependent. Surfaced when the duration-based pytest-split (PR #1865)
    reshuffled which tests share a shard with these. Reset to a clean baseline so
    the assertions test the function, not the ambient state.
    """
    monkeypatch.delenv("GOLDENMATCH_COLUMNAR_PIPELINE", raising=False)
    monkeypatch.delenv("GOLDENMATCH_BUCKET_DEFAULT", raising=False)
    from goldenmatch.core import profile_emitter as _pe

    token = _pe._emitter_stack.set(())
    try:
        yield
    finally:
        _pe._emitter_stack.reset(token)


def _tbl(n: int) -> pa.Table:
    return pa.table({"a": [str(i % 7) for i in range(n)]})


def _cfg(backend=None) -> GoldenMatchConfig:
    c = GoldenMatchConfig()
    c.backend = backend
    return c


def test_unset_backend_routes_to_bucket_in_band():
    assert _use_bucket_scorer(_cfg(None), _tbl(1000)) is True
    assert _use_bucket_scorer(_cfg("polars-direct"), _tbl(1000)) is True


def test_explicit_bucket_always():
    # honored even above the row cap
    assert _use_bucket_scorer(_cfg("bucket"), _tbl(10)) is True


@pytest.mark.parametrize("backend", ["ray", "duckdb", "datafusion", "chunked"])
def test_explicit_scale_backends_never_bucket(backend):
    assert _use_bucket_scorer(_cfg(backend), _tbl(1000)) is False


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_BUCKET_DEFAULT", "0")
    assert _use_bucket_scorer(_cfg(None), _tbl(1000)) is False


def test_columnar_opt_in_wins(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_COLUMNAR_PIPELINE", "1")
    assert _use_bucket_scorer(_cfg(None), _tbl(1000)) is False


def test_controller_profiling_stays_legacy():
    # while a profile capture is active (the auto-config sample path), the unset
    # backend must NOT switch to bucket -- keeps the controller's decisions stable.
    from goldenmatch.core.profile_emitter import profile_capture

    with profile_capture():
        assert _use_bucket_scorer(_cfg(None), _tbl(1000)) is False
    # outside the capture, back to bucket
    assert _use_bucket_scorer(_cfg(None), _tbl(1000)) is True


def test_above_row_cap_stays_legacy():
    assert _use_bucket_scorer(_cfg(None), _tbl(_BUCKET_DEFAULT_MAX_ROWS + 1)) is False
    assert _use_bucket_scorer(_cfg(None), _tbl(_BUCKET_DEFAULT_MAX_ROWS)) is True


def _explicit_fuzzy_cfg() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="name", type="weighted", threshold=0.85,
            fields=[
                MatchkeyField(field="first", scorer="jaro_winkler", weight=0.5),
                MatchkeyField(field="last", scorer="jaro_winkler", weight=0.5),
            ],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["zip"], transforms=["strip"])],
        ),
    )


def test_bucket_default_output_identical_to_legacy(monkeypatch):
    """End-to-end: the bucket-default path (unset backend) finds the SAME
    duplicates as the legacy per-block path (kill-switch on)."""
    from goldenmatch import dedupe_df

    firsts = ["ann", "ann", "bob", "bobby", "cara", "dan", "dan", "eve"]
    lasts = ["smith", "smith", "jones", "jones", "lee", "poe", "poe", "ray"]
    n = 240
    tbl = pa.table({
        "first": (firsts * (n // 8)),
        "last": (lasts * (n // 8)),
        "zip": [str(10000 + (i % 30)) for i in range(n)],
    })
    cfg = _explicit_fuzzy_cfg()

    monkeypatch.setenv("GOLDENMATCH_BUCKET_DEFAULT", "0")  # legacy per-block
    res_legacy = dedupe_df(tbl, config=cfg)
    legacy_dupes = res_legacy.dupes.num_rows if res_legacy.dupes is not None else 0

    monkeypatch.delenv("GOLDENMATCH_BUCKET_DEFAULT", raising=False)  # bucket default
    res_bucket = dedupe_df(tbl, config=cfg)
    bucket_dupes = res_bucket.dupes.num_rows if res_bucket.dupes is not None else 0

    assert bucket_dupes == legacy_dupes


class TestDataScaledNBuckets:
    """#1803 item 5: n_buckets grows with data size above the CPU-derived
    floor (target ~50K rows/bucket, capped 4096); legacy formula when the
    caller gives no height."""

    def test_no_height_keeps_cpu_formula(self):
        import os

        from goldenmatch.backends.score_buckets import _default_n_buckets
        assert _default_n_buckets() == min((os.cpu_count() or 4) * 4, 1024)

    def test_small_height_uses_cpu_floor(self):
        import os

        from goldenmatch.backends.score_buckets import _default_n_buckets
        floor = min((os.cpu_count() or 4) * 4, 1024)
        assert _default_n_buckets(10_000) == floor

    def test_large_height_scales_up(self):
        from goldenmatch.backends.score_buckets import _default_n_buckets
        assert _default_n_buckets(100_000_000) == 2000  # 100M / 50K

    def test_capped_at_4096(self):
        from goldenmatch.backends.score_buckets import _default_n_buckets
        assert _default_n_buckets(1_000_000_000) == 4096
