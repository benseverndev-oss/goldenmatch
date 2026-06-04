"""Repro for issue #715: auto-config commits a RED fuzzy-only config with no
blocking on healthcare-provider-shape data, because high-cardinality identifier
columns are excluded from BOTH candidate paths (the "pincer").

This script uses only synthetic data (no real records) and exercises the real
auto-config internals (`profile_columns` -> `build_matchkeys` -> `build_blocking`)
directly. It does NOT run the full 16-minute dedupe; the pincer is fully
determined by these three calls, and Guard 1 trips at any df.height > 10_000,
so ~20K synthetic rows reproduce the exact mechanism in a couple of seconds.

Run:
    # default (pincer) mode -- positional N_ROWS (back-compat with the workflow)
    python scripts/repro_issue_715.py [N_ROWS]

    # at-scale blocking-bound assertion (#715 reopened)
    python scripts/repro_issue_715.py --mode blocking-scale --rows 500000

Expected output (pincer mode): email hits the O(N^2) exact-matchkey guard,
npi/phone are classified as `identifier` (skipped from matchkeys outright), and
all three are rejected from blocking by the #408 cardinality gate -> matchkeys
are fuzzy-only on name, blocking is empty. That is the unusable config reported
in #715.

The blocking-scale mode is the #715-REOPENED guard: a sample-sized unit test
cannot catch the at-scale blocking blow-up (the gap that let #715 reopen). It
builds a sparse-zip, realistic-surname df at full N, runs build_blocking on the
FULL df (the v0 committed-config path), and asserts every emitted key/pass has a
max block size <= the autoconfig cap. Prints `BLOCKING BOUNDED`,
`BLOCKING REFUSED (degenerate)` (acceptable -- the fix refused a bomb), or
`BLOCKING UNBOUNDED: <fields>=<size> > <cap>` (a real fix gap).
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


def make_healthcare_df(
    n: int,
    seed: int = 715,
    zip_present: float = 0.95,
    rich_surnames: int = 0,
) -> pl.DataFrame:
    """Synthetic healthcare-provider shape, mirroring the #715 description:

    - source: categorical, 15 values
    - npi: 10-digit numeric strings, ~60% non-null, high cardinality
    - email: ~70% non-null, high cardinality
    - phone_number: ~50% non-null, high cardinality
    - first_name / last_name: mostly non-null, moderate cardinality
    - zip5: mostly non-null, moderate cardinality (~5k distinct)
    - matching_id: stable per-record id (not used for config)

    When ``rich_surnames > 0``, ``last_name`` is drawn from a synthetic pool
    of that many distinct surnames (``f"sur{k}"``) instead of the 50-name
    LAST_NAMES list. This gives realistic surname cardinality so a bounded
    last_name-based compound exists at scale (the realistic scenario in the
    #715-reopened at-scale blocking assertion).
    """
    rng = random.Random(seed)

    def maybe(value: str, present_rate: float) -> str | None:
        return value if rng.random() < present_rate else None

    rows = []
    for i in range(n):
        fn = rng.choice(FIRST_NAMES)
        if rich_surnames > 0:
            ln = f"sur{rng.randint(0, rich_surnames - 1)}"
        else:
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
                "zip5": maybe(zip5, zip_present),
                "matching_id": f"rec_{i}",
            }
        )
    return pl.DataFrame(rows)


def _run_pincer_mode(n: int) -> None:
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
        print("  (build_blocking not called - no fuzzy/weighted matchkey)")
    else:
        keys = [k.fields for k in (blocking.keys or [])]
        print(f"  strategy={blocking.strategy} keys={keys}")
    print()

    # -- Verdict --
    def _exact_fields(mks):
        return {f.field for mk in mks if mk.type == "exact" for f in mk.fields}

    exact_mks = [mk for mk in matchkeys if mk.type == "exact"]
    exact_fields = _exact_fields(matchkeys)
    has_blocking = bool(blocking and blocking.keys)
    identifier_eligible = [
        p.name for p in profiles
        if p.col_type in ("identifier", "email", "phone")
        and 0.5 <= p.cardinality_ratio < 1.0
    ]
    print("=== PINCER VERDICT ===")
    print(f"  identifier-eligible cols (identifier/email/phone, card 0.5-1.0): {identifier_eligible}")
    print(f"  exact matchkeys produced: {len(exact_mks)}  fields={sorted(exact_fields)}")
    print(f"  blocking keys present:    {has_blocking}")
    print()
    if identifier_eligible and len(exact_mks) == 0:
        print("  >>> PINCER PRESENT: identifier-eligible columns produced zero exact matchkeys.")
        print("  >>> High-cardinality identifiers fell out of BOTH candidate paths.")
    elif identifier_eligible and len(exact_mks) > 0:
        print("  >>> PINCER RESOLVED: identifier-eligible columns now back exact matchkeys.")
        print(f"  >>> Exact matchkey fields: {sorted(exact_fields)}")
    else:
        print("  >>> inconclusive: no identifier-eligible columns found at this shape/scale.")


def _max_block_full(df: pl.DataFrame, key) -> int:
    """True max block size for one emitted blocking key/pass on the FULL df.

    Mirrors exactly what the static blocker does: build the production block-key
    expression (so transforms AND the ``concat_str`` null-propagation for
    compound keys are honored), filter the null / ``nan`` / ``null`` / ``none``
    sentinel keys the pipeline discards, then take the max surviving group size.
    Counting the filtered null bucket would over-report (e.g. a sparse-zip null
    collision the pipeline never scores). Returns -1 if the key references
    columns absent from df or the expr build fails.
    """
    from goldenmatch.core.blocker import _build_block_key_expr

    if not all(f in df.columns for f in key.fields):
        return -1
    try:
        expr = _build_block_key_expr(key)
        sizes = (
            df.with_columns(expr)
            .filter(
                pl.col("__block_key__").is_not_null()
                & ~pl.col("__block_key__")
                .str.strip_chars()
                .str.to_lowercase()
                .is_in(["nan", "null", "none"])
            )
            .group_by("__block_key__")
            .len()
            .get_column("len")
        )
    except Exception:
        return -1
    return int(sizes.max() or 0)


def _run_blocking_scale_mode(n: int) -> None:
    """At-scale blocking-bound assertion for #715 (reopened).

    Builds a sparse-zip, realistic-surname healthcare-shape df at full N,
    runs the real build_blocking on the FULL df (the v0 committed-config path),
    and asserts every emitted blocking key/pass has a max block size <= the
    autoconfig cap. A sample-sized unit test cannot catch the at-scale
    blocking blow-up -- the gap that let #715 reopen.
    """
    df = make_healthcare_df(n, zip_present=0.5, rich_surnames=5000)
    df = df.drop("matching_id")
    print(
        f"=== blocking-scale: {df.height:,} rows x {df.width} cols "
        f"(sparse zip ~0.5, rich surnames ~5000) ==="
    )
    print()

    profiles = profile_columns(df)
    print("=== Column profiles (classification + cardinality) ===")
    for p in profiles:
        print(
            f"  {p.name:<14} col_type={p.col_type:<11} "
            f"card_ratio={p.cardinality_ratio:.4f} null_rate={p.null_rate:.3f}"
        )
    print()

    blk = build_blocking(profiles, df, n_rows_full=df.height)

    # Same cap build_blocking gates on internally.
    cap = max(1000, min(10_000, n // 200))

    keys = list(blk.keys or [])
    passes = list(blk.passes or [])
    print("=== build_blocking output ===")
    print(f"  strategy={blk.strategy}")
    print(f"  keys=  {[k.fields for k in keys]}")
    print(f"  passes={[k.fields for k in passes]}")
    print(f"  max_safe_block (cap) = {cap:,}")
    print()

    emitted = keys + passes
    print("=== per key/pass max block size vs cap ===")
    oversized: list[str] = []
    if not emitted:
        print("  (no keys or passes emitted)")
    for key in emitted:
        size = _max_block_full(df, key)
        flag = "OK" if 0 <= size <= cap else "OVERSIZED"
        print(f"  {'+'.join(key.fields):<28} max_block={size:<10,} cap={cap:,}  [{flag}]")
        if size > cap:
            oversized.append(f"{'+'.join(key.fields)}={size}")
    print()

    print("=== BLOCKING-SCALE VERDICT ===")
    if oversized:
        worst = oversized[0]
        fields, _, size = worst.partition("=")
        print(f"  BLOCKING UNBOUNDED: {fields}={size} > {cap}")
    elif not emitted:
        # Empty/degenerate config: the fix refused rather than shipping a
        # candidate-pair bomb. Acceptable.
        print("  BLOCKING REFUSED (degenerate)")
    else:
        print("  BLOCKING BOUNDED")


def main() -> None:
    args = sys.argv[1:]
    mode = "pincer"
    rows: int | None = None

    # Parse a tiny CLI by hand so the existing positional `rows` invocation
    # (repro-issue-715.yml: `repro_issue_715.py "$ROWS"`) keeps working.
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--mode":
            mode = args[i + 1]
            i += 2
        elif a == "--rows":
            rows = int(args[i + 1])
            i += 2
        elif not a.startswith("--"):
            rows = int(a)  # positional rows (default-mode back-compat)
            i += 1
        else:
            i += 1

    if mode == "blocking-scale":
        _run_blocking_scale_mode(rows if rows is not None else 500_000)
    else:
        _run_pincer_mode(rows if rows is not None else 20_000)


if __name__ == "__main__":
    main()
