# 0024 — Quality-harness-driven auto-config probabilistic routing

**Status:** Accepted • **Shipped:** harness #1216 + corpus #1226 (merged); routing lever #1254 (gated default-off)

## Context

The auto-config "decision kernel" (`goldenmatch-autoconfig-core` + the Python
`core/autoconfig*.py` / blocking surfaces) decides, for a given DataFrame, the
column classification, exact matchkeys, blocking, scorers, and threshold. It had
accumulated many tunable levers (the S1–S3 arc, FS-v2, noise-aware scorers), and
each had shipped a regression that was caught one-at-a-time across ~38 scattered
`test_autoconfig_*.py` files. There was no single way to ask "did this kernel
change move a decision, and is that move good?"

Separately, the kernel never routed a dataset to the **probabilistic
(Fellegi-Sunter)** strategy on the zero-config `dedupe_df` path — `_legacy_auto_configure_v0`
always emitted exact+weighted matchkeys. The FS path existed but was opt-in only
(`auto_configure_probabilistic_df`). For no-strong-identifier, error-heavy data
(biographical / dirty PII) that under-recalls badly: deterministic matching needs a
clean key it doesn't have.

## Decision

Two coupled pieces:

1. **A decision-kernel quality harness** (`scripts/autoconfig_quality/`,
   `report` / `gate` / `bless`). It runs the kernel over a committed corpus and
   diffs the resulting decisions against a pinned baseline scorecard, in two tiers:
   fast config-signals (classification / matchkeys / blocking cost / planner rung —
   host-independent, hard-gated) and a slow F1 tier (full dedupe + attribution).
   Real benchmarks (FEBRL3, synthetic+real NCVR, historical_50k via a vendored
   parquet) join three synthetic failure-shape anchors; `planner_rung` is WARN-only
   (native/box-coupled, not a kernel decision). A CI `quality_gate` job enforces it.
   This makes a kernel change's quality impact measurable in one run, and — via the
   F1-attribution split — **nominates the next lever on evidence**.

2. **A probabilistic-routing lever** (the first lever the corpus nominated). The
   harness gained a dual-strategy column (`f1` default vs `f1_probabilistic` forced
   FS), which measured a +0.36 F1 gap on historical_50k and, crucially, that
   clean-key datasets do *better* deterministically. So auto-config now, when
   `GOLDENMATCH_AUTOCONFIG_ROUTE_PROBABILISTIC=1`, routes a dataset to FS iff it has
   **no surviving exact matchkey backed by a strong-identity column**
   (`identifier`/`email`/`phone`) **and ≥2 fuzzy fields** — delegating to
   `auto_configure_probabilistic_df` after `build_matchkeys`. The strong-set is
   `{identifier, email, phone}` (not identifier-only) precisely because the
   dual-strategy data showed a clean-email exact key beats FS.

## Consequence

- The lever ships **default-off** — a behavior change gated behind an env flag, so
  default `dedupe_df` output is byte-identical. The flag-on corpus run proves the
  routing: historical_50k default F1 0.466→0.829, ncvr_synthetic 0.983→0.990
  (routed), febrl3 + anchor_person_match unchanged (strong key blocks routing),
  **zero regression**. The default-flip is a deferred follow-up gated on a broader
  regression sweep.
- The trigger is conservative: febrl3 (`soc_sec_id`) stays deterministic even though
  FS would lift it +0.024 — a safe miss, documented for the eventual broadening.
- The harness is now the iterate loop for the kernel: change a lever → `report` →
  read the decision diff + attribution → `bless` the intended move. The committed
  baseline's git history is the trend log.
- Determinism: baselines are blessed memory-off / native-0; the FS path's ±0.004 EM
  wobble sits inside the 0.01 floor tolerance.
- Canonical flag reference stays `docs-site/goldenmatch/tuning.mdx`
  (`GOLDENMATCH_AUTOCONFIG_ROUTE_PROBABILISTIC`).
