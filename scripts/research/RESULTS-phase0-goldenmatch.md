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

## Stronger test at lower true-recall

Phase 0's first runs sat at true recall ~0.99 (the union of decorrelated field
systems catches nearly everything — that's *why* multi-pass works). Dropping to
fewer systems (k) genuinely lowers union recall, giving a real sub-1.0 test:

| k real systems | FP-aware estimate | TRUE recall | err |
|---:|---:|---:|---:|
| 3 | 0.941 | **0.928** | 0.012 |
| 4 | 0.983 | 0.988 | 0.005 |
| 5 | 0.993 | 0.993 | 0.000 |

At k=3 true recall is genuinely 0.928 and the estimate tracks within 0.012 (mild
+0.013 optimism, consistent with the homogeneity bias). (Raising the per-field
*threshold* did NOT vary recall — `dedupe_df(fuzzy={f:x})`'s value isn't a
per-field cutoff; fewer systems is the real lever.)

## Phase 1 — productized into the package

Landed in `packages/python/goldenmatch/`:
- `goldenmatch/core/recall_certificate.py` — the pure FP-aware estimator
  (`estimate_recall(pairsets)` + `clusters_to_pairs`); 5 unit tests in
  `tests/test_recall_certificate.py` (incl. the FP-robustness property). No
  pipeline deps.
- `goldenmatch evaluate --certify` — runs each matchkey/pass as a decorrelated
  system (falls back to splitting a multi-field matchkey into per-field systems
  for >=3), then prints the unsupervised recall estimate. `--gt` is now optional.
  End-to-end verified: estimates 85.7% on a synthetic dedup set via the real
  pipeline; existing `test_evaluate.py` stays green.

Point estimate only (the safe lower bound needs the sub-threshold stratum =
blocker-provenance plumbing, Phase 2). Single-run multi_pass provenance (vs the
current per-matchkey re-runs) is the Phase-2 perf optimization.

## Phase 2 — audit-calibrated safe bound in the package

Landed in `packages/python/goldenmatch/`:
- `core/recall_certificate.py`: added `audit_calibrated_bound(...)` + `wilson_ci(...)`
  — the safe lower bound (Wilson-lower on stratum-A precision, Wilson-upper on
  stratum-B miss rate; blocking-completeness via the stratum-C check). Pure;
  9 unit tests total (incl. the safety property `recall_lower <= true` and
  tightens-with-labels).
- `cli/evaluate.py`: `--certify --audit-out sample.csv` emits a stratified audit
  sample (A = full-config matched, B = sub-threshold = relaxed-threshold matches
  minus strict, C = no-feature pairs) + a `.meta.json` of stratum sizes; the
  steward labels the `is_match` column; `--certify --audit-in sample.csv` prints
  the audit-calibrated SAFE lower bound. Round-trip verified end-to-end: safe
  bound 95.6% <= true recall 1.0, blocking-completeness check passes.

Design fix surfaced during integration: stratum A must be the **full-config**
matched set (high precision), not the union of low-precision decorrelated systems
(whose precision is ~0, making `found = precision*|A|` collapse). The decorrelated
systems are for the point estimate; the audit strata come from the full config.

### The emit/ingest flow IS the labeling-surface integration

`--audit-out` -> label -> `--audit-in` is the non-interactive, testable form of the
Inspector loop: select an audit sample, a steward labels it, compute the bound.
The Inspector GUI / Learning-Memory path is the same flow with a UI writing the
labels (the sampled pairs become review items; their approve/reject decisions are
the `is_match` labels).

## Staged (NOT shipped — cannot be verified in this environment)

These are thin wrappers over the now-tested core; deliberately not shipped as
unverified production surface code (this env lacks `fastapi`/`mcp`, so their test
suites can't run). Exact wiring:
- **Single-run blocker provenance**: requires propagating the matchkey id into
  `EngineResult.scored_pairs` (`(a,b,score)` -> `(a,b,score,matchkey)`) so one
  pipeline pass yields capture histories. Signature change ripples across
  scorer/cluster/chunked — risky; the per-system runs used today give the same
  provenance correctly, just slower. Defer behind a measured need.
- **MCP tool**: add a `certify_recall` Tool to `AGENT_TOOLS` (`mcp/agent_tools.py`)
  + dispatch handler that loads the df+config, calls the CLI's `_build_systems` +
  `_system_pairsets` + `estimate_recall`, returns the dict; bump the server card
  tool count.
- **REST endpoint**: add `POST /certify-recall` to `api/server.py` (auth-gated)
  returning the same dict.
- **Controller signal**: surface the estimate via `web/controller_telemetry.py::
  serialize_telemetry` (the single cross-surface serializer) and raise a
  low-recall warning alongside `ControllerNotConfidentError`. Note: computing the
  estimate inside auto-config costs K extra runs, so gate it behind an opt-in.

## Conclusion

The estimator works on GoldenMatch's real pipeline output, not just synthetic
matchers. The productization plan stands: retain per-pass capture provenance +
sub-threshold candidates (the only new plumbing), then surface `evaluate --certify`
+ a controller recall signal. Phase 0 clears the gate.
