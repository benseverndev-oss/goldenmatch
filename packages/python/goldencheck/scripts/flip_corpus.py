"""Flip §8b differential corpus generator (polars-free: numpy + pyarrow).

Generates a deterministic synthetic corpus that exercises every goldencheck
check family, so the Flip differential (2.x-Polars authoritative vs owned-fused)
measures real finding-set deltas. One dataset exceeds the 100k sample cap so the
owned-sample path fires.

Usage:  python scripts/flip_corpus.py [out_dir]   (default: <pkg>/tests/flip/corpus)
Writes one .parquet per dataset. Seeded; byte-stable across runs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

SEED = 42
EPOCH_DAY = np.datetime64("1970-01-01")


def _rng(tag: str) -> np.random.Generator:
    # Distinct but deterministic stream per dataset (no wall-clock).
    return np.random.default_rng(SEED + (abs(hash(tag)) % 100_000))


def ds_numeric_outliers(n: int = 5_000) -> pa.Table:
    r = _rng("numeric_outliers")
    x = r.normal(100.0, 15.0, n)
    # inject a handful of >3sigma outliers deterministically
    x[:: n // 20] = 1_000.0
    y = r.normal(0.0, 1.0, n)
    ints = r.integers(0, 500, n)
    return pa.table({"score": x, "noise": y, "count": ints})


def ds_sequence_gaps(n: int = 3_000) -> pa.Table:
    r = _rng("sequence_gaps")
    ids = np.arange(1, n + 1, dtype=np.int64)
    # punch gaps
    keep = np.ones(n, dtype=bool)
    keep[r.choice(n, size=n // 50, replace=False)] = False
    seq = ids[keep]
    return pa.table({"row_id": seq})


def ds_freshness(n: int = 4_000) -> pa.Table:
    r = _rng("freshness")
    base = np.datetime64("2024-01-01")
    days = r.integers(-800, 40, n)  # a few future dates
    dates = base + days.astype("timedelta64[D]")
    return pa.table({"event_date": pa.array(dates)})


def ds_duplicates(n: int = 6_000) -> pa.Table:
    r = _rng("duplicates")
    names = np.array(["Alice", "Bob", "Carol", "Dan", "Eve"])
    first = names[r.integers(0, len(names), n)]
    cities = np.array(["NYC", "LA", "SF", "Chicago"])
    city = cities[r.integers(0, len(cities), n)]
    # force exact dup rows: copy first 500 rows onto the last 500
    first = first.copy()
    city = city.copy()
    first[-500:] = first[:500]
    city[-500:] = city[:500]
    emails = np.array([f"user{i % 1000}@example.com" for i in range(n)])
    return pa.table({"first_name": first, "city": city, "email": emails})


def ds_age_dob(n: int = 4_000) -> pa.Table:
    r = _rng("age_dob")
    ages = r.integers(18, 90, n).astype(np.int64)
    # dob consistent with age for most, mismatched for a slice
    ref = np.datetime64("2024-06-01")
    dob = ref - (ages.astype("timedelta64[D]") * 365 + r.integers(0, 364, n).astype("timedelta64[D]"))
    ages_reported = ages.copy()
    ages_reported[:: n // 25] += 20  # deterministic mismatches
    return pa.table({"age": ages_reported, "date_of_birth": pa.array(dob.astype("datetime64[D]"))})


def ds_correlation(n: int = 5_000) -> pa.Table:
    r = _rng("correlation")
    a = r.normal(0, 1, n)
    b = 0.8 * a + r.normal(0, 0.5, n)  # correlated
    c = r.normal(0, 1, n)  # independent
    cat1 = np.array(["x", "y", "z"])[r.integers(0, 3, n)]
    cat2 = np.array(["p", "q"])[r.integers(0, 2, n)]
    return pa.table({"var_a": a, "var_b": b, "var_c": c, "cat1": cat1, "cat2": cat2})


def ds_benford(n: int = 5_000) -> pa.Table:
    r = _rng("benford")
    # Benford-ish: 10**uniform  -> leading-digit distribution follows Benford
    benford_like = np.floor(10 ** r.uniform(0, 6, n)).astype(np.int64) + 1
    # anti-benford: uniform leading digits
    anti = r.integers(100000, 999999, n).astype(np.int64)
    return pa.table({"amount": benford_like, "flat_id": anti})


def ds_mixed_dtypes(n: int = 2_000) -> pa.Table:
    r = _rng("mixed_dtypes")
    return pa.table(
        {
            "i8": pa.array(r.integers(-100, 100, n), type=pa.int8()),
            "u32": pa.array(r.integers(0, 1_000_000, n), type=pa.uint32()),
            "f32": pa.array(r.normal(0, 1, n).astype(np.float32), type=pa.float32()),
            "flag": pa.array(r.integers(0, 2, n).astype(bool)),
            "label": np.array(["a", "b", "c"])[r.integers(0, 3, n)],
            "maybe_num": np.array([str(v) for v in r.integers(0, 1000, n)]),  # numeric-looking strings
        }
    )


def ds_large_sampled(n: int = 250_000) -> pa.Table:
    """> sample_size (100k) so the owned-sample path fires in the differential."""
    r = _rng("large_sampled")
    x = r.normal(50.0, 10.0, n)
    x[:: n // 200] = 5_000.0  # outliers spread through the population
    grp = np.array(["north", "south", "east", "west"])[r.integers(0, 4, n)]
    amt = np.floor(10 ** r.uniform(0, 6, n)).astype(np.int64) + 1
    ids = np.arange(1, n + 1, dtype=np.int64)
    return pa.table({"measure": x, "region": grp, "amount": amt, "rec_id": ids})


DATASETS = {
    "numeric_outliers": ds_numeric_outliers,
    "sequence_gaps": ds_sequence_gaps,
    "freshness": ds_freshness,
    "duplicates": ds_duplicates,
    "age_dob": ds_age_dob,
    "correlation": ds_correlation,
    "benford": ds_benford,
    "mixed_dtypes": ds_mixed_dtypes,
    "large_sampled": ds_large_sampled,
}


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent / "tests" / "flip" / "corpus"
    out.mkdir(parents=True, exist_ok=True)
    for name, fn in DATASETS.items():
        tbl = fn()
        path = out / f"{name}.parquet"
        pq.write_table(tbl, path)
        print(f"wrote {path}  ({tbl.num_rows} rows x {tbl.num_columns} cols)")
    print(f"\ncorpus -> {out}")


if __name__ == "__main__":
    main()
