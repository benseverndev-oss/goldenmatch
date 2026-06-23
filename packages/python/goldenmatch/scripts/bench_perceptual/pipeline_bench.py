"""End-to-end media-dedup F1 + wall over a synthetic image-pHash column.

Closes the gap that the kernel/scorer/blocker lanes measure components in
isolation: this runs the REAL `dedupe_df` pipeline (explicit `phash` matchkey +
`perceptual` blocking) on a frame of perceptual-hash variants and reports F1 vs
ground truth plus wall + per-stage timings (`core.bench.bench_capture`). Imports
goldenmatch's pipeline (heavy); called only by the bench, never the kernel path.
"""
from __future__ import annotations

import time

import datasets  # sibling module; run.py bootstraps sys.path before importing this


def e2e_image_dedupe(n_bases: int = 30, threshold: float = 0.85) -> dict:
    """Hash an image-variant suite to a pHash column, dedupe it through the real
    pipeline, and report F1 (vs the same-entity ground truth) + wall + stages.

    Recall is intentionally bounded by pHash's geometric blind spot (rotate/crop
    score ~0), so this is the honest end-to-end number for the *photometric*
    crawl-tier feature -- the radial feature is what lifts the geometric cases."""
    import polars as pl

    from goldenmatch import dedupe_df
    from goldenmatch.config.schemas import (
        BlockingConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
        PerceptualKeyConfig,
    )
    from goldenmatch.core import perceptual
    from goldenmatch.core.bench import bench_capture
    from goldenmatch.core.evaluate import evaluate_clusters

    suite = datasets.build_image_suite(n_bases)
    # Item i is row i, so the pipeline's positional __row_id__ matches item_id and
    # the suite's (item_id, item_id) ground-truth pairs line up with result.clusters.
    hexes = [perceptual.phash_hex(perceptual.phash_image(it.payload)) for it in suite.items]
    df = pl.DataFrame({"ph": hexes})

    config = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="phash_match",
                type="weighted",
                threshold=threshold,
                fields=[MatchkeyField(field="ph", scorer="phash", weight=1.0)],
            )
        ],
        blocking=BlockingConfig(
            strategy="perceptual", perceptual=PerceptualKeyConfig(column="ph")
        ),
    )

    t0 = time.perf_counter()
    with bench_capture() as bench:
        result = dedupe_df(df, config=config)
    wall = time.perf_counter() - t0

    ev = evaluate_clusters(result.clusters, suite.gt_pairs)
    timings = bench.to_dict().get("stage_timings_seconds", {})
    return {
        "records": df.height,
        "base_entities": n_bases,
        "threshold": threshold,
        "f1": round(ev.f1, 4),
        "precision": round(ev.precision, 4),
        "recall": round(ev.recall, 4),
        "tp": ev.tp,
        "fp": ev.fp,
        "fn": ev.fn,
        "wall_sec": round(wall, 4),
        "throughput_rec_per_sec": round(df.height / wall, 1) if wall else None,
        "stage_timings_seconds": {k: round(v, 4) for k, v in timings.items()},
        "note": "recall is bounded by pHash's geometric blind spot (rotate/crop ~0); "
        "the radial feature addresses those cases",
    }
