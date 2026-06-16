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

from erkgbench.run import load_records  # noqa: E402  (pulls goldenmatch)
from erkgbench.adapters import GoldenMatchAdapter  # noqa: E402
from erkgbench.adapters.modeled import GraphRAGModeled  # noqa: E402
from demo import narrative as nv  # noqa: E402  # pyright: ignore[reportAttributeAccessIssue]  # namespace pkg, resolves at runtime

if TYPE_CHECKING:
    from erkgbench.adapters import Record

DEMO_PATH = _BENCH_ROOT / "demo" / "DEMO.md"
RESULTS_JSON = _BENCH_ROOT / "results" / "results.json"

IBM = "Q37156"
GEORGIA_COUNTRY, GEORGIA_STATE = "Q230", "Q1428"
MJ_A, MJ_B = "Q41421", "Q3308285"


def _maps(records: list[Record], entity_ids: list[str]) -> tuple[dict[int, str], dict[int, str]]:
    mentions = {r.index: r.mention for r in records}
    eids = {r.index: entity_ids[r.index] for r in records}
    return mentions, eids


def _assert_present(eids: dict[int, str], *ids: str) -> None:
    have = set(eids.values())
    missing = [i for i in ids if i not in have]
    if missing:
        raise SystemExit(
            f"demo: entity_id(s) {missing} missing from records.csv "
            "(corpus changed? update the demo protagonists)."
        )


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


def tier1_under_merge(records: list[Record], entity_ids: list[str]) -> str:
    mentions, eids = _maps(records, entity_ids)
    _assert_present(eids, IBM)
    all_idx = [r.index for r in records]
    before = nv.complete_partition(GraphRAGModeled().resolve(records), all_idx)
    after = nv.complete_partition(GoldenMatchAdapter("auto_fields").resolve(records), all_idx)
    return nv.render_demo_md(mentions, eids, IBM, "IBM", before, after, exact_family_f1=_exact_family_f1())


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

    records, entity_ids, _classes = load_records()
    md = tier1_under_merge(records, entity_ids)

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
