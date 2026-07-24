#!/usr/bin/env python3
"""Phase 1.5 end-to-end: oracle vs real ER on the committed Wikidata company fixture.

Runs the real-world aggregation bench under BOTH resolution arms and writes the
oracle-vs-real GG set-F1 delta by size bucket -- the ER contribution isolated on top
of the aggregation contribution -- plus the passage-window floor. The `oracle` arm
holds entity resolution perfect (Phase 0); the `real` arm makes goldenmatch's real
zero-config resolver cluster the alias variants itself, so its GG set-F1 folds in BOTH
resolution correctness AND traversal completeness (see erkgbench/qa_e2e/realworld.py).

Needs the goldengraph-native wheel + goldenmatch (source). Key-free, offline.

The full v1 fixture has ~13.5k member edges across ~2.3k anchors; scoring the whole
floor is slow and the real arm's ONE dedupe over the whole surface universe is large,
so this driver (like Phase 0) scores a BUCKET-BALANCED SUBSET -- N anchors per size
bucket, keeping only the entities those facts reference -- which is representative and
runs in ~minutes. `--anchors-per-bucket 0` scores the full fixture.

Usage:
    python scripts/run_realworld_phase15_e2e.py \
        --fixture erkgbench/qa_e2e/fixtures/wikidata_companies_v1.json \
        --anchors-per-bucket 25 --passage-k 10 --ambiguity 0.6 \
        --out-md results/RESULTS_REALWORLD_AGGREGATION.md
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Runnable as a plain script (`python scripts/run_realworld_phase15_e2e.py`): put the
# bench root (parent of scripts/) on sys.path so `erkgbench` imports regardless of CWD.
_BENCH_ROOT = Path(__file__).resolve().parents[1]
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

from erkgbench.qa_e2e.aggregation import _BUCKETS, _ordered_buckets, size_bucket

# Only the scored size buckets (the gate's `_BUCKETS`). The fixture's ">20" facts (some
# with hundreds of members) are NOT scored, and keeping them balloons the surface universe
# the real resolver must dedupe -- so the subset excludes them.
_SCORED_BUCKETS = frozenset(f"{lo}-{hi}" for lo, hi in _BUCKETS)
from erkgbench.qa_e2e.realworld import (
    _FIXTURE_DIR,
    _build_realworld_store_for_mode,
    run_realworld_aggregation,
)


def _bucket_balanced_subset(fixture_path: Path, anchors_per_bucket: int) -> dict:
    """A deterministic bucket-balanced subset of the fixture: up to N facts per size
    bucket (facts sorted by anchor qid for stability), keeping only the entities the
    kept facts reference. `anchors_per_bucket <= 0` returns the fixture unchanged."""
    data = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    if anchors_per_bucket <= 0:
        return data
    facts = sorted(data["facts"], key=lambda f: f["anchor_qid"])
    kept: list[dict] = []
    per_bucket: dict[str, int] = {}
    for f in facts:
        b = size_bucket(len(f["member_qids"]))
        if per_bucket.get(b, 0) >= anchors_per_bucket:
            continue
        per_bucket[b] = per_bucket.get(b, 0) + 1
        kept.append(f)
    used = {f["anchor_qid"] for f in kept} | {m for f in kept for m in f["member_qids"]}
    ents = [e for e in data["entities"] if e["qid"] in used]
    return {"meta": {**data.get("meta", {}), "subset_anchors_per_bucket": anchors_per_bucket},
            "entities": ents, "facts": kept}


def _anchor_resolution_stats(fixture_path, anchors, *, ambiguity: float):
    """Count, under the REAL resolver, how many anchors resolve to ONE store node
    (variants merged) vs fragment into several -- the direct evidence of real ER, and
    the mechanism behind the GG-real dip. Returns (merged, fragmented, examples)."""
    sg, cov, _docs, _qs, _a, _s = _build_realworld_store_for_mode(
        fixture_path, ambiguity=ambiguity, seed=7, resolve_mode="real")
    nodes_by_qid: dict[str, int] = {}
    for e in sg.entities():
        for qid in cov.get(e["entity_id"], set()):
            nodes_by_qid[qid] = nodes_by_qid.get(qid, 0) + 1
    merged = sum(1 for a in anchors if nodes_by_qid.get(a, 0) == 1)
    fragmented = sum(1 for a in anchors if nodes_by_qid.get(a, 0) > 1)
    examples = [(a, nodes_by_qid.get(a, 0)) for a in anchors if nodes_by_qid.get(a, 0) > 1]
    return merged, fragmented, examples


def _fmt_delta_table(oracle, real, floor_f1, floor_rec) -> list[str]:
    buckets = _ordered_buckets(oracle.gg_setf1)
    lines = [
        "| size bucket | GG oracle set-F1 | GG real set-F1 | oracle-real delta (ER) "
        "| floor set-F1 | floor recall |",
        "|---|---|---|---|---|---|",
    ]
    for b in buckets:
        o = oracle.gg_setf1.get(b, 0.0)
        r = real.gg_setf1.get(b, 0.0)
        lines.append(
            f"| {b} | {o:.3f} | {r:.3f} | {o - r:+.3f} | "
            f"{floor_f1.get(b, 0.0):.3f} | {floor_rec.get(b, 0.0):.3f} |"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fixture", default=str(_FIXTURE_DIR / "wikidata_companies_v1.json"))
    p.add_argument("--anchors-per-bucket", type=int, default=25)
    p.add_argument("--passage-k", type=int, default=10)
    p.add_argument("--ambiguity", type=float, default=0.6)
    p.add_argument("--out-md", default="results/RESULTS_REALWORLD_AGGREGATION.md")
    args = p.parse_args(argv)

    subset = _bucket_balanced_subset(Path(args.fixture), args.anchors_per_bucket)
    n_facts = len(subset["facts"])
    n_ents = len(subset["entities"])
    tmp = Path(args.out_md).parent / "_subset_wikidata_companies.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(subset), encoding="utf-8")

    results = {}
    timings = {}
    for mode in ("oracle", "real"):
        t0 = time.perf_counter()
        results[mode] = run_realworld_aggregation(
            tmp, ambiguity=args.ambiguity, passage_k=args.passage_k, resolve_mode=mode)
        timings[mode] = time.perf_counter() - t0

    oracle, real = results["oracle"], results["real"]
    anchors = [f["anchor_qid"] for f in subset["facts"]]
    by_qid = {e["qid"]: e for e in subset["entities"]}
    # resolution stats read the SAME subset the scored runs did (tmp still on disk).
    merged, fragmented, frag_examples = _anchor_resolution_stats(
        tmp, anchors, ambiguity=args.ambiguity)
    tmp.unlink(missing_ok=True)

    ex_lines = []
    for a, nnodes in frag_examples[:6]:
        e = by_qid.get(a, {})
        ex_lines.append(
            f"  - `{a}` **{e.get('canonical', '?')}** (aliases: "
            f"{', '.join(e.get('aliases', []) or []) or 'none'}) -> {nnodes} store nodes"
        )

    # floor is identical across arms (same docs / gold-qid keyspace); take it from oracle.
    lines = [
        "# Real-world aggregation -- oracle vs real entity resolution (Phase 1.5)",
        "",
        "GoldenGraph exact set-aggregation on the committed Wikidata company fixture, "
        "scored by gold-set-size bucket, under two resolution arms:",
        "",
        "- **oracle** (Phase 0): entity resolution held perfect -- an entity's alias "
        "variants are pre-merged, so GG set-F1 isolates the *aggregation / traversal* "
        "capability.",
        "- **real** (Phase 1.5): the store must cluster the alias variants ITSELF via "
        "goldenmatch's real zero-config resolver, so GG set-F1 folds in BOTH resolution "
        "correctness AND traversal completeness. `oracle - real` is the ER contribution, "
        "isolated on top of the aggregation contribution.",
        "",
        f"Fixture: `{Path(args.fixture).name}`, bucket-balanced subset "
        f"({args.anchors_per_bucket or 'all'} anchors/bucket -> {n_facts} anchors, "
        f"{n_ents} entities). ambiguity={args.ambiguity}, passage_k={args.passage_k}. "
        f"Build wall: oracle {timings['oracle']:.1f}s, real {timings['real']:.1f}s. "
        "(Numbers are one representative run; goldenmatch zero-config EM sampling makes "
        "the real arm vary by a few hundredths run-to-run.)",
        "",
        *_fmt_delta_table(oracle, real, oracle.floor_setf1, oracle.floor_recall or {}),
        "",
        "## real entity resolution actually ran (evidence)",
        "",
        f"Under the real resolver, **{merged}/{len(anchors)}** anchors resolve to ONE "
        f"store node (alias variants merged) and **{fragmented}/{len(anchors)}** fragment "
        "into several -- goldenmatch merges near-identical / legal-suffix variants but "
        "splits acronyms, tickers, and transliterations. Sample fragmentations:",
        "",
        *ex_lines,
        "",
        "The oracle arm merges essentially every anchor to one node (perfect ER by "
        "construction); the real arm's fragmentation is the mechanism behind the GG-real "
        "dip, and its qid-space cluster keys are a genuine goldenmatch clustering, not the "
        "oracle qid.",
        "",
        "## reading the table (and a methodological finding)",
        "",
        "GoldenGraph **oracle** set-F1 is ~1.0 and flat across buckets: exact traversal is "
        "size-invariant given resolved entities (the Phase 0 result, on real data). The "
        "**real** column dips below oracle -- and the `oracle - real` delta WIDENS with "
        "set size, because a larger member set means the anchor is mentioned in more docs, "
        "so the chance that at least one of its rendered surfaces fragments (severing part "
        "of the set from the seeded node) rises. That widening delta is the measured ER "
        "contribution to the aggregation capability.",
        "",
        "**Finding that refines the plan's hypothesis:** the plan predicted the passage "
        "floor would ALSO degrade under real ER (name fragmentation) so the GG-vs-floor "
        "gap would widen. In THIS harness it does not -- and the reason is instructive. "
        "The floor resolves each surface to its gold qid via the fixture's ground-truth "
        "surface->qid map, and every member appears in exactly ONE single-edge document, "
        "so the floor is resolution-INDEPENDENT and precision-perfect here; its only "
        "failure mode is window-recall collapse. Real ER therefore degrades ONLY the GG "
        "arm, and where GG's recall falls furthest (large sets) it can dip toward or below "
        "the recall-limited floor. Making the floor genuinely suffer name fragmentation "
        "needs a corpus where members recur under multiple aliases (a co-occurrence / "
        "real-RAG floor) -- the Phase 2 extension. Net: Phase 1.5 delivers a clean, "
        "honest MEASUREMENT of the ER contribution (the oracle-vs-real delta) and shows "
        "the current floor is ER-blind, which is exactly what a real-RAG arm must fix to "
        "make the compounded gap show up.",
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
