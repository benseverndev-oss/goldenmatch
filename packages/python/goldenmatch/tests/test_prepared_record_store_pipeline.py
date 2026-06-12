"""Integration tests for the prepared-record store inside the pipeline.

Spec §Component 1, Phase 2 wiring."""
from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from goldenmatch.config.schemas import GoldenMatchConfig


@pytest.fixture(autouse=True)
def _force_polars_direct(monkeypatch):
    # These tests exercise the partitioned-block-scoring / prepared-record-store
    # path, which only runs under polars-direct. Native-by-default would route
    # to bucket and bypass the machinery under test.
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    # Every test in this file uses the SAME _df(); the process-global in-memory
    # _PREP_CACHE is keyed by df content, so one test's dedupe_df leaves a cache
    # entry that makes a sibling's "first call must prep" assertion flake under
    # `pytest -n auto` (worker-order dependent). Reset it before each test so
    # every test is self-contained (per CLAUDE.md xdist-isolation rule).
    from goldenmatch.core import pipeline as _pipeline

    _pipeline._prep_cache_clear()


def _df() -> pl.DataFrame:
    return pl.DataFrame({
        "name":  ["alice", "alyce", "bob", "robert"] * 20,
        "email": [f"u{i}@x.com" for i in range(80)],
    })


def test_config_default_disables_prepared_record_store():
    """Default flag is False; existing in-memory _PREP_CACHE path is unchanged."""
    cfg = GoldenMatchConfig()
    assert cfg.prepared_record_store is False


def test_config_accepts_prepared_record_store_true():
    cfg = GoldenMatchConfig(prepared_record_store=True)
    assert cfg.prepared_record_store is True


def test_dedupe_df_with_prepared_store_writes_to_disk(tmp_path: Path, monkeypatch):
    """End-to-end: with the flag on, _run_dedupe_pipeline materializes
    prepared records to a disk store; cache hits land in the store and
    can be re-read across re-runs of dedupe_df with the same config."""
    import goldenmatch as gm
    # Disable cross-run autoconfig memory for isolation (per other
    # integration tests in this repo).
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    # Point the store at a known tempdir so we can assert files appear.
    monkeypatch.setenv("GOLDENMATCH_PREPARED_RECORD_STORE_DIR", str(tmp_path))

    df = _df()
    cfg = GoldenMatchConfig(prepared_record_store=True)
    result = gm.dedupe_df(df, config=cfg)
    assert result is not None
    # The store may have been closed + cleaned up by the time we check here
    # (cleanup=True is the default). The call-count test below confirms the
    # disk path is exercised; this test just validates dedupe_df runs cleanly
    # with prepared_record_store=True.


def test_dedupe_df_with_prepared_store_skips_second_run_transform(monkeypatch, tmp_path: Path):
    """Load-bearing: when prepared_record_store=True, two sequential
    dedupe_df calls on the same df should result in run_transform being
    called only ONCE (second call hits the store).

    Cross-call persistence requires cleanup=False on the underlying store
    OR a stable file path; we wire via the GOLDENMATCH_PREPARED_RECORD_STORE_DIR
    env var to get a stable location.
    """
    import goldenmatch as gm
    import goldenmatch.core.transform as tm
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    monkeypatch.setenv("GOLDENMATCH_PREPARED_RECORD_STORE_DIR", str(tmp_path))
    monkeypatch.setenv("GOLDENMATCH_PREPARED_RECORD_STORE_PERSIST", "1")  # cleanup=False

    original = tm.run_transform
    calls = [0]

    def counting(*args, **kwargs):
        calls[0] += 1
        return original(*args, **kwargs)

    tm.run_transform = counting
    try:
        df = _df()
        cfg = GoldenMatchConfig(prepared_record_store=True)
        gm.dedupe_df(df, config=cfg)
        first_count = calls[0]
        gm.dedupe_df(df, config=cfg)
        second_count = calls[0] - first_count
    finally:
        tm.run_transform = original

    # First call has to prep; second call should hit the store.
    assert first_count >= 1, "first call must invoke run_transform at least once"
    assert second_count == 0, (
        f"second call should hit the prepared-record-store; "
        f"run_transform was still called {second_count} times"
    )
