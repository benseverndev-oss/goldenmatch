#!/usr/bin/env python
"""Issue #1805 (checkbox 4) — moderate-scale FS route-vs-route parity.

No route-vs-route FS parity ran above ~150 rows in the test suite (Febrl3 ~5k
only in the opt-in bench). This runs the arrow-lane vs polars-lane FS parity
(the same axis PR-A pins at a few hundred rows in the per-PR suite) at a
MODERATE scale the per-PR suite can't afford, in the scheduled lane: a
scale-dependent frame-backend divergence in the FS score/block path would
surface here.

The EM is pinned via a persisted ``model_path`` so training is identical across
lanes (FS EM is sample-order sensitive; the frame backend is the axis under
test). Cluster membership must be identical. Exits non-zero on divergence.

Usage:
    uv run python packages/python/goldenmatch/scripts/bench_fs_route_parity.py \
        --rows 20000 --dup-frac 0.2
"""
from __future__ import annotations

import argparse
import os
import tempfile
import time


def _fs_cfg(model_path: str):
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="fs", type="probabilistic", model_path=model_path, fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3, partial_threshold=0.85),
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=2, partial_threshold=0.85),
            MatchkeyField(field="email", scorer="exact", levels=2),
        ])],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["zip"])]),
    )


def membership(clusters) -> frozenset:
    """Canonical multi-member cluster membership: frozenset of frozensets of
    row-ids. The comparable shape across routes (pure, unit-tested)."""
    return frozenset(
        frozenset(int(m) for m in c.get("members", []))
        for c in (clusters or {}).values()
        if len(c.get("members", [])) > 1
    )


def _pin_model(df, cfg) -> None:
    from goldenmatch.core.blocker import build_blocks
    from goldenmatch.core.probabilistic import train_em

    mk = cfg.matchkeys[0]
    blocks = build_blocks(df.lazy(), cfg.blocking)
    train_em(df, mk, blocks=blocks, blocking_fields=["zip"], seed=42).save_json(mk.model_path)


def _dedupe_on_lane(df, cfg, lane: str):
    import goldenmatch as gm

    os.environ["GOLDENMATCH_FRAME"] = lane
    os.environ["GOLDENMATCH_FS_NATIVE"] = "0"  # isolate the frame axis
    t0 = time.perf_counter()
    res = gm.dedupe_df(df, config=cfg, confidence_required=False)
    return membership(res.clusters), time.perf_counter() - t0


def check_route_parity(rows: int, dup_frac: float, seed: int) -> tuple[bool, str]:
    """Run FS dedupe under arrow + polars at ``rows`` scale and compare cluster
    membership. Returns ``(ok, detail)``. Importable so the parity mechanism is
    unit-tested at small scale before the scheduled lane runs it at 10-50k."""
    import importlib.util
    import pathlib

    # Reuse the distributed bench's ground-truth generator.
    spec = importlib.util.spec_from_file_location(
        "bench_fs_distributed", pathlib.Path(__file__).parent / "bench_fs_distributed.py")
    bfd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bfd)
    df, _gt = bfd.gen_with_gt(rows, dup_frac, seed)
    df = df.with_row_index("__row_id__")

    with tempfile.TemporaryDirectory() as td:
        cfg = _fs_cfg(os.path.join(td, "fs_model.json"))
        _pin_model(df, cfg)
        arrow, wa = _dedupe_on_lane(df, cfg, "arrow")
        polars, wp = _dedupe_on_lane(df, cfg, "polars")

    if not arrow:
        return False, f"no multi-member clusters at {df.height} rows (parity vacuous)"
    if arrow != polars:
        return False, (f"arrow-vs-polars FS divergence at {df.height} rows: "
                       f"only-arrow={len(arrow - polars)} only-polars={len(polars - arrow)}")
    return True, (f"arrow==polars at {df.height} rows: {len(arrow)} multi-member "
                  f"clusters (arrow {wa:.1f}s / polars {wp:.1f}s)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=20_000)
    ap.add_argument("--dup-frac", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    ok, detail = check_route_parity(args.rows, args.dup_frac, args.seed)
    print(f"\n## bench-fs-route-parity\n- {'PASS' if ok else 'FAIL'}: {detail}")
    if not ok:
        print(f"::error::FS route parity broke -- {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
