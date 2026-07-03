"""Extraction-F1 in isolation -- the low-noise instrument for comparing extraction levers.

Runs a chosen extractor over the engineered corpus and scores entity/relation-F1 vs the PLANTED gold
triples (`scorecard_llm.extraction_counts`). Every edge-doc is a data point, so this has far more signal
than end-to-end answer-match (which at small N is dominated by per-question noise). The extractor is
selected exactly as the build does -- `_resolve_extractor()` reads `GOLDENGRAPH_EXTRACTOR`
(api|rebel|gliner) and `_extract` honors `GOLDENGRAPH_EXTRACT_JSON_MODE` -- so this measures the SAME
extraction the pipeline runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

_COUNTERS = ("ent_tp", "ent_fp", "ent_fn", "rel_tp", "rel_fp", "rel_fn")
_REL = ("rel_tp", "rel_fp", "rel_fn")


def _norm(s) -> str:
    # Bridge underscore vs space FIRST: the gold schema labels are underscored (`works_at`) but models
    # emit spaced (`works at`), and metrics._normalize deletes the underscore (-> `worksat`) rather than
    # spacing it -- which would unfairly miss a SEMANTICALLY correct predicate. Replacing `_`->space
    # makes the predicate metric measure real label agreement, not a formatting artifact.
    from . import metrics

    return metrics._normalize(str(s).replace("_", " "))


def predicate_counts(gold_src: str, gold_dst: str, gold_rel: str, extraction) -> dict:
    """PREDICATE-AWARE relation counts: an edge hits only if the entity pair matches AND the predicate
    LABEL matches the gold relation (lenient: normalized equality or substring either way -- so "was
    acquired by" still matches gold "acquired"). This is the metric the predicate-specific multi-hop
    questions actually need; the predicate-AGNOSTIC `extraction_counts` does not capture mislabeling."""
    gold_pair = frozenset({_norm(gold_src), _norm(gold_dst)})
    gr = _norm(gold_rel)
    tp, fp = 0, 0
    for r in extraction.relationships:
        if not (r.subj < len(extraction.mentions) and r.obj < len(extraction.mentions)):
            continue
        pair = frozenset({_norm(extraction.mentions[r.subj].name), _norm(extraction.mentions[r.obj].name)})
        pred = _norm(str(r.predicate))
        hit = pair == gold_pair and (pred == gr or (gr and gr in pred) or (pred and pred in gr))
        if hit and tp == 0:
            tp = 1
        else:
            fp += 1
    return {"rel_tp": tp, "rel_fp": fp, "rel_fn": 1 - tp}


@dataclass
class ExtractionF1:
    label: str
    entity: dict  # {precision, recall, f1}
    relation: dict  # edge-existence (predicate-agnostic)
    n_docs: int
    n_failed: int = 0  # docs whose extraction raised (malformed JSON etc.) -> counted as empty
    relation_pred: dict = field(default_factory=dict)  # predicate-EXACT relation F1


def evaluate_extractor(label: str, *, llm, seed: int = 7, n_questions: int = 80,
                       ambiguity: float = 0.6, max_hops: int = 4) -> ExtractionF1:
    """Extraction-F1 of the env-selected extractor over the engineered corpus vs planted gold.

    `llm` is the LLMClient for the `api` extractor (ignored by rebel/gliner). GOLDENGRAPH_EXTRACTOR /
    GOLDENGRAPH_EXTRACT_JSON_MODE must be set by the caller BEFORE this call (one config per call)."""
    from goldengraph.extract import Extraction
    from goldengraph.extract import extract as _extract
    from goldengraph.ingest import _resolve_extractor

    from .engineered import generate_engineered
    from .scorecard_llm import extraction_counts, f1_from_counts

    extractor = _resolve_extractor()  # None for 'api' (-> _extract); a callable for rebel/gliner
    corpus = generate_engineered(
        seed=seed, n_questions=n_questions, ambiguity=ambiguity, max_hops=max_hops
    )
    et = dict.fromkeys(_COUNTERS, 0)
    ep = dict.fromkeys(_REL, 0)  # predicate-EXACT relation counts
    n_docs = n_failed = 0
    for d in corpus.documents:
        parts = d.id.split("::")
        if len(parts) != 3:
            continue
        gold_rel = parts[1]
        # Fail-soft per doc, exactly like ingest._prepare_doc: a malformed-JSON / errored extraction
        # counts as EMPTY (all FN for this doc -- the honest scoring), not a crashed run. The failure
        # rate is itself signal (JSON-mode should reduce it), so we report it.
        try:
            ex = (extractor or _extract)(d.text, llm)
        except Exception:
            ex = Extraction(mentions=[], relationships=[])
            n_failed += 1
        c = extraction_counts(d.src_surface, d.dst_surface, ex)
        pc = predicate_counts(d.src_surface, d.dst_surface, gold_rel, ex)
        for k in _COUNTERS:
            et[k] += c[k]
        for k in _REL:
            ep[k] += pc[k]
        n_docs += 1
    return ExtractionF1(
        label=label,
        entity=f1_from_counts(et["ent_tp"], et["ent_fp"], et["ent_fn"]),
        relation=f1_from_counts(et["rel_tp"], et["rel_fp"], et["rel_fn"]),
        n_docs=n_docs,
        n_failed=n_failed,
        relation_pred=f1_from_counts(ep["rel_tp"], ep["rel_fp"], ep["rel_fn"]),
    )


def render_md(results, *, model: str) -> str:
    lines = [
        "# Extraction-F1 in isolation (vs planted gold)",
        "",
        f"Engineered corpus, chat model `{model}`. Each edge-doc scored vs its planted `src::rel::dst`",
        "triple (entity = name overlap, relation = edge existence either-direction). This isolates",
        "EXTRACTION from synthesis and is far less noisy than end-to-end answer-match.",
        "",
        "relation-F1 is edge EXISTENCE (predicate-agnostic); relation-F1(pred) requires the predicate",
        "LABEL to match too -- the metric the predicate-specific multi-hop questions actually need.",
        "",
        "| config | entity-F1 | relation-F1 | relation-F1(pred) | docs | parse-fail |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        fail = f"{r.n_failed}/{r.n_docs}" if r.n_docs else "0/0"
        predf1 = r.relation_pred.get("f1", 0.0)
        lines.append(
            f"| {r.label} | {r.entity['f1']:.3f} | {r.relation['f1']:.3f} | {predf1:.3f} "
            f"| {r.n_docs} | {fail} |"
        )
    return "\n".join(lines) + "\n"
