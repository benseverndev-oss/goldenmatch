# Agentic config optimizer for auto-config — design

Date: 2026-05-25
Status: implemented (PR #486). Phases 1-4 (optimizer side) shipped: threshold
sweep, the proposer/scorer/loop split, the **full §4 `ConfigEdit` vocabulary**
(`ThresholdShift`/`ScorerSwap`/`BlockingStrategyEdit`/`WeightShift`/
`MatchkeyTypeSwap`/`BlockingKeyEdit`), the deterministic `CoordinateDescentProposer`
(§5.2, now including the weight family), and the `LLMProposer` (§5.3) which **emits
structured `ConfigEdit`s** (closed vocabulary) rather than raw diffs. Open
follow-up (#488): consolidate `LLMRefitPolicy` onto the same `ConfigEdit`
vocabulary so the controller's repair loop and the optimizer speak one lever
language, and expose the surfaces (MCP/CLI/A2A) from §8.
Relationship: this is the **search** layer. Its objective is the
**shared unsupervised score** defined in
`2026-05-25-zero-label-confidence-autoconfig-design.md`. One signal, two
consumers: (a) the controller's commit selection, (b) this optimizer's search.

## 1. Problem statement

The vision: *hand an agent a dataset (CSV/Parquet for v1), it iterates through
the available levers to find the best config, then reruns the full data with the
winner.* Today's `AutoConfigController` is a **single-trajectory repair loop** — it
starts from one config and applies heuristic fixes until a profile stops looking
pathological. It does not *search* a candidate space, and it cannot reach most of
the lever vocabulary (scorers beyond a fixed map, ANN blocking, probabilistic
matchkeys — see #491).

We want a **search** that:
1. Generates candidate configs across lever families.
2. Scores each candidate empirically — label-free by default (zero-label
   confidence), supervised (F1) when ground truth exists.
3. Picks the best and hands it back for a full rerun.
4. Can be driven by a fixed strategy (deterministic, default) **or by an LLM
   agent** that reads the diagnostics and proposes the next moves intelligently.

## 2. Current state (what shipped in PR #486)

- `core/config_optimizer.py` — `optimize_config(df, *, base_config, ground_truth,
  objective, threshold_offsets, sample_size, llm_provider) -> OptimizeResult`.
  Warm-starts from `auto_configure_df`, sweeps matchkey thresholds, scores each
  candidate (zero-label confidence on a sample, or F1 on the full frame), returns
  `OptimizeResult(best_config, best_trial, trials, objective, sample_size)` with a
  `.report()`. Reuses the controller's `_take_sample` / `_run_pipeline_sample` /
  `_assemble_profile` so the objective is identical to the controller's.
- `OptimizerTrial(label, config, objective, score, profile, reasons, error)` is the
  per-candidate record — already a search-history element.
- `core/zero_label_confidence.py` — the label-free objective (`compute_zero_label_confidence`).
- `core/autoconfig_policy.py::LLMRefitPolicy` — an existing Tier-3 hook (env-gated
  `GOLDENMATCH_AUTOCONFIG_LLM=1`, needs `OPENAI_API_KEY`) that lets an LLM propose a
  *repair* when heuristic rules are exhausted. This is the seed of the AI-driven
  proposer, but today it operates inside the single-trajectory controller, not the
  search.

**Gap:** candidate generation is a fixed threshold grid. The "agent iterates
through the levers" vision needs (a) a wider, structured lever vocabulary and
(b) a proposer that can be an LLM.

## 3. Architecture: proposer / scorer / loop

Separate the three concerns so the AI layer is one swappable piece:

```
optimize_config(df, proposer=..., objective=..., budget=...)
  state = SearchState(history=[], base=warm_start(df))
  while not budget.exhausted() and not converged(state):
      candidates = proposer.propose(state)        # <- deterministic OR LLM
      for cand in candidates:
          trial = score(cand, df, objective)      # zero-label or F1 (unchanged)
          state.history.append(trial)
      proposer.observe(state)                      # feedback for next round
  return OptimizeResult(best=argmax(state.history), trials=state.history, ...)
```

- **Scorer** — unchanged from Phase 1 (`_score_confidence` / `_score_f1`). The
  objective is the contract between every proposer and the loop.
- **Loop** — owns budget, convergence (`epsilon` plateau on best score), and the
  warm start. Deterministic given a proposer + seed.
- **Proposer** — the pluggable strategy. The AI-driven layer is just an
  `LLMProposer`.

### `Proposer` protocol

```python
class Proposer(Protocol):
    def propose(self, state: SearchState) -> list[ConfigEdit]: ...
    def observe(self, state: SearchState) -> None: ...   # optional feedback hook
```

A proposer emits **`ConfigEdit`s**, not raw configs (see §4). The loop applies +
validates each edit against the base, scores it, and records the trial.

## 4. Lever vocabulary: structured `ConfigEdit`s

The agent must not emit free-form YAML — that is unsafe and unvalidatable. Define a
small, closed edit language; each edit is a validated mutation of a `GoldenMatchConfig`.

All six are implemented in `core/config_optimizer.py`:

| Edit class | Params | Maps to |
|---|---|---|
| `ThresholdShift` | delta | clamp(mk.threshold + delta) across perturbable matchkeys |
| `ScorerSwap` | matchkey, field, scorer | set a field's scorer (validated against `VALID_SCORERS` + plugins) |
| `BlockingStrategyEdit` | strategy | static / multi_pass / canopy / learned / ... |
| `BlockingKeyEdit` | action, fields, transforms | add/remove a blocking key (rejects if it invalidates the strategy) |
| `MatchkeyTypeSwap` | matchkey, target_type | weighted ↔ probabilistic (backfills threshold + uniform weights on the way back) |
| `WeightShift` | matchkey, field, delta | reweight a weighted field (floor 0.0) |

Each edit type has: an `apply(config) -> config` that returns a deep-copied,
schema-validated config (reject and skip on `ValidationError`), and a human label
for the trial report. The deterministic proposers and the LLM proposer share this
exact vocabulary — so a search is explainable regardless of who drove it.

## 5. Proposers

### 5.1 `GridProposer` (shipped, the default)
Today's threshold sweep, generalized to emit `ThresholdShift` edits. Deterministic.
The safe fallback when no LLM is configured.

### 5.2 `CoordinateDescentProposer` (Phase 2, deterministic)
Budget-bounded local search: hold all levers fixed, sweep one family, keep the best,
move to the next family. Cheaper than full cross-product for a wide vocabulary.
Closes the "multi-lever search" follow-up (#488) without needing an LLM.

### 5.3 `LLMProposer` (Phase 3 — the AI-driven iteration layer)
The headline. Each round it sends the LLM a **compact diagnostic packet** and asks
for the next `ConfigEdit`s as structured JSON.

Diagnostic packet (per round, kept small):
- The current best config (summarized: matchkey types, scorers, thresholds, blocking).
- The last K trials: `label`, `score`, and the zero-label `confidence_reasons`
  tuple (e.g. "high borderline overlap (0.42)", "weak-bridge cluster risk (0.55)").
  These reasons are *designed* to be LLM-legible diagnosis.
- The data profile highlights (column identity/corruption scores, n_rows).
- The remaining budget.

The LLM returns a list of `ConfigEdit`s **with a rationale string per edit**. The
loop validates + scores them; the rationale is stored on the trial so the report
explains *why* each move was tried. The LLM never sees raw data — only emitted
aggregates and reasons (privacy + cost).

Reuse, don't reinvent:
- The prompt/calling machinery and budget enforcement come from
  `core/autoconfig_policy.py::LLMRefitPolicy` + `core/llm_budget.py::BudgetTracker`
  (token/cost caps, model tiering, graceful degradation).
- The lever vocabulary is shared with `LLMRefitPolicy` so the controller's
  single-trajectory repair and the optimizer's search speak the same language. The
  long-term consolidation is: `LLMRefitPolicy.propose` returns `ConfigEdit`s too.

## 6. Objective

Delegates to the zero-label layer (unchanged):
- `objective="confidence"` (default, label-free): score on a sample via
  `compute_zero_label_confidence`. The anti-degeneracy guards (everything-matches /
  no-matches / cluster-collapse) already prevent the agent from "winning" with a
  degenerate config — the search cannot exploit the objective into a pathology.
- `objective="f1"`: supervised, full-frame, when ground-truth pairs are supplied.

Tie-break toward the warm-start baseline (already implemented) so the search only
moves off the default when a candidate is strictly better.

## 7. Budget, stopping, determinism, safety

- **Budget**: `max_trials` and/or `max_seconds`; the LLM proposer additionally
  bounded by `llm_budget` (max calls + max USD). Default LLM off.
- **Stopping**: budget exhausted, or best score plateaus within `epsilon` for N
  rounds, or the proposer returns no edits.
- **Determinism**: with `GridProposer` (default) the whole search is deterministic
  given (df, seed). LLM runs are not deterministic — flagged in the result.
- **Safety**: every edit is schema-validated before scoring; invalid edits are
  skipped (recorded as errored trials, never abort the search — Phase 1 already does
  this). The LLM cannot produce an unvalidated config.

## 8. Public API surface

- Phase 1 (shipped): `goldenmatch.optimize_config`, `OptimizeResult`, `OptimizerTrial`.
- Phase 2-3 (additive): `optimize_config(df, proposer="grid"|"coordinate"|"llm",
  budget=OptimizeBudget(...), llm_provider=...)`. `proposer="llm"` requires
  `GOLDENMATCH_AUTOCONFIG_LLM=1` + `OPENAI_API_KEY`; otherwise falls back to grid with
  a warning (mirrors `LLMRefitPolicy`).
- Surfaces (later): MCP tool `optimize_config`, CLI `goldenmatch optimize <file>
  [--ground-truth Y] [--proposer llm]`, A2A skill. All delegate to the one function.

## 9. Phasing / migration

1. **Phase 1 (done):** `optimize_config` + threshold sweep + zero-label/F1 scoring.
2. **Phase 2 (done):** the loop/proposer/scorer split, `GridProposer`, the
   `ConfigEdit` vocabulary (`ThresholdShift`/`ScorerSwap`/`BlockingStrategyEdit`),
   and the deterministic `CoordinateDescentProposer` (one lever family per round,
   built off the best-so-far). No default behavior change (default proposer ==
   today's sweep). Extending the vocabulary to the full §4 table stays in #488.
3. **Phase 3 (done):** `LLMProposer` — env-gated, default off — the AI-driven
   iteration. A `propose_fn` injection makes it testable without a network.
4b. **Phase 4 (controller consolidation done):** the `ConfigEdit` vocabulary now
   lives in its own cycle-free module `core/config_edits.py` (imported by both the
   optimizer and the policy). `LLMRefitPolicy._call_llm` emits the **same closed
   edit vocabulary** (`{"edits": [{"op": ...}]}`, parsed by `parse_llm_edits`, folded
   onto the current config by `fold_edits`) instead of raw diffs — so the controller's
   single-trajectory repair and the optimizer's search speak one lever language.
   `apply_config_diff` is retained as a legacy helper. Remaining Phase-4 follow-up:
   expose surfaces (§8 — MCP/CLI/A2A).
4. **Phase 4 (optimizer side done):** completed the §4 `ConfigEdit` table
   (`WeightShift`, `MatchkeyTypeSwap` weighted↔probabilistic, `BlockingKeyEdit`
   add/remove), wired the weight **and blocking-key** families into
   `CoordinateDescentProposer` (the blocking-key family closes #488's "vary blocking
   key columns" — a recall lever no threshold move can reach when true matches sit
   in different blocks), and **migrated `LLMProposer` to emit structured
   `ConfigEdit`s** instead of raw diffs (`{"edits": [{"op": ...}]}` parsed by
   `_parse_llm_edits`; each valid edit becomes one attributed candidate
   `llm-r{round}:{edit.label}`). #488's acceptance (a non-threshold lever winning
   where threshold sweep alone is suboptimal) is covered by
   `test_coordinate_descent_wins_with_non_threshold_lever`. Remaining design-Phase-4
   follow-up: consolidate `LLMRefitPolicy` onto the same vocabulary, and expose
   surfaces (§8 — MCP/CLI/A2A).

Each phase is additive and default-safe; the deterministic path is always the
fallback.

## 10. Testing plan

- Phase 2: `ConfigEdit.apply` round-trips + rejects invalid edits; coordinate-descent
  picks a non-threshold lever on a dataset where threshold-only is suboptimal.
- Phase 3: `LLMProposer` with a **mocked** LLM (canned `ConfigEdit` JSON) — assert the
  loop validates, scores, and records rationale; assert budget caps abort cleanly;
  assert fallback-to-grid when the flag is off / no key. No live API in CI.
- Determinism test: `GridProposer` search is byte-identical across runs given a seed.

## 11. Open questions

- **Edit deltas vs whole configs from the LLM.** Deltas (`ConfigEdit`) are safer and
  proposed here; revisit if the LLM reasons better over whole configs.
- **History compaction** for long searches — how many trials to show the LLM before
  summarizing. Start with last K=5 + best-so-far.
- **Controller vs optimizer ownership.** Should the controller eventually *call* the
  optimizer for its initial config, or stay separate? Proposed: separate for now;
  share the `ConfigEdit` vocabulary (Phase 4) before considering a merge.
- **Cost**: a multi-round LLM search could be many calls. Default off; document a
  per-search USD cap prominently.

## 12. Acceptance

- Phase 2: `optimize_config(df, proposer="coordinate", budget=...)` explores >1 lever
  family and the report attributes the win to a specific lever.
- Phase 3: `optimize_config(df, proposer="llm", ...)` runs a mocked end-to-end search
  in tests, respects the budget cap, and falls back to grid when unconfigured.
- No default-path behavior change at any phase; DQbench non-regression (>= 91.04, see
  #489) before any default flip.
