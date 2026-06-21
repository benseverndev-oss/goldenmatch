"""QA corpus shapes + loaders. One normalized shape for every corpus an engine
ingests, so the harness and metrics never special-case a source."""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Document:
    id: str
    text: str


@dataclass(frozen=True)
class QAItem:
    id: str
    question: str
    gold_answer: str
    #: Document ids (MuSiQue) OR entity-path ids (engineered) that contain the answer chain.
    gold_supporting_fact_ids: tuple[str, ...]
    hop_count: int
    #: 0.0 when not applicable (MuSiQue); the dial value for the engineered corpus.
    ambiguity_level: float


@dataclass(frozen=True)
class QACorpus:
    name: str
    documents: tuple[Document, ...]
    questions: tuple[QAItem, ...]


def load_musique(
    *, path: str | Path, max_questions: int, hf_split: str | None = None
) -> QACorpus:
    """Load MuSiQue-Ans into the normalized QACorpus shape.

    ``path`` points at a JSONL file (the committed fixture, or a downloaded
    subset). Each *supporting* paragraph becomes a Document keyed
    ``"<question_id>::p<idx>"``; ``hop_count`` = ``len(question_decomposition)``
    (falling back to 2 when absent)."""
    rows = _read_jsonl(Path(path))
    documents: list[Document] = []
    questions: list[QAItem] = []
    for row in rows[:max_questions]:
        qid = str(row["id"])
        support_ids: list[str] = []
        for para in row.get("paragraphs", []):
            doc_id = f"{qid}::p{para['idx']}"
            documents.append(Document(id=doc_id, text=para["paragraph_text"]))
            if para.get("is_supporting"):
                support_ids.append(doc_id)
        hop = len(row.get("question_decomposition", [])) or 2
        questions.append(
            QAItem(
                id=qid,
                question=row["question"],
                gold_answer=str(row["answer"]),
                gold_supporting_fact_ids=tuple(support_ids),
                hop_count=hop,
                ambiguity_level=0.0,
            )
        )
    return QACorpus(name="musique", documents=tuple(documents), questions=tuple(questions))


def _read_jsonl(path: Path) -> Sequence[dict]:
    with path.open("r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
