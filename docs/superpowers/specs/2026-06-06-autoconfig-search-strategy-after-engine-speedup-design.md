# Auto-config search strategy after the engine speedup

**Date:** 2026-06-06
**Status:** design (approved by Ben; pre-spec-review)
**Scope:** `packages/python/goldenmatch/goldenmatch/core/autoconfig*` (controller,
planner rules, complexity profile, verify, rules), `core/embedder.py`,
`core/ann_blocker.py` / `core/blocker.py`, `config/schemas.py`. The auto-config
*brain*, not the execution backends.
**Related:**
- `2026-05-15-controller-v3-planner-design.md` — the controller became the
  execution planner (picks the backend/rung).
- `2026-05-16-controller-budget-vs-blocking-discovery-design.md` — adaptive
  per-dataset budget + the `ControllerNotConfidentError` confidence gate.
- `2026-06-01-arrow-native-finish-line-design.md`,
  `2026-06-01-bucket-native-default-backend-design.md` — the engine speedup this
  spec responds to (bucket+native, columnar, scale-mode feature matrix).
- `2026-05-02-performance-audit-checklist.md` — the founding measurement
  discipline this spec re-applies to the controller itself.

---

## Why this document exists

The performance arc made candidate evaluation cheap for the costs that used to
dominate auto-config. Block scoring — **~78% of execution wall** (CLAUDE.md, 5M
bucket bench: `bucket_score` 42 min of 53.7 min) — is now **4.5–5.3× faster**
(bucket+native, `bench_fs_and_stages` 2026-06-01); Arrow-native columnar makes
real blocking on full-N affordable; and the **in-house embedding model**
(`goldenembed-rs` + the `goldenmatch-embed` wheel, ONNX, CPU-only, **no cloud
creds**) is wired into `get_embedder("inhouse:...")` for both the
`embedding`/`record_embedding` scorers and the ANN blocker.

But the **auto-config controller's search strategy is still calibrated to the
old "evaluation is expensive" cost model**, confirmed in the current code:

- **≤3–5 heuristic refits** on a **2K–20K-row sample** (`ControllerBudget`,
  `autoconfig_controller.py`); the full data is run **once**, in `_finalize()`,
  *after the iteration loop completes*, for reporting only.
- The planner **extrapolates** pair count linearly to full-N
  (`BlockingProfile.extrapolate_to`, `complexity_profile.py`) and selects the
  execution rung off the projection — **never measuring** the true pair count.
  This is the documented wrong-rung-on-skewed-data failure (controller-v3 open
  question: "sample shows tight blocks, full data has one giant block").
- A **proxy objective** (`mass_above_threshold * (1 - mass_in_borderline)`), with
  no ground truth.
- A **deterministic-only v0 vocabulary**:
  `autoconfig_verify.py::_check_remote_assets()` treats its `_REMOTE_SCORERS` set
  (`embedding` / `record_embedding`) as offline *drift risks* — demoting them to
  `ensemble` and stripping `record_embedding` fields unless `allow_remote_assets`
  is set. So the brain self-limits to the cheap deterministic arsenal (exact /
  fuzzy / soundex / qgram / compound) and **cannot autonomously reach** the
  in-house embedding, ANN, probabilistic, or rerank vectors.

Net: the brain reasons from a thin sample + a linear projection and refuses its
own power tools — exactly the regime the perf work invalidated. It is the one
place still violating the project's founding measurement discipline (*measure,
don't reason from static proxies*) — now that it can finally afford not to.

**Intended outcome:** re-derive the search strategy for the new cost model —
**measure instead of extrapolate, search broader instead of 3 refits, and widen
the vocabulary** (promote the now-cheap in-house embedding/ANN into the
autonomous candidate set) — controlled by a single **planning-effort tier**.

## Decisions that shape this design

1. **Full arc, phased.** Four phases; **Phase 1 (measure-don't-extrapolate) is
   load-bearing** and ships first. Later phases gate on the prior's measured win.
2. **Embedding/ANN = availability-gated CANDIDATES, measured.** If the in-house
   model is present, the search makes embedding/ANN *eligible* and **measures**
   them against the deterministic baseline; measurement — not a heuristic —
   decides if they're used. "Available → candidate → measured."
3. **Proxy objective by default; labeling as a gated opt-in phase.** Freed compute
   tightens the proxy's *variance*; a labeling signal (LLM-judge / active-learning
   on borderline pairs) is a later, budget-capped phase that addresses the proxy's
   *bias*. The spec is explicit about which is which.
4. **Planning effort is a tiered knob: `fast / normal / thinking / einstein`.** It
   parameterizes sample size, search breadth, candidate vocabulary, and whether
   the labeling phase runs. **Default = `normal`** (≈ today's interactive budget).
   This is a *planning ladder* alongside the existing engine ladder.

## Phase 0 — the planning-effort tier (the knob everything hangs off)

Add `planning_effort: Literal["fast","normal","thinking","einstein"]` to
`GoldenMatchConfig` (`config/schemas.py`), plumbed as a kwarg on `dedupe_df` /
`match_df` / `auto_configure_df` and an env override
(`GOLDENMATCH_PLANNING_EFFORT`). It becomes a **second dimension** on
`ControllerBudget.for_dataset(n_rows, effort)`:

| Tier | Sample | Search | Vocabulary | Objective | Wall |
|---|---|---|---|---|---|
| `fast` | tiny / v0 only | no refit | deterministic only | proxy | minimal (today's <5K path) |
| `normal` (default) | today's sqrt-scaled | modest breadth + **Phase 1 measure** | deterministic + in-house embedding/ANN *if present* | proxy | ≈ today's interactive budget |
| `thinking` | larger slices | successive-halving (Phase 2) | + probabilistic, cross-encoder rerank as measured candidates | proxy | minutes |
| `einstein` | largest slices | full enumeration | all of the above | **+ labeling (Phase 4)** | budget-capped, opt-in |

Default `normal` must stay inside today's interactive wall (rebalance freed
cycles, don't spend more). The tier + the search trace land on
`PostflightReport.controller_history` with a distinct `rule_name` per tier.

## Phase 1 (LOAD-BEARING) — measure, don't extrapolate

Run **real blocking + pair-count on the full dataset** (the cheap op — blocking
was never the bottleneck; scoring was, and that's now fast) and feed the
*measured* `BlockingProfile` to `apply_planner_rules`, replacing
`extrapolate_to()` for rung selection. Keep extrapolation only as the `fast`-tier
fallback.

- **Where:** `autoconfig_controller.run`, after the committed config — when
  `planning_effort >= normal`, do a blocking-only pass over the full `df` to get
  the true `total_comparisons` / block-size distribution, then call the planner
  (`autoconfig_planner_rules.py`) with the measured profile instead of the
  extrapolated one.
- **Kills:** the wrong-rung-on-skewed-data failure class entirely.
- **Gate:** on a skewed fixture (`block_sizes_p99/p50 > 5`) the planner picks the
  rung it would pick with full knowledge; no OOM from a mis-selected backend; the
  100K bench stays within the `normal` budget.

## Phase 2 — breadth over depth (successive-halving)

Replace the ≤5 single-refit loop with **successive-halving / Hyperband** over a
candidate set `{blocking key × matchkey field-set × threshold × scorer}`:
evaluate many candidates on a small slice, promote survivors to progressively
larger slices on the now-cheap engine. Breadth scales with the tier.

- **Reuse:** the existing **prepared-record store** (post-transform payload cache
  across iterations) and the **`ExpandSample`** lever as the amortization
  substrate — both already exist, sized today for a 3-step loop.
- **Amdahl note:** once scoring is 5× faster, the *non-scoring* per-candidate cost
  (GoldenCheck / auto-fix / matchkey-transform precompute) becomes the new
  dominator. The prepared-record store covers some; flag the rest as the next perf
  target so breadth doesn't stall on an unchanged fixed cost.
- **Gate:** finds configs at least as good (proxy) as today's heuristic within the
  tier's wall budget; deterministic for a given `(data, effort)`.

## Phase 3 — widen the vocabulary (promote in-house embedding/ANN)

When the in-house model is available (`GOLDENMATCH_INHOUSE_MODEL` set **or** the
`goldenmatch-embed` wheel importable), the search treats `embedding` /
`record_embedding` scorers and `ann` / `ann_pairs` blocking as **eligible
candidates** and measures them against the deterministic baseline.

- **Provider-aware eligibility (the key change):**
  `autoconfig_verify.py::_check_remote_assets()` blanket-demotes the
  `_REMOTE_SCORERS` (`embedding` / `record_embedding`) as offline drift risks —
  correct for *cloud* models, **wrong for the local one**. Distinguish provider:
  in-house ⇒ cheap / local / eligible (do **not** demote); sentence-transformers /
  Vertex ⇒ gated as today via `allow_remote_assets`. Add a discovery probe
  (`get_embedder` already routes the `inhouse:` prefix; wire an availability check
  + optional `GOLDENMATCH_EMBEDDING_PROVIDER=inhouse`).
- At `thinking` / `einstein`, also bring **probabilistic (Fellegi-Sunter)** and
  **cross-encoder rerank** into the measured-candidate set — they exist as policy
  rules / config today but never fire from zero-config v0.
- **Gate:** on a free-text / description fixture the search **auto-selects**
  embedding/ANN and beats the fuzzy-only baseline on the proxy; on a
  clean-identifier fixture it **correctly rejects** embedding (decided by
  measurement, not a heuristic). Embedding eligibility must intersect the
  **scale-mode feature matrix** — the brain must not auto-select a strategy that
  the chosen scale rung drops. *Implementation:* gate embedding eligibility on
  `config.mode != "scale"` (scale mode raises `NotImplementedError` on embedding
  via `backends/datafusion_spine.py::_validate_scale_mode_supported`).

## Phase 4 (gated, opt-in at `einstein`) — labeling for a true objective

Bring a labeling signal — LLM-judge or active-learning boost on **borderline
pairs** (reuse `core/boost.py`, `core/llm_labeler.py`, `core/llm_scorer.py`) — to
rank candidates by *measured* precision/recall on a small labeled set instead of
the proxy. Budget-capped via the existing `LLMScorerConfig.budget`.

- **Honest framing:** this is the only lever that addresses the proxy's *bias*
  (not just its variance). Default off; on only at `einstein` or explicit opt-in;
  requires an API key.
- **Gate:** on a fixture with known ground truth, labeling-ranked selection picks
  a higher *true* F1 config than proxy-ranked selection.

## Cross-cutting concerns

- **Determinism:** same `(data, planning_effort)` → same config (seed sampling
  from data shape, as today).
- **Backward compat:** `normal` default stays within today's wall; explicit
  `backend=` / `config=` overrides still win; the `ControllerNotConfidentError`
  confidence gate is preserved across all tiers.
- **Config portability (pulled forward for GoldenAnalyze):** the committed
  `GoldenMatchConfig` must stay a **clean, serializable, machine-independent
  drop-in** — the search must bake **no** sample- or host-specific state into it.
  This keeps a future standalone extraction cheap (see Future direction).
- **Observability:** the full search trace (candidates tried, scores, winner,
  tier) lands on `controller_history`.

## Verification

- **Unit:** each cell of `ControllerBudget.for_dataset(n_rows, effort)`.
- **Phase 1:** skewed-fixture planner test (no wrong rung, no OOM); 100K bench
  within the `normal` budget.
- **Phase 2:** successive-halving reproducibility + ≥ heuristic proxy within the
  tier budget.
- **Phase 3:** embedding auto-select on a description fixture; correct rejection
  on a clean-id fixture; in-house provider not demoted; scale-mode intersection
  enforced.
- **Phase 4:** labeling-ranked selection beats proxy-ranked on a
  labeled-ground-truth fixture.
- **E2E:** `gm.dedupe_df(df, planning_effort="thinking")` on a description-heavy
  set auto-discovers embedding+ANN and improves the proxy F1 over `normal`.

## What this design explicitly does NOT do

- Re-open the scale-mode feature matrix (embedding / LLM / exotics still drop at
  scale). Touch the engine ladder / execution backends. Change the confidence-gate
  policy. Make embedding always-on, or make the *cloud* embedding model autonomous.

## Future direction (out of scope — captured for continuity)

**GoldenAnalyze as a standalone package.** Extract auto-config into its own
package so a team can feed it their data and get back a **reusable, drop-in
`GoldenMatchConfig`** they version and re-apply. This spec deliberately keeps the
committed config clean / portable (cross-cutting above) so that extraction is a
thin lift later — but the package split, its API, and the config-reuse lifecycle
are a separate brainstorm, not this phase.

## References

- Current code: `core/autoconfig_controller.py` (`ControllerBudget`, run loop,
  `_finalize`, sampling), `core/autoconfig_planner_rules.py`,
  `core/complexity_profile.py` (`BlockingProfile.extrapolate_to`),
  `core/autoconfig.py` (`build_matchkeys`, `build_blocking`),
  `core/autoconfig_rules.py`, `core/autoconfig_verify.py`, `core/embedder.py`
  (`get_embedder` in-house route), `core/ann_blocker.py` / `core/blocker.py`,
  `embeddings/inhouse/model.py`, `packages/rust/extensions/{goldenembed,embed-py}`,
  `core/boost.py` / `core/llm_labeler.py` / `core/llm_scorer.py`,
  `config/schemas.py`.
- Bench evidence: bucket+native 4.5–5.3× (`bench_fs_and_stages`, run 26781636345);
  78%-scoring split (CLAUDE.md 5M bucket bench).
