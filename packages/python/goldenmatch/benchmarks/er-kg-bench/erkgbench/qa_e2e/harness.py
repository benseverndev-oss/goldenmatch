"""Run loop + cost cap for the QA-e2e head-to-head. One QAEngine per system;
the harness builds the KG once, answers every question, scores via metrics, and
enforces a hard USD cap via goldenmatch's BudgetTracker."""
from __future__ import annotations

import concurrent.futures as _cf
import contextlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from goldenmatch.config.schemas import BudgetConfig
from goldenmatch.core.llm_budget import BudgetTracker

from . import metrics


def _name_tokens(name: str) -> set[str]:
    """Word-ish tokens of an entity name (len>1, lowercased) -- approximates the
    token rule goldengraph's cross-doc `_LinkIndex` blocks on, so a shared token
    here means the pair would already be a token-overlap candidate."""
    return {t for t in "".join(c if c.isalnum() else " " for c in name.lower()).split() if len(t) > 1}


#: Cosine threshold the #1090 ANN/semantic blocker uses to PROPOSE a candidate pair
#: (GOLDENMATCH_SEMANTIC_BLOCKING_THRESHOLD default). A cross-component pair at or
#: above this is one semantic blocking WOULD surface; below it, it would not -- so
#: the probe reports the verdict against the same bar the real integration applies.
_SEMANTIC_BLOCKING_COSINE = float(os.environ.get("GOLDENMATCH_SEMANTIC_BLOCKING_THRESHOLD", "0.6"))


def _shatter_probe(seed_names, island_names, *, embedder=None, cap: int = 1500):
    """Recall-vs-scoring fork for a broken-chain (shattered) miss. The bridge entity
    that should join the seed component to the answer component was never merged --
    this decides WHY, and therefore whether #1090 semantic blocking is the fix.

    Two signals across the seed x island name pairs:
      - fuzzy: best rapidfuzz token_sort_ratio pair + whether it shares a name token
        (a shared token means token blocking ALREADY proposes the pair).
      - semantic (when an embedder is given): best cosine pair -- exactly what the
        #1090 ANN blocker would surface, scored against the SAME 0.6 bar it uses.

    Verdict:
      - SCORING-miss: the best bridge pair shares a token (token blocking already
        proposed it) -> goldenprofile under-merged. Semantic blocking does NOT help.
      - RECALL-miss: the bridge is token-disjoint AND a semantic/string near-duplicate
        (cosine >= the blocking bar, or fuzzy>=85 when no embedder) -> token blocking
        never proposes it but ANN blocking WOULD. This is what #1090 fixes.
      - NO-BRIDGE: no token-disjoint pair clears the semantic/fuzzy bar -> the split
        is not a blocking miss (genuinely distinct mentions, or an upstream
        extraction/normalization gap). Semantic blocking won't reconnect it.

    Returns (verdict, fuzzy_score, cosine, seed_name, island_name, shared_token) or
    None when either component is empty. cosine is None when no embedder is given.
    Fail-soft: the caller wraps this in try/except."""
    from rapidfuzz import fuzz, process

    seeds = list(dict.fromkeys(seed_names))[:cap]
    islands = list(dict.fromkeys(island_names))[:cap]
    if not seeds or not islands:
        return None
    # Best fuzzy pair across the split.
    best = None  # (score, seed_name, island_name)
    for iname in islands:
        m = process.extractOne(iname, seeds, scorer=fuzz.token_sort_ratio)
        if m is not None and (best is None or m[1] > best[0]):
            best = (m[1], m[0], iname)
    if best is None:
        return None
    fscore, sname, iname = best
    shared = bool(_name_tokens(sname) & _name_tokens(iname))

    # Best cosine pair across the split -- the ANN blocker's actual candidate signal.
    # Entity names were already embedded at build time, so this is a cache hit.
    cosine = None
    if embedder is not None:
        try:
            import numpy as np

            sv = np.asarray(embedder.embed(seeds), dtype=float)
            iv = np.asarray(embedder.embed(islands), dtype=float)
            sv /= np.linalg.norm(sv, axis=1, keepdims=True) + 1e-12
            iv /= np.linalg.norm(iv, axis=1, keepdims=True) + 1e-12
            sims = sv @ iv.T  # (len(seeds), len(islands))
            si, ii = np.unravel_index(int(np.argmax(sims)), sims.shape)
            cosine = float(sims[si, ii])
            # Report the actual best-cosine pair (the bridge ANN blocking would pick).
            sname, iname = seeds[si], islands[ii]
            shared = bool(_name_tokens(sname) & _name_tokens(iname))
        except Exception:
            cosine = None  # fall back to fuzzy-only verdict

    near_dup = cosine >= _SEMANTIC_BLOCKING_COSINE if cosine is not None else fscore >= 85
    if shared:
        verdict = "SCORING-miss (candidate exists, under-merged)"
    elif near_dup:
        verdict = "RECALL-miss (token-disjoint near-dup; semantic blocking fixes)"
    else:
        verdict = "NO-BRIDGE (no token-disjoint pair clears the blocking bar)"
    return verdict, fscore, cosine, sname, iname, shared


@dataclass(frozen=True)
class BuildResult:
    handle: Any
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0


@dataclass(frozen=True)
class AnswerResult:
    text: str
    retrieved_fact_ids: tuple[str, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0


@runtime_checkable
class QAEngine(Protocol):
    name: str
    fidelity: str

    def build_kg(self, corpus) -> BuildResult: ...
    def answer(self, handle, question: str) -> AnswerResult: ...


#: How many questions the localize trace inspects. `localize` is LLM-free (cached
#: embeddings), so probing every question is nearly free -- the cap only bounds log
#: volume. Raise it (GOLDENGRAPH_QA_TRACE_LIMIT=0 -> all) to get a real SCORING-vs-
#: RECALL distribution from the shatter-probe instead of a first-10 sample.
_TRACE_LIMIT = int(os.environ.get("GOLDENGRAPH_QA_TRACE_LIMIT") or "10")


def _localize_trace(engine, handle, corpus, *, limit: int = _TRACE_LIMIT) -> None:
    """Diagnostic: for each question, classify WHERE the gold answer is lost --
    extraction (gold entity never made it into the graph), retrieval (it's in the
    graph but the seed-walk didn't surface it), or synthesis (it was retrieved but
    the LLM wrote a wrong answer). Opt-in via GOLDENGRAPH_QA_TRACE; only engines
    exposing `localize` (goldengraph) participate. Uses the same token-containment
    as answer_match so "in graph/ball" lines up with the headline scoring."""
    qs = corpus.questions if limit <= 0 else corpus.questions[:limit]
    print(f"== localize trace (n={len(qs)}; where is the answer lost?) ==", flush=True)
    stage_counts: dict[str, int] = {}
    verdict_counts: dict[str, int] = {}
    for q in qs:
        try:
            loc = engine.localize(handle, q.question)
        except Exception as exc:  # diagnostic must never break the scored run
            print(f"  [{q.id}] localize failed: {exc!r}", flush=True)
            continue
        in_graph = bool(metrics.answer_match(" ".join(loc["graph_names"]), q.gold_answer))
        in_wide = bool(metrics.answer_match(" ".join(loc.get("wide_names", [])), q.gold_answer))
        in_ball = bool(metrics.answer_match(" ".join(loc["retrieved_names"]), q.gold_answer))
        if not in_graph:
            stage = "EXTRACTION (gold not a graph node: never extracted, or a non-entity answer)"
        elif not in_wide:
            stage = "RETRIEVAL-BROKEN-CHAIN (in graph but unreachable from the seeds)"
        elif not in_ball:
            stage = "RETRIEVAL-BUDGET (reachable from seeds but outside the budget-capped ball)"
        else:
            stage = "SYNTHESIS (retrieved, wrong answer written)"
        stage_counts[stage.split(" ", 1)[0]] = stage_counts.get(stage.split(" ", 1)[0], 0) + 1
        print(
            f"  [{q.id}] hop{q.hop_count} gold={q.gold_answer!r} "
            f"in_graph={in_graph} in_wide={in_wide} in_ball={in_ball} -> {stage}",
            flush=True,
        )
        print(
            f"      seeds={loc['seed_names']} graph={loc['n_graph_entities']}ent "
            f"wide={loc.get('n_wide_entities', '?')}ent "
            f"ball={loc['n_retrieved_entities']}ent/{loc['n_retrieved_edges']}edges",
            flush=True,
        )
        # When the answer was retrieved (SYNTHESIS miss), dump the retrieved edges
        # that mention the gold answer -- the exact relationship lines the LLM was
        # handed. If these are present and sensible, the miss is the model failing to
        # walk the chain (prompt/synthesis), not retrieval dropping the answer edge.
        if in_ball:
            ans_edges = [
                e for e in loc.get("retrieved_edges", ())
                if metrics.answer_match(e, q.gold_answer)
            ]
            if ans_edges:
                print(f"      answer-edges in ball ({len(ans_edges)}):", flush=True)
                for e in ans_edges[:8]:
                    print(f"        {e}", flush=True)
            else:
                print(
                    "      answer in ball entities but NO retrieved edge mentions it "
                    "(answer node is an isolated leaf in the ball)",
                    flush=True,
                )
        comps = loc.get("component_names")
        if comps is not None:
            seed_idx = loc.get("seed_component_idx", -1)
            ans_idx = next(
                (i for i, names in enumerate(comps)
                 if metrics.answer_match(" ".join(names), q.gold_answer)),
                -1,
            )
            same = ans_idx == seed_idx and ans_idx >= 0
            seed_sz = len(comps[seed_idx]) if seed_idx >= 0 else 0
            ans_sz = len(comps[ans_idx]) if ans_idx >= 0 else 0
            sizes = loc.get("component_sizes", [])
            print(
                f"      components: {loc.get('n_components', '?')} total "
                f"(top sizes {sizes[:6]}); seed_comp={seed_sz}ent "
                f"answer_comp={ans_sz}ent same_component={same}",
                flush=True,
            )
            # Recall-vs-scoring probe: for a SHATTERED broken-chain miss (answer sits
            # in a DIFFERENT component than the seeds), decide whether a near-duplicate
            # bridge entity is stranded in the island that token blocking would miss
            # (RECALL -> semantic blocking fixes it) vs one that IS already a candidate
            # but goldenprofile under-merged (SCORING -> semantic blocking won't help).
            # This is the single signal that gates the #1090 cross-doc integration.
            if stage.startswith("RETRIEVAL-BROKEN-CHAIN") and not same and ans_idx >= 0 and seed_idx >= 0:
                # The engine's embedder (cached) lets the probe score the bridge pair
                # against the real #1090 cosine bar; absent it, the probe is fuzzy-only.
                embedder = getattr(engine, "_embedder", None)
                try:
                    probe = _shatter_probe(comps[seed_idx], comps[ans_idx], embedder=embedder)
                except Exception as exc:  # diagnostic must never break the scored run
                    probe = None
                    print(f"      shatter-probe failed: {exc!r}", flush=True)
                if probe is not None:
                    verdict, fscore, cosine, sname, iname, shared = probe
                    cos_s = f"{cosine:.3f}" if cosine is not None else "n/a"
                    verdict_counts[verdict.split(" ", 1)[0]] = (
                        verdict_counts.get(verdict.split(" ", 1)[0], 0) + 1
                    )
                    print(
                        f"      shatter-probe: {verdict} "
                        f"(cosine={cos_s} fuzzy={fscore:.0f} shared_token={shared} "
                        f"seed={sname!r} island={iname!r})",
                        flush=True,
                    )
    # Roll-up so the SCORING-vs-RECALL split (the #1090 gate) and the loss-stage mix
    # are one glance, not a grep across every per-question line.
    stage_mix = ", ".join(f"{k}:{v}" for k, v in sorted(stage_counts.items()))
    print(f"== trace summary: stages {{{stage_mix}}} ==", flush=True)
    if verdict_counts:
        verdict_mix = ", ".join(f"{k}:{v}" for k, v in sorted(verdict_counts.items()))
        print(f"== shatter-probe verdicts {{{verdict_mix}}} ==", flush=True)


def _score_question(ans, q, judge) -> dict:
    """Score ONE answer against its gold. Single-sourced so `run_engine` and
    `run_engine_ab` compute identical metrics. Returns the derived scalars plus the
    persisted per-question record."""
    am = metrics.answer_match(ans.text, q.gold_answer)
    em = metrics.exact_match(ans.text, q.gold_answer)
    f1 = metrics.token_f1(ans.text, q.gold_answer)
    rec = metrics.supporting_fact_recall(ans.retrieved_fact_ids, q.gold_supporting_fact_ids)
    atype = metrics.classify_answer_type(q.gold_answer)
    aj: float | None = None
    if judge is not None:
        # An empty prediction is a non-answer -- score it NO without a call.
        aj = (
            metrics.parse_judge(judge(metrics.judge_prompt(q.question, q.gold_answer, ans.text)))
            if ans.text.strip()
            else 0.0
        )
    return {
        "am": am, "em": em, "f1": f1, "rec": rec, "atype": atype, "aj": aj,
        # Persist the per-question record so a near-zero aggregate is debuggable
        # post-hoc (wrong reasoning vs. async-corrupted output vs. phrasing the
        # matcher misses). The prediction is truncated so a verbose engine can't
        # bloat the artifact.
        "record": {
            "id": q.id,
            "question": q.question,
            "gold_answer": q.gold_answer,
            "prediction": _truncate(ans.text),
            "hop_count": q.hop_count,
            "answer_type": atype,
            "answer_match": am,
            "answer_judge": aj,
            "exact_match": em,
            "token_f1": round(f1, 4),
        },
    }


def _build_scorecard(engine, corpus, model, scored, *, answered, cost_usd, budget_exhausted) -> dict:
    """Aggregate a list of `_score_question` outputs into the result dict. Byte-for-byte
    the shape `run_engine` has always returned (locked by tests/test_qa_harness.py)."""
    matches = [s["am"] for s in scored]
    ems = [s["em"] for s in scored]
    f1s = [s["f1"] for s in scored]
    recalls = [s["rec"] for s in scored]
    # answer_match restricted to entity-answerable golds -- the honest denominator for
    # an entity-graph engine that can only ever emit a node (metrics.classify_answer_type).
    matches_entity = [s["am"] for s in scored if s["atype"] == "entity"]
    # Format-fair LLM-judge equivalence (None-safe: aj is a float only when a judge ran).
    judges = [s["aj"] for s in scored if s["aj"] is not None]
    judges_entity = [s["aj"] for s in scored if s["aj"] is not None and s["atype"] == "entity"]
    type_counts: dict[str, int] = {}
    for s in scored:
        type_counts[s["atype"]] = type_counts.get(s["atype"], 0) + 1
    # Decay = correctness by hop count (answer_match is the free-text correctness signal).
    decay_rows = [(s["record"]["hop_count"], s["am"]) for s in scored]
    return {
        "engine": engine.name,
        "fidelity": engine.fidelity,
        "corpus": corpus.name,
        "model": model,
        "ambiguity": corpus.questions[0].ambiguity_level if corpus.questions else 0.0,
        "n_questions": len(corpus.questions),
        "n_answered": answered,
        "answer_match": _mean(matches),
        "answer_match_entity": _mean(matches_entity),
        "n_entity_answerable": len(matches_entity),
        "answer_type_counts": type_counts,
        "answer_judge": _mean(judges) if judges else None,
        "answer_judge_entity": _mean(judges_entity) if judges_entity else None,
        "exact_match": _mean(ems),
        "token_f1": _mean(f1s),
        "support_recall": _mean(recalls),
        "decay_curve": metrics.decay_curve(decay_rows),
        "cost_usd": round(cost_usd, 6),
        "budget_exhausted": budget_exhausted,
        "per_question": [s["record"] for s in scored],
    }


#: Default width of the bounded thread pool that answers questions. Each answer is a
#: network-bound LLM call (synthesis inside engine.answer + the judge inside
#: _score_question) that releases the GIL, and questions are independent, so a small
#: pool cuts the sequential per-question wall ~linearly (~41 min at N=150 was one
#: question at a time). Override with QA_E2E_ANSWER_WORKERS; `1` forces the
#: byte-identical sequential path, a non-integer value falls back to this default.
_DEFAULT_ANSWER_WORKERS = 8


def _answer_workers() -> int:
    """Resolve QA_E2E_ANSWER_WORKERS -> pool width. Default 8; `1` = sequential;
    a non-integer (or unset) value -> the default. Values < 1 clamp to 1."""
    raw = os.environ.get("QA_E2E_ANSWER_WORKERS")
    if raw is None:
        return _DEFAULT_ANSWER_WORKERS
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_ANSWER_WORKERS
    return max(1, v)


def _engine_answer_workers(engine, requested: int) -> int:
    """The effective pool width for THIS engine. An engine that declares
    `answer_parallel_safe = False` (its answer() mutates shared state or drives a
    single asyncio loop -- e.g. graphiti, lightrag) is forced sequential regardless
    of the requested width."""
    if requested <= 1:
        return 1
    if not getattr(engine, "answer_parallel_safe", True):
        return 1
    return requested


def _answer_all(
    engine,
    handle,
    questions,
    judge,
    *,
    mode: str | None = None,
    workers: int,
    tracker,
    model: str,
    on_result=None,
    reserve: int = 1,
    only_indices=None,
):
    """Answer `questions` and score each in a bounded thread pool, returning
    ``(scored_ordered, answered_indices)`` where `scored_ordered` is ordered by the
    ORIGINAL question index -- independent of completion order and of worker count.

    Shared by all three run_* entry points so the pool + ordering + budget policy live
    in one place.

    - `mode`: forwarded as ``engine.answer(..., mode=mode)`` when not None (the A/B
      mode arm); a 2-arg ``engine.answer(handle, q)`` call otherwise.
    - `tracker`: the shared enforce/budget ``BudgetTracker``. Charged (thread-safe)
      once per answer with that answer's tokens, and consulted to stop SUBMITTING new
      tasks once over budget -- in-flight tasks still finish (a small, bounded
      overshoot). The total after a parallel run equals the sequential total (a
      sum over the same per-answer tokens, order-independent).
    - `on_result(idx, ans)`: optional hook, called under no lock as each answer lands
      (per-arm cost attribution charges the arm's tracker here).
    - `reserve`: multiply the per-question token estimate when checking ``can_send``,
      so a caller that answers each question under N arms reserves the whole set up
      front (keeps A/B arms aligned on the same question set).
    - `only_indices`: when given, answer exactly these question indices with NO budget
      gate -- a subsequent A/B arm re-answering the first arm's committed set to stay
      aligned. When None, the budget gate selects the answered prefix.

    ``workers <= 1`` runs the exact sequential loop (byte-identical fallback)."""

    def _do_answer(q):
        return engine.answer(handle, q.question) if mode is None else engine.answer(
            handle, q.question, mode=mode
        )

    def _charge(i, q, ans):
        # Budget/enforce tracker (its record_usage is lock-guarded) + optional per-arm
        # attribution. Judge cost is NOT charged here (eval overhead, per run_engine).
        tracker.record_usage(ans.input_tokens, ans.output_tokens, model)
        if on_result is not None:
            on_result(i, ans)

    scored_map: dict[int, dict] = {}
    answered: list[int] = []

    # Sequential path -- byte-identical to the pre-parallel loop (gate -> answer ->
    # record_usage -> score, in question order). Also the forced path for
    # answer_parallel_safe=False engines.
    if workers <= 1:
        indices = list(only_indices) if only_indices is not None else range(len(questions))
        for i in indices:
            q = questions[i]
            if only_indices is None and (
                tracker.budget_exhausted
                or not tracker.can_send(_estimate_tokens(q.question) * reserve)
            ):
                break
            ans = _do_answer(q)
            _charge(i, q, ans)
            scored_map[i] = _score_question(ans, q, judge)
            answered.append(i)
        return [scored_map[i] for i in sorted(scored_map)], sorted(answered)

    # Parallel path -- bounded pool, streaming submission so cost recorded by completed
    # tasks feeds the budget gate before the next submit (in-flight bounded to
    # `workers`, so the overshoot past the cap is at most ~workers questions). The
    # answer AND its scoring (which runs the network-bound judge) both happen on the
    # worker thread; the main thread only charges the tracker and files the result.
    def _task(i, q):
        ans = _do_answer(q)
        return i, q, ans, _score_question(ans, q, judge)

    src = iter(only_indices) if only_indices is not None else iter(range(len(questions)))
    with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
        pending: set = set()
        src_done = False

        def _fill():
            nonlocal src_done
            while not src_done and len(pending) < workers:
                try:
                    i = next(src)
                except StopIteration:
                    src_done = True
                    break
                q = questions[i]
                if only_indices is None and (
                    tracker.budget_exhausted
                    or not tracker.can_send(_estimate_tokens(q.question) * reserve)
                ):
                    src_done = True  # over budget: stop submitting, let in-flight finish
                    break
                pending.add(ex.submit(_task, i, q))

        _fill()
        while pending:
            done, pending = _cf.wait(pending, return_when=_cf.FIRST_COMPLETED)
            for fut in done:
                i, q, ans, scored = fut.result()
                _charge(i, q, ans)
                scored_map[i] = scored
                answered.append(i)
            _fill()

    return [scored_map[i] for i in sorted(scored_map)], sorted(answered)


def run_engine(
    engine: QAEngine, corpus, *, model: str, budget_usd: float, judge=None
) -> dict:
    """Build the KG, answer every question under a hard cost cap, score, return a
    result dict. Stops cleanly (partial result) when the budget is exhausted.

    `judge`, if given, is a callable(prompt)->str used for the format-fair LLM-judge
    metric (see metrics.judge_prompt); it is eval overhead and is NOT charged against
    the engine's answer budget."""
    tracker = BudgetTracker(BudgetConfig(max_cost_usd=budget_usd))

    build = engine.build_kg(corpus)
    tracker.record_usage(build.input_tokens, build.output_tokens, model)

    if os.environ.get("GOLDENGRAPH_QA_TRACE", "") not in ("", "0", "false") and hasattr(
        engine, "localize"
    ):
        _localize_trace(engine, build.handle, corpus)

    # Answer every question under the hard cost cap, in a bounded thread pool
    # (QA_E2E_ANSWER_WORKERS, default 8; 1 = the sequential path). Results are ordered
    # by original question index, so the scorecard is independent of worker count.
    workers = _engine_answer_workers(engine, _answer_workers())
    scored, answered_idx = _answer_all(
        engine, build.handle, list(corpus.questions), judge,
        workers=workers, tracker=tracker, model=model,
    )

    return _build_scorecard(
        engine, corpus, model, scored,
        answered=len(answered_idx),
        cost_usd=tracker.total_cost_usd,
        budget_exhausted=tracker.budget_exhausted,
    )


def run_engine_ab(
    engine, corpus, *, model: str, budget_usd: float, modes, judge=None
) -> dict:
    """Same-run A/B: build the KG ONCE, then answer every question under EACH mode in
    `modes` against the IDENTICAL graph, so the only variable is answer-time routing
    (e.g. local vs auto). Removes build variance from the comparison -- the deltas are
    purely the mode's effect.

    The engine's `answer()` must accept a `mode=` override (goldengraph does). One
    shared budget bounds the WHOLE run (build + both arms); a question is only started
    when the budget can afford answering it under every mode, so the arms stay aligned
    (same n_answered). Per-arm answer cost is attributed separately for the report.

    Returns `{"arms": {mode: scorecard}, "comparison": {...}, "build_cost_usd",
    "total_cost_usd", "n_answered"}`."""
    modes = list(modes)
    # Duplicate modes would collapse the `scored`/`arms` dict keys into a single arm and
    # silently produce a misleading one-arm "A/B" -- reject rather than mislead.
    if len(set(modes)) != len(modes):
        raise ValueError(f"run_engine_ab modes must be unique, got {modes!r}")
    enforce = BudgetTracker(BudgetConfig(max_cost_usd=budget_usd))
    # Attribution-only trackers (effectively uncapped) for per-arm answer cost.
    arm_trackers = {m: BudgetTracker(BudgetConfig(max_cost_usd=10**9)) for m in modes}

    build = engine.build_kg(corpus)
    enforce.record_usage(build.input_tokens, build.output_tokens, model)
    build_cost = enforce.total_cost_usd

    # Answer arm-major (env-free -- the mode rides the answer() kwarg), each arm in a
    # bounded thread pool. The FIRST arm's budget gate reserves the whole mode-set per
    # question (reserve=len(modes)), fixing the answered-question set; the remaining
    # arms re-answer exactly that set (only_indices) so all arms stay aligned. The
    # shared `enforce` tracker accumulates every arm's cost; per-arm cost is attributed
    # via on_result. Byte-identical to the old question-major loop when the budget is
    # not hit; a small bounded overshoot at the cap boundary otherwise.
    questions = list(corpus.questions)
    workers = _engine_answer_workers(engine, _answer_workers())
    scored: dict[str, list[dict]] = {}

    def _arm_charger(m):
        return lambda i, ans: arm_trackers[m].record_usage(
            ans.input_tokens, ans.output_tokens, model
        )

    first = modes[0]
    scored[first], answered_idx = _answer_all(
        engine, build.handle, questions, judge, mode=first, workers=workers,
        tracker=enforce, model=model, on_result=_arm_charger(first), reserve=len(modes),
    )
    for m in modes[1:]:
        scored[m], _ = _answer_all(
            engine, build.handle, questions, judge, mode=m, workers=workers,
            tracker=enforce, model=model, on_result=_arm_charger(m),
            only_indices=answered_idx,
        )
    answered = len(answered_idx)

    arms = {
        m: _build_scorecard(
            engine, corpus, model, scored[m],
            answered=answered,
            cost_usd=arm_trackers[m].total_cost_usd,
            budget_exhausted=enforce.budget_exhausted,
        )
        for m in modes
    }
    return {
        "arms": arms,
        "comparison": _ab_comparison(arms, modes),
        "build_cost_usd": round(build_cost, 6),
        "total_cost_usd": round(enforce.total_cost_usd, 6),
        "n_answered": answered,
    }


def run_engine_ab_env(
    engine, corpus, *, model: str, budget_usd: float, arms, judge=None
) -> dict:
    """Same-graph env-A/B: build the KG ONCE, then answer every question under EACH
    env-config arm against the IDENTICAL graph. The ONLY variable is the answer-time
    environment (e.g. `GOLDENGRAPH_SYNTH_SAMPLES=1` vs `=5`), so build variance -- which
    otherwise swamps a downstream-only change under head_to_head's rebuild-per-run
    (support_recall swung +-0.26 on a retrieval-only metric voting cannot touch) -- is
    removed by construction.

    `arms` is a list of `(label, env_dict)`; each `env_dict` maps env-var name -> value
    (str) applied around the answer call and restored afterward, so the arms don't leak
    into each other or the build. Unlike `run_engine_ab`, the engine's `answer()` is
    called WITHOUT a `mode=` kwarg -- the knob under test lives entirely in the env, so
    ANY env-gated behavior is A/B-able without an engine-level mode hook.

    One shared budget bounds the WHOLE run (build + every arm); a question is only started
    when the budget can afford answering it under every arm, so the arms stay aligned
    (same n_answered). Per-arm answer cost is attributed separately for the report.

    Returns `{"arms": {label: scorecard}, "comparison": {...}, "build_cost_usd",
    "total_cost_usd", "n_answered"}`."""
    arms = list(arms)
    labels = [label for label, _ in arms]
    # Duplicate labels would collapse the `scored`/`arms` dict keys into one arm and
    # silently produce a misleading one-arm "A/B" -- reject rather than mislead.
    if len(set(labels)) != len(labels):
        raise ValueError(f"run_engine_ab_env arm labels must be unique, got {labels!r}")
    enforce = BudgetTracker(BudgetConfig(max_cost_usd=budget_usd))
    # Attribution-only trackers (effectively uncapped) for per-arm answer cost.
    arm_trackers = {label: BudgetTracker(BudgetConfig(max_cost_usd=10**9)) for label in labels}

    build = engine.build_kg(corpus)
    enforce.record_usage(build.input_tokens, build.output_tokens, model)
    build_cost = enforce.total_cost_usd

    # Answer arm-major, each arm's whole batch under ONE `_env_overrides(env)` context.
    # This is what makes the env A/B thread-safe: os.environ is process-global, so two
    # arms' overrides cannot run concurrently -- but WITHIN one arm the env is fixed, so
    # every worker thread in that arm's pool sees the same (correct) env. The first
    # arm's budget gate reserves the whole arm-set per question (reserve=len(arms)),
    # fixing the answered set; the rest re-answer exactly that set (only_indices) so the
    # arms stay aligned. Byte-identical to the old question-major loop when the budget
    # is not hit; a small bounded overshoot at the cap boundary otherwise.
    questions = list(corpus.questions)
    workers = _engine_answer_workers(engine, _answer_workers())
    scored: dict[str, list[dict]] = {}

    def _arm_charger(label):
        return lambda i, ans: arm_trackers[label].record_usage(
            ans.input_tokens, ans.output_tokens, model
        )

    first_label, first_env = arms[0]
    with _env_overrides(first_env):
        scored[first_label], answered_idx = _answer_all(
            engine, build.handle, questions, judge, workers=workers,
            tracker=enforce, model=model, on_result=_arm_charger(first_label),
            reserve=len(arms),
        )
    for label, env in arms[1:]:
        with _env_overrides(env):
            scored[label], _ = _answer_all(
                engine, build.handle, questions, judge, workers=workers,
                tracker=enforce, model=model, on_result=_arm_charger(label),
                only_indices=answered_idx,
            )
    answered = len(answered_idx)

    arm_cards = {
        label: _build_scorecard(
            engine, corpus, model, scored[label],
            answered=answered,
            cost_usd=arm_trackers[label].total_cost_usd,
            budget_exhausted=enforce.budget_exhausted,
        )
        for label in labels
    }
    return {
        "arms": arm_cards,
        "comparison": _ab_comparison(arm_cards, labels),
        "build_cost_usd": round(build_cost, 6),
        "total_cost_usd": round(enforce.total_cost_usd, 6),
        "n_answered": answered,
    }


@contextlib.contextmanager
def _env_overrides(env: dict):
    """Apply `env` (name->value) to os.environ for the duration, then restore the prior
    state EXACTLY -- keys that were absent are deleted, keys that were set are put back to
    their old value. So an arm's config can't leak into the next arm or the shared build."""
    saved: dict[str, str | None] = {k: os.environ.get(k) for k in env}
    try:
        for k, v in env.items():
            os.environ[k] = str(v)
        yield
    finally:
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


#: The headline metrics compared side-by-side across the A/B arms.
_AB_METRICS = (
    "answer_match", "answer_match_entity", "exact_match",
    "token_f1", "support_recall", "answer_judge",
)


def _ab_comparison(arms: dict, modes: list) -> dict:
    """Per-metric side-by-side of the arms, plus an explicit `<b> - <a>` delta for the
    common 2-mode case (e.g. auto - local) so the headline is legible without math."""
    out: dict = {}
    for k in _AB_METRICS:
        out[k] = {m: arms[m].get(k) for m in modes}
        if len(modes) == 2:
            a, b = arms[modes[0]].get(k), arms[modes[1]].get(k)
            out[k]["delta"] = (
                round(b - a, 4) if isinstance(a, (int, float)) and isinstance(b, (int, float))
                else None
            )
    return out


def _truncate(text: str, limit: int = 2000) -> str:
    """Cap a stored prediction so a verbose engine (LightRAG essays) can't bloat
    the results artifact; the head is what the answer_match check reads anyway."""
    if len(text) <= limit:
        return text
    return text[:limit] + " ...[truncated]"


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _mean(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 4) if xs else 0.0


def write_results(results: list[dict], *, md_path: str | Path, json_path: str | Path) -> None:
    """Write the headline markdown table + the raw JSON. ASCII only."""
    Path(json_path).write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    lines = ["# ER-KG-Bench -- end-to-end multi-hop QA (evidence program #1)", ""]
    lines.append(
        "| engine | corpus | answer-match | LLM-judge | judge (entity-subset) | "
        "AM (entity-subset) | EM | token-F1 | support-recall | cost (USD) | "
        "answered | budget hit |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        ent_am = r.get("answer_match_entity", 0.0)
        n_ent = r.get("n_entity_answerable", 0)
        judge = r.get("answer_judge")
        judge_ent = r.get("answer_judge_entity")
        judge_s = "n/a" if judge is None else f"{judge}"
        judge_ent_s = "n/a" if judge_ent is None else f"{judge_ent}"
        lines.append(
            f"| {r['engine']} | {r['corpus']} | {r['answer_match']} | "
            f"{judge_s} | {judge_ent_s} | {ent_am} (n={n_ent}) | {r['exact_match']} | "
            f"{r['token_f1']} | {r['support_recall']} | {r['cost_usd']} | "
            f"{r['n_answered']}/{r['n_questions']} | "
            f"{'yes' if r['budget_exhausted'] else 'no'} |"
        )
    lines.append("")
    lines.append("## Gold answer-type mix (entity-graph engines can only answer 'entity')")
    for r in results:
        counts = r.get("answer_type_counts") or {}
        if counts:
            mix = ", ".join(f"{k}:{v}" for k, v in sorted(counts.items()))
            lines.append(f"- {r['engine']} ({r['corpus']}): {mix}")
    lines.append("")
    lines.append("## Decay curve (engineered corpus: mean answer-match by hop count)")
    for r in results:
        if r["corpus"] == "engineered":
            curve = ", ".join(f"{h}:{v}" for h, v in r["decay_curve"].items())
            lines.append(f"- {r['engine']}: {curve}")
    Path(md_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
