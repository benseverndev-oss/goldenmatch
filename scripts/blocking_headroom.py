"""What does blocking actually cost on DBLP-ACM, and which lever moves it?

#2633 reports a candidate-set ceiling: static blocking on `__title_key__`
produces 33,563 candidate pairs for 2,224 true ones. This measures that claim,
the shipped alternatives, and the two levers the controller could pull -- so the
next attempt starts from numbers rather than from the issue's prose.

TWO TRAPS, BOTH PAID FOR ONCE ALREADY.

**The config cache.** `GOLDENMATCH_AUTOCONFIG_MEMORY` defaults to ON, and a
bare script is the one context where it is live -- `tests/conftest.py` disables
it for the suite and every `bench-*` workflow sets it to "0". A cached run
replays a config a previous run committed, which is valid, plausible, and not
what the engine would derive today. Measured cost of missing this: three wrong
comments on #2633, one of them a headline recall figure that was really a
reading of `~/.goldenmatch/autoconfig_memory.db`. This module pins it OFF at
import, before any goldenmatch import can read it.

**Reimplementing the key.** `__title_key__` is a derived column the pipeline
adds via `extract_biblio_features`; the raw CSV has no such column. Rolling
your own version of it (and counting cross-source pairs only) gives
17,588 / 0.9784 / 0.124 -- two variables wrong at once, and a table that looks
plausible. This calls the shipped extractor and counts within-source pairs, and
cross-checks the result against the real blocker.

MEASURED 2026-08-25 (DBLP-ACM, 4,910 rows, 2,224 ground-truth pairs):

    key                          candidates   recall   ceiling
    __title_key__ (ships)            33,563   0.9717    0.0644
    __title_key__ + year              5,749   0.9717    0.3759

    strategy (same key)          candidates   recall   ceiling
    static (ships)                   33,563   0.9717    0.0644
    adaptive                         33,563   0.9717    0.0644

Two findings worth carrying forward.

`year` is free selectivity: 5.8x fewer candidates at IDENTICAL recall, because
every ground-truth pair agrees on it.

And `adaptive` is not the lever. The controller has a rule that promotes
`static -> adaptive` on a heavy tail (`rule_blocking_adaptive_on_p99_outlier`),
and widening its gate to fire here -- which is the obvious small fix, since the
blocking profile IS red -- would ship a no-op. Adaptive sub-blocks only blocks
above the size cap, and this frame's largest is 104 against 1,201 blocks
averaging 4.1.

WHY THE CONTROLLER DOES NOTHING (measured separately, recorded here):
`blocking_skewed` fires on `largest_block_pair_share` = C(104,2)/33,563 = 0.16,
a SHARE. Both rules registered against it gate on SIZE -- p99 >= 1000, and
p99 > 10 x avg = 40.9 -- against a p99 of 31. The diagnosis and every remedy
are on different axes, so the RED is unanswerable on any frame shaped like this.

    python scripts/blocking_headroom.py
    python scripts/blocking_headroom.py --datasets-dir <dir> --json out.json
"""

from __future__ import annotations

import os

# Before any goldenmatch import: the flag is read at import time.
os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")

import argparse  # noqa: E402
import itertools  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from collections import defaultdict  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = (
    ROOT / "packages" / "python" / "goldenmatch" / "tests" / "benchmarks" / "datasets" / "DBLP-ACM"
)


def _read(path: Path):
    import polars as pl

    return pl.read_csv(
        path,
        infer_schema_length=0,
        encoding="utf8-lossy",
        truncate_ragged_lines=True,
        ignore_errors=True,
    )


def load(datasets_dir: Path):
    """The concatenated frame the benchmark dedupes, plus resolved truth pairs."""
    import polars as pl

    a = _read(datasets_dir / "DBLP2.csv")
    b = _read(datasets_dir / "ACM.csv")
    gt = _read(datasets_dir / "DBLP-ACM_perfectMapping.csv")
    common = [c for c in a.columns if c in b.columns]
    df = pl.concat([a.select(common), b.select(common)], how="vertical_relaxed")

    pos = {v: i for i, v in enumerate(df["id"].to_list())}
    gc = gt.columns
    truth = {
        (min(pos[x], pos[y]), max(pos[x], pos[y]))
        for x, y in zip(gt[gc[0]].to_list(), gt[gc[1]].to_list())
        if x in pos and y in pos
    }
    return df, pos, truth


def _pairs_from_groups(groups) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for members in groups:
        m = sorted(members)
        if len(m) < 2:
            continue
        out.update(itertools.combinations(m, 2))
    return out


def _score(label: str, cands: set, truth: set) -> dict:
    hit = len(cands & truth)
    return {
        "key": label,
        "candidates": len(cands),
        "recall": round(hit / max(len(truth), 1), 4),
        "ceiling": round(hit / max(len(cands), 1), 4),
    }


def measure_keys(df, truth) -> list[dict]:
    """Candidate/recall for the shipped key and the `+ year` compound."""
    from goldenmatch.core.domain import extract_biblio_features

    tkeys = [extract_biblio_features(t or "").get("title_key") for t in df["title"].to_list()]
    years = [str(v) for v in df["year"].to_list()] if "year" in df.columns else None

    rows = []
    for label, keyfn in [
        ("__title_key__ (ships)", lambda i: tkeys[i]),
        (
            "__title_key__ + year",
            (lambda i: (tkeys[i], years[i]) if tkeys[i] else None) if years else None,
        ),
    ]:
        if keyfn is None:
            continue
        buckets = defaultdict(list)
        for i in range(df.height):
            k = keyfn(i)
            if k is not None:
                buckets[k].append(i)
        rows.append(_score(label, _pairs_from_groups(buckets.values()), truth))
    return rows


def measure_strategies(df, pos, truth) -> list[dict]:
    """The same key through the REAL blocker, static vs adaptive.

    Goes through `build_blocks` rather than re-bucketing, so the numbers are the
    engine's own and cross-check the key measurement above.
    """
    import polars as pl
    from goldenmatch.core.autoconfig import auto_configure_df
    from goldenmatch.core.blocker import build_blocks
    from goldenmatch.core.domain import extract_biblio_features

    base = auto_configure_df(df, allow_red_config=True)
    enriched = df.with_columns(
        pl.Series(
            "__title_key__",
            [extract_biblio_features(t or "").get("title_key") for t in df["title"].to_list()],
        )
    )

    rows = []
    for label, strategy in [("static (ships)", None), ("adaptive", "adaptive")]:
        cfg = base.model_copy(deep=True)
        if strategy:
            cfg.blocking.strategy = strategy
        try:
            results = build_blocks(enriched.lazy(), cfg.blocking)
        except Exception as exc:  # noqa: BLE001 - one arm must not lose the table
            rows.append({"key": label, "error": f"{type(exc).__name__}: {exc}"[:120]})
            continue
        groups = []
        for br in results:
            sub = br.df
            sub = sub.collect() if hasattr(sub, "collect") else sub
            sub = sub.native if hasattr(sub, "native") else sub
            groups.append([pos[v] for v in sub["id"].to_list() if v in pos])
        r = _score(label, _pairs_from_groups(groups), truth)
        r["blocks"] = len(results)
        rows.append(r)
    return rows


def run(datasets_dir: Path) -> dict:
    df, pos, truth = load(datasets_dir)
    return {
        "rows": df.height,
        "gt_pairs": len(truth),
        "keys": measure_keys(df, truth),
        "strategies": measure_strategies(df, pos, truth),
    }


def report(result: dict) -> None:
    print("=" * 72)
    print(f"DBLP-ACM blocking headroom  rows={result['rows']} gt_pairs={result['gt_pairs']}")
    print("  config cache OFF (GOLDENMATCH_AUTOCONFIG_MEMORY=0)")
    print("=" * 72)
    for section in ("keys", "strategies"):
        print(f"\n{section}:")
        for r in result[section]:
            if "error" in r:
                print(f"  {r['key']:28s} ERROR {r['error']}")
                continue
            print(
                f"  {r['key']:28s} candidates={r['candidates']:7d} "
                f"recall={r['recall']:.4f}  ceiling={r['ceiling']:.4f}"
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--datasets-dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()

    if not (args.datasets_dir / "DBLP2.csv").exists():
        print(
            f"DBLP-ACM not present at {args.datasets_dir}. "
            "It is fetched in CI by scripts/suggest_quality/fetch_datasets.py (#2660).",
            file=sys.stderr,
        )
        return 2

    result = run(args.datasets_dir)
    report(result)
    if args.json:
        args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
