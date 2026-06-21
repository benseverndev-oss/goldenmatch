"""SP6 fact-completeness eval -- deterministic, no goldenmatch.

Tiny fixtures pin the resolved=1.0 / split<1.0 behaviour; the real-corpus test
builds the perfect-ER and exact-match-floor partitions by hand (no resolver) and
asserts resolved co-locates every fact while the floor strands them -- the
deterministic core of the CI gate.
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

from erkgbench import qa_eval  # noqa: E402
from erkgbench.qa_loader import QAItem, load_qa, load_qa_facts  # noqa: E402

# tiny fixture: entity "E" has two surface forms (records 0,1), each with a fact.
_MEN = {0: "A", 1: "A-form2", 2: "B"}
_TYP = {0: "org", 1: "org", 2: "org"}
_CTX = {0: "", 1: "", 2: ""}
_FACTS = {0: ["f0"], 1: ["f1"]}
_ITEM = QAItem(
    qa_id="x", entity_id="E", seed_surface="A", question="?",
    facts={0: "f0", 1: "f1"}, gold_answer="f0 and f1",
)


def test_completeness_resolved_is_one():
    # both forms in one node -> querying "A" retrieves both facts
    assert qa_eval.item_completeness([[0, 1], [2]], _ITEM, _MEN, _TYP, _CTX, _FACTS) == 1.0


def test_completeness_split_misses_stranded_facts():
    # each form its own node -> querying "A" gets f0 only, f1 stranded
    assert qa_eval.item_completeness([[0], [1], [2]], _ITEM, _MEN, _TYP, _CTX, _FACTS) == 0.5


def test_run_qa_eval_ok_and_skip():
    class _Resolved:
        name = "resolved"

        def resolve(self, records):
            return [[0, 1], [2]]

    class _Boom:
        name = "boom"

        def resolve(self, records):
            raise ImportError("missing dep")

    rows = qa_eval.run_qa_eval(
        [_Resolved(), _Boom()], records=[], items=[_ITEM], facts_by_record=_FACTS,
        mentions=_MEN, types=_TYP, contexts=_CTX, failure_class={0: "abbr", 1: "abbr"},
    )
    by = {r["name"]: r for r in rows}
    assert by["resolved"]["status"] == "ok"
    assert by["resolved"]["mean_completeness"] == 1.0
    assert by["boom"]["status"] == "skipped"


def test_llm_judge_plumbing_stub():
    # stub judge: 1.0 when ALL gold facts were retrieved, else 0.0 -- asserts the
    # plumbing (called once per item, correctness threaded through), NOT accuracy.
    calls = []

    def stub_judge(item, retrieved):
        calls.append(item.qa_id)
        return 1.0 if set(item.gold_facts) <= retrieved else 0.0

    res_resolved = qa_eval.engine_completeness(
        [[0, 1], [2]], [_ITEM], _MEN, _TYP, _CTX, _FACTS, judge=stub_judge
    )
    res_split = qa_eval.engine_completeness(
        [[0], [1], [2]], [_ITEM], _MEN, _TYP, _CTX, _FACTS, judge=stub_judge
    )
    assert calls == ["x", "x"]  # one judge call per item per engine
    assert res_resolved["items"][0]["correctness"] == 1.0
    assert res_resolved["mean_correctness"] == 1.0
    assert res_split["items"][0]["correctness"] == 0.0  # f1 stranded -> not all gold


def test_render_results_qa_has_columns_and_disclaimer():
    rows = [
        {"name": "goldengraph", "status": "ok", "mean_completeness": 0.9,
         "per_class": {"abbreviation": 1.0}, "items": []},
        {"name": "exact-match-floor", "status": "ok", "mean_completeness": 0.33,
         "per_class": {"abbreviation": 0.33}, "items": []},
        {"name": "neo4j-graphrag", "status": "skipped", "error": "no dep"},
    ]
    md = qa_eval.render_results_qa(rows)
    assert "fact-completeness" in md
    assert "authored" in md and "synthetic" in md  # the disclaimer
    assert "goldengraph" in md and "exact-match-floor" in md
    assert "skipped" in md
    assert "Per-failure-class" in md


def test_exact_match_floor_adapter_groups_by_form():
    from erkgbench.adapters import Record

    recs = [Record(0, "IBM", "org", ""), Record(1, "IBM", "org", ""), Record(2, "Apple", "org", "")]
    part = qa_eval.ExactMatchFloorAdapter().resolve(recs)
    assert sorted(sorted(g) for g in part) == [[0, 1], [2]]


def test_real_corpus_resolved_beats_exact_floor():
    items = load_qa()
    facts = load_qa_facts(items)
    mentions, types, contexts, fclass = qa_eval.load_corpus()

    rows = list(csv.DictReader((_BENCH_ROOT / "dataset" / "records.csv").open(encoding="utf-8")))
    by_entity: dict[str, list[int]] = defaultdict(list)
    by_form: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        rid = int(r["record_id"])
        by_entity[r["entity_id"]].append(rid)
        by_form[r["mention"]].append(rid)

    resolved = list(by_entity.values())          # perfect ER
    floor = list(by_form.values())               # exact-match-string KG

    res = qa_eval.engine_completeness(resolved, items, mentions, types, contexts, facts, fclass)
    flo = qa_eval.engine_completeness(floor, items, mentions, types, contexts, facts, fclass)

    assert res["mean_completeness"] == 1.0       # perfect ER co-locates every fact
    assert flo["mean_completeness"] < 0.6        # the floor strands the other forms
    assert res["mean_completeness"] - flo["mean_completeness"] >= 0.25
