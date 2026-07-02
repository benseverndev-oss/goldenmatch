# Seed Determinism + Win Re-Confirmation — Verdict

**Date:** 2026-07-02
**Branch:** `feat/llm-seed`
**Spec:** brainstorm spike (measure-first gate for the replication harness the REBEL verdict called for)
**Run:** Modal `gg-bench`, 7B (`qwen2.5-7b-instruct`), `--corpus wiki`, `name_ci`, `GOLDENGRAPH_LLM_SEED` set. 19 docs, 65 gold.

## What this tested

The REBEL verdict discovered the substrate R(B)/F1 metric swings ~0.14 F1 run-to-run (control F1 0.370 vs 0.511, same config) because the 7B extraction is non-deterministic — making single-leg A/B deltas underpowered and casting doubt on the chunking + re-prompt "wins." The fix was assumed to be a replication harness. First, the cheap test: extraction already runs at `temperature=0` but with **no seed**. Does adding a fixed `GOLDENGRAPH_LLM_SEED` make it reproducible? If so, no harness is needed.

## Result 1 — the seed fixes reproducibility (harness unnecessary)

Same best-config fired as two independent Modal legs, both `GOLDENGRAPH_LLM_SEED=42`:

| leg | R(B) | P(B) | F1(B) | coverage | components |
|---|---|---|---|---|---|
| 120 | 0.3030 | 1.0000 | 0.4651 | 0.4923 | 15 |
| 121 | 0.3030 | 1.0000 | 0.4651 | 0.4923 | 16 |

**R(B), P(B), F1, coverage are byte-identical across containers.** Only `components` differs by 1 (15 vs 16) — negligible residual GPU non-determinism that does not touch the B-cubed headline. The missing seed *was* the dominant variance source. **The replication harness is unnecessary:** a fixed seed makes single-leg deltas reproducible. Set `GOLDENGRAPH_LLM_SEED` for all bench runs.

## Result 2 — re-confirming the wins at fixed seeds (the important part)

With reproducibility, the two shipped "wins" were re-measured at **two seeds** (42, 7). Configs: A = `name_ci` only; B = +chunking `(6,2)`; C = +re-prompt.

| config | F1 @ seed 42 | F1 @ seed 7 |
|---|---|---|
| A: name_ci only | 0.3149 | 0.4348 |
| B: +chunking | 0.4651 | 0.5113 |
| C: +re-prompt | 0.4651 | 0.4651 |

- **Chunking delta (B − A): +0.150 (s42), +0.077 (s7) — positive and substantial at BOTH seeds. The chunking win is REAL** (also lifts coverage 0.40→0.49 / 0.43→0.51). Confirmed.
- **Re-prompt delta (C − B): 0.000 (s42), −0.046 (s7) — neutral-to-negative at both seeds. The re-prompt "win" does NOT replicate.** At seed 42 it is byte-identical to no-re-prompt (F1/R(B)/coverage all equal; only components move 15→9); at seed 7 it *lowers* F1. **Refuted as a win.**

### Why the re-prompt looked like a win before

The original re-prompt verdict compared an unseeded control (leg 100, F1 0.370) against an unseeded re-prompt leg (leg 101, F1 0.465) and read the +0.095 as the treatment effect. But those were two *independent* draws from a distribution with ~0.14 F1 spread — the "effect" was almost entirely the sampling difference between the two draws, not the re-prompt. Controlling the draw with a fixed seed collapses the effect to ~0. This is exactly the false positive the REBEL verdict warned single-leg deltas could produce.

Re-prompt *does* change the graph (at seed 42 it dropped components 15→9 — more cross-doc merging) but that structural change does not improve B-cubed quality and at seed 7 hurts it. Graph-connectivity change ≠ substrate-quality gain.

## Corrections to the record

- **`2026-07-01-relation-reprompt-verdict.md` overclaimed.** Its "WIN (R(B) +33%, F1 +26%)" was an unseeded single-leg artifact. A correction note is appended to that file. The `GOLDENGRAPH_RELATION_REPROMPT` gate is default-off (opt-in), so nothing incorrect shipped to users — but the claim was wrong, and the gate is **not recommended** (no measured benefit; slightly negative at seed 7).
- **Chunking (#1350) stands, now on firmer ground** — positive at both seeds.

## Decisions

- **Ship `GOLDENGRAPH_LLM_SEED`** (this change). Reproducible bench is now the standard.
- **New measurement standard:** every substrate delta claim must (a) set a fixed seed and (b) report ≥2 seeds; a "win" requires the delta positive across seeds, not a single unseeded pair.
- **No replication harness.** The seed + multi-seed reporting is the methodology; a K-replicate aggregator is not worth building.
- **Re-prompt is refuted as a win**; chunking is confirmed. The real-prose substrate stack that holds up is **name_ci + chunking**.

## Honest caveats

- **Two seeds, small N.** The chunking win is positive at both (robust); the re-prompt non-win is 0.000/−0.046 at both (consistent). A third seed would tighten both but the signs are stable.
- **Seed reproducibility is metric-level, not bit-level** — components still wobble ±1 at a fixed seed, so extremely fine-grained metrics retain a little noise; R(B)/F1/coverage do not.
- **This does not re-audit the whole arc.** name_ci was validated at multiple levels earlier; the two levers re-checked here are the ones the REBEL verdict flagged. Other single-leg deltas in the arc (e.g. the L1/L2 numbers) were larger-magnitude and less likely to be pure noise, but the general lesson — seed everything, report multiple seeds — now applies going forward.

## Follow-ons

1. **Optional third seed** for the re-prompt to fully close it (expected: still ~0).
2. **Consider deprecating the `RELATION_REPROMPT` gate** (or leave as an opt-in, clearly not-recommended, since it costs an LLM call for no gain).
3. Apply the seed + multi-seed standard to any future substrate lever.
