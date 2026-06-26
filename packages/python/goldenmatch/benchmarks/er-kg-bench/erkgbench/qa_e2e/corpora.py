"""QA corpus shapes + loaders. One normalized shape for every corpus an engine
ingests, so the harness and metrics never special-case a source."""
from __future__ import annotations

import json
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

#: Default real-world MuSiQue-Ans source on the HuggingFace Hub. The canonical
#: MuSiQue release ships as a Google-Drive zip; this mirror is the same
#: MuSiQue-Ans rows in the official schema (id / question / answer / paragraphs /
#: question_decomposition), loadable with `datasets.load_dataset` and no config.
MUSIQUE_HF_DATASET = "dgslibisey/MuSiQue"
MUSIQUE_HF_SPLIT = "validation"
#: Deterministic-subset seed -- shared with the engineered generator so a "seed
#: 20260620" run is reproducible across both corpora.
MUSIQUE_SUBSET_SEED = 20260620


@dataclass(frozen=True)
class Document:
    id: str
    text: str
    #: Engineered edge docs only: the rendered (ambiguity-dialed) surface forms of
    #: the src/dst mentions, so the ER-ablation can assign per-mention record_keys
    #: without re-parsing `text`. Empty for MuSiQue / non-edge docs.
    src_surface: str = ""
    dst_surface: str = ""


@dataclass(frozen=True)
class QAItem:
    id: str
    question: str
    gold_answer: str
    #: Document ids that contain the answer chain (MuSiQue supporting paragraphs;
    #: engineered traversed-edge documents).
    gold_supporting_fact_ids: tuple[str, ...]
    hop_count: int
    #: 0.0 when not applicable (MuSiQue); the dial value for the engineered corpus.
    ambiguity_level: float
    #: Engineered-corpus gold metadata that makes the question answerable + lets a
    #: pure-Python oracle verify it without parsing English (empty for MuSiQue). The
    #: question is "start at `start_entity_id`, follow `relation_chain` in order";
    #: because each (entity, relation) has a unique edge, this determines one answer.
    start_entity_id: str = ""
    relation_chain: tuple[str, ...] = ()


@dataclass(frozen=True)
class QACorpus:
    name: str
    documents: tuple[Document, ...]
    questions: tuple[QAItem, ...]


def load_musique(
    *, path: str | Path, max_questions: int, hf_split: str | None = None
) -> QACorpus:
    """Load MuSiQue-Ans from a JSONL file into the normalized QACorpus shape.

    ``path`` points at a JSONL file (the committed fixture, or a previously
    downloaded subset). For an on-demand HuggingFace fetch (no committed file),
    use :func:`fetch_musique`."""
    rows = _read_jsonl(Path(path))
    return _musique_corpus_from_rows(rows, max_questions=max_questions)


def fetch_musique(
    *,
    dataset: str = MUSIQUE_HF_DATASET,
    split: str = MUSIQUE_HF_SPLIT,
    max_questions: int,
    seed: int = MUSIQUE_SUBSET_SEED,
) -> QACorpus:
    """Fetch MuSiQue-Ans on demand from the HuggingFace Hub and normalize it.

    The full validation split is ~2.4k multi-hop questions (CC-BY-4.0). We fetch
    it on demand -- the corpus is never redistributed in-repo -- and take a
    *seeded* id-sorted subset of ``max_questions`` so a run is deterministic
    without committing a question list. ``datasets`` is an opt-in dependency of
    the bench lane (the engineered corpus needs no network), so the import is
    local and its absence raises a pointed error."""
    try:
        from datasets import load_dataset
    except ImportError as exc:  # pragma: no cover - exercised in CI install, not unit
        raise RuntimeError(
            "fetch_musique needs the `datasets` package (pip install datasets). "
            "The engineered corpus has no such dependency; only the real-world "
            "MuSiQue anchor fetches from the Hub."
        ) from exc

    ds = load_dataset(dataset, split=split)
    # Sort by id first so the seeded sample is independent of the Hub's row order,
    # then sample without replacement for a stable, shuffled subset.
    rows = sorted((dict(r) for r in ds), key=lambda r: str(r["id"]))
    k = min(max_questions, len(rows))
    subset = random.Random(seed).sample(rows, k=k)
    return _musique_corpus_from_rows(subset, max_questions=k)


def _musique_corpus_from_rows(
    rows: Iterable[dict], *, max_questions: int
) -> QACorpus:
    """Normalize MuSiQue-Ans rows (from JSONL or the Hub) into a QACorpus.

    Each paragraph (supporting *and* distractor) becomes a Document keyed
    ``"<question_id>::p<idx>"`` so the graph ingests the realistic noise; only
    *supporting* paragraphs are recorded as gold support. ``hop_count`` =
    ``len(question_decomposition)`` (falling back to 2 when absent). MuSiQue has
    no ambiguity dial, so ``ambiguity_level`` is fixed at 0.0."""
    documents: list[Document] = []
    questions: list[QAItem] = []
    for row in list(rows)[:max_questions]:
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
