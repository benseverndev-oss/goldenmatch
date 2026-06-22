"""GoldenGraph QA engine adapter: extract -> resolve+ingest -> ask, behind the
QAEngine protocol. LLM, embedder, and resolver are injected (stubs in tests; the
real OpenAIClient + GoldenmatchEmbedder + default goldenmatch resolver in the
opt-in lane). goldengraph + its native PyStore are imported lazily so importing
this module for the registry never drags the wheel."""
from __future__ import annotations

import os
import time
from typing import Any

from ..harness import AnswerResult, BuildResult

#: as-of coordinates large enough to see every appended batch (ingest uses at=i+1).
_AS_OF = 10**12

#: Local-retrieval expansion depth. The default-1 ball couldn't reach k-hop answers
#: (the 1->2 hop accuracy cliff in the 2026-06-22 headline); 4 covers the engineered
#: corpus's 1-4 hop range. Overridable for tuning sweeps.
_RETRIEVAL_HOPS = int(os.environ.get("GOLDENGRAPH_QA_RETRIEVAL_HOPS", "4"))

#: Deep, unbudgeted hop count for the localize trace's "wide ball" -- approximates
#: "everything reachable from the seeds" to split a retrieval miss (answer in the
#: graph but outside the budget-capped ball) from a broken-chain miss (answer in
#: the graph but disconnected from the seeds within any reasonable depth).
_WIDE_HOPS = 8


class _CountingLLM:
    """Wraps any goldengraph LLMClient and estimates token usage per .complete
    call (len//4), so the bench owns cost accounting regardless of the client."""

    def __init__(self, inner: Any):
        self._inner = inner
        self.input_tokens = 0
        self.output_tokens = 0

    def complete(self, prompt: str) -> str:
        self.input_tokens += max(1, len(prompt) // 4)
        out = self._inner.complete(prompt)
        self.output_tokens += max(1, len(out) // 4)
        return out


class GoldenGraphQAEngine:
    name = "goldengraph"
    fidelity = "real-e2e"

    def __init__(
        self,
        *,
        llm: Any,
        embedder: Any,
        resolver: Any | None = None,
        retrieval_hops: int = _RETRIEVAL_HOPS,
    ):
        self._llm = _CountingLLM(llm)
        self._embedder = embedder
        self._resolver = resolver  # None -> ingest uses the goldenmatch-backed default
        self._retrieval_hops = retrieval_hops

    def build_kg(self, corpus) -> BuildResult:
        from goldengraph import ingest
        from goldengraph_native import _native as ggn

        t0 = time.perf_counter()
        before_in, before_out = self._llm.input_tokens, self._llm.output_tokens
        store = ggn.PyStore()
        for i, doc in enumerate(corpus.documents):
            ingest(doc.text, store, at=i + 1, llm=self._llm, resolver=self._resolver)
        handle = {"store": store, "valid_t": _AS_OF, "tx_t": _AS_OF}
        return BuildResult(
            handle=handle,
            input_tokens=self._llm.input_tokens - before_in,
            output_tokens=self._llm.output_tokens - before_out,
            latency_s=time.perf_counter() - t0,
        )

    def answer(self, handle, question: str) -> AnswerResult:
        from goldengraph.answer import ask

        t0 = time.perf_counter()
        before_in, before_out = self._llm.input_tokens, self._llm.output_tokens
        text = ask(
            question,
            handle["store"],
            llm=self._llm,
            embedder=self._embedder,
            valid_t=handle["valid_t"],
            tx_t=handle["tx_t"],
            mode="local",
            hops=self._retrieval_hops,
        )
        return AnswerResult(
            text=text,
            retrieved_fact_ids=(),  # see support-recall note in the plan/spec
            input_tokens=self._llm.input_tokens - before_in,
            output_tokens=self._llm.output_tokens - before_out,
            latency_s=time.perf_counter() - t0,
        )

    def localize(self, handle, question: str) -> dict:
        """Diagnostic for the harness trace: replay the retrieval half of `ask`
        (seed -> adaptive ball) and surface the raw material to localize a miss --
        the seed entities, EVERY entity name in the graph, the entity names in the
        retrieved ball, AND the entity names reachable in a WIDE ball (deep,
        unbudgeted neighborhood from the same seeds). The harness checks gold
        containment against graph (extraction) vs wide (reachable-from-seeds) vs
        ball (budget-capped retrieval). NO LLM call -- only embedding-based seeding
        runs, so a trace costs ~nothing beyond the build."""
        from goldengraph.answer import _retrieve_local
        from goldengraph.embed import seed_by_query

        slice_graph = handle["store"].as_of(handle["valid_t"], handle["tx_t"])
        seeds = seed_by_query(slice_graph, question, self._embedder, k=5)
        subgraph = _retrieve_local(
            slice_graph, seeds, max_hops=self._retrieval_hops, node_budget=64
        )
        # Wide ball: deep neighborhood, no node budget -- "is the answer reachable
        # from the seeds at ALL?" Splits a retrieval miss into budget-too-small
        # (in wide, not in ball) vs disconnected/broken-chain (not even in wide).
        wide = slice_graph.query(seeds, _WIDE_HOPS) if seeds else {"entities": []}

        def _names(ents) -> list[str]:
            out: list[str] = []
            for e in ents:
                out.append(e["canonical_name"])
                out.extend(e.get("surface_names", ()))
            return out

        all_ents = slice_graph.entities()
        retr_ents = subgraph.get("entities", [])
        wide_ents = wide.get("entities", [])
        id_to_name = {e["entity_id"]: e["canonical_name"] for e in all_ents}

        # Connected components of the WHOLE graph: query 1-hop from every node to get
        # the full edge set, then union-find. This makes the wide==ball symptom
        # concrete -- if the answer and the question's seeds land in DIFFERENT
        # components, the multi-hop chain is severed (a bridge entity that should
        # connect adjacent paragraphs was never resolved into one node).
        all_ids = [e["entity_id"] for e in all_ents]
        full = slice_graph.query(all_ids, 1) if all_ids else {"edges": []}
        parent = {e["entity_id"]: e["entity_id"] for e in all_ents}

        def _find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for ed in full.get("edges", ()):
            a, b = ed.get("subj"), ed.get("obj")
            if a in parent and b in parent:
                parent[_find(a)] = _find(b)
        comp_members: dict[int, list] = {}
        for e in all_ents:
            comp_members.setdefault(_find(e["entity_id"]), []).append(e)
        components = sorted(comp_members.values(), key=len, reverse=True)
        seed_set = set(seeds)
        seed_component_idx = next(
            (i for i, c in enumerate(components)
             if any(e["entity_id"] in seed_set for e in c)),
            -1,
        )

        return {
            "seed_names": [id_to_name.get(s, str(s)) for s in seeds],
            "graph_names": _names(all_ents),
            "retrieved_names": _names(retr_ents),
            "wide_names": _names(wide_ents),
            "component_names": [_names(c) for c in components],
            "component_sizes": [len(c) for c in components],
            "seed_component_idx": seed_component_idx,
            "n_components": len(components),
            "n_graph_entities": len(all_ents),
            "n_retrieved_entities": len(retr_ents),
            "n_wide_entities": len(wide_ents),
            "n_retrieved_edges": len(subgraph.get("edges", ())),
        }
