"""Integration test locking Febrl3 F1 parity between bucket and polars-direct.

Guards the regression that motivated the multi-pass fix: the bucket backend was
dropping blocking passes, tanking Febrl3 recall (F1 ~0.85 vs polars-direct
~0.93). This test confirms both backends stay within 0.02 F1 of each other and
that bucket never drops below 0.90.

Gated with `pytest.mark.benchmark` and `pytest.importorskip("recordlinkage")`
so it only runs where the Febrl3 data + recordlinkage are available (the
benchmark lane in CI, or explicit `-m benchmark` locally). Excluded from the
default CI run via `--ignore=...test_autoconfig_benchmarks.py` pattern (this
file is listed alongside that file in the visibility_lanes benchmarks job).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("recordlinkage")

# dqbench_adapters lives under repo-root scripts/.
# parents[4] from packages/python/goldenmatch/tests/<file> resolves to repo root:
#   0: tests/
#   1: goldenmatch/  (package dir)
#   2: python/
#   3: packages/
#   4: goldenmatch/  (repo root)
_REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO / "scripts"))

pytestmark = pytest.mark.benchmark


def _febrl3_f1(backend: str | None) -> float:
    import goldenmatch as gm
    from dqbench_adapters.febrl3 import evaluate_febrl3, load_febrl3_df_and_gt
    from goldenmatch.core.autoconfig import auto_configure_df

    loaded = load_febrl3_df_and_gt()
    assert loaded is not None  # guarded by importorskip above
    df, gt = loaded

    def _dd(frame):
        cfg = auto_configure_df(frame)
        for mk in cfg.get_matchkeys():
            if getattr(mk, "rerank", None):
                mk.rerank = False
        if backend is not None:
            cfg.backend = backend
        return gm.dedupe_df(frame, config=cfg)

    return evaluate_febrl3(df, gt, _dd).f1


def test_bucket_febrl3_f1_matches_polars(monkeypatch):
    """bucket backend Febrl3 F1 must be >= 0.90 and within 0.02 of polars-direct."""
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    polars_f1 = _febrl3_f1("polars-direct")
    bucket_f1 = _febrl3_f1("bucket")
    assert bucket_f1 >= 0.90, f"bucket Febrl3 F1 regressed: {bucket_f1:.4f}"
    assert abs(bucket_f1 - polars_f1) <= 0.02, (
        f"bucket {bucket_f1:.4f} vs polars-direct {polars_f1:.4f} — delta exceeds 0.02"
    )
