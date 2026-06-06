# Auto-config search strategy after the engine speedup

The performance arc (bucket+native, columnar, the in-house `goldenembed-rs`
model) made candidate evaluation cheap for the costs that used to dominate
auto-config — but the controller's **search strategy was still calibrated to the
old "evaluation is expensive" cost model**. This arc re-derives it. Spec:
`docs/superpowers/specs/2026-06-06-autoconfig-search-strategy-after-engine-speedup-design.md`.

## The thesis
The controller reasoned from a 2K–20K sample + a **linear extrapolation** of
pair counts and self-limited to the cheap **deterministic** scorers — because
running a candidate used to be expensive. Block scoring (≈78% of execution wall)
is now ~5x cheaper, and the in-house embedding model is local/CPU-only. So the
brain can now **measure instead of guess**, **search wider**, and **use its power
tools** — and was the one place still violating the project's own measurement
discipline.

## The arc (four phases, load-bearing-first)
1. **Measure, don't extrapolate** — run real blocking on the full frame to pick
   the backend off measured pair counts (kills wrong-rung-on-skewed-data).
2. **Breadth over depth** — successive-halving over a candidate grid instead of
   ≤5 heuristic refits.
3. **Widen the vocabulary** — promote the in-house embedding/ANN into the
   autonomous candidate set (availability → candidate → measured).
4. **Labeling objective (gated)** — an LLM-judge / active-learning signal to fix
   the proxy objective's *bias* (not just variance).
All controlled by a single **planning-effort tier**: `fast` / `normal` /
`thinking` / `einstein` (a planning ladder alongside the engine ladder).

## Shipped — v1.28.0 (2026-06-06)
The load-bearing spine:
- **Planning-effort tier.** `GoldenMatchConfig.planning_effort` +
  `planning_effort=` kwarg on `dedupe_df`/`match_df`/`auto_configure_df` +
  `GOLDENMATCH_PLANNING_EFFORT`. `ControllerBudget.for_dataset(n_rows, effort)`
  scales sample size / iterations / wall by tier. **Default `normal` is
  byte-for-byte the prior behavior.**
- **Phase 1 (measure-don't-extrapolate)** at `thinking`/`einstein`:
  `blocker.measure_blocking_profile`, with extrapolation as the fallback for
  `normal`/`fast`/distributed and on any failure.
- **Phase 3 (provider-aware in-house embedding):** `_check_remote_assets` no
  longer demotes the local in-house model; cloud embedders still gated. Probe:
  `embedder.inhouse_embedding_available()`.

## Staged for a follow-up (behind the thinking/einstein seam)
- **Phase 2** full successive-halving / Hyperband over the candidate grid.
- **Phase 3 auto-proposal** — the brain *adding* embedding/ANN candidates on its
  own (today the exemption is the bugfix; the higher tiers reserve the seam).
- **Phase 4** the labeling objective (LLM-judge on borderline pairs).

## Future direction (out of scope)
**GoldenAnalyze** — extract auto-config into a standalone package that produces a
reusable, drop-in `GoldenMatchConfig` from a team's data. The 1.28.0 work keeps
the committed config clean/portable so the extraction stays cheap later.
