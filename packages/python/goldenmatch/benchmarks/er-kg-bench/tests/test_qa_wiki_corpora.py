"""HotpotQA + 2WikiMultiHopQA loaders -- offline. The fixture path exercises the
JSONL loaders; the fetch path injects a stub `datasets` module (no network, no real
`datasets` install) to assert row->QACorpus normalization: every context paragraph
becomes a `<qid>::p<idx>` Document, gold support is the paragraph-granular doc ids of
the SUPPORTING titles (so support_recall is measurable), hop_count is derived, and the
seeded subset is deterministic. Mirrors test_qa_musique_fetch.py."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erkgbench.qa_e2e.corpora import (  # noqa: E402
    QACorpus,
    fetch_2wikimultihop,
    fetch_hotpotqa,
    load_2wikimultihop,
    load_hotpotqa,
)

_FIX = Path(__file__).resolve().parent.parent / "erkgbench" / "qa_e2e" / "fixtures"


def _stub_datasets(monkeypatch, rows):
    """Inject a fake `datasets` whose load_dataset(dataset, config=None, *, split) returns rows."""
    calls: dict = {}

    def load_dataset(dataset, config=None, *, split=None):
        calls["dataset"], calls["config"], calls["split"] = dataset, config, split
        return list(rows)

    mod = types.ModuleType("datasets")
    mod.load_dataset = load_dataset  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "datasets", mod)
    return calls


# ---------------- HotpotQA ----------------------------------------------------


def _hotpot_row(qid, answer, titles, support_titles, sents):
    return {
        "id": qid,
        "question": f"question for {qid}?",
        "answer": answer,
        "type": "bridge",
        "level": "hard",
        "context": {"title": list(titles), "sentences": [list(s) for s in sents]},
        "supporting_facts": {"title": list(support_titles), "sent_id": [0] * len(support_titles)},
    }


_HOTPOT_ROWS = [
    _hotpot_row("hp_a", "Alpha", ["Alice", "Bob", "Distractor"],
                ["Alice", "Bob"], [["s0"], ["s1"], ["noise"]]),
    _hotpot_row("hp_b", "Bravo", ["Carol", "Dave", "Eve", "Filler"],
                ["Carol", "Eve"], [["s0"], ["s1"], ["s2"], ["s3"]]),
]


def test_hotpotqa_fetch_normalizes_rows(monkeypatch):
    calls = _stub_datasets(monkeypatch, _HOTPOT_ROWS)
    corpus = fetch_hotpotqa(dataset="x/y", config="distractor", split="validation",
                            max_questions=10, seed=1)
    assert isinstance(corpus, QACorpus)
    assert corpus.name == "hotpotqa"
    assert calls["config"] == "distractor" and calls["split"] == "validation"
    assert len(corpus.questions) == 2
    # every context paragraph is a Document keyed <qid>::p<idx>
    assert len(corpus.documents) == 3 + 4
    by_id = {q.id: q for q in corpus.questions}
    qa = by_id["hp_a"]
    assert qa.gold_answer == "Alpha"
    assert qa.hop_count == 2  # HotpotQA is 2-hop by construction
    # gold support = the two SUPPORTING titles' paragraphs (Alice=p0, Bob=p1), NOT the distractor
    assert qa.gold_supporting_fact_ids == ("hp_a::p0", "hp_a::p1")
    doc_ids = {d.id for d in corpus.documents}
    assert set(qa.gold_supporting_fact_ids) <= doc_ids


def test_hotpotqa_load_from_fixture():
    corpus = load_hotpotqa(path=_FIX / "hotpotqa_sample.jsonl", max_questions=10)
    assert corpus.name == "hotpotqa"
    assert len(corpus.questions) == 2
    q0 = corpus.questions[0]
    assert q0.gold_answer == "yes"
    assert q0.hop_count == 2
    assert q0.gold_supporting_fact_ids == ("hp_1::p0", "hp_1::p1")
    # the supporting paragraph text is joined from its sentences
    docs = {d.id: d.text for d in corpus.documents}
    assert docs["hp_1::p0"] == "Alice was born in France."


# ---------------- 2WikiMultiHopQA ---------------------------------------------


def _2wiki_row(qid, answer, context, support, evidences):
    return {"_id": qid, "type": "compositional", "question": f"q {qid}?",
            "answer": answer, "context": context, "supporting_facts": support,
            "evidences": evidences}


_2WIKI_ROWS = [
    # wrapped-quote titles (voidful mirror shape) must still match support titles
    _2wiki_row("2w_a", "Marie",
               [['"X (film)"', ["X is a film.", "X was directed by Zed."]],
                ["Zed", ["Zed is a director.", "Zed's mother is Marie."]],
                ["Distractor", ["noise"]]],
               [['"X (film)"', 1], ["Zed", 1]],
               [["X", "director", "Zed"], ["Zed", "mother", "Marie"]]),
    # a 3-evidence chain -> hop_count 3
    _2wiki_row("2w_b", "Result",
               [["A", ["a"]], ["B", ["b"]], ["C", ["c"]]],
               [["A", 0], ["C", 0]],
               [["A", "r", "B"], ["B", "r", "C"], ["C", "r", "D"]]),
]


def test_2wiki_fetch_normalizes_rows(monkeypatch):
    calls = _stub_datasets(monkeypatch, _2WIKI_ROWS)
    corpus = fetch_2wikimultihop(dataset="x/y", split="validation", max_questions=10, seed=1)
    assert corpus.name == "2wikimultihop"
    assert calls["config"] is None  # 2Wiki mirror has no config
    by_id = {q.id: q for q in corpus.questions}
    qa = by_id["2w_a"]
    assert qa.gold_answer == "Marie"
    assert qa.hop_count == 2  # len(evidences)
    # wrapped-quote support title ("X (film)" -> p0) resolves despite the quotes
    assert qa.gold_supporting_fact_ids == ("2w_a::p0", "2w_a::p1")
    assert by_id["2w_b"].hop_count == 3  # 3-evidence chain
    doc_ids = {d.id for d in corpus.documents}
    assert set(qa.gold_supporting_fact_ids) <= doc_ids


def test_2wiki_load_from_fixture():
    corpus = load_2wikimultihop(path=_FIX / "2wikimultihop_sample.jsonl", max_questions=10)
    assert corpus.name == "2wikimultihop"
    assert len(corpus.questions) == 2
    q0 = corpus.questions[0]
    assert q0.gold_answer == "Marie"
    assert q0.hop_count == 2
    assert q0.gold_supporting_fact_ids == ("2w_1::p0", "2w_1::p1")


def test_wiki_fetch_respects_max_questions_and_is_deterministic(monkeypatch):
    _stub_datasets(monkeypatch, _HOTPOT_ROWS)
    a = fetch_hotpotqa(dataset="x/y", max_questions=1, seed=7)
    b = fetch_hotpotqa(dataset="x/y", max_questions=1, seed=7)
    assert len(a.questions) == 1
    assert [q.id for q in a.questions] == [q.id for q in b.questions]


def test_fetch_without_datasets_raises_pointed_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "datasets", None)
    with pytest.raises(RuntimeError, match="datasets"):
        fetch_hotpotqa(dataset="x/y", max_questions=2)
    with pytest.raises(RuntimeError, match="datasets"):
        fetch_2wikimultihop(dataset="x/y", max_questions=2)
