from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

# _BENCH_ROOT bootstrap: make `erkgbench` importable regardless of pytest import mode.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erkgbench.qa_e2e.corpora import Document, QACorpus, QAItem, load_musique  # noqa: E402

_FIX = (
    Path(__file__).resolve().parent.parent
    / "erkgbench" / "qa_e2e" / "fixtures" / "musique_sample.jsonl"
)


def test_qacorpus_shapes_are_frozen_and_indexable():
    doc = Document(id="d1", text="Acme was founded by Ada.")
    item = QAItem(
        id="q1",
        question="Who founded Acme?",
        gold_answer="Ada",
        gold_supporting_fact_ids=("d1",),
        hop_count=1,
        ambiguity_level=0.0,
    )
    corpus = QACorpus(name="toy", documents=(doc,), questions=(item,))
    assert corpus.name == "toy"
    assert corpus.documents[0].text.startswith("Acme")
    assert corpus.questions[0].hop_count == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        corpus.questions[0].hop_count = 2  # type: ignore[misc]


def test_load_musique_from_fixture():
    corpus = load_musique(path=_FIX, max_questions=10)
    assert corpus.name == "musique"
    assert len(corpus.questions) == 2
    q0 = corpus.questions[0]
    assert q0.gold_answer == "Ada Lovelace"
    assert q0.hop_count == 2  # from question_decomposition length
    assert len(q0.gold_supporting_fact_ids) == 2
    doc_ids = {d.id for d in corpus.documents}
    assert set(q0.gold_supporting_fact_ids) <= doc_ids


def test_load_musique_respects_max_questions():
    corpus = load_musique(path=_FIX, max_questions=1)
    assert len(corpus.questions) == 1
