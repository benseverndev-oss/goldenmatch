# Auto-config probabilistic-routing lever

**Status:** Design approved 2026-06-23. Builds on the quality harness + its
broadened corpus (PRs #1216, #1226). Two phases: a harness dual-strategy
measurement, then the kernel routing lever (gated, default-off).

## Motivation (measured, not hypothesized)

historical_50k is the canonical probabilistic (Fellegi-Sunter) shape — biographical
records, no unique key, several weak fuzzy fields where you need m/u weights. The
default `dedupe_df` path scores it deterministically (exact + weighted matchkeys)
and lands at F1 **0.4663** (recall 0.394). The opt-in probabilistic path
(`auto_configure_probabilistic_df` → `dedupe_df(config=...)`) lands at F1 **~0.826**
(recall ~0.748) — same data, same engine, **+0.36 F1 purely from strategy**, and
faster (27s vs 100s). Measured twice (0.8244 / 0.8284: recall stable, F1 wobbles
±0.004 from EM convergence tolerance).

The gap is a **routing miss**: auto-config never routes the probabilistic shape to
the FS path. `_legacy_auto_configure_v0` calls `build_matchkeys()` unconditionally
(`autoconfig.py:3469`), which only emits `exact` + `weighted` matchkeys; nothing
checks "no strong identifier + many weak fuzzy fields → go probabilistic." The FS
machinery exists (`build_probabilistic_matchkeys`, `auto_configure_probabilistic_df`,
EM training + probabilistic block scoring in `pipeline.py`) but is opt-in only.

This is the first lever the harness corpus nominated on evidence. Out of scope:
flipping the default (a deferred follow-up gated on the proof), N-strategy support,
EM-seeding rework, new datasets.

## Phase 1 — Harness: dual-strategy measurement (the evidence base)

The F1 tier currently runs one strategy (`dedupe_df(df)`, the default) and records
one `f1` block per ground-truth dataset. Extend it to ALSO run the probabilistic
strategy and record a second block, so every GT dataset shows both numbers
side-by-side — the systematic view that proves the lever helps where it should and
doesn't where it shouldn't.

**Schema (additive, backward-compatible).** Keep the existing `f1` block as the
**default** strategy (what `dedupe_df` decides — reflects the routing lever once
it's wired). Add a parallel `f1_probabilistic` block: force
`cfg = auto_configure_probabilistic_df(df); dedupe_df(df, config=cfg)`, same
F1/P/R/attribution shape. `evaluate_f1` grows a sibling `evaluate_f1_probabilistic`
(or a `strategy` arg) that builds the forced-FS config; `__main__.run()` records
both for any GT dataset (skip when `--fast-only` / no GT, same as today).

**Gate semantics.** The diff floors **both** blocks (`current >= floor - tolerance`
each), reusing the existing real-dataset F1-floor rule — `f1` is the primary
(what ships), `f1_probabilistic` is a second floored number (a real measured
capability; a drop is a regression). `planner_rung` stays WARN; attribution stays
informational; both blocks honor the attribution scale guard.

**Determinism.** The probabilistic strategy carries EM's ±0.004 wobble (recall is
the stable component). The harness's floor+tolerance (default 0.01) absorbs it; we
bless `f1_probabilistic` conservatively (a hair below the observed min) so the
wobble never flaps the gate. No EM-seeding change needed (seed is already fixed at
42; the wobble is convergence-tolerance, not unseeded sampling).

**Cost.** ~2× the F1 tier per GT dataset (two dedupes). historical_50k ≈ 100s
(default) + 27s (FS) ≈ 130s; still within budget. `--fast-only` and `--datasets`
keep the iterate loop fast.

**Re-bless.** The schema change requires a re-bless (memory-off, native-0) to pin
the new `f1_probabilistic` floors across the corpus — and this is the evidence
artifact: the committed scorecard shows det-vs-prob for every dataset (historical_50k
0.466 vs 0.826; the others, where prob should be ≤ default).

## Phase 2 — Kernel: the routing lever

**Decision site.** In `_legacy_auto_configure_v0` (`autoconfig.py`), immediately
after `build_matchkeys()` (`:3469`), check the trigger; if it fires, return a
probabilistic config from `build_probabilistic_matchkeys()` instead and **skip the
iterative controller loop** (that loop refines *weighted* matchkeys; FS configs are
stable post-EM and don't need it — this mirrors the existing non-iterative
`auto_configure_probabilistic_df`). The controller already supports early-return for
configs that don't iterate.

**Trigger (v1 proposal, empirically tuned against the dual-strategy corpus).** Route
when ALL hold:
1. `_route_to_probabilistic_enabled()` — env `GOLDENMATCH_AUTOCONFIG_ROUTE_PROBABILISTIC`, **default OFF** (house style, same shape as `_fs_autoconfig_v2_enabled`);
2. `not multi_source` (multi-source configs are already hand-tuned);
3. **no surviving exact matchkey is backed by an identifier-typed column** — for each emitted `exact` matchkey field, look up its `ColumnProfile.col_type`; if any is `identifier`, a strong key exists → don't route. (historical_50k's exact matchkeys are on `dob`/name composites → no identifier → eligible; febrl3's `soc_sec_id` is an identifier → excluded; ncvr's `ncid` is identifier-typed but ceiling-excluded so it's not a surviving matchkey → ncvr is genuinely fuzzy-shaped → eligible, and the dual-strategy corpus tells us whether FS actually helps it);
4. **≥2 fuzzy-scorable fields** (weighted-matchkey fields) for EM to weight.

All four are computable from the already-emitted matchkeys + profiles — no new
profiling. The trigger is a *proposal*: the dual-strategy corpus is the validator.
It must route historical_50k (default F1 jumps to ~0.826) and must NOT route any
dataset where `f1_probabilistic < f1_default` (no regression). If `col_type ==
"identifier"` proves too coarse, the fallback signal is a cardinality threshold (no
exact matchkey field with `cardinality_ratio >= θ`); the harness picks the winner.

**Gating helper** (house style):
```python
def _route_to_probabilistic_enabled() -> bool:
    """Auto-route to Fellegi-Sunter when the dataset is probabilistic-shaped.
    Default OFF (2026-06-23). Enable: GOLDENMATCH_AUTOCONFIG_ROUTE_PROBABILISTIC=1."""
    return os.environ.get("GOLDENMATCH_AUTOCONFIG_ROUTE_PROBABILISTIC", "0").lower() in (
        "1", "true", "yes", "on", "enabled",
    )
```

**Effect when on.** `dedupe_df`'s default path routes matching shapes to FS, so the
harness's `f1` (default) block for historical_50k jumps to ~0.826 and converges with
its `f1_probabilistic` block. For correctly-excluded datasets the two stay apart
(deterministic higher).

## EM determinism

EM seeds pair sampling at 42 (deterministic); the run-to-run ±0.004 is convergence
tolerance / float rounding, with recall stable. For v1 we accept it under the
harness's 0.01 floor tolerance and bless conservatively. `load_or_train_em` supports
a `mk.model_path` cache (Splink-style) for byte-identical reuse — noted as a future
option, not needed now. EM trains per `dedupe_df` call on a routed dataset (cost
inherent to probabilistic matching; the win justifies it).

## Validation + the deferred flip

Phase 2 ships **default-off**. Validation: run the harness with the flag off vs on
and confirm (a) historical_50k's `f1` (default) jumps to ~`f1_probabilistic`, and
(b) no dataset where `f1_probabilistic < f1_default` got routed (the `default` floor
catches any misroute as a FAIL). Once clean across the corpus, a **separate
follow-up** flips the default on (house pattern: FS-v2 and noise-aware both flipped
after proof) with the env kept as a kill-switch. That flip is its own small,
reviewable change — not bundled here — and is where the broader-than-historical_50k
regression sweep happens.

## File structure

Phase 1 (harness):
- `scripts/autoconfig_quality/f1.py` — add the forced-probabilistic evaluation
  (`auto_configure_probabilistic_df` → `dedupe_df(config=...)`), same F1/attribution
  shape + scale guard.
- `scripts/autoconfig_quality/__main__.py` — record `f1_probabilistic` alongside
  `f1` for GT datasets.
- `scripts/autoconfig_quality/diff.py` — floor `f1_probabilistic` like `f1`.
- `scripts/autoconfig_quality/scorecard.py` — carry the new block (round floats).
- `scripts/autoconfig_quality/baselines/scorecard.json` — re-bless (both floors).
- `scripts/autoconfig_quality/tests/test_f1.py`, `test_diff.py` — dual-strategy tests.
- `scripts/autoconfig_quality/README.md` — document the second strategy column.

Phase 2 (kernel):
- `packages/python/goldenmatch/goldenmatch/core/autoconfig.py` — `_route_to_probabilistic_enabled()` + the trigger check after `build_matchkeys()` in `_legacy_auto_configure_v0`.
- `packages/python/goldenmatch/tests/test_autoconfig_*.py` — trigger unit tests (probabilistic-shape df routes; identifier-rich df doesn't; flag off → deterministic).
- `docs-site/goldenmatch/tuning.mdx` — document the new env flag (docs-staleness gate).

## Testing

- Phase 1: a GT dataset records both `f1` and `f1_probabilistic`; the diff floors
  both (a drop in either → FAIL); bless captures both; the scale guard applies to
  each. Reuse small fixtures — no heavy dedupe in unit tests.
- Phase 2: build a tiny probabilistic-shape df (no identifier column, ≥2 fuzzy
  fields) → with the flag on, the emitted config's matchkeys are `type=="probabilistic"`;
  with the flag off, `exact`/`weighted`. Build an identifier-rich df (a near-unique
  id column) → never routed regardless of the flag. Assert at the config level
  (no full dedupe needed).

## Scope / YAGNI

Two strategies only (default + probabilistic), not N. No EM-seeding rework (floor
tolerance handles the wobble). The default-flip is a deferred follow-up gated on the
corpus proof. No new datasets. The trigger ships as a tuned-against-the-corpus
proposal, not a perfect oracle — the harness is the validator and the regression
guard.
