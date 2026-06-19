"""Generate a synthetic smoke dataset for the Sail-on-GKE proof.

Writes a DIRECTORY of parquet parts (distributed-read friendly) with the
columns the Sail bench expects: ``__row_id__`` (int), ``last_name``,
``last_name_soundex`` (the block key), ``first_name``, ``email``.

Critical invariant (see memory ``feedback_synthetic_surname_fixtures``):
surnames are drawn from a large pool that spreads across many soundex codes,
so no single soundex block blows up into an O(block^2) self-join that hangs.
The script prints the block-size distribution so the build log proves it.

Usage: ``python gen_sail_smoke.py <out_dir> [n_rows] [n_parts]``
"""
from __future__ import annotations

import os
import random
import sys


def soundex(name: str) -> str:
    """Standard American Soundex (letter + 3 digits)."""
    s = "".join(ch for ch in name.upper() if ch.isalpha())
    if not s:
        return "0000"
    codes = {}
    for letters, d in (
        ("BFPV", "1"),
        ("CGJKQSXZ", "2"),
        ("DT", "3"),
        ("L", "4"),
        ("MN", "5"),
        ("R", "6"),
    ):
        for ch in letters:
            codes[ch] = d
    first = s[0]
    prev = codes.get(first, "")
    out = ""
    for ch in s[1:]:
        c = codes.get(ch, "")
        if c and c != prev:
            out += c
        if ch in "AEIOUY":
            prev = ""
        elif ch not in "HW":
            prev = c
        if len(out) >= 3:
            break
    return (first + out + "000")[:4]


def build_surname_pool() -> list[str]:
    """A few hundred thousand distinct synthetic surnames -> thousands of
    distinct soundex codes, so uniform sampling yields small bounded blocks."""
    cons = "bcdfghjklmnprstvwz"
    vows = "aeiou"
    stems = [c + v for c in cons for v in vows]  # 85
    ends = [
        "ner", "son", "man", "ton", "ler", "der", "sen", "ley", "ford",
        "well", "wood", "ham", "by", "ston", "mer", "field", "worth",
        "ridge", "land", "dale",
    ]  # 20
    pool = {(a + b + e).capitalize() for a in stems for b in stems for e in ends}
    return sorted(pool)


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "/data/smoke"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 300_000
    parts = int(sys.argv[3]) if len(sys.argv) > 3 else 8

    import polars as pl

    rng = random.Random(42)
    pool = build_surname_pool()
    firsts = [
        "James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael",
        "Linda", "William", "Elizabeth", "David", "Barbara", "Richard",
        "Susan", "Joseph", "Jessica", "Thomas", "Sarah", "Charles", "Karen",
        "Chris", "Nancy", "Daniel", "Lisa", "Matthew", "Betty", "Anthony",
        "Sandra", "Mark", "Ashley",
    ]
    domains = ["example.com", "mail.com", "test.org", "corp.net", "inbox.io"]

    surnames = rng.choices(pool, k=n)
    firstnames: list[str | None] = rng.choices(firsts, k=n)
    emails: list[str | None] = []
    for i in range(n):
        if rng.random() < 0.30:  # blank ~30% of emails -> survivorship has work
            emails.append(None)
        else:
            fn = firstnames[i].lower()  # type: ignore[union-attr]
            emails.append(
                f"{fn}.{surnames[i].lower()}{rng.randrange(1000)}@{rng.choice(domains)}"
            )
    for i in range(n):
        if rng.random() < 0.10:  # blank ~10% of first names
            firstnames[i] = None

    df = pl.DataFrame(
        {
            "__row_id__": list(range(n)),
            "last_name": surnames,
            "first_name": firstnames,
            "email": emails,
        }
    ).with_columns(
        pl.col("last_name")
        .map_elements(soundex, return_dtype=pl.String)
        .alias("last_name_soundex")
    )

    stats = df.group_by("last_name_soundex").len().sort("len", descending=True)
    print(
        f"[gen] rows={n} parts={parts} distinct_soundex={stats.height} "
        f"max_block={stats['len'][0]} "
        f"top5={stats['len'][:5].to_list()}",
        flush=True,
    )

    os.makedirs(out, exist_ok=True)
    rows_per = (n + parts - 1) // parts
    written = 0
    for k in range(parts):
        sub = df.slice(k * rows_per, rows_per)
        if sub.height:
            sub.write_parquet(os.path.join(out, f"part-{k:02d}.parquet"))
            written += 1
    print(f"[gen] wrote {written} parquet parts to {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
