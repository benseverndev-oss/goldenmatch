from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

# _BENCH_ROOT bootstrap: make `erkgbench` importable regardless of pytest import mode.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erkgbench.qa_e2e.corpora import Document, QACorpus, QAItem, load_musique  # noqa: E402
from erkgbench.qa_e2e.engineered import generate_engineered  # noqa: E402

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


def test_engineered_is_deterministic_for_a_seed():
    a = generate_engineered(seed=7, n_questions=20, ambiguity=0.5)
    b = generate_engineered(seed=7, n_questions=20, ambiguity=0.5)
    assert [d.text for d in a.documents] == [d.text for d in b.documents]
    assert [(q.id, q.question, q.gold_answer) for q in a.questions] == [
        (q.id, q.question, q.gold_answer) for q in b.questions
    ]


def test_engineered_records_hop_and_ambiguity_and_path():
    c = generate_engineered(seed=1, n_questions=30, ambiguity=0.5, max_hops=4)
    assert c.name == "engineered"
    assert c.questions  # at least some questions were produced
    assert all(1 <= q.hop_count <= 4 for q in c.questions)
    assert all(len(q.gold_supporting_fact_ids) == q.hop_count for q in c.questions)
    assert all(q.ambiguity_level == 0.5 for q in c.questions)


def test_engineered_ambiguity_dial_changes_surface_forms():
    clean = generate_engineered(seed=3, n_questions=40, ambiguity=0.0)
    noisy = generate_engineered(seed=3, n_questions=40, ambiguity=1.0)
    clean_text = " ".join(d.text for d in clean.documents)
    noisy_text = " ".join(d.text for d in noisy.documents)
    assert clean_text != noisy_text
