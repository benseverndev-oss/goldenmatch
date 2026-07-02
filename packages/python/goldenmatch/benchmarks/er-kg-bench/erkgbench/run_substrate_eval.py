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


def _build_graph_from_documents(documents) -> dict:
    """Level B: run the full build over a list of `Document`s, then return the whole graph as the
    `{entities, edges}` dict `substrate_eval` consumes (edges carry `subj`/`obj`/`source_refs`)."""
    from goldengraph.embed import GoldenmatchEmbedder
    from goldengraph.ingest import ingest_corpus
    from goldengraph.llm import OpenAIClient
    from goldengraph_native import _native as ggn

    store = ggn.PyStore()
    llm = OpenAIClient(model=os.environ.get("OPENAI_MODEL") or "gpt-4o-mini")
    embedder = GoldenmatchEmbedder(provider="openai", model=os.environ.get("OPENAI_EMBED_MODEL") or None)
    # Diagnostic lever: GOLDENGRAPH_SUBSTRATE_RESOLVER=exact swaps the fuzzy dedupe for exact (name,typ)
    # resolution -> zero within-doc over-merge. If edge_recall/R(B) jump under exact, fuzzy over-merge was
    # collapsing src+dst into dropped self-loops; if flat, the loss is pure extraction drop. Default fuzzy.
    resolver = None
    if os.environ.get("GOLDENGRAPH_SUBSTRATE_RESOLVER", "").strip().lower() == "exact":
        from goldengraph.resolve import _exact_resolve
        resolver = _exact_resolve
    ingest_corpus(
        [d.text for d in documents], store, llm=llm, embedder=embedder,
        doc_ids=[d.id for d in documents], resolver=resolver,
    )
    slice_graph = store.as_of(_AS_OF, _AS_OF)
    all_ids = [e["entity_id"] for e in slice_graph.entities()]
    return slice_graph.query(all_ids, 1) if all_ids else {"entities": [], "edges": []}


def _build_graph(corpus) -> dict:
    return _build_graph_from_documents(corpus.documents)


def _wiki_build():
    """Load the wiki corpus and build the graph with the current env config. Returns
    (documents, gold, qid_aliases, graph) so both run_wiki and the GLiNER probe reuse it."""
    from erkgbench.qa_e2e.wiki_corpus import load_wiki_corpus

    documents, gold, qid_aliases = load_wiki_corpus()
    graph = _build_graph_from_documents(documents)
    return documents, gold, qid_aliases, graph


def run_wiki() -> dict:
    """Level 2: build over REAL Wikipedia prose (committed snapshot), align gold to nodes by SURFACE+DOC
    (no engineered doc-id oracle), and score R(B)/P(B) + alignment coverage. Baseline-vs-`name_ci` is
    selected by `GOLDENGRAPH_XDOC_KEY` as usual. No ambiguity dial -- real prose has its own variance."""
    from erkgbench import metrics

    documents, gold, qid_aliases, graph = _wiki_build()
    clustering = substrate_eval.align_real_mentions_to_nodes_aliased(graph, gold, qid_aliases)
    coverage = substrate_eval.real_alignment_coverage_aliased(graph, gold, qid_aliases)
    b = metrics.score([m[0] for m in gold], clustering)
    coh = substrate_eval.graph_coherence(graph)
    return {"er_r_b": b.recall, "er_p_b": b.precision, "er_f1_b": b.f1, "coverage": coverage,
            "n_docs": len(documents), "n_gold": len(gold), "components": coh["components"]}


def _gliner_by_doc(documents, *, threshold: float) -> dict:
    """Run GLiNER per-doc (whole lead), returning {base_doc_id: set(entity surfaces)}. GLiNER loads once."""
    from goldengraph.extract_local import gliner_extractor

    from erkgbench.substrate_eval import _base_doc_id

    extractor = gliner_extractor(threshold=threshold)
    out: dict[str, set[str]] = {}
    for d in documents:
        ex = extractor(d.text)
        out[_base_doc_id(d.id)] = {m.name for m in ex.mentions}
    return out


def run_wiki_gliner_probe() -> dict:
    """GLiNER entity-recall probe: build best-config graph, run GLiNER per-doc, report NER-addressable
    recovery of the residual. Threshold from GOLDENGRAPH_GLINER_THRESHOLD (default 0.4)."""
    documents, gold, qid_aliases, graph = _wiki_build()
    threshold = float(os.environ.get("GOLDENGRAPH_GLINER_THRESHOLD", "0.4") or "0.4")
    try:
        gbd = _gliner_by_doc(documents, threshold=threshold)
    except Exception as e:  # noqa: BLE001 -- fail-soft: still report the LLM baseline
        print(f"[gliner-probe] GLiNER failed ({e!r}); reporting empty gliner_by_doc", flush=True)
        gbd = {}
    r = substrate_eval.gliner_probe_report(graph, gold, qid_aliases, gbd)
    r.update(n_docs=len(documents), threshold=threshold)
    return r


def run_wiki_presence_probe() -> dict:
    """Presence-aligner diagnostic: build the best-config graph, report strict vs relaxed alignment
    (coverage / R(B) / P(B)). Quantifies how much of the coverage ceiling is edgeless-but-present."""
    documents, gold, qid_aliases, graph = _wiki_build()
    r = substrate_eval.presence_aligner_report(graph, gold, qid_aliases)
    r.update(n_docs=len(documents))
    return r


def run_one(seed: int, ambiguity: float) -> dict:
    """One substrate scoreboard at a given `ambiguity`. Gold comes DIRECTLY off the generated
    Documents (no rng replay), so surfaces match the build by construction. Cooccur OFF -> clean base
    docs (one edge per doc)."""
    os.environ.pop("GOLDENGRAPH_BENCH_COOCCUR", None)
    corpus = generate_engineered(seed=seed, n_questions=1, ambiguity=ambiguity)
    gold = emit_gold_mentions(corpus.documents)
    resolver_clusters = _resolver_clusters(gold)
    graph = _build_graph(corpus)
    if os.environ.get("GOLDENGRAPH_SUBSTRATE_FRAG", "") not in ("", "0", "false"):
        fr = substrate_eval.fragmentation_report(graph, gold)
        print(
            f"[frag] ambiguity={ambiguity}: mean_nodes/entity={fr['mean_nodes_per_entity']:.3f} "
            f"max={fr['max_nodes_per_entity']} fragmented={fr['fragmented_entities']}/{fr['total_entities']} "
            f"name_jitter={fr['name_jitter_frac']:.3f} type_jitter={fr['type_jitter_frac']:.3f} "
            f"identical={fr['identical_frac']:.3f}",
            flush=True,
        )
        for eid, k, nts in fr["worst"]:
            print(f"[frag]   {eid}: {k} nodes -> {nts}", flush=True)
    return substrate_eval.score_substrate(
        gold_mentions=gold, resolver_clusters=resolver_clusters, graph=graph
    )


def _to_markdown(rows: list[tuple[float, dict]]) -> str:
    head = (
        "# Substrate-Quality Scoreboard\n\n"
        "| ambiguity | ER-F1(A) | ER-F1(B) | P(B) | R(B) | edge-recall | A-B gap | components | largest-frac | provenance |\n"
        "|---|---|---|---|---|---|---|---|---|---|\n"
    )
    body = "".join(
        f"| {amb} | {sb['er_f1_a']:.4f} | {sb['er_f1_b']:.4f} | {sb['er_p_b']:.4f} | {sb['er_r_b']:.4f} | "
        f"{sb['edge_recall']:.4f} | {sb['ab_gap']:.4f} | {sb['components']} | {sb['largest_fraction']:.4f} | "
        f"{sb['provenance']:.4f} |\n"
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
    ap.add_argument("--corpus", choices=["engineered", "wiki"], default="engineered")
    ap.add_argument("--out-md", default="SUBSTRATE.md")
    ap.add_argument("--gliner-probe", action="store_true",
                    help="run the GLiNER entity-recall probe instead of the plain wiki eval")
    ap.add_argument("--presence-probe", action="store_true",
                    help="run the strict-vs-relaxed presence-aligner diagnostic")
    args = ap.parse_args()

    _probe = args.gliner_probe or os.environ.get("GOLDENGRAPH_GLINER_PROBE", "") not in ("", "0", "false")
    if args.corpus == "wiki" and _probe:
        r = run_wiki_gliner_probe()
        print(
            f"[gliner-probe] thr={r['threshold']} gliner_recall={r['gliner_recall']:.4f} "
            f"llm_coverage={r['llm_coverage']:.4f} n_missed={r['n_missed']} "
            f"ner_miss={r['n_ner_miss']} edge_miss={r['n_edge_miss']} "
            f"NER_recovered={r['ner_recovered_frac']:.4f} residual_recovered={r['residual_recovered_frac']:.4f} "
            f"junk_rate={r['junk_rate']:.4f}",
            flush=True,
        )
        md = (
            "# GLiNER Entity-Recall Probe (wiki)\n\n"
            "| threshold | gliner_recall | llm_coverage | n_missed | ner_miss | edge_miss | "
            "NER_recovered | residual_recovered | junk_rate |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            f"| {r['threshold']} | {r['gliner_recall']:.4f} | {r['llm_coverage']:.4f} | {r['n_missed']} | "
            f"{r['n_ner_miss']} | {r['n_edge_miss']} | {r['ner_recovered_frac']:.4f} | "
            f"{r['residual_recovered_frac']:.4f} | {r['junk_rate']:.4f} |\n\n"
            "NER_recovered = of the NER-miss gold (entity absent from the graph), share GLiNER surfaces. "
            "residual_recovered conflates NER-miss + edge-miss (context only). junk_rate is inflated by "
            "wikilink-only gold.\n"
        )
        with open(args.out_md, "w", encoding="utf-8") as fh:
            fh.write(md)
        print("\n" + md, flush=True)
        return

    _presence = (args.presence_probe
                 or os.environ.get("GOLDENGRAPH_PRESENCE_PROBE", "") not in ("", "0", "false"))
    if args.corpus == "wiki" and _presence:
        r = run_wiki_presence_probe()
        print(
            f"[presence-probe] strict_cov={r['strict_coverage']:.4f} relaxed_cov={r['relaxed_coverage']:.4f} "
            f"strict_pb={r['strict_pb']:.4f} relaxed_pb={r['relaxed_pb']:.4f} "
            f"strict_rb={r['strict_rb']:.4f} relaxed_rb={r['relaxed_rb']:.4f} "
            f"strict_fb={r['strict_fb']:.4f} relaxed_fb={r['relaxed_fb']:.4f}",
            flush=True,
        )
        md = (
            "# Presence-Aligner Probe (wiki)\n\n"
            "| axis | strict (edge) | relaxed (global surface) |\n"
            "|---|---|---|\n"
            f"| coverage | {r['strict_coverage']:.4f} | {r['relaxed_coverage']:.4f} |\n"
            f"| R(B) | {r['strict_rb']:.4f} | {r['relaxed_rb']:.4f} |\n"
            f"| P(B) | {r['strict_pb']:.4f} | {r['relaxed_pb']:.4f} |\n"
            f"| F1(B) | {r['strict_fb']:.4f} | {r['relaxed_fb']:.4f} |\n\n"
            "relaxed reaches edgeless-but-present nodes globally (any doc). A P(B) drop means "
            "more-aligned-with-some-collisions, not pure error -- the two P columns are over different "
            "pair populations.\n"
        )
        with open(args.out_md, "w", encoding="utf-8") as fh:
            fh.write(md)
        print("\n" + md, flush=True)
        return

    if args.corpus == "wiki":
        r = run_wiki()
        print(
            f"[substrate-wiki] R(B)={r['er_r_b']:.4f} P(B)={r['er_p_b']:.4f} F1(B)={r['er_f1_b']:.4f} "
            f"coverage={r['coverage']:.4f} docs={r['n_docs']} gold={r['n_gold']} components={r['components']}",
            flush=True,
        )
        md = (
            "# Substrate-Quality (real Wikipedia prose)\n\n"
            "| R(B) | P(B) | F1(B) | coverage | docs | gold | components |\n"
            "|---|---|---|---|---|---|---|\n"
            f"| {r['er_r_b']:.4f} | {r['er_p_b']:.4f} | {r['er_f1_b']:.4f} | {r['coverage']:.4f} | "
            f"{r['n_docs']} | {r['n_gold']} | {r['components']} |\n\n"
            "Real Wikipedia lead-section prose; gold = wikilink->QID; nodes aligned by surface+doc. "
            "coverage = fraction of gold mentions aligned to a built node (low coverage => alignment noise, "
            "not resolution).\n"
        )
        with open(args.out_md, "w", encoding="utf-8") as fh:
            fh.write(md)
        print("\n" + md, flush=True)
        return

    rows: list[tuple[float, dict]] = []
    for amb in args.ambiguity:
        sb = run_one(args.seed, amb)
        rows.append((amb, sb))
        print(
            f"[substrate] ambiguity={amb}: ER-F1(A)={sb['er_f1_a']:.4f} ER-F1(B)={sb['er_f1_b']:.4f} "
            f"P(B)={sb['er_p_b']:.4f} R(B)={sb['er_r_b']:.4f} edge_recall={sb['edge_recall']:.4f} "
            f"gap={sb['ab_gap']:.4f} components={sb['components']} provenance={sb['provenance']:.3f}",
            flush=True,
        )
    md = _to_markdown(rows)
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    print("\n" + md, flush=True)


if __name__ == "__main__":
    main()
