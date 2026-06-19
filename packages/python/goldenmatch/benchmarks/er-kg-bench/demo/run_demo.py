"""ER-KG-Bench GraphRAG before/after demo.

    python demo/run_demo.py            # regenerate DEMO.md + demo.html (if OPENAI_API_KEY)
    python demo/run_demo.py --check    # assert committed DEMO.md + demo.html/snapshot match

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

import demo.agent as ag  # noqa: E402  # pyright: ignore[reportMissingImports]
import demo.render_html as rh  # noqa: E402  # pyright: ignore[reportMissingImports]
from demo import (
    narrative as nv,  # noqa: E402  # pyright: ignore[reportAttributeAccessIssue]  # namespace pkg, resolves at runtime
)
from erkgbench.adapters import GoldenMatchAdapter  # noqa: E402
from erkgbench.adapters.real.exact_family import RealGraphRAG  # noqa: E402
from erkgbench.run import load_records  # noqa: E402  (pulls goldenmatch)

if TYPE_CHECKING:
    from erkgbench.adapters import Record

import demo.kg as kg  # noqa: E402  # pyright: ignore[reportMissingImports]

DEMO_PATH = _BENCH_ROOT / "demo" / "DEMO.md"
RESULTS_JSON = _BENCH_ROOT / "results" / "results.json"
SNAPSHOT_PATH = _BENCH_ROOT / "demo" / "demo.snapshot.json"
HTML_PATH = _BENCH_ROOT / "demo" / "demo.html"

# Under-merge protagonist auto-selection: positive classes where unifying surface
# forms is CORRECT. Real-source classes (Wikidata/RxNorm variants) come first and
# are preferred; org_suffix (auto+fields F1 1.0) guarantees a clean win exists.
# Excluded: same_name_collision / temporal_version (negative precision classes) and
# cross_document_exact (the exact family already merges identical strings).
_UNDER_MERGE_CLASSES = (
    "abbreviation", "nickname_alias", "cross_lingual", "synonym_brand",  # real-source
    "org_suffix", "typo",  # synthetic-over-real
)

# Cap on distractor entities shown alongside the protagonist in the KG subgraph.
_DISTRACTOR_CAP = 2

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


def _serialize_subgraph(sub: kg.Subgraph) -> dict:
    """Serialize a Subgraph to the snapshot dict shape."""
    nodes = [
        {
            "node_id": n.node_id,
            "names": list(n.names),
            "type": n.type,
            "context": n.context,
            "record_indices": list(n.record_indices),
        }
        for n in sub.nodes
    ]
    return {
        "nodes": nodes,
        "retrieved_node_ids": [n.node_id for n in sub.nodes],
    }


def _pick_protagonist(
    mentions: dict[int, str],
    eids: dict[int, str],
    fc_map: dict[int, str],
    types: dict[int, str],
    before: list[list[int]],
    after: list[list[int]],
) -> tuple[str, str, str]:
    """Same logic as _pick_under_merge_entity but operates on pre-built dicts
    (index -> value) so it works for any record index range (not just 0-based)."""
    # entity_id -> failure_class (first record wins)
    fc_eid: dict[str, str] = {}
    etype_eid: dict[str, str] = {}
    for i, e in eids.items():
        if e not in fc_eid:
            fc_eid[e] = fc_map.get(i, "")
            etype_eid[e] = types.get(i, "org")

    def class_rank(e: str) -> int:
        c = fc_eid.get(e, "")
        return _UNDER_MERGE_CLASSES.index(c) if c in _UNDER_MERGE_CLASSES else len(_UNDER_MERGE_CLASSES)

    clean: list[tuple[int, int, str, str, str]] = []
    partial: list[tuple[int, int, str, str, str]] = []
    for e in {e for e in eids.values() if fc_eid.get(e) in _UNDER_MERGE_CLASSES}:
        idxs = [i for i, eid in eids.items() if eid == e]
        if len({mentions[i] for i in idxs}) < 2:
            continue
        query = min((mentions[i] for i in idxs), key=len)
        b = nv.under_merge_answer(before, mentions, eids, e, query)
        a = nv.under_merge_answer(after, mentions, eids, e, query)
        if b["distinct_nodes"] >= 2 and a["distinct_nodes"] == 1 and a["complete"]:
            clean.append((class_rank(e), -len(a["all_names"]), e, query, etype_eid[e]))
        elif b["distinct_nodes"] > a["distinct_nodes"]:
            reduction = b["distinct_nodes"] - a["distinct_nodes"]
            partial.append((class_rank(e), -reduction, e, query, etype_eid[e]))
    pool = clean or partial
    if not pool:
        raise SystemExit("demo: no under-merge protagonist found (corpus changed?).")
    pool.sort()
    _rank, _score, eid, query, et = pool[0]
    return eid, query, et


def _build_scaffolding(
    records: list[Record],
    entity_ids: list[str],
    failure_classes: list[str],
    before_partition: list[list[int]],
    after_partition: list[list[int]],
) -> tuple[dict, kg.Subgraph, kg.Subgraph]:
    """Build the deterministic scaffolding dict and return it with the two Subgraph
    objects (to avoid recomputing them in build_snapshot).

    Pure given the partitions -- no LLM, no heavy adapters. Designed for testability.
    """
    mentions = {r.index: r.mention for r in records}
    types = {r.index: r.entity_type for r in records}
    contexts = {r.index: r.context for r in records}
    # entity_ids / failure_classes are positional lists (entity_ids[j] = entity for records[j])
    eids = {r.index: entity_ids[j] for j, r in enumerate(records)}
    fc_map = {r.index: failure_classes[j] for j, r in enumerate(records)}
    all_idx = [r.index for r in records]

    before_complete = nv.complete_partition(before_partition, all_idx)
    after_complete = nv.complete_partition(after_partition, all_idx)

    # Pick protagonist using the corrected eids/fc_map dicts (avoids the
    # positional-list index assumption in _pick_under_merge_entity).
    eid, query, etype = _pick_protagonist(
        mentions, eids, fc_map, types, before_complete, after_complete
    )

    # Protagonist's record indices
    protagonist_idxs = {i for i in all_idx if eids.get(i) == eid}

    # Distractor entities: same type, different entity_id, deterministic order
    # (sorted by their first/minimum record index for determinism)
    entity_to_idxs: dict[str, list[int]] = {}
    for i in all_idx:
        e = eids.get(i)
        if e and e != eid:
            entity_to_idxs.setdefault(e, []).append(i)

    distractor_eids = [
        e for e, idxs in sorted(entity_to_idxs.items(), key=lambda kv: min(kv[1]))
        if any(types.get(i) == etype for i in idxs)
    ][:_DISTRACTOR_CAP]

    distractor_idxs: set[int] = set()
    for e in distractor_eids:
        distractor_idxs.update(entity_to_idxs[e])

    selected_idxs = protagonist_idxs | distractor_idxs

    # Slice each completed partition to clusters that intersect selected_idxs
    def _slice(partition: list[list[int]]) -> list[list[int]]:
        return [c for c in partition if any(i in selected_idxs for i in c)]

    before_sliced = _slice(before_complete)
    after_sliced = _slice(after_complete)

    before_kg = kg.build_kg(before_sliced, mentions, types, contexts)
    after_kg = kg.build_kg(after_sliced, mentions, types, contexts)

    # Present the WHOLE bounded KG (the protagonist's nodes + a few distractor
    # entities) as the agent's memory -- NOT a query-string lookup. In the BEFORE
    # graph the exact-match family scatters the protagonist across several nodes; a
    # query-only retrieval would surface just the one node literally named `query`
    # and hide that fragmentation (making before == after). The slice already bounds
    # the memory, so the whole slice IS the retrieved subgraph.
    before_sub = kg.Subgraph(query=query, nodes=tuple(before_kg.nodes))
    after_sub = kg.Subgraph(query=query, nodes=tuple(after_kg.nodes))

    noun_plural = {
        "org": "organizations",
        "person": "people",
        "place": "places",
        "drug": "drugs",
        "event": "events",
    }.get(etype, "entities")

    # A COUNT question -- the canonical agent-memory under-merge failure. The
    # before-agent, seeing the protagonist scattered across distinct nodes, lists each
    # surface form as a SEPARATE entity and over-counts; the after-agent, seeing one
    # unified node, returns the true count. The question must NOT enumerate the aliases
    # (a "what are X's names?" question is defeated -- a capable model reasons the
    # aliases together from world knowledge even across fragmented nodes; the COUNT is
    # the robust, honest signal, measured against a real keyed run).
    question = f"How many distinct {noun_plural} are in your memory? List each one."

    scaffolding = {
        "protagonist": {
            "entity_id": eid,
            "query": query,
            "type": etype,
        },
        "question": question,
        "before": _serialize_subgraph(before_sub),
        "after": _serialize_subgraph(after_sub),
        "numbers": {
            "exact_family_f1": _exact_family_f1(),
        },
    }

    return scaffolding, before_sub, after_sub


def build_snapshot(
    records: list[Record],
    entity_ids: list[str],
    failure_classes: list[str],
    before_partition: list[list[int]],
    after_partition: list[list[int]],
    llm_fn,
    *,
    recorded_at: str,
) -> dict:
    """Build the full snapshot dict (scaffolding + recorded_llm).

    llm_fn is a Callable[[str], ag.LLMResponse] -- either ag.make_openai_llm_fn(...)
    for a real run or a stub for tests. No tracker is required; cost is computed from
    the AgentAnswer token fields when no tracker is provided.
    """
    scaffolding, before_sub, after_sub = _build_scaffolding(
        records, entity_ids, failure_classes, before_partition, after_partition
    )

    question = scaffolding["question"]
    before_ans = ag.answer(question, before_sub, llm_fn)
    after_ans = ag.answer(question, after_sub, llm_fn)

    total_input = before_ans.input_tokens + after_ans.input_tokens
    total_output = before_ans.output_tokens + after_ans.output_tokens
    total_tokens = total_input + total_output

    recorded_llm = {
        "model": before_ans.model,
        "recorded_at": recorded_at,
        "before_answer": before_ans.text,
        "after_answer": after_ans.text,
        "cost": {
            "llm_calls": 2,
            "llm_tokens": total_tokens,
            "llm_usd": 0.0,
        },
    }

    return {"scaffolding": scaffolding, "recorded_llm": recorded_llm}


def _write_outputs(snapshot: dict) -> None:
    """Write demo.snapshot.json and demo.html to the demo/ directory."""
    demo_dir = Path(__file__).parent
    snapshot_path = demo_dir / "demo.snapshot.json"
    html_path = demo_dir / "demo.html"
    snapshot_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    html_path.write_text(rh.render(snapshot), encoding="utf-8")


def _check() -> int:
    """Check that the committed demo.snapshot.json and demo.html are current.

    Returns 0 on success or if snapshot is absent (bootstrap pending).
    Returns 1 if the scaffolding or HTML is stale.
    """
    if not SNAPSHOT_PATH.exists():
        print("demo: demo.snapshot.json not committed yet (bootstrap pending).")
        return 0

    committed_snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    # Recompute scaffolding via the real adapters.
    records, entity_ids, failure_classes = load_records()
    before = RealGraphRAG().resolve(records)
    after = GoldenMatchAdapter("auto_fields").resolve(records)

    fresh_scaffolding, _before_sub, _after_sub = _build_scaffolding(
        records, entity_ids, failure_classes, before, after
    )

    if fresh_scaffolding != committed_snapshot.get("scaffolding"):
        print(
            "demo: scaffolding is stale -- regenerate with `python demo/run_demo.py`.\n"
            f"  committed protagonist: {committed_snapshot.get('scaffolding', {}).get('protagonist')}\n"
            f"  fresh protagonist:     {fresh_scaffolding.get('protagonist')}"
        )
        return 1

    # Re-render HTML and compare (newlines normalized).
    expected_html = rh.render(committed_snapshot)
    if not HTML_PATH.exists():
        print("demo: demo.html is missing -- regenerate with `python demo/run_demo.py`.")
        return 1
    committed_html = HTML_PATH.read_text(encoding="utf-8")
    if expected_html.replace("\r\n", "\n") != committed_html.replace("\r\n", "\n"):
        print("demo: demo.html out of sync with snapshot (hand-edited?)")
        return 1

    print("demo: HTML scaffolding committed and current.")
    return 0


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
        help="assert committed DEMO.md + demo.snapshot.json + demo.html match a fresh run; do not write",
    )
    args = ap.parse_args()

    if args.check:
        # DEMO.md check
        records, entity_ids, failure_classes = load_records()
        md = tier1_under_merge(records, entity_ids, failure_classes)
        current = DEMO_PATH.read_text(encoding="utf-8") if DEMO_PATH.exists() else ""
        if current != md:
            print(
                "demo: DEMO.md is stale -- regenerate with `python demo/run_demo.py`",
                file=sys.stderr,
            )
            sys.exit(1)
        print("demo: DEMO.md up to date.")
        # HTML snapshot check
        sys.exit(_check())

    records, entity_ids, failure_classes = load_records()
    md = tier1_under_merge(records, entity_ids, failure_classes)

    DEMO_PATH.write_text(md, encoding="utf-8")
    print(md)

    if os.environ.get("OPENAI_API_KEY"):
        print(tier2_over_merge(records, entity_ids))

        # HTML round-trip: build snapshot and write outputs.
        import datetime

        from goldenmatch.core.llm_budget import BudgetConfig, BudgetTracker  # noqa: E402

        tracker = BudgetTracker(BudgetConfig())
        llm_fn = ag.make_openai_llm_fn(tracker=tracker)

        before_raw = RealGraphRAG().resolve(records)
        after_raw = GoldenMatchAdapter("auto_fields").resolve(records)

        snapshot = build_snapshot(
            records, entity_ids, failure_classes,
            before_raw, after_raw,
            llm_fn,
            recorded_at=datetime.date.today().isoformat(),
        )
        # Overwrite cost from tracker (real tokens + cost)
        summary = {
            "llm_calls": tracker._total_calls,
            "llm_tokens": tracker._total_input_tokens + tracker._total_output_tokens,
            "llm_usd": round(tracker._total_cost, 6),
        }
        snapshot["recorded_llm"]["cost"] = summary

        _write_outputs(snapshot)
        print("demo: demo.snapshot.json and demo.html written.")
    else:
        print("demo: OPENAI_API_KEY not set -- HTML round-trip skipped (no key).")


if __name__ == "__main__":
    main()
