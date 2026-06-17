"""ER-KG-Bench GraphRAG before/after demo.

    python demo/run_demo.py            # regenerate DEMO.md (+ Tier 2 if OPENAI_API_KEY)
    python demo/run_demo.py --check    # assert committed DEMO.md matches a fresh run

Tier 1 (under-merge, IBM) is deterministic and committed. Tier 2 (over-merge,
Georgia) needs OPENAI_API_KEY, prints to stdout / CI summary, and is NEVER written
to DEMO.md. Heavy: the goldenmatch rows run in CI (bench-er-kg lane), not locally.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

from demo import (
    narrative as nv,  # noqa: E402  # pyright: ignore[reportAttributeAccessIssue]  # namespace pkg, resolves at runtime
)
from erkgbench.adapters import GoldenMatchAdapter  # noqa: E402
from erkgbench.adapters.real.exact_family import RealGraphRAG  # noqa: E402
from erkgbench.run import load_records  # noqa: E402  (pulls goldenmatch)

if TYPE_CHECKING:
    from erkgbench.adapters import Record

DEMO_PATH = _BENCH_ROOT / "demo" / "DEMO.md"
RESULTS_JSON = _BENCH_ROOT / "results" / "results.json"

# Under-merge protagonist auto-selection: positive classes where unifying surface
# forms is CORRECT. Real-source classes (Wikidata/RxNorm variants) come first and
# are preferred; org_suffix (auto+fields F1 1.0) guarantees a clean win exists.
# Excluded: same_name_collision / temporal_version (negative precision classes) and
# cross_document_exact (the exact family already merges identical strings).
_UNDER_MERGE_CLASSES = (
    "abbreviation", "nickname_alias", "cross_lingual", "synonym_brand",  # real-source
    "org_suffix", "typo",  # synthetic-over-real
)

GEORGIA_COUNTRY, GEORGIA_STATE = "Q230", "Q1428"
MJ_A, MJ_B = "Q41421", "Q3308285"


def _maps(records: list[Record], entity_ids: list[str]) -> tuple[dict[int, str], dict[int, str]]:
    mentions = {r.index: r.mention for r in records}
    eids = {r.index: entity_ids[r.index] for r in records}
    return mentions, eids


def _pick_under_merge_entity(
    records: list[Record],
    entity_ids: list[str],
    failure_classes: list[str],
    before: list[list[int]],
    after: list[list[int]],
) -> tuple[str, str, str]:
    """Pick the protagonist that best shows a CLEAN under-merge: the exact-match
    family fragments it (>=2 nodes) and auto_fields fully unifies it (1 node, all
    names). Prefers real-source classes, then the most surface forms; deterministic
    by entity_id. Falls back to the biggest node-reduction if none fully unify."""
    mentions = {r.index: r.mention for r in records}
    eids = {r.index: entity_ids[r.index] for r in records}
    fc = {entity_ids[r.index]: failure_classes[r.index] for r in records}
    etype = {entity_ids[r.index]: r.entity_type for r in records}

    def class_rank(e: str) -> int:
        c = fc.get(e, "")
        return _UNDER_MERGE_CLASSES.index(c) if c in _UNDER_MERGE_CLASSES else len(_UNDER_MERGE_CLASSES)

    clean: list[tuple[int, int, str, str, str]] = []
    partial: list[tuple[int, int, str, str, str]] = []
    for e in {ent for ent in entity_ids if fc.get(ent) in _UNDER_MERGE_CLASSES}:
        idxs = [i for i in mentions if eids[i] == e]
        if len({mentions[i] for i in idxs}) < 2:
            continue  # a single surface form: the exact family would not fragment it
        query = min((mentions[i] for i in idxs), key=len)
        b = nv.under_merge_answer(before, mentions, eids, e, query)
        a = nv.under_merge_answer(after, mentions, eids, e, query)
        if b["distinct_nodes"] >= 2 and a["distinct_nodes"] == 1 and a["complete"]:
            clean.append((class_rank(e), -len(a["all_names"]), e, query, etype[e]))
        elif b["distinct_nodes"] > a["distinct_nodes"]:
            reduction = b["distinct_nodes"] - a["distinct_nodes"]
            partial.append((class_rank(e), -reduction, e, query, etype[e]))
    pool = clean or partial
    if not pool:
        raise SystemExit("demo: no under-merge protagonist found (corpus changed?).")
    pool.sort()
    _rank, _score, eid, query, et = pool[0]
    return eid, query, et


def _exact_family_f1() -> str:
    """Read the exact-match-family F1 from the freshly-generated results.json so the
    demo's cited number never drifts from RESULTS.md. Falls back to the narrative
    constant if results.json is missing/unreadable."""
    try:
        data = json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
        for row in data.get("results", []):
            if row.get("name") == "MS-GraphRAG" and "overall" in row:
                return f"F1 {row['overall']['f1']}"
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return nv.EXACT_FAMILY_F1


def tier1_under_merge(records: list[Record], entity_ids: list[str], failure_classes: list[str]) -> str:
    mentions, eids = _maps(records, entity_ids)
    all_idx = [r.index for r in records]
    before = nv.complete_partition(RealGraphRAG().resolve(records), all_idx)
    after = nv.complete_partition(GoldenMatchAdapter("auto_fields").resolve(records), all_idx)
    eid, query, etype = _pick_under_merge_entity(records, entity_ids, failure_classes, before, after)
    return nv.render_demo_md(
        mentions, eids, eid, query, before, after,
        exact_family_f1=_exact_family_f1(), entity_type=etype,
    )


def tier2_over_merge(records: list[Record], entity_ids: list[str]) -> str:
    """Prose only; never written to DEMO.md. Requires OPENAI_API_KEY."""
    _mentions, eids = _maps(records, entity_ids)
    all_idx = [r.index for r in records]
    det = nv.complete_partition(GoldenMatchAdapter("auto_fields").resolve(records), all_idx)
    llm = nv.complete_partition(GoldenMatchAdapter("auto_llm").resolve(records), all_idx)
    pairs = [
        (GEORGIA_COUNTRY, GEORGIA_STATE, "the two Georgias (country vs US state)"),
        (MJ_A, MJ_B, "the two Michael Jordans"),
    ]
    chosen = next((p for p in pairs if nv.pair_merged(det, eids, p[0], p[1])), pairs[0])
    a, b, label = chosen
    det_merged = nv.pair_merged(det, eids, a, b)
    llm_merged = nv.pair_merged(llm, eids, a, b)
    return (
        "## Over-merge (key-gated, prose only -- NOT committed)\n\n"
        f"Collision pair: {label} (`{a}` vs `{b}`).\n"
        f"- Before, deterministic `auto+fields`: merged = {det_merged} "
        f"(collision precision ~{nv.COLL_P_DET}).\n"
        f"- After, `auto+llm`: merged = {llm_merged} "
        f"(the LLM refuses the wrong merge; collision precision {nv.COLL_P_LLM}).\n"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check",
        action="store_true",
        help="assert committed DEMO.md matches a fresh run; do not write",
    )
    args = ap.parse_args()

    records, entity_ids, failure_classes = load_records()
    md = tier1_under_merge(records, entity_ids, failure_classes)

    if args.check:
        current = DEMO_PATH.read_text(encoding="utf-8") if DEMO_PATH.exists() else ""
        if current != md:
            print(
                "demo: DEMO.md is stale -- regenerate with `python demo/run_demo.py`",
                file=sys.stderr,
            )
            sys.exit(1)
        print("demo: DEMO.md up to date.")
    else:
        DEMO_PATH.write_text(md, encoding="utf-8")
        print(md)

    if os.environ.get("OPENAI_API_KEY"):
        print(tier2_over_merge(records, entity_ids))


if __name__ == "__main__":
    main()
