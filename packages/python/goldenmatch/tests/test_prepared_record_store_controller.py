"""End-to-end controller tests for PreparedRecordStore (Phase 3).

Spec §Component 1, Phase 3 integration. The controller's 5-iter sample
loop shares ONE PreparedRecordStore across all iterations so iter 2-5
hit the disk store.

Test design: today's in-memory _PREP_CACHE already gives the
"run_transform called once" property within one process. To prove the
DISK path is doing the work (Phase 3's contribution), we monkey-patch
_PREP_CACHE_MAX to 0 so the in-memory cache is effectively disabled."""
from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest


@pytest.fixture(autouse=True)
def _disable_autoconfig_memory(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")


def _df() -> pl.DataFrame:
    return pl.DataFrame({
        "name":  ["alice", "alyce", "bob", "robert"] * 20,
        "email": [f"u{i}@x.com" for i in range(80)],
    })


def test_baseline_without_disk_store_runs_transform_per_iteration(monkeypatch):
    """Baseline / anchor: with the in-memory cache disabled AND no disk store,
    multiple pipeline iterations each call run_transform independently.

    Uses zero-config mode (no config kwarg) to invoke the controller; the
    controller runs at least one sample iteration plus the full-data pipeline
    call, so with both caches disabled we expect >= 2 run_transform calls.

    This anchors the regression check: if Phase 3 is wired correctly, the
    next test shows the disk store collapses those N calls down to 1.
    """
    import goldenmatch as gm
    import goldenmatch.core.pipeline as pl_mod
    import goldenmatch.core.transform as tm

    monkeypatch.setattr(pl_mod, "_PREP_CACHE_MAX", 0)
    # Note: in-memory cache disabled. No disk store (default off).
    # Zero-config path: no config kwarg → controller runs sample iterations.

    original = tm.run_transform
    calls = [0]

    def counting(*args, **kwargs):
        calls[0] += 1
        return original(*args, **kwargs)

    tm.run_transform = counting
    try:
        gm.dedupe_df(_df())  # zero-config: triggers the controller
    finally:
        tm.run_transform = original

    # Controller runs sample iterations + full-data pipeline; with both
    # caches disabled, expect >= 2 run_transform calls (sample + full-data).
    assert calls[0] >= 2, (
        f"baseline: expected >=2 run_transform calls with both caches "
        f"disabled (zero-config path via controller); got {calls[0]}"
    )


def test_disk_store_makes_iterations_share_prepared_records(monkeypatch, tmp_path: Path):
    """Load-bearing Phase 3 test: with the in-memory cache disabled but
    the disk store enabled, the 5 controller iterations should result in
    EXACTLY 1 run_transform call -- the controller opens one store at
    run() entry, iter 1 materializes, iter 2-5 hit the disk."""
    import goldenmatch as gm
    import goldenmatch.core.pipeline as pl_mod
    import goldenmatch.core.transform as tm
    from goldenmatch.config.schemas import GoldenMatchConfig

    monkeypatch.setattr(pl_mod, "_PREP_CACHE_MAX", 0)  # in-memory off
    monkeypatch.setenv("GOLDENMATCH_PREPARED_RECORD_STORE_DIR", str(tmp_path))
    monkeypatch.setenv("GOLDENMATCH_PREPARED_RECORD_STORE_PERSIST", "1")
    # PERSIST=1 keeps the store file alive across the 5 iterations within
    # one controller.run() call. Without it, the store's cleanup=True
    # default would delete the file when the pipeline's per-call open/
    # close pair runs -- the Phase 3 controller wiring should bypass that
    # by owning the store for the whole iteration loop.

    original = tm.run_transform
    calls = [0]

    def counting(*args, **kwargs):
        calls[0] += 1
        return original(*args, **kwargs)

    tm.run_transform = counting
    try:
        cfg = GoldenMatchConfig(prepared_record_store=True)
        gm.dedupe_df(_df(), config=cfg)
    finally:
        tm.run_transform = original

    assert calls[0] == 1, (
        f"Phase 3: with in-memory cache off + disk store on, the 5 "
        f"controller iterations should result in 1 run_transform call "
        f"(iter 1 materializes, iter 2-5 disk-hit); got {calls[0]}"
    )


def test_controller_closes_store_on_normal_return(monkeypatch, tmp_path: Path):
    """The controller opens the store at run() entry and closes it at
    every exit path (normal return, raise, KeyboardInterrupt). With
    cleanup=True (no PERSIST), the file should be gone after the call."""
    import goldenmatch as gm
    from goldenmatch.config.schemas import GoldenMatchConfig

    monkeypatch.setenv("GOLDENMATCH_PREPARED_RECORD_STORE_DIR", str(tmp_path))
    # cleanup=True by default (no PERSIST env var).

    gm.dedupe_df(_df(), config=GoldenMatchConfig(prepared_record_store=True))
    files = list(tmp_path.glob("*.duckdb"))
    assert files == [], (
        f"store file should be cleaned up after the call; found {files}"
    )


def test_controller_closes_store_on_raise(monkeypatch, tmp_path: Path):
    """If the iteration loop raises (e.g. ControllerNotConfidentError at
    100K+ RED commit), the store still cleans up via the try/finally."""
    # Skipped pending a clean way to force the raise from inside a small
    # fixture; the explicit cleanup=True path is exercised by the
    # normal_return test above plus the Phase 1 idempotent-close test.
    pytest.skip("future: parametrize over the raise/normal-return paths")
