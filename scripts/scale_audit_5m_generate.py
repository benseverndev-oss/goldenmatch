"""Synthetic person-record generator for the 5M scale audit.

Vectorized (numpy draws + polars string assembly / corruption / write) so 5M
rows generate in seconds instead of the ~15-23 min the old per-row
``random.Random`` + ``csv.writerow`` loop took. Pulls names from the bundled
refdata packs (US Census 2010 surnames + given-name canonicals) so the blocking
distribution at 5M is realistic instead of pathologically uniform.

Output schema: ``id, cluster_id, first_name, last_name, email, phone, address,
city, state, zip, specialty``. Records sharing a ``cluster_id`` are duplicates
of one another (ground truth for F1). Duplicates carry realistic messiness
(case / whitespace / typo variation + null'd secondary fields).

Deterministic for a given ``(n_records, dupe_rate, seed)`` -- the seed drives a
single ``np.random.default_rng``, so the fixture is reproducible (and cacheable
by ``fetch_or_gen_fixture.sh``). NOT byte-identical to the old row-loop output
(different RNG), but statistically equivalent: same split, same census-weighted
surname skew, same corruption kinds and rates.

Usage:

    python scripts/scale_audit_5m_generate.py \\
        --n-records 5000000 \\
        --dupe-rate 0.12 \\
        --output tests/benchmarks/datasets/synthetic_5m.csv

The ground-truth CSV (``<output>.ground_truth.csv``) is written alongside for
``goldenmatch evaluate``.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from goldenmatch.refdata import given_names, surnames

# ── value pools ────────────────────────────────────────────────────────────

STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
]
SPECIALTIES = [
    "Cardiology", "Oncology", "Neurology", "Orthopedics", "Dermatology",
    "Pediatrics", "Radiology", "Psychiatry", "Urology", "Gastroenterology",
    "Endocrinology", "Pulmonology", "Nephrology", "Rheumatology", "Hematology",
    "Ophthalmology", "Anesthesiology", "Pathology", "Emergency Medicine",
    "Family Medicine", "Internal Medicine", "Obstetrics", "Surgery",
]
DOMAINS = [
    "gmail.com", "yahoo.com", "outlook.com", "aol.com", "hospital.org",
    "clinic.com", "health.net", "mayoclinic.org", "kaiser.org", "pennmedicine.org",
]
STREETS = [
    "Main St", "Oak Ave", "Elm Blvd", "Pine Dr", "Maple Ln", "Cedar Rd",
    "Birch Ct", "Walnut Way", "Park Ave", "Lake Dr", "Hill Rd", "Spring St",
    "Washington Blvd", "Lincoln Ave", "Jefferson Dr", "Madison St",
]
CITIES = [
    "Philadelphia", "New York", "Newark", "Wilmington", "Baltimore",
    "Hartford", "Richmond", "Pittsburgh", "Boston", "Chicago", "Houston",
    "Phoenix", "San Francisco", "Seattle", "Denver", "Atlanta", "Miami",
    "Dallas", "Detroit", "Portland", "Cleveland", "Indianapolis", "Minneapolis",
]

FIELDNAMES = [
    "id", "cluster_id", "first_name", "last_name", "email", "phone",
    "address", "city", "state", "zip", "specialty",
]


def _load_name_pools() -> tuple[list[str], list[int], list[str]]:
    """Census-weighted surnames + uniform first-name canonicals.

    Returns ``(surnames_list, surname_weights, first_names_list)``.
    Weights track 2010 Census ``count`` so blocking has a realistic skew.
    """
    surnames._load()
    given_names._load()
    if surnames._state is None or given_names._state is None:
        raise RuntimeError(
            "refdata not loaded — install with the wheel that bundles data files."
        )
    pool_last = list(surnames._state.ranks.keys())
    weights_last = [surnames._state.counts[n] for n in pool_last]
    pool_first = sorted(given_names._state.canonicals)
    # Title-case both pools to match real-world casing.
    pool_last = [n.title() for n in pool_last]
    pool_first = [n.title() for n in pool_first]
    return pool_last, weights_last, pool_first


def _apply_case(col, choice):
    """Polars expr: per-row case variation (upper/lower/title/unchanged) keyed
    by an integer ``choice`` Series in [0, 4)."""
    import polars as pl

    return (
        pl.when(choice == 0).then(col.str.to_uppercase())
        .when(choice == 1).then(col.str.to_lowercase())
        .when(choice == 2).then(col.str.to_titlecase())
        .otherwise(col)
    )


def generate(
    output_path: Path,
    n_records: int,
    dupe_rate: float,
    seed: int = 42,
) -> dict[str, int | float]:
    """Vectorized generate of ``n_records`` rows with controlled duplicates.

    Returns a stats dict for the runner to log.
    """
    import polars as pl

    rng = np.random.default_rng(seed)
    pool_last_l, weights_last, pool_first_l = _load_name_pools()
    pool_last = np.asarray(pool_last_l)
    pool_first = np.asarray(pool_first_l)
    p_last = np.asarray(weights_last, dtype=np.float64)
    p_last /= p_last.sum()

    n_unique = int(n_records * (1 - dupe_rate))
    n_dupes = n_records - n_unique
    # No pure-junk rows (they crash the GoldenCheck reader during controller
    # sample iteration); the scale audit measures dedupe throughput, not reader
    # robustness. Retained as a stat for parity with prior fixtures.
    n_junk = 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ground_truth_path = output_path.with_suffix(".ground_truth.csv")
    print(f"Generating {n_unique:,} unique + {n_dupes:,} dupes = {n_records:,} "
          f"total to {output_path}", flush=True)
    t0 = time.time()

    def _pick(pool, n):
        return pool[rng.integers(0, len(pool), n)]

    # ── unique base records (fully vectorized) ──
    first = _pick(pool_first, n_unique)
    last = pool_last[rng.choice(len(pool_last), size=n_unique, p=p_last)]
    city = _pick(np.asarray(CITIES), n_unique)
    state = _pick(np.asarray(STATES), n_unique)
    specialty = _pick(np.asarray(SPECIALTIES), n_unique)
    street = _pick(np.asarray(STREETS), n_unique)
    email_num = rng.integers(1, 1000, n_unique).astype(str)
    domain = _pick(np.asarray(DOMAINS), n_unique)
    phone = np.char.add(np.char.add(np.char.add(np.char.add(
        rng.integers(200, 1000, n_unique).astype(str), "-"),
        rng.integers(100, 1000, n_unique).astype(str)), "-"),
        rng.integers(1000, 10000, n_unique).astype(str))
    address = np.char.add(np.char.add(
        rng.integers(1, 10000, n_unique).astype(str), " "), street)
    zip_code = rng.integers(10000, 100000, n_unique).astype(str)
    # email = first.lower() + "." + last.lower() + num + "@" + domain
    email = np.char.add(np.char.add(np.char.add(np.char.add(np.char.add(
        np.char.lower(first), "."), np.char.lower(last)),
        email_num), "@"), domain)

    ids = np.arange(1, n_unique + 1, dtype=np.int64)
    uniq = pl.DataFrame({
        "id": ids, "cluster_id": ids,
        "first_name": first, "last_name": last, "email": email,
        "phone": phone, "address": address, "city": city, "state": state,
        "zip": zip_code, "specialty": specialty,
    })

    # ── duplicates: gather base rows, new ids, keep cluster_id ──
    src = rng.integers(0, n_unique, n_dupes)
    dupe_ids = np.arange(n_unique + 1, n_records + 1, dtype=np.int64)
    dupes = uniq[src].with_columns(pl.Series("id", dupe_ids))

    # Corruption (vectorized; matches the OLD per-row _mess_up KINDS + rates):
    #   - case variation on the key match fields + city/state/specialty
    #   - whitespace padding on a subset of names
    #   - single-char interior typo on a subset of last names (random position)
    #   - null 1-2 secondary fields on ~30% of dupes
    lc = pl.col

    def _rc(n):  # per-row case choice Series in [0, 4)
        return pl.Series(rng.integers(0, 4, n))

    def _mask(n, p):
        return pl.Series(rng.random(n) < p)

    dupes = dupes.with_columns([
        _apply_case(lc("first_name"), _rc(n_dupes)).alias("first_name"),
        _apply_case(lc("last_name"), _rc(n_dupes)).alias("last_name"),
        _apply_case(lc("city"), _rc(n_dupes)).alias("city"),
        _apply_case(lc("state"), _rc(n_dupes)).alias("state"),
        _apply_case(lc("specialty"), _rc(n_dupes)).alias("specialty"),
    ])
    # Whitespace pad ~20% of first names (leading), ~20% of last names (trailing).
    dupes = dupes.with_columns([
        pl.when(_mask(n_dupes, 0.2)).then(pl.lit("  ") + lc("first_name"))
          .otherwise(lc("first_name")).alias("first_name"),
        pl.when(_mask(n_dupes, 0.2)).then(lc("last_name") + pl.lit("  "))
          .otherwise(lc("last_name")).alias("last_name"),
    ])
    # Single-char interior typo on a subset of names (>3 chars): replace one
    # char at a per-row random position (str.slice takes per-row expression
    # offsets). Applied to last (~45%) and first (~35%) names so nearly every
    # dupe carries at least one fuzzy match field (old loop guaranteed >=1 mess).
    _alpha = np.asarray(list("abcdefghijklmnopqrstuvwxyz"))

    def _typo(field, p):
        flen = lc(field).str.len_chars()
        pos = pl.Series(rng.integers(0, 1_000_000, n_dupes)) % (flen - 2).clip(lower_bound=1) + 1
        tchar = pl.Series(_alpha[rng.integers(0, 26, n_dupes)])
        return (
            pl.when(_mask(n_dupes, p) & (flen > 3))
            .then(lc(field).str.slice(0, pos) + tchar + lc(field).str.slice(pos + 1))
            .otherwise(lc(field)).alias(field)
        )

    dupes = dupes.with_columns([_typo("last_name", 0.45), _typo("first_name", 0.35)])
    # Null 1-2 secondary fields on ~30% of dupes.
    null_row = rng.random(n_dupes) < 0.3
    for fld in ("phone", "email", "address", "specialty"):
        fmask = pl.Series(null_row & (rng.random(n_dupes) < 0.45))
        dupes = dupes.with_columns(
            pl.when(fmask).then(pl.lit("")).otherwise(lc(fld)).alias(fld)
        )

    full = pl.concat([uniq, dupes], how="vertical").select(FIELDNAMES)
    full.write_csv(output_path)
    full.select(["id", "cluster_id"]).write_csv(ground_truth_path)

    elapsed = time.time() - t0
    size_mb = output_path.stat().st_size / 1024 / 1024
    stats = {
        "n_unique_base": n_unique,
        "n_dupes": n_dupes,
        "n_junk": n_junk,
        "n_total": n_unique + n_dupes,
        "size_mb": round(size_mb, 1),
        "elapsed_seconds": round(elapsed, 1),
        "n_surnames_in_pool": len(pool_last),
    }
    print(f"Done in {elapsed:.1f}s. {size_mb:.1f} MB. Stats: {stats}", flush=True)
    return stats


def validate_block_distribution(csv_path: Path, max_p95: int = 5000) -> bool:
    """Sanity check the last_name distribution. Fails fast if the generator
    produced a pathological skew that would hang scoring."""
    import polars as pl

    df = pl.read_csv(csv_path, ignore_errors=True, infer_schema_length=0)
    counts = df.group_by("last_name").len().sort("len", descending=True)
    block_sizes = counts["len"].to_list()
    if not block_sizes:
        print("FAIL: no surname blocks observed")
        return False
    block_sizes.sort()
    p50 = block_sizes[len(block_sizes) // 2]
    p95 = block_sizes[max(0, int(0.95 * len(block_sizes)) - 1)]
    p99 = block_sizes[max(0, int(0.99 * len(block_sizes)) - 1)]
    largest = block_sizes[-1]
    print(f"last_name block sizes — P50={p50:,} P95={p95:,} P99={p99:,} max={largest:,}")
    if p95 > max_p95:
        print(f"FAIL: P95 block size {p95:,} > {max_p95:,} threshold")
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-records", type=int, default=5_000_000)
    parser.add_argument("--dupe-rate", type=float, default=0.12)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tests/benchmarks/datasets/synthetic_5m.csv"),
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip the post-generation P95 block-size sanity check.",
    )
    parser.add_argument(
        "--max-block-p95",
        type=int,
        default=5000,
        help="Fail if last_name P95 block size exceeds this. Default 5000.",
    )
    args = parser.parse_args(argv)

    stats = generate(args.output, args.n_records, args.dupe_rate, args.seed)
    if not args.skip_validate:
        ok = validate_block_distribution(args.output, max_p95=args.max_block_p95)
        if not ok:
            print("Generation succeeded but block-size sanity check FAILED.")
            return 1
    print("Stats:", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
