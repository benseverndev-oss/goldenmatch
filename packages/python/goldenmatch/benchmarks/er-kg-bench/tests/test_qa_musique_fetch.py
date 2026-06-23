"""fetch_musique unit tests -- offline. A stub `datasets` module is injected into
sys.modules so the HuggingFace fetch path is exercised without any network or the
real `datasets` install: we assert the row->corpus normalization, the deterministic
seeded subset, and the import-absent error message."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erkgbench.qa_e2e.corpora import QACorpus, fetch_musique  # noqa: E402


def _row(qid: str, answer: str, n_para: int, n_decomp: int) -> dict:
    paragraphs = [
        {
            "idx": i,
            "title": f"T{i}",
            "paragraph_text": f"{qid} paragraph {i}.",
            "is_supporting": i < n_decomp,  # first n_decomp are supporting
        }
        for i in range(n_para)
    ]
    return {
        "id": qid,
        "question": f"question for {qid}?",
        "answer": answer,
        "paragraphs": paragraphs,
        "question_decomposition": [{"id": j} for j in range(n_decomp)],
    }


_FAKE_ROWS = [
    _row("2hop__a", "Alpha", n_para=4, n_decomp=2),
    _row("3hop__b", "Bravo", n_para=6, n_decomp=3),
    _row("2hop__c", "Charlie", n_para=3, n_decomp=2),
    _row("4hop__d", "Delta", n_para=8, n_decomp=4),
]


@pytest.fixture
def stub_datasets(monkeypatch):
    """Inject a fake `datasets` module whose load_dataset returns _FAKE_ROWS."""
    calls = {}

    def load_dataset(dataset, split):
        calls["dataset"] = dataset
        calls["split"] = split
        return list(_FAKE_ROWS)

    mod = types.ModuleType("datasets")
    mod.load_dataset = load_dataset  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "datasets", mod)
    return calls


def test_fetch_normalizes_rows(stub_datasets):
    corpus = fetch_musique(dataset="x/y", split="validation", max_questions=10, seed=1)
    assert isinstance(corpus, QACorpus)
    assert corpus.name == "musique"
    assert stub_datasets["dataset"] == "x/y"
    assert stub_datasets["split"] == "validation"
    # 4 questions; every paragraph (supporting + distractor) is a Document.
    assert len(corpus.questions) == 4
    assert len(corpus.documents) == 4 + 6 + 3 + 8
    by_id = {q.id: q for q in corpus.questions}
    qa = by_id["2hop__a"]
    assert qa.gold_answer == "Alpha"
    assert qa.hop_count == 2  # len(question_decomposition)
    assert len(qa.gold_supporting_fact_ids) == 2  # only supporting paragraphs
    assert all(fid.startswith("2hop__a::p") for fid in qa.gold_supporting_fact_ids)
    assert by_id["4hop__d"].hop_count == 4
    # gold support ids resolve to real documents
    doc_ids = {d.id for d in corpus.documents}
    assert set(qa.gold_supporting_fact_ids) <= doc_ids


def test_fetch_subset_is_deterministic_for_a_seed(stub_datasets):
    a = fetch_musique(dataset="x/y", split="validation", max_questions=2, seed=20260620)
    b = fetch_musique(dataset="x/y", split="validation", max_questions=2, seed=20260620)
    assert [q.id for q in a.questions] == [q.id for q in b.questions]
    assert len(a.questions) == 2


def test_fetch_subset_varies_with_seed(stub_datasets):
    # Different seeds pick (in general) different subsets/orders from the 4 rows.
    seeds = {
        tuple(
            q.id
            for q in fetch_musique(
                dataset="x/y", split="validation", max_questions=2, seed=s
            ).questions
        )
        for s in range(12)
    }
    assert len(seeds) > 1


def test_fetch_caps_at_available_rows(stub_datasets):
    corpus = fetch_musique(dataset="x/y", split="validation", max_questions=100, seed=1)
    assert len(corpus.questions) == 4  # only 4 rows available, no error


def test_fetch_without_datasets_raises_pointed_error(monkeypatch):
    # Simulate `datasets` not installed: the import inside fetch_musique must fail.
    monkeypatch.setitem(sys.modules, "datasets", None)
    with pytest.raises(RuntimeError, match="datasets"):
        fetch_musique(dataset="x/y", split="validation", max_questions=2)
