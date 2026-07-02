"""SP-B2 substrate staged tuner.

A deterministic staged-ejection optimizer: build a config on a cheap gold slice, score it with the
SP-A three-axis scorecard (erkgbench.substrate_eval.substrate_scorecard), escalate the config along
the failing axis, and promote the best-so-far config to the full build. The real build is injected
(build_and_score) so the whole control flow is pure + box-testable; build_and_score_real wires the
actual ingest_corpus + scorecard (Modal-smoke only). See
docs/superpowers/specs/2026-07-02-substrate-staged-tuner-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from goldengraph.config import SubstrateConfig, for_profile, profile_corpus

from erkgbench.substrate_eval import LEVER_AXIS_MAP

_REFUTED = ("relation_reprompt", "rebel_fuse", "extract_recall")


@dataclass(frozen=True)
class GateThresholds:
    """Per-axis pass floors. Hypotheses tuned by running the harness; env-overridable by the caller."""
    presence_min: float = 0.90
    relational_f1_min: float = 0.50


@dataclass(frozen=True)
class GateResult:
    passed: bool
    failing_axis: str | None
    scorecard: dict


def evaluate_gate(scorecard: dict, thresholds: GateThresholds) -> GateResult:
    """Presence before relational (can't fix relational quality on absent entities). Presence is
    skipped when the scorecard's presence is None (engineered/no-alias path)."""
    presence = scorecard.get("presence")
    if presence is not None and presence["coverage"] < thresholds.presence_min:
        return GateResult(False, "presence", scorecard)
    if scorecard["relational"]["f1"] < thresholds.relational_f1_min:
        return GateResult(False, "relational", scorecard)
    return GateResult(True, None, scorecard)


# --- escalation: per-lever next-state advances (return (next_value_repr, new_config) | None) ----------
def _advance_chunk_extract(c):
    if c.chunk_extract:
        return None
    return "True", replace(c, chunk_extract=True, chunk_sentences=6, chunk_overlap=2)


def _advance_extractor(c):
    if c.extractor != "api":
        return None
    return "gliner", replace(c, extractor="gliner")


def _advance_xdoc_key(c):
    nxt = {"": "name_ci", "name_ci": "name_ci_type"}.get(c.xdoc_key)
    if nxt is None:
        return None
    if nxt == "name_ci_type":
        return nxt, replace(c, xdoc_key=nxt, entity_type_canon=True)
    return nxt, replace(c, xdoc_key=nxt)


def _advance_entity_type_canon(c):
    if c.entity_type_canon:
        return None
    return "True", replace(c, entity_type_canon=True)


def _advance_schema_canon(c, relation_vocab):
    if c.schema_canon or not relation_vocab:
        return None
    return "True", replace(c, schema_canon=True, relation_vocab=tuple(relation_vocab))


#: lever -> its _advance. Refuted levers + relation_vocab (no self-ladder) are absent -> never advanced.
_ADVANCERS = {
    "chunk_extract": lambda c, v: _advance_chunk_extract(c),
    "extractor": lambda c, v: _advance_extractor(c),
    "xdoc_key": lambda c, v: _advance_xdoc_key(c),
    "entity_type_canon": lambda c, v: _advance_entity_type_canon(c),
    "schema_canon": lambda c, v: _advance_schema_canon(c, v),
}


def escalate(config, failing_axis, tried, *, relation_vocab=(), allow_refuted=False):
    """First eligible next-state advance along LEVER_AXIS_MAP[failing_axis]. Returns (lever, new_config)
    and records (lever, next_value_repr) in `tried` (sole mutator), or None when the axis is exhausted."""
    for lever in LEVER_AXIS_MAP.get(failing_axis, ()):
        if lever in _REFUTED and not allow_refuted:
            continue
        adv = _ADVANCERS.get(lever)
        if adv is None:
            continue
        step = adv(config, relation_vocab)
        if step is None:
            continue
        next_repr, new_config = step
        if (lever, next_repr) in tried:
            continue
        tried.add((lever, next_repr))
        return lever, new_config
    return None


@dataclass(frozen=True)
class RoundReport:
    round: int
    config: SubstrateConfig
    scorecard: dict
    gate: GateResult
    escalated_to: str | None


@dataclass(frozen=True)
class TunerResult:
    config: SubstrateConfig
    slice_scorecard: dict
    full_scorecard: dict
    trace: list = field(default_factory=list)
    stopped_reason: str = ""


def _score(scorecard: dict) -> float:
    """Round-ranking scalar: relational.f1 (+ presence.coverage when present). Higher wins."""
    s = scorecard["relational"]["f1"]
    presence = scorecard.get("presence")
    if presence is not None:
        s += presence["coverage"]
    return s


def run_staged(docs, gold, qid_aliases, *, build_and_score, thresholds=None, budget=5,
               relation_vocab=(), expect_homographs=False, has_known_schema=False,
               initial_config=None):
    """Slice-gated deterministic optimizer. Escalates on the failing axis up to `budget` rounds, then
    promotes the best-so-far (argmax _score) config to a full build. `build_and_score(config, dataset)`
    is the injection seam (dataset = (docs, gold, qid_aliases)). Raises ValueError on budget < 1."""
    if budget < 1:
        raise ValueError(f"budget must be >= 1, got {budget}")
    thresholds = thresholds or GateThresholds()
    if initial_config is None:
        initial_config = for_profile(
            profile_corpus(docs), has_known_schema=has_known_schema,
            expect_homographs=expect_homographs, relation_vocab=relation_vocab,
        )
    dataset = (docs, gold, qid_aliases)
    config = initial_config
    tried: set = set()
    rounds: list[RoundReport] = []
    stopped = "budget"
    for r in range(budget):
        sc = build_and_score(config, dataset)
        gate = evaluate_gate(sc, thresholds)
        step = None if gate.passed else escalate(config, gate.failing_axis, tried,
                                                 relation_vocab=relation_vocab)
        escalated_to, next_config = (None, None) if step is None else step
        rounds.append(RoundReport(r, config, sc, gate, escalated_to))
        if gate.passed:
            stopped = "passed"
            break
        if step is None:
            stopped = "exhausted"
            break
        config = next_config
    best = max(rounds, key=lambda rr: _score(rr.scorecard))
    full_sc = build_and_score(best.config, dataset)
    return TunerResult(best.config, best.scorecard, full_sc, rounds, stopped)


def build_and_score_real(config, dataset):
    """Real build_and_score: under config.apply(), build the graph over the dataset docs and score it
    with the SP-A three-axis scorecard. Needs the native store + an LLM -> Modal/CI only, NOT box-safe.
    Kept thin; the pure harness above is the tested surface."""
    from erkgbench import substrate_eval
    from erkgbench.run_substrate_eval import _build_graph_from_documents

    docs, gold, qid_aliases = dataset
    if not docs:
        raise ValueError("build_and_score_real: empty docs")

    class _Doc:  # _build_graph_from_documents wants .text/.id; wrap raw text strings
        __slots__ = ("text", "id")

        def __init__(self, text, doc_id):
            self.text, self.id = text, doc_id

    with config.apply():
        documents = docs if hasattr(docs[0], "text") else [_Doc(t, f"d{i}") for i, t in enumerate(docs)]
        graph = _build_graph_from_documents(documents)
    return substrate_eval.substrate_scorecard(graph, gold, qid_aliases)
