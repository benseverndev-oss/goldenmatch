#!/usr/bin/env python
"""Streaming person-shaped dedupe fixture generator for the ER head-to-head bench.

Writes a single parquet that BOTH engines (Splink and GoldenMatch) read, plus a
separate ground-truth parquet ({record_id, cluster_id}) for optional F1.

Bounded memory by design: rows are generated and flushed one row-group at a time
via pyarrow.ParquetWriter, so generating 100M rows never materialises 100M rows
in RAM. All string columns are produced by vectorised fancy-indexing into small
precomputed pools (no per-row Python), so generation stays fast at 100M.

Schema (5 fields both engines can match on):
    record_id  int64
    first_name str
    surname    str
    dob        str   (YYYY-MM-DD)
    postcode   str
    city       str

Duplicates carry realistic fuzzy variation: surname/first-name single-char typos,
occasional nulls on weaker fields. Strong identity fields (dob, postcode) mostly
agree so the clusters are genuinely resolvable.

Usage:
    python generate_fixture.py --rows 1000000 --dupe-rate 0.20 \
        --out fixtures/bench_1000000.parquet \
        --ground-truth fixtures/bench_1000000.truth.parquet
"""
from __future__ import annotations

import argparse
import csv
import json
import string
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

# Pool sizes chosen so compound blocking (surname + dob-year) yields small blocks
# at every scale, keeping candidate-pair growth ~linear rather than quadratic.
N_POSTCODE = 200_000
N_CITY = 1_000
DOB_DAYS = 25_000  # ~68 years of distinct birth dates

_ALPHA = np.array(list(string.ascii_lowercase))


def _refdata_dir() -> Path | None:
    """Locate goldenmatch's bundled reference data, if importable."""
    try:
        import goldenmatch  # noqa: PLC0415

        d = Path(goldenmatch.__file__).parent / "refdata" / "data"
        return d if d.exists() else None
    except Exception:
        return None


def _syllable_pool(n: int, rng: np.random.Generator, min_len: int, max_len: int) -> list[str]:
    """Deterministic pronounceable-ish token pool (synthetic fallback only)."""
    cons = list("bcdfghjklmnpqrstvwxyz")
    vows = list("aeiou")
    out: list[str] = []
    for _ in range(n):
        ln = int(rng.integers(min_len, max_len + 1))
        chars = [rng.choice(cons) if i % 2 == 0 else rng.choice(vows) for i in range(ln)]
        out.append("".join(chars).capitalize())
    return out


def _load_real_names(rng: np.random.Generator):
    """Census-weighted surnames + real given names. (surnames, cum_weights, first_names).

    Returns frequency-weighted surnames (Zipfian, like real data -> realistic hot
    blocks) and a pool of real given names. Falls back to synthetic pools if the
    bundled reference data isn't available.
    """
    d = _refdata_dir()
    if d is None:
        sur = _syllable_pool(50_000, rng, 4, 9)
        first = _syllable_pool(5_000, rng, 3, 7)
        cumw = np.cumsum(np.full(len(sur), 1.0 / len(sur)))
        return sur, cumw, first
    # Census surnames: name, rank, count -> frequency-weighted sampling.
    surnames, counts = [], []
    with open(d / "census_surnames_2010_top10k.csv") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            surnames.append(row[0].capitalize())
            counts.append(float(row[2]))
    counts = np.asarray(counts)
    cumw = np.cumsum(counts / counts.sum())
    # Given names from the alias table (canonical + variants = a few thousand real ones).
    first = set()
    try:
        gn = json.load(open(d / "given_name_aliases.json"))
        for canon, variants in (gn.get("aliases") or {}).items():
            first.add(canon.capitalize())
            for v in variants:
                first.add(str(v).capitalize())
    except Exception:
        pass
    if len(first) < 200:
        first.update(_syllable_pool(2_000, rng, 3, 7))
    return surnames, cumw, sorted(first)


def _typo_variant(s: str, rng: np.random.Generator) -> str:
    """Single-char edit (substitution / transposition / drop) — realistic fuzzy noise."""
    if len(s) < 3:
        return s + "e"
    kind = rng.integers(0, 3)
    i = int(rng.integers(1, len(s) - 1))
    if kind == 0:  # substitution
        return s[:i] + str(rng.choice(_ALPHA)) + s[i + 1 :]
    if kind == 1:  # transposition
        return s[:i] + s[i + 1] + s[i] + s[i + 2 :]
    return s[:i] + s[i + 1 :]  # drop


def _build_pools(seed: int):
    rng = np.random.default_rng(seed)
    sur_base, sur_cumw, first_base = _load_real_names(rng)
    # Parallel typo arrays: index i -> one typo'd variant of base i. Built once.
    first_typo = [_typo_variant(s, rng) for s in first_base]
    sur_typo = [_typo_variant(s, rng) for s in sur_base]
    cities = [c + " City" for c in _syllable_pool(N_CITY, rng, 4, 8)]
    base = np.datetime64("1940-01-01")
    dobs = (base + np.arange(DOB_DAYS).astype("timedelta64[D]")).astype("datetime64[D]")
    dob_pool = np.datetime_as_string(dobs)
    postcodes = [f"{int(rng.integers(10,99))}{rng.choice(_ALPHA).upper()}{rng.choice(_ALPHA).upper()} {int(rng.integers(0,9))}{rng.choice(_ALPHA).upper()}{rng.choice(_ALPHA).upper()}" for _ in range(N_POSTCODE)]
    return {
        # Combined [base | typo] arrays so a single fancy-index picks exact-or-typo.
        "first": np.array(first_base + first_typo, dtype=object),
        "surname": np.array(sur_base + sur_typo, dtype=object),
        "surname_cumw": sur_cumw,
        "n_first": len(first_base),
        "n_surname": len(sur_base),
        "city": np.array(cities, dtype=object),
        "dob": np.asarray(dob_pool, dtype=object),
        "postcode": np.array(postcodes, dtype=object),
    }


SCHEMA = pa.schema(
    [
        ("record_id", pa.int64()),
        ("first_name", pa.string()),
        ("surname", pa.string()),
        ("dob", pa.string()),
        ("postcode", pa.string()),
        ("city", pa.string()),
    ]
)
TRUTH_SCHEMA = pa.schema([("record_id", pa.int64()), ("cluster_id", pa.int64())])


def generate(rows: int, dupe_rate: float, out: Path, truth: Path, seed: int, batch: int) -> dict:
    pools = _build_pools(seed)
    n_first = pools["n_first"]
    n_surname = pools["n_surname"]
    sur_cumw = pools["surname_cumw"]
    rng = np.random.default_rng(seed + 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    truth.parent.mkdir(parents=True, exist_ok=True)

    # Cluster-size categorical tuned so the duplicate fraction ~= dupe_rate.
    # sizes {1,2,3}; expected dup fraction = (p2 + 2*p3) / (p1 + 2*p2 + 3*p3).
    p_dup = max(0.0, min(0.9, dupe_rate))
    size_probs = np.array([1 - p_dup, 0.75 * p_dup, 0.25 * p_dup])
    size_probs /= size_probs.sum()

    written = 0
    next_rid = 0
    next_cid = 0
    n_dupes = 0
    t0 = time.perf_counter()

    writer = pq.ParquetWriter(out, SCHEMA, compression="zstd")
    twriter = pq.ParquetWriter(truth, TRUTH_SCHEMA, compression="zstd")
    try:
        while written < rows:
            # Over-draw identities, then trim the expanded rows to the batch budget.
            target = min(batch, rows - written)
            n_ident = int(target / (1 + p_dup)) + 16
            sizes = rng.choice([1, 2, 3], size=n_ident, p=size_probs)
            cum = np.cumsum(sizes)
            keep = np.searchsorted(cum, target, side="right") + 1
            sizes = sizes[:keep]
            total = int(sizes.sum())
            if written + total > rows:  # final batch: clip last cluster
                excess = written + total - rows
                sizes[-1] = max(1, sizes[-1] - excess)
                total = int(sizes.sum())

            cids = np.arange(next_cid, next_cid + len(sizes))
            row_cid = np.repeat(cids, sizes)
            # position within cluster: 0 == canonical, >0 == duplicate variant
            pos = np.arange(total) - np.repeat(cum[: len(sizes)] - sizes, sizes)
            is_dup = pos > 0
            n_dupes += int(is_dup.sum())

            # Canonical attribute indices per identity, broadcast to rows.
            # Surnames are census frequency-weighted (Zipfian -> realistic hot blocks).
            si_ident = np.searchsorted(sur_cumw, rng.random(len(sizes)))
            fi = np.repeat(rng.integers(0, n_first, len(sizes)), sizes)
            si = np.repeat(si_ident, sizes)
            di = np.repeat(rng.integers(0, DOB_DAYS, len(sizes)), sizes)
            pi = np.repeat(rng.integers(0, N_POSTCODE, len(sizes)), sizes)
            ci = np.repeat(rng.integers(0, N_CITY, len(sizes)), sizes)

            # Duplicate variation (vectorised masks). Typo => offset into [base|typo] half.
            r = rng.random(total)
            fi_pick = fi + np.where(is_dup & (r < 0.40), n_first, 0)
            r = rng.random(total)
            si_pick = si + np.where(is_dup & (r < 0.50), n_surname, 0)

            first = pools["first"][fi_pick]
            surname = pools["surname"][si_pick]
            dob = pools["dob"][di].copy()
            postcode = pools["postcode"][pi].copy()
            city = pools["city"][ci].copy()

            # Occasional nulls on weaker / strong fields for duplicate rows.
            city = np.where(is_dup & (rng.random(total) < 0.20), None, city)
            dnull = is_dup & (rng.random(total) < 0.05)
            dob = np.where(dnull, None, dob)
            pnull = is_dup & (rng.random(total) < 0.05)
            postcode = np.where(pnull, None, postcode)

            rids = np.arange(next_rid, next_rid + total)
            writer.write_table(
                pa.table(
                    {
                        "record_id": pa.array(rids, pa.int64()),
                        "first_name": pa.array(first, pa.string()),
                        "surname": pa.array(surname, pa.string()),
                        "dob": pa.array(dob, pa.string()),
                        "postcode": pa.array(postcode, pa.string()),
                        "city": pa.array(city, pa.string()),
                    },
                    schema=SCHEMA,
                )
            )
            twriter.write_table(
                pa.table(
                    {"record_id": pa.array(rids, pa.int64()), "cluster_id": pa.array(row_cid, pa.int64())},
                    schema=TRUTH_SCHEMA,
                )
            )
            written += total
            next_rid += total
            next_cid += len(sizes)
    finally:
        writer.close()
        twriter.close()

    meta = {
        "rows": written,
        "clusters": next_cid,
        "duplicate_rows": n_dupes,
        "duplicate_rate_actual": round(n_dupes / written, 4) if written else 0.0,
        "gen_wall_seconds": round(time.perf_counter() - t0, 2),
        "fixture_path": str(out),
        "fixture_size_mb": round(out.stat().st_size / 1e6, 1),
    }
    return meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, required=True)
    ap.add_argument("--dupe-rate", type=float, default=0.20)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--ground-truth", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--batch", type=int, default=1_000_000)
    args = ap.parse_args()

    meta = generate(args.rows, args.dupe_rate, args.out, args.ground_truth, args.seed, args.batch)
    print(
        f"[generate] {meta['rows']:,} rows / {meta['clusters']:,} clusters / "
        f"dup={meta['duplicate_rate_actual']} / {meta['fixture_size_mb']} MB / "
        f"{meta['gen_wall_seconds']}s -> {meta['fixture_path']}"
    )


if __name__ == "__main__":
    main()
