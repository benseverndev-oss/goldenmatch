"""Repro for issue #715: auto-config commits a RED fuzzy-only config with no
blocking on healthcare-provider-shape data, because high-cardinality identifier
columns are excluded from BOTH candidate paths (the "pincer").

This script uses only synthetic data (no real records) and exercises the real
auto-config internals (`profile_columns` -> `build_matchkeys` -> `build_blocking`)
directly. It does NOT run the full 16-minute dedupe; the pincer is fully
determined by these three calls, and Guard 1 trips at any df.height > 10_000,
so ~20K synthetic rows reproduce the exact mechanism in a couple of seconds.

Run:
    python scripts/repro_issue_715.py [N_ROWS]

Expected output: email hits the O(N^2) exact-matchkey guard, npi/phone are
classified as `identifier` (skipped from matchkeys outright), and all three are
rejected from blocking by the #408 cardinality gate -> matchkeys are fuzzy-only
on name, blocking is empty. That is the unusable config reported in #715.
"""
from __future__ import annotations

import random
import sys

import polars as pl

from goldenmatch.core.autoconfig import (
    build_blocking,
    build_matchkeys,
    profile_columns,
)

SOURCES = [f"src_{i:02d}" for i in range(15)]  # 15 categorical sources
FIRST_NAMES = [
    "james", "mary", "john", "patricia", "robert", "jennifer", "michael",
    "linda", "william", "elizabeth", "david", "barbara", "richard", "susan",
    "joseph", "jessica", "thomas", "sarah", "charles", "karen",
]
# A broad surname pool so blocking on last_name[:4] doesn't degenerate (and so
# names stay moderate-cardinality, not near-unique). See the synthetic-surname
# fixture lesson in CLAUDE.md.
LAST_NAMES = [
    "smith", "johnson", "williams", "brown", "jones", "garcia", "miller",
    "davis", "rodriguez", "martinez", "hernandez", "lopez", "gonzalez",
    "wilson", "anderson", "thomas", "taylor", "moore", "jackson", "martin",
    "lee", "perez", "thompson", "white", "harris", "sanchez", "clark",
    "ramirez", "lewis", "robinson", "walker", "young", "allen", "king",
    "wright", "scott", "torres", "nguyen", "hill", "flores", "green",
    "adams", "nelson", "baker", "hall", "rivera", "campbell", "mitchell",
    "carter", "roberts",
]


def make_healthcare_df(n: int, seed: int = 715) -> pl.DataFrame:
    """Synthetic healthcare-provider shape, mirroring the #715 description:

    - source: categorical, 15 values
    - npi: 10-digit numeric strings, ~60% non-null, high cardinality
    - email: ~70% non-null, high cardinality
    - phone_number: ~50% non-null, high cardinality
    - first_name / last_name: mostly non-null, moderate cardinality
    - zip5: mostly non-null, moderate cardinality (~5k distinct)
    - matching_id: stable per-record id (not used for config)
    """
    rng = random.Random(seed)

    def maybe(value: str, present_rate: float) -> str | None:
        return value if rng.random() < present_rate else None

    rows = []
    for i in range(n):
        fn = rng.choice(FIRST_NAMES)
        ln = rng.choice(LAST_NAMES)
        # High-cardinality identifiers: unique-ish per record among non-nulls.
        npi = f"{rng.randint(1_000_000_000, 1_999_999_999)}"
        email = f"{fn}.{ln}{rng.randint(0, 9_999_999)}@example.org"
        phone = f"{rng.randint(2_000_000_000, 9_999_999_999)}"
        zip5 = f"{rng.randint(10_000, 14_999)}"  # ~5k distinct
        rows.append(
            {
                "source": rng.choice(SOURCES),
                "npi": maybe(npi, 0.60),
                "email": maybe(email, 0.70),
                "phone_number": maybe(phone, 0.50),
                "first_name": maybe(fn, 0.98),
                "last_name": maybe(ln, 0.98),
                "zip5": maybe(zip5, 0.95),
                "matching_id": f"rec_{i}",
            }
        )
    return pl.DataFrame(rows)


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20_000
    df = make_healthcare_df(n)
    print(f"=== Synthetic healthcare df: {df.height:,} rows x {df.width} cols ===")
    print(df.head(5))
    print()

    profiles = profile_columns(df)
    print("=== Column profiles (classification + cardinality) ===")
    for p in profiles:
        print(
            f"  {p.name:<14} col_type={p.col_type:<11} "
            f"card_ratio={p.cardinality_ratio:.4f} null_rate={p.null_rate:.3f}"
        )
    print()

    matchkeys = build_matchkeys(profiles, df=df)
    print("=== build_matchkeys output ===")
    if not matchkeys:
        print("  (no matchkeys at all)")
    for mk in matchkeys:
        fields = [f.field for f in mk.fields]
        thr = getattr(mk, "threshold", None)
        print(f"  {mk.type:<12} t={thr} fields={fields}")
    print()

    has_fuzzy = any(mk.type in ("weighted", "probabilistic") for mk in matchkeys)
    blocking = (
        build_blocking(profiles, df, n_rows_full=df.height) if has_fuzzy else None
    )
    print("=== build_blocking output ===")
    if blocking is None:
        print("  (build_blocking not called — no fuzzy/weighted matchkey)")
    else:
        keys = [k.fields for k in (blocking.keys or [])]
        print(f"  strategy={blocking.strategy} keys={keys}")
    print()

    # ── Verdict ──
    exact_mks = [mk for mk in matchkeys if mk.type == "exact"]
    has_blocking = bool(blocking and blocking.keys)
    identifier_cols = [p.name for p in profiles if p.col_type == "identifier"]
    exact_eligible = [
        p.name for p in profiles
        if p.col_type in ("email", "phone", "zip", "geo")
    ]
    print("=== PINCER VERDICT ===")
    print(f"  identifier-typed cols (skipped from matchkeys): {identifier_cols}")
    print(f"  exact-eligible cols (email/phone/zip/geo):       {exact_eligible}")
    print(f"  exact matchkeys produced:                        {len(exact_mks)}")
    print(f"  blocking keys produced:                          {has_blocking}")
    pincer = (len(exact_mks) == 0) and (not has_blocking)
    print()
    if pincer:
        print("  >>> PINCER CONFIRMED: zero exact matchkeys AND zero blocking keys.")
        print("  >>> High-cardinality identifiers fell out of BOTH paths.")
    else:
        print("  >>> Pincer NOT reproduced at this shape/scale.")


if __name__ == "__main__":
    main()
