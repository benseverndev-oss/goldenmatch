"""Substrate-quality eval runner (Task 5).

Runs, across an `ambiguity` sweep on the engineered corpus, the two-level substrate measurement:
  Level A -- the resolver in ISOLATION over the gold mentions' surfaces (a clean record set);
  Level B -- the END-TO-END build (extract + resolve over the rendered TEXT) -> the built graph,
             with each gold mention assigned to its built node by the doc's `source_refs`.
The **A-B gap** is the extraction-induced fragmentation -- the construction ceiling as a number. Plus
graph coherence (components) and provenance (source_refs coverage). Emits a scoreboard markdown.

Level A and Level B use the SAME goldengraph resolver, so the gap isolates EXTRACTION (inconsistent
mentions across docs), not resolution. Needs the native `goldengraph_native` store + an LLM -> this is a
Modal/CI run, not box-local (the pure scoring is `erkgbench.substrate_eval`, unit-tested separately).
"""
from __future__ import annotations

import argparse
import os

from erkgbench import substrate_eval
from erkgbench.qa_e2e.engineered import emit_gold_mentions, generate_engineered

#: as-of coordinates large enough to see every appended batch (ingest uses at=i+1). Mirrors the engine.
_AS_OF = 10**12


def _resolver_clusters(gold_mentions) -> list[list[int]]:
    """Level A: cluster the gold mentions' SURFACES with goldengraph's resolver in isolation. Each
    `ResolvedEntity.member_idx` is a list of indices into the mentions list -- exactly the clustering
    over the gold-mention index space that `score_substrate` expects."""
    from goldengraph.extract import Mention
    from goldengraph.resolve import resolve

    mentions = [Mention(name=surface, typ="thing") for (_eid, surface, _doc) in gold_mentions]
    return [list(r.member_idx) for r in resolve(mentions)]


def _build_graph(corpus) -> dict:
    """Level B: run the full build over the rendered text, then return the whole graph as the
    `{entities, edges}` dict `substrate_eval` consumes (edges carry `subj`/`obj`/`source_refs`)."""
    from goldengraph.embed import GoldenmatchEmbedder
    from goldengraph.ingest import ingest_corpus
    from goldengraph.llm import OpenAIClient
    from goldengraph_native import _native as ggn

    store = ggn.PyStore()
    llm = OpenAIClient(model=os.environ.get("OPENAI_MODEL") or "gpt-4o-mini")
    embedder = GoldenmatchEmbedder(provider="openai", model=os.environ.get("OPENAI_EMBED_MODEL") or None)
    ingest_corpus(
        [d.text for d in corpus.documents], store, llm=llm, embedder=embedder,
        doc_ids=[d.id for d in corpus.documents],
    )
    slice_graph = store.as_of(_AS_OF, _AS_OF)
    all_ids = [e["entity_id"] for e in slice_graph.entities()]
    return slice_graph.query(all_ids, 1) if all_ids else {"entities": [], "edges": []}


def run_one(seed: int, ambiguity: float) -> dict:
    """One substrate scoreboard at a given `ambiguity`. Gold comes DIRECTLY off the generated
    Documents (no rng replay), so surfaces match the build by construction. Cooccur OFF -> clean base
    docs (one edge per doc)."""
    os.environ.pop("GOLDENGRAPH_BENCH_COOCCUR", None)
    corpus = generate_engineered(seed=seed, n_questions=1, ambiguity=ambiguity)
    gold = emit_gold_mentions(corpus.documents)
    resolver_clusters = _resolver_clusters(gold)
    graph = _build_graph(corpus)
    return substrate_eval.score_substrate(
        gold_mentions=gold, resolver_clusters=resolver_clusters, graph=graph
    )


def _to_markdown(rows: list[tuple[float, dict]]) -> str:
    head = (
        "# Substrate-Quality Scoreboard\n\n"
        "| ambiguity | ER-F1(A) | ER-F1(B) | P(B) | R(B) | A-B gap | components | largest-frac | provenance |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
    )
    body = "".join(
        f"| {amb} | {sb['er_f1_a']:.4f} | {sb['er_f1_b']:.4f} | {sb['er_p_b']:.4f} | {sb['er_r_b']:.4f} | "
        f"{sb['ab_gap']:.4f} | {sb['components']} | {sb['largest_fraction']:.4f} | {sb['provenance']:.4f} |\n"
        for amb, sb in rows
    )
    note = (
        "\nA = resolver in isolation (clean gold surfaces); B = end-to-end build (extract+resolve over "
        "text). **A-B gap = extraction-induced fragmentation.** The instrument is validated if the gap "
        "widens as ambiguity rises (B drops below A) -- reproducing the construction ceiling as a number.\n"
    )
    return head + body + note


def main() -> None:
    ap = argparse.ArgumentParser(description="Substrate-quality eval: A vs B ER-F1 + coherence + provenance.")
    ap.add_argument("--seed", type=int, default=20260620)
    ap.add_argument("--ambiguity", type=float, nargs="+", default=[0.0, 0.3, 0.6])
    ap.add_argument("--out-md", default="SUBSTRATE.md")
    args = ap.parse_args()

    rows: list[tuple[float, dict]] = []
    for amb in args.ambiguity:
        sb = run_one(args.seed, amb)
        rows.append((amb, sb))
        print(
            f"[substrate] ambiguity={amb}: ER-F1(A)={sb['er_f1_a']:.4f} ER-F1(B)={sb['er_f1_b']:.4f} "
            f"P(B)={sb['er_p_b']:.4f} R(B)={sb['er_r_b']:.4f} gap={sb['ab_gap']:.4f} "
            f"components={sb['components']} provenance={sb['provenance']:.3f}",
            flush=True,
        )
    md = _to_markdown(rows)
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    print("\n" + md, flush=True)


if __name__ == "__main__":
    main()
