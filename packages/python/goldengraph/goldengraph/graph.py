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

    def __init__(self, *, store, plan, execution_plan, budget, llm, embedder, llm_classifier):
        self._store = store
        self._plan = plan
        self._execution_plan = execution_plan
        self._budget = budget
        self._llm = llm  # the _BudgetedLLM, reused by ask -> shared pool
        self._embedder = embedder
        self._llm_classifier = llm_classifier

    @classmethod
    def build(cls, docs, workload, *, llm, embedder, predicates=None, llm_classifier=None,
              budget=None, resolver=None, corpus_records=None, store=None):
        from .budget import Budget, _BudgetedLLM
        from .ingest import ingest_corpus
        from .unified import plan_resolver

        budget = budget if budget is not None else Budget()
        plan, planned_resolver = plan_resolver(
            workload, predicates=predicates, llm_classifier=llm_classifier
        )
        execution_plan = plan_er_execution(docs, corpus_records=corpus_records)
        store = store if store is not None else _new_store()
        bllm = _BudgetedLLM(llm, budget)
        ingest_corpus(docs, store, llm=bllm, resolver=resolver or planned_resolver, embedder=embedder)
        return cls(store=store, plan=plan, execution_plan=execution_plan, budget=budget,
                   llm=bllm, embedder=embedder, llm_classifier=llm_classifier)

    @classmethod
    def from_store(cls, store, *, llm, embedder, llm_classifier=None, plan=None,
                   execution_plan=None, budget=None):
        """Wrap an ALREADY-built store (e.g. a reopened/persisted store) in the facade -- skips the
        build. The llm is wrapped in the same `_BudgetedLLM` seam so `ask` draws from `budget`."""
        from .budget import Budget, _BudgetedLLM

        budget = budget if budget is not None else Budget()
        return cls(store=store, plan=plan, execution_plan=execution_plan, budget=budget,
                   llm=_BudgetedLLM(llm, budget), embedder=embedder, llm_classifier=llm_classifier)

    def ask(self, query, *, valid_t, tx_t, mode="auto", **ask_kwargs):
        from .answer import ask as _ask

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
