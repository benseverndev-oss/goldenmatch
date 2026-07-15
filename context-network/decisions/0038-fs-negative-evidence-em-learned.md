# 0038 — Negative evidence on Fellegi-Sunter matchkeys is EM-learned (Formulation B)

**Status:** Accepted • **Shipped:** `goldenmatch 3.3.0` + `goldenmatch-native 0.1.15` + `goldenmatch-js 1.3.0` + `golden-suite 0.2.5`

## Context

`negative_evidence` was weighted/exact-only; on `type: probabilistic` (FS)
matchkeys it was silently ignored — which meant every Splink-converted config
(exactly one FS matchkey) had no defense against the fan-out/homonym snowball.
The 2026-05 Wave D investigation
(`docs/superpowers/specs/2026-05-21-ne-fs-investigation.md`) judged the
Bayesian-factor formulation correct but **deferred** it, believing
`P(disagree_NE | match)` needed labeled pairs. That premise was stale: EM
already estimates match-conditional probabilities for every regular FS field
without labels.

## Decision

**NE fields join `train_em` as constrained 2-state EM-learned dimensions
(Formulation B) — no labels needed.** Each NE field is an `__ne__<field>`
dimension estimated by the same EM loop and random-pair sample as the regular
fields, with a storage-only `[w_fired, 0]` clamp: it contributes
`log2(m_fired/u_fired)` bits when it **fires** (both values present AND
`scorer(a, b)` strictly below `threshold`) and exactly 0 otherwise — agreement
never adds weight; only a hard disagreement subtracts it. `penalty_bits` is a
fixed log2-LLR override (probabilistic-only; weighted/exact keep `penalty` and
reject `penalty_bits`, and vice versa). `EMResult.validate_for` requires the
`__ne__<field>` entries, so pre-feature models (including imported Splink
models) fail loudly instead of silently scoring NE at weight 0.

Declines, all loud: the continuous/Winkler EM path rejects NE on both
surfaces; the TS pipeline (`dedupe`/`matchRecords`) throws on probabilistic+NE
(its probabilistic scoring is a simplified weighted-style average); the fused
kernel declines `derive_from` NE (its raw-columns entry never materializes
derived columns).

## Consequence

- **Converted Splink configs get fan-out defenses**: the `fan_out` upgrade
  lever in `import-splink --upgrade` (default lever set, between
  `distance_thresholds` and `calibration`) suggests NE on unused
  identity-grade columns behind a contradiction-rate risk gate and tunes
  `golden_rules.max_cluster_size` from the reference clusters; the calibration
  lever is NE-aware.
- **Native + TS surfaces score NE with capability-gated fallbacks**: the Rust
  kernels score NE from `goldenmatch-native >= 0.1.15` (`FS_SUPPORTS_NE`;
  older wheels keep the pure-Python fallback automatically), and
  `goldenmatch-js 1.3.0` mirrors the full FS-NE matrix with loud declines
  where the surface can't score it.
- **Cross-surface parity is pinned bit-exact**: committed Python-generated
  fixtures re-score in TS at full float equality.
