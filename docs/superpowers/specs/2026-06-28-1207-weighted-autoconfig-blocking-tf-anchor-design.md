# #1207 — Weighted auto-config: blocking-union + data-driven TF name weighting + precision anchor

- **Issue:** [#1207](https://github.com/benseverndev-oss/goldenmatch/issues/1207) — "Auto-config under-blocks and precision-collapses on null-sparse multi-source person data" (`bug`, `autoconfig`).
- **Date:** 2026-06-28
- **Status:** Design — pending review.
- **Surface:** the **weighted / zero-config default path** (`dedupe_df` / `auto_configure_df` → curated weighted config, `name_freq_weighted_jw`). NOT the probabilistic Fellegi-Sunter path.

## Problem

On a ~1M-row person/provider dataset deduplicated across ~10 source systems where every strong identifier is sparsely populated (npi ~39% null, email ~25%, name ~46%, phone ~71%, zip ~69%), zero-config `dedupe_df` / `auto_configure_df` exhibits two failures that trace to the same root — no field is reliably present, so the controller leans on the most-populated but least-discriminating field (name):

1. **Under-blocking caps recall.** `auto_configure_df` emits ONE blocking key (e.g. `[last_name, npi]`). With the strong id ~39% null, that key structurally excludes a large fraction of records from candidate generation, and the name/email matchkeys are starved of candidates. Replacing the single key with a UNION of one key per identifier plus a name+geo key (`[npi] | [email] | [phone] | [first_name,last_name] | [last_name,zip]`) lifted recall substantially. Candidate generation, not the post-cluster prune, is the recall ceiling.

2. **Precision collapse on common names.** The committed config blocks on `first_name`+soundex and weights scoring on names; on common full names this over-merges (two distinct "John Smith", two distinct "Jane Lee" with different npi, get fused). The frequency-weighted name scorer `name_freq_weighted_jw` does **not** lower the per-pair similarity of identical high-frequency names — two identical common names still score ~1.0 — so raising the weighted-matchkey threshold cannot separate same-name strangers from true matches. The controller's telemetry already surfaces the pathology (`stop_reason=BUDGET_ITERATIONS`, `failing_subprofile=cluster`, `mass_above_threshold=1.0`) but commits the name-weighted config anyway.

On a hand-labeled jackknife evaluation of this data, the best precision-safe config reachable was ~91% P / ~75% R, versus a tuned Splink baseline at ~96% / ~87%. The gap is the weak-signal fuzzy matches that term-frequency-weighted scoring captures and the current name scoring + single-key blocking cannot.

## Root cause (confirmed in code)

Both failures are **structural and deterministic** — visible in the code at any scale, not scale-emergent like #715's block-size-∝-N blowup. (This is why a shaped, moderate-scale fixture reproduces them faithfully; see Testing.)

- **Obs 1:** `core/autoconfig.py::build_blocking` (~`:1778–1790`) sorts exact columns by **cardinality**, keeps those whose block size is safe, and returns the single highest-cardinality safe key — with **no population-coverage / null-rate gate**. A 39%-null npi is highest-cardinality and block-size-safe, so it is returned as a lone key even though it excludes 39% of rows from blocking entirely.
- **Obs 2:** `refdata/scorer.py::NameFreqWeightedJW` only applies its surname-IDF downweight in the borderline JW zone `[0.70, 0.95)` (`_BORDERLINE_LOW`/`_BORDERLINE_HIGH`, lines 75–76 / 119). Identical or near-identical names score JW ≥ 0.95, so the scorer returns plain JW **unchanged** — two "John Smith" stay at ~1.0. The downweight is also static-census-based, not dataset-specific.

## Prior art to reuse

The analogous fixes already shipped for the **probabilistic path** via `GOLDENMATCH_FS_AUTOCONFIG_V2` (default ON, see package CLAUDE.md):
- Lever (3) diversifies blocking onto orthogonal stable keys (date-year + postcode/zip/identifier passes, additive) — the FS-path analogue of Obs 1.
- `MatchkeyField.tf_adjustment` + `core/probabilistic.py::_build_tf_tables` (Splink-style `+log2(Σfreq² / freq(value))`) — the FS-path analogue of Obs 2.

#1207 is essentially **porting these proven levers onto the default weighted path**, which received none of them. This de-risks the design (proven shape, new surface) and provides reusable code (`_build_tf_tables`). There is also additive-union precedent on the weighted path itself: `apply_quality_aware_blocking` converts a static/multi_pass config into an explicit `multi_pass` union by appending fuzzy-tolerant passes.

### Rejected alternative

"Route zero-config to the probabilistic path, which FS-v2 already fixes." Rejected: the weighted path is the *default* `dedupe_df` behavior and must be correct on its own; silently switching the default engine is a far larger behavior change than the issue requests, and FS-v2 is opt-in/path-specific. We fix the weighted path directly.

## Design

Three changes, all **default-on** (no opt-in flag), each guarded by golden-vector / regression tests and the standing CI quality gates (#528 synthetic-benchmark parity, DQbench non-regression). Shipped as one spec, **staged PRs**:

- **PR1 — Obs 1 (blocking-union).** The recall ceiling; lands first.
- **PR2 — Obs 2 (data-driven TF name scorer + precision-anchor controller rule).**

### PR1 — Obs 1: coverage-gated blocking-union

In `core/autoconfig.py::build_blocking`, add a **population-coverage gate** to the single-exact-key return:

- A lone exact key is accepted only if its non-null coverage ≥ `_BLOCKING_COVERAGE_TARGET` (default ~0.95).
- If the best exact key fails coverage, build `strategy="multi_pass"` with a `passes` **union**:
  - one pass per strong-identifier field (`identifier`/`email`/`phone`) present above a minimal coverage floor, and
  - a `[first_name, last_name]` pass and a `[last_name, zip]` (name+geo) pass.
- Run the union through the existing `_gate_passes` projected-full-N size guard (#715-safe) before emitting; if nothing survives, fall through to today's existing fallbacks (compound / name multi-pass / degenerate-refuse) unchanged.

Reuses the `BlockingConfig(strategy="multi_pass", passes=[...])` machinery already present in `build_blocking`; mirrors FS-v2 lever-3 and the `apply_quality_aware_blocking` additive-union precedent. Purely widens candidate generation — precision is still decided downstream by scoring, so recall can only rise.

Cross-surface: the union is expressed entirely in the emitted `BlockingConfig`, so every consumer (CLI, REST, MCP, A2A, web, SQL bridge) inherits it with no per-surface change.

### PR2a — Obs 2: data-driven TF on the weighted name scorer

- Extract the per-value-frequency computation from `core/probabilistic.py::_build_tf_tables` into a shared helper (so both the FS path and the weighted path use one implementation; the FS call site keeps its current behavior).
- At auto-config / scoring time, compute a per-dataset value-frequency table for the name field(s) backing a `name_freq_weighted_jw` matchkey.
- Thread that table into `NameFreqWeightedJW` so the downweight is **data-driven and applies across the whole score range, including identical names** — i.e. drop the `jw ≥ 0.95` exemption **when a frequency table is present**. A "Smith" agreement common in *this* dataset carries less weight than a rare-surname agreement, so a higher matchkey threshold gates out common-name collisions while keeping rare-name matches.
- **Fallback:** when no frequency table is available (pairwise use, no dataset context), the scorer keeps today's static census-IDF, borderline-zone-only behavior. This preserves the existing stateless-scorer contract and is byte-identical to today on that path.

**Main implementation risk / open seam:** `NameFreqWeightedJW` is currently a stateless plugin scorer (`score_pair` / vectorized `score_matrix(values)`). Handing it a per-run frequency table requires an injection seam — either a per-run configured scorer instance or a scoring-context object threaded through `core/scorer._fuzzy_score_matrix`. The exact seam is resolved in the implementation plan; the TS port mirrors whatever Python lands (parity case required).

### PR2b — Obs 2: precision-anchor controller rule

Add a controller rule (in `core/autoconfig_rules.py`, fired from the controller loop) that acts on the controller's own diagnosis:

- **Trigger:** the cluster subprofile shows `mass_above_threshold ≥ ~0.95` (the everything-matches pathology; `RunHistory.pick_committed`'s `precision_collapse_floor=0.9` already demotes such RED entries to rank 3).
- **Action when strong-id fields exist:** demote name-weighted matchkeys and promote the high-identity-score fields (`email`/`npi`/`phone` — the controller's column priors already rate these ~0.95) as the precision anchor, then re-verify.
- **Action when no strong-id field exists to anchor on:** the existing posture stands — at `df.height ≥ 100_000` a committed RED config raises `ControllerNotConfidentError` (don't silently ship a low-precision name config). We do not add a silent-fallback path.
- Surfaces in controller telemetry via the single `serialize_telemetry` path and in `RunHistory.decisions` (the audit trail of which rules fired and why).

## Testing & validation

- **Repro fixture (TDD red, committed):** `_null_sparse_multisource_person_df(n)` in `tests/test_autoconfig_regressions.py`, reusing the surname→soundex distribution discipline from `tests/fixtures/realistic_person.py` (surnames must spread across soundex codes or blocking hangs). Shape: a highest-cardinality but ~39%-null `npi`; sparser `email`/`phone`/`zip`, none 1:1; common-name collisions (distinct people sharing `first_name+last_name` across different `npi`). Moderate scale (~5–20k rows).
  - Red test A (Obs 1): asserts `build_blocking` emits a multi-pass union (not a single null-heavy key) and that recall clears a target the single key cannot.
  - Red test B (Obs 2): asserts two same-name / different-npi records score below two rare-surname agreements on the name component, and that the committed config re-anchors off name (no `mass_above_threshold≈1.0` commit).
- **Unit tests:** TF scorer (data-driven downweight on identical common names; static fallback when no table); coverage-gate boundary (key just above/below the coverage target).
- **Regression guard:** the existing CI gates — #528 `synthetic_benchmarks` (clean-precision), DQbench composite non-regression (≥ 91.04). Spot-check Febrl / DBLP-ACM F1 unmoved by the weighted-path changes (the weighted path, not FS, is what changes).
- **Cross-surface:** blocking-union flows through `BlockingConfig` (no per-surface change); telemetry change uses the single `serialize_telemetry` serializer; TS parity case added for the TF scorer.

## Out of scope

- The probabilistic / Fellegi-Sunter path (already addressed by FS-v2).
- Changing the zero-config default engine (rejected alternative above).
- Multi-field / record-level embedding TF.
