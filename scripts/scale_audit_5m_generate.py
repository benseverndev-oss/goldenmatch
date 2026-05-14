"""Synthetic person-record generator for the 5M scale audit.

Streaming CSV writer. Pulls names from the bundled refdata packs
(US Census 2010 surnames + given-name canonicals) so the blocking
distribution at 5M is realistic instead of pathologically uniform
across 46 buckets like ``tests/generate_synthetic.py``.

Output schema: ``id, cluster_id, first_name, last_name, email,
phone, address, city, state, zip, specialty``. Records sharing a
``cluster_id`` are duplicates of one another (ground truth for F1).

Usage:

    python scripts/scale_audit_5m_generate.py \\
        --n-records 5000000 \\
        --dupe-rate 0.12 \\
        --output tests/benchmarks/datasets/synthetic_5m.csv

The ground-truth CSV (``<output>.ground_truth.csv``) is written
alongside for ``goldenmatch evaluate``.
"""
from __future__ import annotations

import argparse
import csv
import random
import string
import sys
import time
from pathlib import Path

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


def _random_phone(rng: random.Random) -> str:
    return f"{rng.randint(200, 999)}-{rng.randint(100, 999)}-{rng.randint(1000, 9999)}"


def _random_zip(rng: random.Random) -> str:
    return f"{rng.randint(10000, 99999)}"


def _random_address(rng: random.Random) -> str:
    return f"{rng.randint(1, 9999)} {rng.choice(STREETS)}"


def _mess_up(value: str | None, mess_type: str, rng: random.Random) -> str | None:
    """Apply realistic messiness to a duplicated value."""
    if value is None or value == "":
        return value
    if mess_type == "case":
        return rng.choice([value.upper(), value.lower(), value.title(), value])
    if mess_type == "whitespace":
        spaces = " " * rng.randint(1, 4)
        return rng.choice([spaces + value, value + spaces, spaces + value + spaces])
    if mess_type == "typo":
        if len(value) > 2:
            i = rng.randint(1, len(value) - 2)
            return value[:i] + rng.choice(string.ascii_lowercase) + value[i + 1:]
        return value
    if mess_type == "null":
        return rng.choice(["", "NULL", "N/A", "  "])
    if mess_type == "phone_format":
        digits = value.replace("-", "")
        return rng.choice([
            digits,
            f"({digits[:3]}) {digits[3:6]}-{digits[6:]}",
            f"{digits[:3]}.{digits[3:6]}.{digits[6:]}",
            f"1-{value}",
            f"+1{digits}",
        ])
    if mess_type == "email_mess":
        return rng.choice([
            value.upper(),
            " " + value,
            value + " ",
            value.replace("@", " @ "),
        ])
    return value


_MESS_OPTIONS = [
    ("first_name", "case"), ("first_name", "typo"), ("first_name", "whitespace"),
    ("last_name", "case"), ("last_name", "typo"), ("last_name", "whitespace"),
    ("email", "email_mess"), ("email", "case"),
    ("phone", "phone_format"),
    ("address", "case"), ("address", "whitespace"),
    ("city", "case"),
    ("state", "case"),
    # No ("zip", "whitespace") — Polars infers zip as int and chokes on the
    # whitespace-wrapped value during downstream CSV re-reads (GoldenCheck).
    ("specialty", "case"), ("specialty", "typo"),
]


def generate(
    output_path: Path,
    n_records: int,
    dupe_rate: float,
    seed: int = 42,
) -> dict[str, int | float]:
    """Stream-generate ``n_records`` rows with controlled duplicates.

    Returns a stats dict for the runner to log.
    """
    rng = random.Random(seed)
    pool_last, weights_last, pool_first = _load_name_pools()

    n_unique = int(n_records * (1 - dupe_rate))
    n_dupes = n_records - n_unique
    # No pure-junk rows in this fixture — they exist in real data but crash
    # the GoldenCheck reader during controller sample iteration before any
    # downstream quality override can fire. The scale audit measures dedupe
    # throughput, not GoldenCheck robustness; that's tested elsewhere.
    n_junk = 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ground_truth_path = output_path.with_suffix(".ground_truth.csv")

    print(f"Streaming {n_unique:,} unique + {n_dupes:,} dupes + {n_junk:,} junk "
          f"= {n_records:,} total to {output_path}")

    # Phase 1: write base records to CSV, keep a tuple-index in memory for
    # the duplicate-sampling phase. Tuple is the minimum needed to rebuild
    # a duplicate row (id, cluster_id, name fields, email, phone, address).
    t0 = time.time()
    base_index: list[tuple] = []
    with output_path.open("w", newline="", encoding="utf-8") as out_f, \
         ground_truth_path.open("w", newline="", encoding="utf-8") as gt_f:
        writer = csv.DictWriter(out_f, fieldnames=FIELDNAMES)
        writer.writeheader()
        gt_writer = csv.writer(gt_f)
        gt_writer.writerow(["id", "cluster_id"])

        for i in range(n_unique):
            cluster_id = i + 1
            first = rng.choice(pool_first)
            last = rng.choices(pool_last, weights=weights_last, k=1)[0]
            email = f"{first.lower()}.{last.lower()}{rng.randint(1, 999)}@{rng.choice(DOMAINS)}"
            phone = _random_phone(rng)
            address = _random_address(rng)
            city = rng.choice(CITIES)
            state = rng.choice(STATES)
            zip_code = _random_zip(rng)
            specialty = rng.choice(SPECIALTIES)
            record = {
                "id": cluster_id,
                "cluster_id": cluster_id,
                "first_name": first,
                "last_name": last,
                "email": email,
                "phone": phone,
                "address": address,
                "city": city,
                "state": state,
                "zip": zip_code,
                "specialty": specialty,
            }
            writer.writerow(record)
            gt_writer.writerow([cluster_id, cluster_id])
            base_index.append(record)

        # Phase 2: stream duplicates, sampling from base_index.
        next_id = n_unique + 1
        for _ in range(n_dupes):
            original = rng.choice(base_index)
            dupe = dict(original)
            dupe["id"] = next_id
            # cluster_id stays — that's the ground-truth link.
            next_id += 1

            # 30% chance to null out 0-2 secondary fields.
            if rng.random() < 0.3:
                null_fields = rng.sample(
                    ["phone", "email", "address", "specialty"],
                    k=rng.randint(1, 2),
                )
                for f in null_fields:
                    dupe[f] = ""

            # Apply 1-3 messiness types.
            n_messes = rng.randint(1, 3)
            for field, mess_type in rng.sample(_MESS_OPTIONS, min(n_messes, len(_MESS_OPTIONS))):
                v = dupe.get(field)
                if v and v not in ("NULL", "N/A"):
                    dupe[field] = _mess_up(v, mess_type, rng)

            writer.writerow(dupe)
            gt_writer.writerow([dupe["id"], dupe["cluster_id"]])

        # Phase 3: pure-junk rows (no cluster_id — they should land in their
        # own singleton clusters in the output).
        for _ in range(n_junk):
            cluster_id = next_id
            next_id += 1
            junk_type = rng.choice(["empty", "garbage"])
            if junk_type == "empty":
                row = {k: "" for k in FIELDNAMES}
                row["id"] = cluster_id
                row["cluster_id"] = cluster_id
            else:
                row = {
                    k: "".join(rng.choices(string.printable[:62], k=rng.randint(1, 20)))
                    for k in FIELDNAMES
                }
                row["id"] = cluster_id
                row["cluster_id"] = cluster_id
            writer.writerow(row)
            gt_writer.writerow([cluster_id, cluster_id])

    elapsed = time.time() - t0
    size_mb = output_path.stat().st_size / 1024 / 1024
    stats = {
        "n_unique_base": n_unique,
        "n_dupes": n_dupes,
        "n_junk": n_junk,
        "n_total": n_unique + n_dupes + n_junk,
        "size_mb": round(size_mb, 1),
        "elapsed_seconds": round(elapsed, 1),
        "n_surnames_in_pool": len(pool_last),
    }
    print(f"Done in {elapsed:.1f}s. {size_mb:.1f} MB. Stats: {stats}")
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
