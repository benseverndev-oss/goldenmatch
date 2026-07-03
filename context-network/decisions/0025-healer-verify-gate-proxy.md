# 0025 — Healer self-verify gate: default proxy flipped to cohesion

**Status:** Accepted • **Shipped:** 2026-06-26 (branch `feat/suggest-verify-gate-proxy`, PR #1272, stacked on #1267/#1271)

## Context

The **healer** (`review_config`, the config-suggestion loop) keeps only suggestions that pass a **self-verify gate**: at suggestion time there is no ground truth, so each candidate edit is simulated (apply → re-run) and kept only if an *unsupervised* health proxy does not worsen. This is the structural guarantee behind "a suggestion never makes your results worse" — the core of the [healing-loop thesis](../foundation/project-definition.md).

The original proxy (`legacy` = `matched_rate × avg_conf − HHI penalty`) is **recall-biased**: it rewards match *volume*, so it discards precision-improving fixes (raising an over-loose threshold → fewer-but-stronger matches reads as "worse"). The suggester gym quantified the cost: `headline_raw = 0.555` (recovery the kernel *found*) vs `headline_live = 0.151` (what the gate *kept*) — the gate was throwing away ~73% of real, correct fixes. The clearest case: `ncvr_synthetic/threshold_too_low` recovered +0.93 raw but **0.0** live.

## Decision

Flip the default self-verify proxy to **cohesion** (`GOLDENMATCH_SUGGEST_HEALTH=cohesion`, statistic `min_edge`, coverage cap `0.50` via `GOLDENMATCH_SUGGEST_COVERAGE_CAP`). Cohesion scores clusters by their weakest intra-cluster edge × saturating coverage — precision-sensitive, so it keeps the correct precision-improving fixes the legacy proxy discarded.

The choice was made by a **bake-off**, not by intuition: a new harness (`scripts/suggest_quality/bakeoff.py`, `bakeoff` CLI mode) scores every candidate proxy as an accept/reject classifier against the F1 oracle across the suite plus deliberately-adversarial perturbations, and reports per-proxy precision/recall/net-value. `cohesion_min_edge_cap50` won: recall 1.0 (recovers every real win), net +2.63 F1, with **zero net-negatives on real pairs**. (Two adversarial precision-trap perturbations were added to harden the bar; `cap50` specifically is the variant that drops the one real-pair net-negative the tighter caps took.)

The adopted guarantee is **zero net-negative on real pairs**, not strict-zero-on-any: the winner takes one tiny (−0.034 absolute F1) miss on a synthetic near-valley trap built to be near-unwinnable. The strict bar would have kept only `legacy` (recall 0.286). All proxies stay reachable via env for rollback.

## Consequence

- Gym `headline_live` **0.151 → 0.543** (now equal to `headline_raw` — the gate stops discarding correct fixes). The healing loop's "results improve" beat is now real, not aspirational.
- Production-code change is contained: the default `mode` in `suggestion_health_from_clusters`, `_COVERAGE_CAP` 0.30 → 0.50, and a new `cap` param + `GOLDENMATCH_SUGGEST_COVERAGE_CAP` env. Rollback knobs preserved: `GOLDENMATCH_SUGGEST_HEALTH=legacy`, `GOLDENMATCH_SUGGEST_COHESION`, `GOLDENMATCH_SUGGEST_COVERAGE_CAP`.
- This **supersedes** the earlier "cohesion fails, escalate to pseudo-labels (Approach C)" conclusion: that attempt pre-dated enabling `GOLDENMATCH_SUGGEST_FULL_DIST` in the gym (which fixed the rule-misfire that had entangled cohesion with wrong-rule suggestions) and pre-dated the coverage-cap sweep.
- The blessed `gym_scorecard.json` floor is **pinned to `native_version 0.1.12` + FULL_DIST**; a kernel bump can move live recovery and needs a re-bless via the `bench-suggest-quality.yml mode=gym-bless` dispatch.
- Honest ceiling: this closes the *gate* gap (live → raw). The absolute ceiling is what the kernel finds; further accuracy needs new suggestion rules and real-world headroom, which are separate.

Spec / plan / findings: `docs/superpowers/specs/2026-06-26-suggest-verify-gate-proxy-design.md`, `docs/superpowers/plans/2026-06-26-suggest-verify-gate-proxy.md`. User-facing docs: [config-suggestions](../../docs-site/goldenmatch/config-suggestions.mdx).
