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

# Answer-time retrieval mode ("hybrid" default since 2026-07-22, measured +169% am /
# +143% judge over "local") is read per-engine in __init__ from GOLDENGRAPH_QA_MODE; there
# is no module-level constant (a prior `_QA_MODE` global was dead -- __init__ reads the env
# directly so a test can monkeypatch it per-construction).

#: Passages retrieved per question in hybrid mode (matches the goldenmatch_rag /
#: text_rag context budget so the comparison is apples-to-apples).
_QA_PASSAGE_K = int(os.environ.get("GOLDENGRAPH_QA_PASSAGE_K", "10"))


def _passage_embed_model() -> str:
    """Passage-retriever embedding model: the local lane's OPENAI_EMBED_MODEL (e.g. nomic-embed-text via
    Ollama) when set, else the OpenAI default. Routes the passage half through the SAME endpoint as the
    chat/graph halves on the local stack, unblocking hybrid mode without OpenAI spend. (Intentional
    duplicate of run_qa_e2e._rag_embed_model -- the engine must not import the CLI module.)"""
    return os.environ.get("OPENAI_EMBED_MODEL") or "text-embedding-3-large"


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
    call (len//4), so the bench owns cost accounting regardless of the client.

    Two counter layers, both fed by every call:
      * global `input_tokens`/`output_tokens` -- the whole-run cumulative totals,
        lock-guarded so the parallel BUILD (ingest_corpus fans extraction across
        worker threads) sums correctly. `build_kg` brackets these before/after.
      * a thread-local per-call accumulator -- read by `answer()` on its own worker
        thread so PARALLEL questions can't cross-contaminate per-question token
        attribution. Before/after deltas on the shared global counter DOUBLE-COUNT
        when questions overlap (an increment landing inside two questions' [before,
        after] windows is charged to both), which would also inflate the summed
        total; the thread-local delta is exact per question and the per-question
        deltas still sum to the true total. See `answer()`.
    """

    def __init__(self, inner: Any):
        self._inner = inner
        self.input_tokens = 0
        self.output_tokens = 0
        self._lock = threading.Lock()
        # Per-thread call accumulator. answer() runs entirely on one pool worker and
        # `ask()` issues its synthesis call synchronously on that same thread, so the
        # thread-local captures exactly this question's tokens.
        self._tl = threading.local()

    def _charge(self, in_tok: int, out_tok: int) -> None:
        with self._lock:
            self.input_tokens += in_tok
            self.output_tokens += out_tok
        self._tl.in_tok = getattr(self._tl, "in_tok", 0) + in_tok
        self._tl.out_tok = getattr(self._tl, "out_tok", 0) + out_tok

    def reset_thread_tokens(self) -> None:
        """Zero THIS thread's per-call accumulator (call before a metered section)."""
        self._tl.in_tok = 0
        self._tl.out_tok = 0

    def thread_tokens(self) -> tuple[int, int]:
        """(input, output) charged on THIS thread since the last reset."""
        return getattr(self._tl, "in_tok", 0), getattr(self._tl, "out_tok", 0)

    def complete(self, prompt: str) -> str:
        # Concurrent build calls share this wrapper; guard the counters (the inner
        # OpenAI client is itself thread-safe for concurrent requests). Retry
        # rate-limit/transient errors so high concurrency doesn't drop documents.
        out = _with_retry(lambda: self._inner.complete(prompt))
        self._charge(max(1, len(prompt) // 4), max(1, len(out) // 4))
        return out

    def complete_json(self, prompt: str) -> str:
        # JSON-constrained extraction path; forward to the inner client's complete_json
        # when present (the OpenAIClient/Ollama path), else fall back to complete.
        fn = getattr(self._inner, "complete_json", self._inner.complete)
        out = _with_retry(lambda: fn(prompt))
        self._charge(max(1, len(prompt) // 4), max(1, len(out) // 4))
        return out


def _build_synthesis_llm(default_llm):
    """The synthesis-stage LLM. When GOLDENGRAPH_SYNTHESIS_MODEL is set, build a SEPARATE client (own
    model + endpoint + key) so a frontier reasoning model handles the ~20 low-volume synthesis calls
    while the cheap 7B keeps the ~400 parallel extractions -- the cascade. Unset -> the extraction llm
    (byte-identical). `openai.OpenAI` + `OpenAIClient` are imported lazily so the unset path adds no deps."""
    model = os.environ.get("GOLDENGRAPH_SYNTHESIS_MODEL") or ""
    if not model:
        return default_llm
    import openai
    from goldengraph.llm import OpenAIClient

    base = os.environ.get("GOLDENGRAPH_SYNTHESIS_BASE_URL") or None
    key = os.environ.get("GOLDENGRAPH_SYNTHESIS_API_KEY") or None
    client = openai.OpenAI(base_url=base, api_key=key) if (base or key) else openai.OpenAI()
    return _CountingLLM(OpenAIClient(model=model, client=client))


class GoldenGraphQAEngine:
    name = "goldengraph"
    fidelity = "real-e2e"
    # answer() is read-only against the built handle (store.as_of() snapshot queries +
    # synthesis; `provenance` is a local set) and, since the _CountingLLM fix, tracks
    # per-question tokens on a thread-local -- so the harness may answer questions in
    # parallel (QA_E2E_ANSWER_WORKERS).
    answer_parallel_safe = True

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
        base_llm = _CountingLLM(llm)
        # Cascade seam: synthesis may use a separate (larger) model; extraction stays
        # on the base client. Build the synth client FIRST, off the UNcached base --
        # answers must reflect the current model, so only the extraction/build client
        # is cached (below). When GOLDENGRAPH_SYNTHESIS_MODEL is unset, synth IS the
        # base client; caching only the build wrapper keeps synthesis uncached even
        # in that shared-object case.
        self._synth_llm = _build_synthesis_llm(base_llm)
        # Prompt-hash LLM response cache for the (network-bound, ~50 min at N=150)
        # extraction build: re-running the QA bench on the same corpus then skips the
        # identical per-document LLM calls. Opt-in + measurement-safe -- the key is the
        # exact prompt, so a cached response IS the model's output for that prompt and
        # can never be stale while the prompt matches; a prompt change misses and
        # repopulates. GOLDENGRAPH_LLM_CACHE unset/empty -> byte-identical (no wrap).
        # The env value is a DIRECTORY (what the workflow's actions/cache persists);
        # the JSON-lines backing file lives inside it.
        self._llm = base_llm
        cache_dir = os.environ.get("GOLDENGRAPH_LLM_CACHE", "").strip()
        if cache_dir:
            from goldengraph.llm import CachingLLMClient

            try:
                os.makedirs(cache_dir, exist_ok=True)
            except OSError:
                pass
            self._llm = CachingLLMClient(
                base_llm, os.path.join(cache_dir, "extraction_cache.jsonl")
            )
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
            else os.environ.get("GOLDENGRAPH_QA_MODE", "hybrid")
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
        # Hybrid mode also indexes the raw paragraphs for answer-time passage retrieval, using the
        # passage-embedding model from `_passage_embed_model()` (OPENAI_EMBED_MODEL -> nomic on the local
        # lane, text-embedding-3-large on the OpenAI lane). Same model as goldenmatch_rag/text_rag on
        # each lane, so the passage half stays comparable; the graph half stays the store's job.
        # Embedding calls here are NOT charged to the engine token budget (parity with text_rag).
        passages = None
        if self._retrieval_mode == "hybrid":
            # Hybrid passage retrieval embeds through the OpenAI-compatible client
            # (OpenAI on the paid lane, a local nomic endpoint on the free lane). The
            # `goldengraph-pipeline` smoke lane runs with a stub LLM/embedder and does
            # NOT install `openai`; a missing package there degrades to passages=None
            # (answer() then falls back to LOCAL synthesis -- the safe no-passages
            # path), rather than crashing the build. Real bench lanes install openai,
            # so they always build the index. The warning keeps a genuinely-absent
            # backend from silently degrading a paid run unnoticed.
            try:
                from .goldenmatch_rag import _OpenAIEmbedderAdapter
                from .text_rag import make_openai_client

                # `make_openai_client()` resolves OPENAI_BASE_URL the guarded way: the paid
                # head_to_head lane sets it to the EMPTY string, which a bare OpenAI() would
                # use verbatim -> protocol-less URL -> every passage embed fails
                # (httpx.UnsupportedProtocol) -> hybrid silently collapses to entity-only.
                adapter = _OpenAIEmbedderAdapter(make_openai_client(), _passage_embed_model())
                passages = _PassageRetriever(
                    [d.id for d in corpus.documents],
                    [d.text for d in corpus.documents],
                    adapter,
                )
            except ImportError:
                import sys as _sys

                print(
                    "goldengraph QA: hybrid mode requested but `openai` is not "
                    "installed -- indexing no passages, answer() falls back to local "
                    "synthesis.",
                    file=_sys.stderr,
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

    def answer(self, handle, question: str, mode: str | None = None) -> AnswerResult:
        from goldengraph.answer import ask

        # Resolve the answer-time retrieval/synth mode, most-specific first:
        #  1. `mode` kwarg -- the local-vs-auto A/B (run_engine_ab) sets it per call.
        #  2. `GOLDENGRAPH_QA_ANSWER_MODE` env -- an ANSWER-time override so the generic
        #     env-A/B (run_engine_ab_env) can flip local-vs-hybrid SYNTHESIS over ONE
        #     shared build. Build the graph in hybrid (GOLDENGRAPH_QA_MODE=hybrid ->
        #     passages indexed once), then A/B `GOLDENGRAPH_QA_ANSWER_MODE:local,hybrid`:
        #     `local` synthesizes over the graph only, `hybrid` layers the passages back
        #     in, holding the build fixed. Empty/unset -> ignored.
        #  3. the engine's configured `self._retrieval_mode` (byte-identical default).
        retrieval_mode = (
            mode or (os.environ.get("GOLDENGRAPH_QA_ANSWER_MODE") or None) or self._retrieval_mode
        )
        # Answer-time `passage_k` override so the generic env-A/B (run_engine_ab_env) can SWEEP how
        # many passages hybrid retrieves per question over ONE shared build (e.g.
        # `GOLDENGRAPH_QA_PASSAGE_K:3,5,10,20`). Read per-call (not just at __init__) so the flip
        # takes effect inside `_env_overrides`; unset/invalid -> the engine's configured
        # `self._passage_k` (byte-identical default). MuSiQue/2wiki docs are paragraph-granular, so
        # this is the retrieval-BREADTH knob (granularity is fixed at one paragraph per Document).
        try:
            passage_k = int(os.environ["GOLDENGRAPH_QA_PASSAGE_K"])
        except (KeyError, ValueError):
            passage_k = self._passage_k
        t0 = time.perf_counter()
        # Per-question token attribution via the synth client's THREAD-LOCAL counter,
        # not a before/after delta on the shared global counter: under parallel
        # answering (QA_E2E_ANSWER_WORKERS) overlapping questions' [before, after]
        # windows would each charge the other's synthesis tokens (double-count),
        # corrupting both per-question tokens AND the summed total. reset->ask->read
        # on this worker thread is exact, and the per-question deltas still sum to the
        # run total. `ask()` issues its synthesis call synchronously on this thread.
        self._synth_llm.reset_thread_tokens()
        # `provenance_out` collects the source-doc ids of every edge the retrieval/traversal touched.
        # The store stamps each edge with its owning document id (ingest doc_ids); intersecting these
        # with the question's gold_supporting_fact_ids is the supporting-fact recall the harness scores.
        provenance: set = set()
        text = ask(
            question,
            handle["store"],
            llm=self._synth_llm,
            embedder=self._embedder,
            valid_t=handle["valid_t"],
            tx_t=handle["tx_t"],
            mode=retrieval_mode,
            hops=self._retrieval_hops,
            node_budget=self._node_budget,
            passages=handle.get("passages"),
            passage_k=passage_k,
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
            input_tokens=self._synth_llm.thread_tokens()[0],
            output_tokens=self._synth_llm.thread_tokens()[1],
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
