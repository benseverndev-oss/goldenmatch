"""Slice 4c GoldenGraph facade + the ER ExecutionPlan surface.

`plan_er_execution` surfaces the goldenmatch controller-v3 ExecutionPlan the ER controller WOULD pick
for the aggregate corpus ER workload at this scale. ADVISORY: the per-doc build resolver still runs
small per document; this plan is the scale SIGNAL (flip to a batched/distributed resolution path at
scale), not yet consumed by the build. goldenmatch is imported lazily so the package imports without it.
"""
from __future__ import annotations

_AVG_ENTITIES_PER_DOC = 8  # rough heuristic when the caller gives no corpus_records


def _estimate_records(docs) -> int:
    return _AVG_ENTITIES_PER_DOC * sum(1 for _ in docs)


def _representative_complexity(n_rows_full: int):
    # The DEFAULT_RULES scale predicates key off n_rows_full + runtime.available_ram_gb (verified in
    # autoconfig_planner_rules: simple/fast_box/bucket/chunked thresholds), NOT the data-shape sub-
    # profiles -- so a default ComplexityProfile is sufficient and n_rows_full drives the separation.
    from goldenmatch.core.complexity_profile import ComplexityProfile

    return ComplexityProfile()


def plan_er_execution(docs, *, corpus_records: int | None = None):
    """Return the ER controller's scale ExecutionPlan for this corpus (ADVISORY -- the build does not
    consume it; it is the signal to flip to a batched/distributed resolution path at scale)."""
    n_rows_full = corpus_records if corpus_records is not None else _estimate_records(docs)
    from goldenmatch.core.autoconfig_planner import apply_planner_rules

    # Import DEFAULT_RULES directly (not the in-module _default_rules() wrapper, which exists only to
    # break goldenmatch's OWN import cycle and is absent on older goldenmatch builds). From outside
    # goldenmatch there is no cycle, and this resolves on both current and older installs.
    from goldenmatch.core.autoconfig_planner_rules import DEFAULT_RULES
    from goldenmatch.core.runtime_profile import capture_runtime_profile

    runtime = capture_runtime_profile()
    profile = _representative_complexity(n_rows_full)
    return apply_planner_rules(profile, runtime, n_rows_full, DEFAULT_RULES)


def _new_store():
    # One gated entry point: the loader honors GOLDENGRAPH_NATIVE and finds either
    # the in-tree build or the goldengraph-native wheel (native wheel: e2e lane).
    from .core._native_loader import new_store

    return new_store()


class GoldenGraph:
    """One stateful entry point: profile a workload -> tier-resolver build (4a/4b) -> auto answers
    (slices 1/2/3), with the ER scale plan surfaced (`execution_plan`, ADVISORY) and ONE `budget`
    threaded across build + answer LLM work (the cross-controller ceiling)."""

    def __init__(self, *, store, plan, execution_plan, budget, llm, embedder, llm_classifier,
                 passages=None, query_schema=None):
        self._store = store
        self._plan = plan
        self._execution_plan = execution_plan
        self._budget = budget
        self._llm = llm  # the _BudgetedLLM, reused by ask -> shared pool
        self._embedder = embedder
        self._llm_classifier = llm_classifier
        # Zero-touch hybrid: the PassageIndex built over the corpus (None when the build skipped
        # it or the store was wrapped via from_store) + the discovered RelationSchema. Threaded
        # into every `ask` so mode="hybrid" retrieves passages and query relations canonicalize
        # through the same schema the edges did -- no per-call wiring by the caller.
        self._passages = passages
        self._query_schema = query_schema

    @classmethod
    def build(cls, docs, workload, *, llm, embedder, predicates=None, llm_classifier=None,
              budget=None, resolver=None, corpus_records=None, store=None, index_passages=True):
        from .budget import Budget, _BudgetedLLM
        from .ingest import CorpusBuild, ingest_corpus
        from .unified import plan_resolver

        budget = budget if budget is not None else Budget()
        plan, planned_resolver = plan_resolver(
            workload, predicates=predicates, llm_classifier=llm_classifier
        )
        execution_plan = plan_er_execution(docs, corpus_records=corpus_records)
        store = store if store is not None else _new_store()
        bllm = _BudgetedLLM(llm, budget)
        # index_passages defaults ON (ask() defaults to the hybrid-capable auto path) so the built
        # graph answers hybrid out of the box; the passages embed once here via the same embedder.
        # Guarded on embedder presence -- with no embedder there is nothing to embed passages with,
        # so skip indexing (ask falls back to local) rather than raise. ingest_corpus returns a
        # CorpusBuild(schema, passages) when indexing, else the bare schema.
        do_index = index_passages and embedder is not None
        result = ingest_corpus(
            docs, store, llm=bllm, resolver=resolver or planned_resolver, embedder=embedder,
            index_passages=do_index,
        )
        if isinstance(result, CorpusBuild):
            query_schema, passages = result.schema, result.passages
        else:
            query_schema, passages = result, None
        return cls(store=store, plan=plan, execution_plan=execution_plan, budget=budget,
                   llm=bllm, embedder=embedder, llm_classifier=llm_classifier,
                   passages=passages, query_schema=query_schema)

    @classmethod
    def from_store(cls, store, *, llm, embedder, llm_classifier=None, plan=None,
                   execution_plan=None, budget=None, passages=None, query_schema=None):
        """Wrap an ALREADY-built store (e.g. a reopened/persisted store) in the facade -- skips the
        build. The llm is wrapped in the same `_BudgetedLLM` seam so `ask` draws from `budget`.
        `passages` (a PassageIndex) + `query_schema` are optional -- supply them to keep hybrid
        answering + schema-aligned routing on a reopened store; None leaves ask on the local
        fallback (byte-identical to a passage-less build)."""
        from .budget import Budget, _BudgetedLLM

        budget = budget if budget is not None else Budget()
        return cls(store=store, plan=plan, execution_plan=execution_plan, budget=budget,
                   llm=_BudgetedLLM(llm, budget), embedder=embedder, llm_classifier=llm_classifier,
                   passages=passages, query_schema=query_schema)

    def ask(self, query, *, valid_t, tx_t, mode="auto", **ask_kwargs):
        from .answer import ask as _ask

        # Thread the build-time passages + discovered schema by default; an explicit kwarg wins,
        # so a caller can still override (e.g. pass mode="local" or a different retriever).
        ask_kwargs.setdefault("passages", self._passages)
        ask_kwargs.setdefault("query_schema", self._query_schema)
        return _ask(query, self._store, llm=self._llm, embedder=self._embedder,
                    valid_t=valid_t, tx_t=tx_t, mode=mode,
                    query_classifier=self._llm_classifier, **ask_kwargs)

    @property
    def plan(self):
        return self._plan

    @property
    def execution_plan(self):
        return self._execution_plan

    @property
    def budget(self):
        return self._budget

    @property
    def store(self):
        return self._store
