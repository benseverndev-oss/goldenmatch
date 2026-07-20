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

#: HotpotQA (distractor setting): the canonical 2-hop real-world multi-hop QA set.
#: `hotpotqa/hotpot_qa` ships as parquet on the Hub; the `distractor` config gives
#: each question 10 context paragraphs (2 supporting + 8 distractor) in the columnar
#: schema {question, answer, type, supporting_facts:{title,sent_id}, context:{title,sentences}}.
HOTPOTQA_HF_DATASET = "hotpotqa/hotpot_qa"
HOTPOTQA_HF_CONFIG = "distractor"
HOTPOTQA_HF_SPLIT = "validation"

#: 2WikiMultiHopQA: 2-4 hop questions over Wikipedia/Wikidata with typed reasoning
#: (comparison / inference / compositional / bridge_comparison). `voidful/2WikiMultihopQA`
#: is a parquet mirror (no legacy dataset script) in the row-wise schema
#: {_id, question, answer, type, context:[[title,[sent...]]], supporting_facts:[[title,sent_id]], evidences:[[s,p,o]]}.
TWOWIKI_HF_DATASET = "voidful/2WikiMultihopQA"
TWOWIKI_HF_SPLIT = "validation"


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


#: -------- HotpotQA + 2WikiMultiHopQA -----------------------------------------
#: Both are 2-4 hop Wikipedia QA sets whose gold support references *paragraph
#: titles*, not paragraph indices. They normalize into the SAME QACorpus shape as
#: MuSiQue: every context paragraph (supporting + distractor) becomes a Document
#: keyed ``"<qid>::p<idx>"`` so the graph ingests the realistic noise, and the gold
#: support is the paragraph-granular doc ids of the supporting titles -- so
#: support_recall is measured identically across all three real corpora.


def _clean_title(title: object) -> str:
    """Normalize a paragraph/support title to a comparison key. Some Hub mirrors
    wrap titles in an extra layer of quotes (voidful/2Wiki renders ``"Maheen Khan"``
    as the literal ``\"Maheen Khan\"``); stripping one layer of wrapping quotes +
    whitespace on BOTH the context side and the support side keeps them matched."""
    s = str(title).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1].strip()
    return s


def _coerce(value: object) -> object:
    """Return a Python object for a field that some mirrors ship as a JSON string
    (e.g. somebody-had-to-do-it/2WikiMultiHopQA) and others as native lists."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value


def _join_sentences(sentences: object) -> str:
    """Join a paragraph's sentences into text. Some mirrors ship the sentence list
    as a native ``list[str]`` (HF hotpot_qa); others (voidful/2Wiki) ship it as a
    JSON-string of that list, or as a single paragraph string. Coerce first, so a
    bare string is used as-is instead of being joined character-by-character."""
    sentences = _coerce(sentences)
    if isinstance(sentences, str):
        return sentences.strip()
    if isinstance(sentences, Iterable):
        return " ".join(str(s) for s in sentences)
    return str(sentences)


def _columnar_or_rows(value: object, keys: Sequence[str]):
    """Yield tuples from a field that may be columnar (``{"title":[...],"sentences":[...]}``,
    the HF hotpot_qa shape) OR row-wise (``[[title, sentences], ...]``). ``keys`` names
    the columnar sub-fields in tuple order."""
    value = _coerce(value)
    if isinstance(value, dict):
        cols = [value.get(k, []) for k in keys]
        yield from zip(*cols)
    elif isinstance(value, Iterable):
        for row in value:
            yield tuple(row)


def _hotpot_paragraphs(row: dict):
    """(qid, [(title, text)], {support_title}, hop_count) for one HotpotQA row."""
    qid = str(row.get("id") or row.get("_id"))
    paragraphs = [
        (_clean_title(title), _join_sentences(sentences))
        for title, sentences in _columnar_or_rows(row.get("context", {}), ("title", "sentences"))
    ]
    support = {
        _clean_title(title)
        for title, _sent_id in _columnar_or_rows(row.get("supporting_facts", {}), ("title", "sent_id"))
    }
    return qid, paragraphs, support, 2  # HotpotQA is 2-hop by construction


def _2wiki_paragraphs(row: dict):
    """(qid, [(title, text)], {support_title}, hop_count) for one 2WikiMultiHopQA row.

    hop_count = len(evidences) (the gold reasoning chain), falling back to 2."""
    qid = str(row.get("_id") or row.get("id"))
    paragraphs = [
        (_clean_title(title), _join_sentences(sentences))
        for title, sentences in _columnar_or_rows(row.get("context", []), ("title", "sentences"))
    ]
    support = {
        _clean_title(item[0])
        for item in _columnar_or_rows(row.get("supporting_facts", []), ("title", "sent_id"))
    }
    hop = len(_coerce(row.get("evidences", []))) or 2
    return qid, paragraphs, support, hop


def _wiki_corpus_from_rows(rows, *, name: str, para_fn, max_questions: int) -> QACorpus:
    """Shared normalizer for title-referenced multi-hop corpora (HotpotQA / 2Wiki).

    Each context paragraph becomes a ``"<qid>::p<idx>"`` Document (matching MuSiQue's
    key scheme); gold support is the paragraph-granular doc ids whose title is a
    supporting title (context order, deduped by paragraph)."""
    documents: list[Document] = []
    questions: list[QAItem] = []
    for row in list(rows)[:max_questions]:
        qid, paragraphs, support_titles, hop = para_fn(row)
        support_ids: list[str] = []
        for idx, (title, text) in enumerate(paragraphs):
            doc_id = f"{qid}::p{idx}"
            documents.append(Document(id=doc_id, text=text))
            if title in support_titles:
                support_ids.append(doc_id)
        questions.append(
            QAItem(
                id=qid,
                question=str(row["question"]),
                gold_answer=str(row["answer"]),
                gold_supporting_fact_ids=tuple(support_ids),
                hop_count=hop,
                ambiguity_level=0.0,
            )
        )
    return QACorpus(name=name, documents=tuple(documents), questions=tuple(questions))


def _fetch_hub_subset(dataset: str, config: str | None, split: str, *, id_key: str,
                      max_questions: int, seed: int) -> list[dict]:
    """Fetch a Hub split and take a stable seeded subset (id-sorted first, so the
    sample is independent of the Hub's row order). Shared by the wiki fetchers; the
    ``datasets`` import is local + pointed on absence (bench-only dependency)."""
    try:
        from datasets import load_dataset
    except ImportError as exc:  # pragma: no cover - exercised in CI install, not unit
        raise RuntimeError(
            "fetch needs the `datasets` package (pip install datasets). It is a "
            "bench-only dependency; the engineered corpus has no such requirement."
        ) from exc
    ds = load_dataset(dataset, config, split=split) if config else load_dataset(dataset, split=split)
    rows = sorted((dict(r) for r in ds), key=lambda r: str(r.get(id_key, "")))
    k = min(max_questions, len(rows))
    return random.Random(seed).sample(rows, k=k)


def load_hotpotqa(*, path: str | Path, max_questions: int) -> QACorpus:
    """Load HotpotQA rows from a JSONL file into the normalized QACorpus shape."""
    rows = _read_jsonl(Path(path))
    return _wiki_corpus_from_rows(rows, name="hotpotqa", para_fn=_hotpot_paragraphs,
                                  max_questions=max_questions)


def fetch_hotpotqa(*, dataset: str = HOTPOTQA_HF_DATASET, config: str | None = HOTPOTQA_HF_CONFIG,
                   split: str = HOTPOTQA_HF_SPLIT, max_questions: int,
                   seed: int = MUSIQUE_SUBSET_SEED) -> QACorpus:
    """Fetch a seeded HotpotQA (distractor) subset on demand from the HuggingFace Hub."""
    subset = _fetch_hub_subset(dataset, config, split, id_key="id",
                               max_questions=max_questions, seed=seed)
    return _wiki_corpus_from_rows(subset, name="hotpotqa", para_fn=_hotpot_paragraphs,
                                  max_questions=max_questions)


def load_2wikimultihop(*, path: str | Path, max_questions: int) -> QACorpus:
    """Load 2WikiMultiHopQA rows from a JSONL file into the normalized QACorpus shape."""
    rows = _read_jsonl(Path(path))
    return _wiki_corpus_from_rows(rows, name="2wikimultihop", para_fn=_2wiki_paragraphs,
                                  max_questions=max_questions)


def fetch_2wikimultihop(*, dataset: str = TWOWIKI_HF_DATASET, split: str = TWOWIKI_HF_SPLIT,
                        max_questions: int, seed: int = MUSIQUE_SUBSET_SEED) -> QACorpus:
    """Fetch a seeded 2WikiMultiHopQA subset on demand from the HuggingFace Hub."""
    subset = _fetch_hub_subset(dataset, None, split, id_key="_id",
                               max_questions=max_questions, seed=seed)
    return _wiki_corpus_from_rows(subset, name="2wikimultihop", para_fn=_2wiki_paragraphs,
                                  max_questions=max_questions)


def _read_jsonl(path: Path) -> Sequence[dict]:
    with path.open("r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
