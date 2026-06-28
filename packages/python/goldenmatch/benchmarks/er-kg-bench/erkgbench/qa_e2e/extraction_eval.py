"""Extraction-F1 in isolation -- the low-noise instrument for comparing extraction levers.

Runs a chosen extractor over the engineered corpus and scores entity/relation-F1 vs the PLANTED gold
triples (`scorecard_llm.extraction_counts`). Every edge-doc is a data point, so this has far more signal
than end-to-end answer-match (which at small N is dominated by per-question noise). The extractor is
selected exactly as the build does -- `_resolve_extractor()` reads `GOLDENGRAPH_EXTRACTOR`
(api|rebel|gliner) and `_extract` honors `GOLDENGRAPH_EXTRACT_JSON_MODE` -- so this measures the SAME
extraction the pipeline runs.
"""
from __future__ import annotations

from dataclasses import dataclass

_COUNTERS = ("ent_tp", "ent_fp", "ent_fn", "rel_tp", "rel_fp", "rel_fn")


@dataclass
class ExtractionF1:
    label: str
    entity: dict  # {precision, recall, f1}
    relation: dict
    n_docs: int


def evaluate_extractor(label: str, *, llm, seed: int = 7, n_questions: int = 80,
                       ambiguity: float = 0.6, max_hops: int = 4) -> ExtractionF1:
    """Extraction-F1 of the env-selected extractor over the engineered corpus vs planted gold.

    `llm` is the LLMClient for the `api` extractor (ignored by rebel/gliner). GOLDENGRAPH_EXTRACTOR /
    GOLDENGRAPH_EXTRACT_JSON_MODE must be set by the caller BEFORE this call (one config per call)."""
    from goldengraph.extract import extract as _extract
    from goldengraph.ingest import _resolve_extractor

    from .engineered import generate_engineered
    from .scorecard_llm import extraction_counts, f1_from_counts

    extractor = _resolve_extractor()  # None for 'api' (-> _extract); a callable for rebel/gliner
    corpus = generate_engineered(
        seed=seed, n_questions=n_questions, ambiguity=ambiguity, max_hops=max_hops
    )
    et = dict.fromkeys(_COUNTERS, 0)
    n_docs = 0
    for d in corpus.documents:
        if len(d.id.split("::")) != 3:
            continue
        ex = (extractor or _extract)(d.text, llm)
        c = extraction_counts(d.src_surface, d.dst_surface, ex)
        for k in _COUNTERS:
            et[k] += c[k]
        n_docs += 1
    return ExtractionF1(
        label=label,
        entity=f1_from_counts(et["ent_tp"], et["ent_fp"], et["ent_fn"]),
        relation=f1_from_counts(et["rel_tp"], et["rel_fp"], et["rel_fn"]),
        n_docs=n_docs,
    )


def render_md(results, *, model: str) -> str:
    lines = [
        "# Extraction-F1 in isolation (vs planted gold)",
        "",
        f"Engineered corpus, chat model `{model}`. Each edge-doc scored vs its planted `src::rel::dst`",
        "triple (entity = name overlap, relation = edge existence either-direction). This isolates",
        "EXTRACTION from synthesis and is far less noisy than end-to-end answer-match.",
        "",
        "| config | entity-F1 | relation-F1 | docs |",
        "|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.label} | {r.entity['f1']:.3f} | {r.relation['f1']:.3f} | {r.n_docs} |"
        )
    return "\n".join(lines) + "\n"
