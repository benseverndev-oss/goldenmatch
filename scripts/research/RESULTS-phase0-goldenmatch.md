# Phase 0 — recall certificate validated on REAL GoldenMatch output

Runner: `scripts/research/phase0_goldenmatch_recall.py`. goldenmatch 1.28.1
(installed editable) + recordlinkage; real Febrl3 via the existing
`dqbench_adapters` loader + ground truth. 2026-06-07.

> **Verdict: PASS — productization de-risked.** The FP-aware capture-recapture
> recall estimator, run over K REAL GoldenMatch single-field matchers, tracks true
> recall within 0.002–0.005 at full scale with no labels. GoldenMatch's real
> passes are decorrelated enough (overlap 0.52) and its FPs are singleton enough
> (the capture structure the estimator needs holds on real output).

## What Phase 0 tests

The estimator was validated in research on SYNTHETIC field-group matchers. The
only question gating productization: do GoldenMatch's REAL matchers produce the
capture structure the estimator needs? Phase 0 builds the K "systems" from the
actual pipeline — `system_k = dedupe_df(df, fuzzy={field_k: 0.8})`, using
GoldenMatch's production scorer / blocking / clustering — and checks the
label-free recall estimate against the known recall.

## Results (full Febrl3, N=5000)

| K real systems | naive Chao2 | FP-aware estimate | TRUE recall | \|err\| | overlap |
|---:|---:|---:|---:|---:|---:|
| 6 | 0.021 | **0.997** | 0.999 | 0.002 | 0.52 |
| 4 | 0.038 | **0.983** | 0.988 | 0.005 | 0.52 |

Per-system real recall ranged 0.57–0.84 (single weak fields), precision 0.03–0.45.
Capture-count histogram (k=6): `1:230726  2:2022  3:1274  4:1996  5:1861  6:748` —
i.e. ~231k singletons (false positives) and a few thousand multi-captured (true)
pairs. (The 800-row subsample gave the same picture: FP-aware 0.998 vs true 1.000.)

## What this confirms on REAL output

1. **The FP-aware estimator tracks true recall** (err 0.002–0.005, no labels).
2. **FP contamination is real and the fix works**: naive Chao2 is *fooled* by the
   ~231k FP singletons (reports 0.02); the FP-aware estimator (ignore the singleton
   cell, fit p from k≥2) resists them and lands on 0.997. So it is doing real work,
   not trivially outputting 1.0.
3. **FPs are singletons** on real output (the k=1 cell dwarfs k≥2) — the key
   structural assumption holds for GoldenMatch's decorrelated single-field passes.
4. **Real passes are decorrelated** (true-pair capture overlap 0.52 < 0.85).
5. **Full scale is fine** (5000 rows × K real dedupe runs).

## Honest caveats / scope

- **Validated at high true-recall** (0.99). The estimator's job here is non-trivial
  (resist 231k FP singletons), but a real-output test at true recall ~0.7–0.9 would
  be stronger; the <1.0 regime was covered by the synthetic experiments
  (`RESULTS-recall-certificate.md`), where the FP-aware point estimate also tracked.
- **Point estimate only.** The audit-calibrated SAFE bound needs the sub-threshold
  candidate stratum, which the high-level `dedupe_df` API doesn't expose — that
  needs the blocker-provenance plumbing (productization Phase 2).
- **Dedupe path only.** Cross-source record linkage (`match_df`) not yet tested.
- Systems here are single-field fuzzy configs; production would use the real
  multi_pass / multi-matchkey provenance as the K systems (cleaner, one pipeline run).

## Conclusion

The estimator works on GoldenMatch's real pipeline output, not just synthetic
matchers. The productization plan stands: retain per-pass capture provenance +
sub-threshold candidates (the only new plumbing), then surface `evaluate --certify`
+ a controller recall signal. Phase 0 clears the gate.
