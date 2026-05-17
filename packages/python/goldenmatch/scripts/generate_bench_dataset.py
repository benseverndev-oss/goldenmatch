"""Pre-generate the C3 5M bench dataset and publish as a Release asset.

Why pre-generate (PR #295 lesson):
  * The original Phase 6 fixture had no real duplicates -- 5M bench
    measured two backends doing prep + zero scoring. Took 48 min on
    `large-new-64GB` to discover that.
  * Generating at run time means every fixture edit silently changes
    what "5M" means. A frozen artifact gives a stable reference.
  * Pre-generation lets us vet the dataset's correctness ONCE
    (multi-member clusters > 0, fuzzy pairs > 0) before burning
    runner minutes on the full bench.

The dataset is a Parquet file. Stored as a GitHub Release asset
(stable URL, no LFS quota, 2GB/file limit -- well above the ~50 MB
this needs). Tagged `bench-dataset-vN`; bumping the tag is the
explicit signal that the dataset's contents have changed.

Usage::

    # Regenerate locally.
    python scripts/generate_bench_dataset.py \
        --rows 5000000 \
        --out bench-dataset/bench_5m.parquet

    # Validation slice (~10s).
    python scripts/generate_bench_dataset.py --validate \
        bench-dataset/bench_5m.parquet
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

import polars as pl

# ── Fixture construction ────────────────────────────────────────────


_BASE_FIRSTS = [
    "Alice", "Bob", "Charlie", "Dana", "Eve", "Frank",
    "Grace", "Henry", "Iris", "Jack",
]
_BASE_LASTS = [
    "Smith", "Johnson", "Williams", "Brown", "Jones",
    "Garcia", "Miller", "Davis", "Rodriguez", "Martinez",
    "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
    "Thomas", "Taylor", "Moore", "Jackson", "Martin",
]


def _variant(s: str, kind: int) -> str:
    """Three typo variants per (first, last) base identity.

    kind=0: canonical (no change).
    kind=1: lowercase (case variant -- exact scorer misses, fuzzy hits).
    kind=2: drop second character (typo variant).

    Strings shorter than 4 chars fall back to appending 'e'.
    """
    if kind == 0:
        return s
    if kind == 1:
        return s.lower()
    if len(s) > 3:
        return s[0] + s[2:]
    return s + "e"


def build_df(n: int) -> pl.DataFrame:
    """Person-shape df with REAL fuzzy duplicates per email block.

    Groups of 3 consecutive rows share an email AND have small name-typo
    variants of a base (first, last) identity. Fuzzy scoring on names
    finds these as match pairs; exact scoring does not.

    Yields ~n/3 multi-member clusters at the dedupe stage. Each cluster
    has size 3.
    """
    rows = []
    for i in range(n):
        group_id = i // 3
        within = i % 3
        first_base = _BASE_FIRSTS[group_id % len(_BASE_FIRSTS)]
        last_base = _BASE_LASTS[group_id % len(_BASE_LASTS)]
        rows.append({
            "first_name": _variant(first_base, within),
            "last_name":  _variant(last_base, within),
            "email":      f"u{group_id}@example.com",
            "zip":        f"{10000 + (group_id % 100):05d}",
        })
    return pl.DataFrame(rows)


# ── Structural sanity (cheap, no dedupe run) ────────────────────────


def structural_summary(df: pl.DataFrame) -> dict:
    """Stats computable directly from the df. Cheap; runs in seconds
    at 5M scale because it's just Polars aggregations."""
    n = df.height
    return {
        "n_rows": n,
        "n_columns": df.width,
        "columns": df.columns,
        "n_unique_emails": df["email"].n_unique(),
        "n_unique_first_names": df["first_name"].n_unique(),
        "n_unique_last_names": df["last_name"].n_unique(),
        "n_unique_zips": df["zip"].n_unique(),
        "expected_multi_member_clusters": n // 3,
        "expected_cluster_size": 3,
    }


# ── End-to-end validation (small slice, real dedupe) ────────────────


def end_to_end_validation_slice(df: pl.DataFrame, *, slice_rows: int = 10_000) -> dict:
    """Run a small actual `dedupe_df` to confirm fuzzy scoring finds
    the duplicates we built into the fixture. Catches "fixture compiles
    but scoring still emits zero pairs" -- the Phase 6 failure mode.

    Uses the first `slice_rows` rows so the validation runs in seconds.
    """
    import goldenmatch as gm
    from goldenmatch.core.autoconfig import auto_configure_df

    sample = df.head(slice_rows)
    cfg = auto_configure_df(sample, confidence_required=False)
    t0 = perf_counter()
    result = gm.dedupe_df(sample, config=cfg, confidence_required=False)
    wall = perf_counter() - t0
    multi = sum(1 for c in result.clusters.values() if c.get("size", 0) > 1)
    return {
        "validation_rows": sample.height,
        "validation_wall_seconds": round(wall, 3),
        "clusters_total": len(result.clusters),
        "clusters_multi_member": multi,
        "min_expected_multi_member": sample.height // 3,
        # Anything well below n/3 means our typo variants slipped past
        # the default fuzzy scoring. Anything well above means rows
        # we DIDN'T design as duplicates are merging -- also a fixture bug.
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rows", type=int, default=5_000_000,
        help="Row count for the generated dataset.",
    )
    parser.add_argument(
        "--out", type=Path, default=Path("bench-dataset/bench_5m.parquet"),
        help="Output Parquet path.",
    )
    parser.add_argument(
        "--validate", type=Path, default=None,
        help="Validate an EXISTING parquet instead of generating. "
             "Runs structural + end-to-end-slice checks; exits 1 if "
             "the fixture is degenerate.",
    )
    parser.add_argument(
        "--skip-e2e-validation", action="store_true",
        help="Skip the dedupe-slice validation. Use only when generating "
             "a fresh fixture for offline inspection; CI should always "
             "run the e2e check.",
    )
    args = parser.parse_args(argv)

    if args.validate is not None:
        print(f"Validating existing fixture at {args.validate}...", flush=True)
        df = pl.read_parquet(args.validate)
        summary = structural_summary(df)
        e2e = end_to_end_validation_slice(df) if not args.skip_e2e_validation else None
        report = {"structural": summary, "end_to_end_slice": e2e}
        print(json.dumps(report, indent=2), flush=True)
        if e2e is not None:
            min_expected = e2e["min_expected_multi_member"]
            actual = e2e["clusters_multi_member"]
            # Allow a 30% margin -- fuzzy scoring may miss a few of the
            # designed-duplicate pairs depending on which typo variant
            # falls into which block. 70% recall is the floor for
            # "fixture is structurally sound."
            floor = int(min_expected * 0.7)
            if actual < floor:
                print(
                    f"\nVALIDATION FAILED: only {actual:,} multi-member "
                    f"clusters found in {e2e['validation_rows']:,}-row "
                    f"slice; expected >= {floor:,} (70% of {min_expected:,}).",
                    flush=True,
                )
                return 1
        print("\nValidation passed.", flush=True)
        return 0

    print(f"Generating {args.rows:,}-row bench dataset...", flush=True)
    t0 = perf_counter()
    df = build_df(args.rows)
    wall = perf_counter() - t0
    print(f"  build: {wall:.1f}s", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    t0 = perf_counter()
    df.write_parquet(args.out, compression="snappy")
    wall = perf_counter() - t0
    size_mb = args.out.stat().st_size / (1024 * 1024)
    print(f"  write: {wall:.1f}s ({size_mb:.1f} MB)", flush=True)

    print("\nStructural summary:", flush=True)
    print(json.dumps(structural_summary(df), indent=2), flush=True)

    if not args.skip_e2e_validation:
        print("\nEnd-to-end validation slice (10K rows, real dedupe)...", flush=True)
        e2e = end_to_end_validation_slice(df)
        print(json.dumps(e2e, indent=2), flush=True)
        floor = int(e2e["min_expected_multi_member"] * 0.7)
        if e2e["clusters_multi_member"] < floor:
            print(
                f"\nVALIDATION FAILED: only {e2e['clusters_multi_member']:,} "
                f"multi-member clusters found; expected >= {floor:,}. "
                f"Refusing to publish a degenerate fixture.",
                flush=True,
            )
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
