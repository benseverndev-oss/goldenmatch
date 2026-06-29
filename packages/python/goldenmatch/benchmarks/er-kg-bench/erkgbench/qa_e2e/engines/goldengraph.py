"""GoldenGraph QA engine adapter: extract -> resolve+ingest -> ask, behind the
QAEngine protocol. LLM, embedder, and resolver are injected (stubs in tests; the
real OpenAIClient + GoldenmatchEmbedder + default goldenmatch resolver in the
opt-in lane). goldengraph + its native PyStore are imported lazily so importing
this module for the registry never drags the wheel."""
from __future__ import annotations

import os
import random
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError

from ..harness import AnswerResult, BuildResult

#: Provider responses worth retrying: rate limit (429) + transient 5xx. A 400 (bad
#: request) is a real error and is re-raised immediately.
_RETRY_CODES = {429, 500, 502, 503, 504}


def _with_retry(fn, *, attempts: int = 6, base: float = 2.0, cap: float = 45.0):
    """Run `fn`, retrying rate-limit/transient provider errors with exponential
    backoff + jitter. This is what lets the build crank concurrency: when the
    OpenAI account's RPM/TPM is hit, callers back off and re-issue instead of the
    fail-soft path silently dropping a document's extraction (which would quietly
    degrade the graph)."""
    for i in range(attempts):
        try:
            return fn()
        except HTTPError as e:
            if getattr(e, "code", None) in _RETRY_CODES and i < attempts - 1:
                time.sleep(min(cap, base**i) + random.random())
                continue
            raise
        except (URLError, TimeoutError):
            if i < attempts - 1:
                time.sleep(min(cap, base**i) + random.random())
                continue
            raise

#: as-of coordinates large enough to see every appended batch (ingest uses at=i+1).
_AS_OF = 10**12

#: Local-retrieval expansion depth. The default-1 ball couldn't reach k-hop answers
#: (the 1->2 hop accuracy cliff in the 2026-06-22 headline). Raised 4->6 (2026-06-23):
#: the N=50 trace showed connected answers (same_component=True) sitting JUST outside
#: the hops=4 ball -- RETRIEVAL-BUDGET misses. Env-tunable for sweeps.
_RETRIEVAL_HOPS = int(os.environ.get("GOLDENGRAPH_QA_RETRIEVAL_HOPS", "6"))

#: Max entities in the budget-capped answer ball (the `ask` node_budget). 256, not
#: the library default 64: the N=50 RETRIEVAL-BUDGET misses had balls of ~64-171
#: entities while the connected answer sat out in the 1300+ "wide" ball -- 64 was
#: starving multi-hop answers. The budget still BOUNDS the synthesis prompt; this
#: trades a bigger (costlier) ball for reach. Env-tunable for the sweep.
_NODE_BUDGET = int(os.environ.get("GOLDENGRAPH_QA_NODE_BUDGET", "256"))

#: Deep, unbudgeted hop count for the localize trace's "wide ball" -- approximates
#: "everything reachable from the seeds" to split a retrieval miss (answer in the
#: graph but outside the budget-capped ball) from a broken-chain miss (answer in
#: the graph but disconnected from the seeds within any reasonable depth).
_WIDE_HOPS = 8

#: Answer-time retrieval mode. "local" (default) = the entity-graph BFS over extracted
#: triples (the historical goldengraph path). "hybrid" = that ball PLUS raw source
#: passages retrieved by goldenmatch's own retrieval surface, fed to synthesis as the
#: ground truth with the graph as a multi-hop map. The bench's structural finding was
#: that the triple-only graph is a LOSSY intermediate (it lost to plain paragraph RAG);
#: hybrid tests whether layering the passages back in -- while keeping the graph for
#: cross-passage bridging -- closes that gap. Env-tunable for the A/B.
_QA_MODE = os.environ.get("GOLDENGRAPH_QA_MODE", "local")

#: Passages retrieved per question in hybrid mode (matches the goldenmatch_rag /
#: text_rag context budget so the comparison is apples-to-apples).
_QA_PASSAGE_K = int(os.environ.get("GOLDENGRAPH_QA_PASSAGE_K", "10"))


class _PassageRetriever:
    """goldenmatch paragraph retrieval over the corpus -- literally the goldenmatch_rag
    retriever (`retrieve_similar_records` + the SAME OpenAI embedder), reused as the
    passage source for goldengraph's hybrid `ask`. The injected embedder caches by
    cache_key, so the corpus embeds once across all questions; each query embeds once.
    Holds NO graph state -- the graph half stays the store's job."""

    def __init__(self, ids, texts, embedder):
        from .goldenmatch_rag import _make_frame

        self._frame = _make_frame(ids, texts)
        self._embedder = embedder

    def retrieve(self, query: str, k: int) -> list[str]:
        from goldenmatch.core.retrieval import retrieve_similar_records

        hits = retrieve_similar_records(
            self._frame, query, "text", k=k, embedder=self._embedder
        )
        return [str(h.record.get("text", "")) for h in hits]


class _CachingEmbedder:
    """Memoizes embeddings by text across the WHOLE run. Two callers re-embed the
    same texts repeatedly: cross-document linking re-embeds the growing entity set
    on every document (O(N^2) at build), and `seed_by_query` re-embeds every entity
    NAME on every question (O(questions x entities) at answer-time). Text -> vector
    is deterministic, so each distinct text embeds exactly once; later docs/queries
    hit the cache. Thread-safe so the parallel build can share one instance."""

    def __init__(self, inner: Any):
        self._inner = inner
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()

    def embed(self, texts):
        import numpy as np

        texts = list(texts)
        with self._lock:
            missing = list(dict.fromkeys(t for t in texts if t not in self._cache))
        if missing:
            vecs = np.asarray(_with_retry(lambda: self._inner.embed(missing)), dtype=float)
            with self._lock:
                for t, v in zip(missing, vecs):
                    self._cache[t] = v
        with self._lock:
            return np.asarray([self._cache[t] for t in texts], dtype=float)


class _CountingLLM:
    """Wraps any goldengraph LLMClient and estimates token usage per .complete
    call (len//4), so the bench owns cost accounting regardless of the client."""

    def __init__(self, inner: Any):
        self._inner = inner
        self.input_tokens = 0
        self.output_tokens = 0
        self._lock = threading.Lock()

    def complete(self, prompt: str) -> str:
        # Concurrent build calls share this wrapper; guard the counters (the inner
        # OpenAI client is itself thread-safe for concurrent requests). Retry
        # rate-limit/transient errors so high concurrency doesn't drop documents.
        out = _with_retry(lambda: self._inner.complete(prompt))
        with self._lock:
            self.input_tokens += max(1, len(prompt) // 4)
            self.output_tokens += max(1, len(out) // 4)
        return out

    def complete_json(self, prompt: str) -> str:
        # JSON-constrained extraction path; forward to the inner client's complete_json
        # when present (the OpenAIClient/Ollama path), else fall back to complete.
        fn = getattr(self._inner, "complete_json", self._inner.complete)
        out = _with_retry(lambda: fn(prompt))
        with self._lock:
            self.input_tokens += max(1, len(prompt) // 4)
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
        retrieval_hops: int | None = None,
        node_budget: int | None = None,
        retrieval_mode: str | None = None,
        passage_k: int | None = None,
    ):
        self._llm = _CountingLLM(llm)
        # Cache embeddings across the whole run: the build's cross-doc linking and
        # answer-time seeding both re-embed the same entity texts many times, which
        # is the O(N^2) network wall at large N.
        self._embedder = _CachingEmbedder(embedder)
        self._resolver = resolver  # None -> ingest uses the goldenmatch-backed default
        # Retrieval budget read at construction (None -> env/default) so a sweep can
        # set GOLDENGRAPH_QA_RETRIEVAL_HOPS / _NODE_BUDGET per run.
        self._retrieval_hops = (
            retrieval_hops if retrieval_hops is not None
            else int(os.environ.get("GOLDENGRAPH_QA_RETRIEVAL_HOPS", "6"))
        )
        self._node_budget = (
            node_budget if node_budget is not None
            else int(os.environ.get("GOLDENGRAPH_QA_NODE_BUDGET", "256"))
        )
        # "local" (entity-graph BFS, default) vs "hybrid" (BFS + goldenmatch passage
        # retrieval fed to synthesis). hybrid builds a passage index at build time.
        self._retrieval_mode = (
            retrieval_mode if retrieval_mode is not None
            else os.environ.get("GOLDENGRAPH_QA_MODE", "local")
        )
        self._passage_k = (
            passage_k if passage_k is not None
            else int(os.environ.get("GOLDENGRAPH_QA_PASSAGE_K", "10"))
        )

    def build_kg(self, corpus) -> BuildResult:
        from goldengraph.ingest import ingest_corpus
        from goldengraph_native import _native as ggn

        t0 = time.perf_counter()
        before_in, before_out = self._llm.input_tokens, self._llm.output_tokens
        store = ggn.PyStore()
        # Persistent record_key -> LLM-fingerprint map for GOLDENGRAPH_PROFILE_LINK:
        # carries each entity's fingerprint across documents (the store doesn't), so
        # a bridge's later appearance can be matched against its earlier fingerprint.
        fp_index: dict[str, str] = {}
        # ingest_corpus parallelizes the per-doc LLM work (extraction + fingerprint
        # synthesis -- the build's dominant, network-bound cost) across documents,
        # committing to the store serially in document order (identical result). The
        # shared caching embedder (self._embedder) embeds each entity text once.
        query_schema = ingest_corpus(
            [doc.text for doc in corpus.documents], store, llm=self._llm,
            resolver=self._resolver, embedder=self._embedder, fp_index=fp_index,
            doc_ids=[doc.id for doc in corpus.documents],  # stamp doc ids onto edges -> support_recall
        )  # the discovered RelationSchema (or None) -> canonicalize QUERY relations through it too
        # Hybrid mode also indexes the raw paragraphs for answer-time passage
        # retrieval. Built with a SEPARATE OpenAI embedder (text-embedding-3-large,
        # matching goldenmatch_rag/text_rag) so the passage half is identical to the
        # standalone goldenmatch_rag engine -- the graph half stays the store's job.
        # Embedding calls here are NOT charged to the engine token budget (parity with
        # text_rag/goldenmatch_rag, which meter only synthesis chat tokens).
        passages = None
        if self._retrieval_mode == "hybrid":
            from openai import OpenAI

            from .goldenmatch_rag import _OpenAIEmbedderAdapter

            adapter = _OpenAIEmbedderAdapter(OpenAI(), "text-embedding-3-large")
            passages = _PassageRetriever(
                [d.id for d in corpus.documents],
                [d.text for d in corpus.documents],
                adapter,
            )
        handle = {
            "store": store, "valid_t": _AS_OF, "tx_t": _AS_OF, "passages": passages,
            "query_schema": query_schema,
        }
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
        # `provenance_out` collects the source-doc ids of every edge the retrieval/traversal touched.
        # The store stamps each edge with its owning document id (ingest doc_ids); intersecting these
        # with the question's gold_supporting_fact_ids is the supporting-fact recall the harness scores.
        provenance: set = set()
        text = ask(
            question,
            handle["store"],
            llm=self._llm,
            embedder=self._embedder,
            valid_t=handle["valid_t"],
            tx_t=handle["tx_t"],
            mode=self._retrieval_mode,
            hops=self._retrieval_hops,
            node_budget=self._node_budget,
            passages=handle.get("passages"),
            passage_k=self._passage_k,
            query_schema=handle.get("query_schema"),
            provenance_out=provenance,
        )
        return AnswerResult(
            text=text,
            # Supporting-fact recall is now wired: `ask(provenance_out=)` returns the source-doc ids
            # of the retrieved/traversed edges (the store stamps each edge with its document id at
            # ingest), which the harness intersects with `gold_supporting_fact_ids`. The co-occurrence
            # corpus renders an edge in several docs (base id + `::N` suffixes); the base id IS the gold
            # id, so the intersection still hits. Empty when retrieval surfaced no stored edge.
            retrieved_fact_ids=tuple(sorted(provenance)),
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
            slice_graph, seeds, max_hops=self._retrieval_hops, node_budget=self._node_budget
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
        retr_edges = [
            f"{id_to_name.get(e['subj'], e['subj'])} -[{e.get('predicate', '')}]-> "
            f"{id_to_name.get(e['obj'], e['obj'])}"
            for e in subgraph.get("edges", ())
        ]

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
            "retrieved_edges": retr_edges,
        }
