"""Differential harness: learned blocking vs its lowered multi_pass equivalent (#1839).

WHY THIS EXISTS
---------------
Zero-config runs >= 50K rows set ``strategy="learned"``, which
``_use_bucket_scorer`` refuses -- so the default path at scale forfeits the
bucket scorer and pays the legacy per-block path. Lowering learned rules into
``multi_pass`` + ``field_transforms`` closes that gap (see
``learned_blocking.lower_rules_to_blocking_config``).

The transforms lower exactly, and on CLEAN data the two paths generate identical
pairs. Divergence is confined to two edge cases (measured by this harness -- an
earlier hand-written version of this table got the NULL row wrong, which is the
whole argument for having the harness):

    | case                    | learned               | static/multi_pass  | same? |
    |-------------------------|-----------------------|--------------------|-------|
    | all-empty key, depth 1  | dropped ("" is falsy) | kept               | NO    |
    | all-empty key, depth 2  | KEPT ("||" is truthy) | kept               | yes   |
    | NULL                    | -> "" -> falsy -> dropped | filtered out   | yes   |
    | "nan"/"null"/"none"     | kept as literal       | filtered sentinels | NO    |

Two things worth naming:

* The depth-1 vs depth-2 split is a string-truthiness accident, not a rule --
  and ``learned_predicate_depth`` DEFAULTS to 2, so the default path keeps the
  zero-information block that depth 1 drops.
* NULLs agree by COINCIDENCE, not by a shared rule: static filters is_not_null,
  while learned maps None -> "" and drops it for being falsy. Same outcome at
  depth 1; at depth 2 learned's "||" would be truthy again.

These cannot be resolved by argument -- each is a recall/cost tradeoff. PR #390
recorded the only hard evidence: dropping empty keys "lost 3 records on the
cross-file dedupe regression suite". This harness produces that same class of
evidence on demand: it reports not just THAT the block sets differ, but WHICH
records are at stake, so the divergences adjudicate themselves.

USAGE
-----
    # built-in synthetic probes (each isolates one divergence)
    python scripts/learned_lowering_diff.py

    # against a real corpus
    python scripts/learned_lowering_diff.py --csv path/to/data.csv \\
        --rule last:soundex+city:first_3 --rule first:first_token

Exit code is 0 always: this is an evidence tool, not a gate. Nothing here is
wired into the pipeline and no default changes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG = REPO_ROOT / "packages" / "python" / "goldenmatch"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import polars as pl  # noqa: E402
from goldenmatch.core.blocker import build_blocks  # noqa: E402
from goldenmatch.core.learned_blocking import (  # noqa: E402
    BlockingPredicate,
    BlockingRule,
    LoweringUnsupportedError,
    apply_learned_blocks,
    lower_rules_to_blocking_config,
)

BIG = 10**9  # take max_block_size out of the picture; we compare semantics


def _member_sets(blocks) -> set[frozenset[int]]:
    """Block member sets, keyed by __row_id__. Singletons are dropped -- they
    generate no pairs, so they cannot account for a recall difference."""
    out = set()
    for b in blocks:
        ids = frozenset(b.materialize().native["__row_id__"].to_list())
        if len(ids) >= 2:
            out.add(ids)
    return out


def _pairs(member_sets: set[frozenset[int]]) -> set[tuple[int, int]]:
    """Candidate pairs implied by a block set. This -- not block identity -- is
    what actually decides recall: two paths can disagree on block SHAPE while
    generating the same pairs."""
    out: set[tuple[int, int]] = set()
    for s in member_sets:
        ids = sorted(s)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                out.add((a, b))
    return out


def diff(df: pl.DataFrame, rules: list[BlockingRule], label: str) -> dict:
    """Compare learned blocks against lowered multi_pass blocks on one frame."""
    print(f"\n=== {label} ===")
    print(f"    rows={df.height}  rules={[r.key() for r in rules]}")

    learned_blocks = apply_learned_blocks(df.lazy(), rules, max_block_size=BIG)
    learned_sets = _member_sets(learned_blocks)

    try:
        cfg = lower_rules_to_blocking_config(rules, max_block_size=BIG, skip_oversized=False)
    except LoweringUnsupportedError as e:
        print(f"    NOT LOWERABLE: {e}")
        return {"label": label, "lowerable": False, "reason": str(e)}

    lowered_sets = _member_sets(build_blocks(df.lazy(), cfg))

    lp, bp = _pairs(learned_sets), _pairs(lowered_sets)
    only_learned, only_lowered = lp - bp, bp - lp

    print(f"    blocks:  learned={len(learned_sets):5d}  lowered={len(lowered_sets):5d}")
    print(f"    pairs:   learned={len(lp):5d}  lowered={len(bp):5d}")

    if not only_learned and not only_lowered:
        print("    PAIRS IDENTICAL")
    else:
        print(f"    DIVERGE: only-learned={len(only_learned)}  only-lowered={len(only_lowered)}")
        for tag, pairs in (("only-learned", only_learned), ("only-lowered", only_lowered)):
            for a, b in sorted(pairs)[:4]:
                ra = df.filter(pl.col("__row_id__") == a).to_dicts()[0]
                rb = df.filter(pl.col("__row_id__") == b).to_dicts()[0]
                ra.pop("__row_id__", None)
                rb.pop("__row_id__", None)
                print(f"      {tag}: {a} {ra}")
                print(f"      {' ' * len(tag)}  {b} {rb}")
            if len(pairs) > 4:
                print(f"      {tag}: ... and {len(pairs) - 4} more")

    return {
        "label": label,
        "lowerable": True,
        "identical": not only_learned and not only_lowered,
        "only_learned": len(only_learned),
        "only_lowered": len(only_lowered),
    }


def _p(field: str, transform: str) -> BlockingPredicate:
    return BlockingPredicate(field=field, transform=transform)


def _probes() -> list[tuple[str, pl.DataFrame, list[BlockingRule]]]:
    """Synthetic frames, each isolating ONE divergence from the table above."""
    clean = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3, 4, 5],
        "last": ["Smith", "Smyth", "Jones", "Jones", "Brown", "Brown"],
        "city": ["Boston", "Boston", "Newark", "New York", "Chicago", "Chicago"],
    })
    empties = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3, 4, 5],
        "last": ["", "", "", "Smith", "Smith", "Jones"],
        "city": ["", "", "", "Boston", "Boston", "Newark"],
    })
    nulls = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3, 4, 5],
        "last": [None, None, None, "Smith", "Smith", "Jones"],
        "city": [None, None, None, "Boston", "Boston", "Newark"],
    })
    sentinels = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3],
        "last": ["null", "null", "Smith", "Smith"],
        "city": ["nan", "nan", "Boston", "Boston"],
    })
    return [
        ("clean / depth-1", clean, [BlockingRule(predicates=[_p("last", "soundex")])]),
        ("clean / depth-2", clean, [BlockingRule(predicates=[_p("last", "soundex"), _p("city", "first_3")])]),
        ("clean / multi-rule union", clean, [
            BlockingRule(predicates=[_p("last", "soundex")]),
            BlockingRule(predicates=[_p("city", "first_3")]),
        ]),
        ("EMPTY strings / depth-1  (learned drops, static keeps)", empties,
         [BlockingRule(predicates=[_p("last", "exact")])]),
        ("EMPTY strings / depth-2  (truthiness accident: both keep)", empties,
         [BlockingRule(predicates=[_p("last", "exact"), _p("city", "exact")])]),
        ("NULLs / depth-1  (agree, but by coincidence)", nulls,
         [BlockingRule(predicates=[_p("last", "exact")])]),
        ("SENTINELS 'null'/'nan'  (static filters, learned keeps literal)", sentinels,
         [BlockingRule(predicates=[_p("last", "exact")])]),
        ("SAME-FIELD conjunction  (not lowerable by construction)", clean,
         [BlockingRule(predicates=[_p("last", "exact"), _p("last", "soundex")])]),
    ]


def _parse_rule(spec: str) -> BlockingRule:
    preds = []
    for part in spec.split("+"):
        field, _, transform = part.partition(":")
        if not transform:
            raise ValueError(f"bad rule spec {spec!r}; want field:transform[+field:transform]")
        preds.append(_p(field, transform))
    return BlockingRule(predicates=preds)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path, help="corpus to diff against (else: synthetic probes)")
    ap.add_argument("--rule", action="append", default=[],
                    help="field:transform[+field:transform], repeatable")
    args = ap.parse_args()

    if args.csv:
        df = pl.read_csv(args.csv)
        if "__row_id__" not in df.columns:
            df = df.with_row_index("__row_id__")
        if not args.rule:
            print("--csv requires at least one --rule", file=sys.stderr)
            return 2
        results = [diff(df, [_parse_rule(r) for r in args.rule], str(args.csv))]
    else:
        results = [diff(df, rules, label) for label, df, rules in _probes()]

    print("\n" + "=" * 72)
    print("SUMMARY")
    for r in results:
        if not r["lowerable"]:
            verdict = "NOT LOWERABLE"
        elif r["identical"]:
            verdict = "pairs identical"
        else:
            verdict = f"DIVERGE (-{r['only_learned']} / +{r['only_lowered']} pairs)"
        print(f"  {r['label']:58} {verdict}")
    print("\nDivergences are evidence, not failures: each names the records a")
    print("semantics decision would gain or lose. Nothing here changes a default.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
