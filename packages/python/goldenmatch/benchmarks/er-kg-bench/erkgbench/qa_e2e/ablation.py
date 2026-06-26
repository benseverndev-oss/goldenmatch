"""ER-quality ablation: build a goldengraph store directly from the engineered
gold triples under each resolution dial, oracle-seed retrieval, and measure
bridge-recall by hop. The deterministic, $0, CI-gateable proof of (ER)^hops.

Store-build bypasses ingest_corpus/_extract (which always call the LLM): per edge
document we synthesize an Extraction from the gold triple, attach the dial's
record_key to each mention, build_batch -> store.append. The store merges across
documents by record_key overlap; the dial therefore controls cross-doc identity.

run_ablation needs the goldengraph_native wheel; AblationResult + render_ablation_md
are wheel-free.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import dials
from .engineered import generate_engineered
from .gold import GoldGraph, gold_chain
from .scorecard import bridge_recall

_DIALS = ("oracle", "goldengraph", "name_only", "none")
_KEYFN = {
    "oracle": dials.oracle_keys,
    "goldengraph": dials.goldengraph_keys,
    "name_only": dials.name_only_keys,
    "none": dials.none_keys,
}


@dataclass
class AblationResult:
    # dial -> {"mean": float, "by_hop": {hop:int -> float}}
    recall: dict[str, dict]


def _typ_of(g: GoldGraph) -> dict[str, str]:
    """entity_id -> entity_type, from the concept universe."""
    from dataset.concepts_loader import load_concepts  # type: ignore

    bench_root = Path(__file__).resolve().parents[2]
    return {
        c.canonical_id: c.entity_type
        for c in load_concepts(bench_root / "dataset" / "concepts.jsonl")
    }


def _build_store(corpus, g, km, typ_of):
    """Build a native store from gold triples under a dial's record_key map.
    Returns (slice_graph, coverage: entity_id -> set(canonical_id))."""
    from goldengraph.extract import Extraction, Mention, Relationship
    from goldengraph.ingest import build_batch
    from goldengraph.resolve import ResolvedEntity
    from goldengraph_native import _native as ggn

    from .engines.goldengraph import _AS_OF

    store = ggn.PyStore()
    at = 0
    for d in corpus.documents:
        parts = d.id.split("::")
        if len(parts) != 3:
            continue
        src_id, rel, dst_id = parts
        at += 1
        s_surf, o_surf = d.src_surface, d.dst_surface
        extraction = Extraction(
            mentions=[
                Mention(name=s_surf, typ=typ_of.get(src_id, "concept")),
                Mention(name=o_surf, typ=typ_of.get(dst_id, "concept")),
            ],
            relationships=[Relationship(subj=0, predicate=rel, obj=1)],
        )
        entities = [
            ResolvedEntity(
                local_id=0,
                canonical_name=s_surf,
                typ=typ_of.get(src_id, "concept"),
                surface_names=[s_surf],
                record_keys=[km[(src_id, s_surf)]],
                member_idx=[0],
            ),
            ResolvedEntity(
                local_id=1,
                canonical_name=o_surf,
                typ=typ_of.get(dst_id, "concept"),
                surface_names=[o_surf],
                record_keys=[km[(dst_id, o_surf)]],
                member_idx=[1],
            ),
        ]
        store.append(json.dumps(build_batch(extraction, entities, at=at)))

    slice_graph = store.as_of(_AS_OF, _AS_OF)
    s2c = dials.surface_to_canon(g)
    coverage: dict[int, set] = {}
    for e in slice_graph.entities():
        cov: set = set()
        for s in e.get("surface_names", ()):
            cov |= s2c.get(s, set())
        coverage[e["entity_id"]] = cov
    return slice_graph, coverage


def run_ablation(*, seed: int, n_questions: int, ambiguity: float, max_hops: int = 4) -> AblationResult:
    from goldengraph.answer import _retrieve_local

    from .engines.goldengraph import _NODE_BUDGET, _RETRIEVAL_HOPS

    corpus = generate_engineered(
        seed=seed, n_questions=n_questions, ambiguity=ambiguity, max_hops=max_hops
    )
    g = GoldGraph.from_corpus(corpus)
    typ_of = _typ_of(g)
    chains = {qa.id: gold_chain(g, qa) for qa in corpus.questions}

    recall: dict[str, dict] = {}
    for dial in _DIALS:
        km = _KEYFN[dial](corpus, g)
        slice_graph, coverage = _build_store(corpus, g, km, typ_of)
        # invert coverage: canonical -> the (deterministic) store node to seed from
        seed_of: dict[str, int] = {}
        for nid in sorted(coverage):  # ascending id => deterministic tie-break
            for c in coverage[nid]:
                seed_of.setdefault(c, nid)

        whole: list[float] = []
        by_hop: dict[int, list[float]] = {}
        for qa in corpus.questions:
            seed_node = seed_of.get(qa.start_entity_id)
            if seed_node is None:
                br = {"whole_chain": 0.0, "edge_recall": 0.0}
            else:
                subgraph = _retrieve_local(
                    slice_graph, [seed_node], max_hops=_RETRIEVAL_HOPS, node_budget=_NODE_BUDGET
                )
                br = bridge_recall(chains[qa.id], subgraph, coverage)
            whole.append(br["whole_chain"])
            by_hop.setdefault(qa.hop_count, []).append(br["whole_chain"])

        recall[dial] = {
            "mean": (sum(whole) / len(whole)) if whole else 0.0,
            "by_hop": {h: (sum(v) / len(v)) for h, v in sorted(by_hop.items())},
        }
    return AblationResult(recall=recall)


# --- assertions + markdown (wheel-free) ---


def evaluate_assertions(res: AblationResult) -> list[tuple[str, bool, bool]]:
    """[(label, passed, is_hard), ...]. Hard failures gate; soft only warn."""
    r = res.recall
    means = {d: r[d]["mean"] for d in _DIALS}
    tol = 1e-9
    monotonic = (
        means["oracle"] + tol >= means["goldengraph"] >= means["name_only"] - tol
        and means["name_only"] + tol >= means["none"]
    )

    def gap(h):
        return r["oracle"]["by_hop"].get(h, 0.0) - r["none"]["by_hop"].get(h, 0.0)

    hops = sorted(r["oracle"]["by_hop"])
    widen = bool(hops) and gap(hops[-1]) > gap(hops[0])
    resolver_edge = means["goldengraph"] + tol >= means["name_only"]
    return [
        ("monotonic in ER quality (oracle>=goldengraph>=name_only>=none)", monotonic, True),
        ("oracle-none gap widens with hops", widen, True),
        ("resolver earns its keep (goldengraph>=name_only)", resolver_edge, False),
    ]


def render_ablation_md(res: AblationResult) -> str:
    r = res.recall
    hops = sorted(r["oracle"]["by_hop"])
    lines = [
        "# GoldenGraph ER-quality ablation -- bridge-recall (no LLM)",
        "",
        "Whole-chain bridge-recall: can the resolved+retrieved subgraph WALK the gold",
        "answer chain? The (ER_accuracy)^hops thesis at the retrieval layer.",
        "",
        "| dial | mean | " + " | ".join(f"{h}-hop" for h in hops) + " |",
        "|---|---|" + "---|" * len(hops),
    ]
    for d in _DIALS:
        cells = " | ".join(f"{r[d]['by_hop'].get(h, 0.0):.3f}" for h in hops)
        lines.append(f"| {d} | {r[d]['mean']:.3f} | {cells} |")
    lines += ["", "## verdicts", ""]
    for label, passed, is_hard in evaluate_assertions(res):
        tag = "PASS" if passed else ("FAIL" if is_hard else "WARN")
        lines.append(f"- [{tag}] {label}{'' if is_hard else ' (soft)'}")
    return "\n".join(lines) + "\n"
