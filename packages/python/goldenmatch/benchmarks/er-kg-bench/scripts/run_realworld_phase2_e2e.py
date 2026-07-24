#!/usr/bin/env python3
"""Phase 2 end-to-end: the COMPOUNDED ER + aggregation win on the committed Wikidata fixture.

On a CO-OCCURRENCE corpus (each member recurs under several real aliases across docs), the
"how many members?" query is answered three ways and scored by gold-set-size bucket:
  - goldengraph: builds the store from the co-occurrence docs with REAL goldenmatch dedup
    (resolve_mode='real'), traverses, counts distinct store nodes.
  - oracle floor: window docs resolved surface->qid (perfect ER) -> exact count (the ceiling).
  - ER-blind floor: window docs clustered by naive normalization -> OVER-counts un-mergeable
    aliases (a naive RAG that cannot dedup).

goldenmatch's real dedup lands between the two floors -- above the ER-blind floor (the
compounded win: dedup value showing up inside the aggregation capability) and below/at the
perfect-ER ceiling. This is the result Phase 1.5 could not show, because its floor was
ER-blind BY CONSTRUCTION (oracle surface->qid + one doc per member).

Needs the goldengraph-native wheel + goldenmatch (source). Key-free, offline.

The full v1 fixture is large; like Phase 0/1.5 this driver scores a BUCKET-BALANCED SUBSET
(N anchors per size bucket, keeping only the entities those facts reference) -- representative
and minutes-fast. `--anchors-per-bucket 0` scores the full fixture.

Usage:
    python scripts/run_realworld_phase2_e2e.py \
        --fixture erkgbench/qa_e2e/fixtures/wikidata_companies_v1.json \
        --anchors-per-bucket 8 --passage-k 10 --mentions-per-member 3 \
        --out-md results/RESULTS_REALWORLD_COOCCURRENCE.md
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Runnable as a plain script: put the bench root (parent of scripts/) on sys.path so
# `erkgbench` + the sibling Phase 1.5 driver import regardless of CWD.
_BENCH_ROOT = Path(__file__).resolve().parents[1]
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from erkgbench.qa_e2e.aggregation import _ordered_buckets
from erkgbench.qa_e2e.realworld import _FIXTURE_DIR, run_realworld_cooccurrence
from run_realworld_phase15_e2e import _bucket_balanced_subset  # reuse the deterministic subset


def _fmt_count_table(res: dict) -> list[str]:
    gg = res["gg_count_acc"]
    orc = res["oracle_floor_count_acc"]
    erb = res["er_blind_count_acc"]
    lines = [
        "| size bucket | GG count-acc (real dedup) | oracle-floor (perfect ER) "
        "| ER-blind-floor (no ER) | GG - ER-blind (the win) |",
        "|---|---|---|---|---|",
    ]
    for b in _ordered_buckets(gg):
        g, o, e = gg.get(b, 0.0), orc.get(b, 0.0), erb.get(b, 0.0)
        lines.append(f"| {b} | {g:.3f} | {o:.3f} | {e:.3f} | {g - e:+.3f} |")
    return lines


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fixture", default=str(_FIXTURE_DIR / "wikidata_companies_v1.json"))
    p.add_argument("--anchors-per-bucket", type=int, default=8)
    p.add_argument("--passage-k", type=int, default=10)
    p.add_argument("--mentions-per-member", type=int, default=3)
    p.add_argument("--out-md", default="results/RESULTS_REALWORLD_COOCCURRENCE.md")
    args = p.parse_args(argv)

    subset = _bucket_balanced_subset(Path(args.fixture), args.anchors_per_bucket)
    n_facts, n_ents = len(subset["facts"]), len(subset["entities"])
    tmp = Path(args.out_md).parent / "_subset_cooccurrence.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(subset), encoding="utf-8")

    t0 = time.perf_counter()
    res = run_realworld_cooccurrence(
        tmp, mentions_per_member=args.mentions_per_member, passage_k=args.passage_k)
    wall = time.perf_counter() - t0
    tmp.unlink(missing_ok=True)

    lines = [
        "# Real-world COMPOUNDED ER + aggregation -- goldengraph vs ER-blind RAG (Phase 2)",
        "",
        "Co-occurrence corpus on the committed Wikidata company fixture: each member recurs "
        "under several real aliases across docs. The 'how many members?' (COUNT) query is "
        "answered by three arms, scored by gold-set-size bucket:",
        "",
        "- **goldengraph (real dedup)**: builds the store with real goldenmatch entity "
        "resolution, then traverses -- alias variants it merges count ONCE.",
        "- **oracle floor (perfect ER)**: resolves surface->qid -> exact count (the ceiling).",
        "- **ER-blind floor (no ER)**: a naive RAG clustering surfaces by normalization -> "
        "OVER-counts the aliases it cannot merge.",
        "",
        f"Fixture: `{Path(args.fixture).name}`, bucket-balanced subset "
        f"({args.anchors_per_bucket or 'all'} anchors/bucket -> {n_facts} anchors, "
        f"{n_ents} entities). passage_k={args.passage_k}, mentions_per_member="
        f"{args.mentions_per_member}. Wall: {wall:.1f}s. (One representative run; goldenmatch "
        "zero-config EM sampling makes the real-dedup arm vary a few hundredths run-to-run.)",
        "",
        *_fmt_count_table(res),
        "",
        "## reading the table",
        "",
        "The **GG - ER-blind** column is the compounded win: goldenmatch's real deduplication "
        "recovers the count the naive RAG floor inflates by counting each un-mergeable alias "
        "separately. goldengraph sits below the perfect-ER oracle ceiling (its real ER is "
        "imperfect -- it splits acronyms/tickers/transliterations) but above the ER-blind "
        "floor. This is goldenmatch's differentiated value -- entity resolution -- showing up "
        "INSIDE the aggregation capability, which a text-RAG cannot replicate. It is the "
        "result Phase 1.5 could not show: its floor got the oracle surface->qid map and one "
        "doc per member, so it was ER-blind by construction and never over-counted.",
        "",
        "Wikidata is gold-by-construction (text->structure->answer over the same rendered "
        "docs); the win measured is recovery of what is IN the fixture, not world truth.",
    ]
    out = Path(args.out_md)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[wrote {out}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
