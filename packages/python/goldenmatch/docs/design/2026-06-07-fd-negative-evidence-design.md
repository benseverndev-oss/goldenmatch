# FD-driven negative evidence — design spec

Date: 2026-06-07
Branch: `claude/goldenmatch-fd-negative-evidence` (stacked on
`claude/goldenmatch-quality-aware-blocking` #795 → #794 → #793)
Status: PROPOSED — spec for review before implementation.

**Door #3** of the GoldenCheck → GoldenMatch map. Door #1 (blocking, #795) is a
*recall* lever; this is a *precision* lever. It feeds GoldenCheck's discovered
functional dependencies into GoldenMatch's **negative-evidence** machinery to
suppress false merges.

---

## 1. Problem & hypothesis

GoldenMatch already has negative evidence (NE): a field where *disagreement*
penalizes a pair's score (`NegativeEvidenceField{field, scorer, threshold,
penalty}`, applied in `core/scorer.py`). NE fields are auto-selected by
`promote_negative_evidence` (`core/autoconfig_negative_evidence.py`), gated by:

```
column_priors[col].identity_score >= 0.75   # NAME / heuristic "is this an ID?"
AND cardinality_ratio >= 0.5                 # near-key
AND col has an exact-matchkey counterpart
AND col not in this matchkey's fields / blocking keys
```

The weak link is **`identity_score` — a name/heuristic signal**. It catches
`ssn`, `email`, `customer_id`; it *misses* a high-cardinality identity anchor
with an unhelpful name (`ref`, `acct`, `x7`, `member_no`). Those columns are
exactly the ones whose disagreement is decisive negative evidence — and they're
silently skipped.

**Hypothesis:** a column that *functionally determines other columns* is a
**data-driven identity anchor** — distinct values map to distinct fact-sets.
Admitting such columns as NE fields (when the name heuristic missed them)
suppresses false merges → higher precision, with no recall cost (NE only
*subtracts* from borderline pairs; it never creates matches).

GoldenCheck now discovers exactly this (strict FDs + approximate FDs with
confidence, PR #793) — the signal just needs a channel into NE selection.

---

## 2. Why "determinant + high cardinality" is the right criterion

Disagreement is decisive negative evidence only for a **stable, discriminative
attribute of the entity** — a near-key. The FD signal alone is not enough:
- `country → continent` is a valid FD, but `country` is low-cardinality and two
  records of the *same* entity can legitimately differ on it over time → a BAD
  NE field.
- `customer_id → (name, address, phone)` — high-cardinality *and* determines
  other fields → a GOOD NE field (different id ⇒ different customer).

So the criterion is **`cardinality_ratio >= 0.5` AND (it determines ≥1 other
column at high FD confidence)**. Cardinality (already gated) filters the
`country` case; the FD signal is what *upgrades* a high-card column from
"maybe noise" to "confirmed identity anchor", replacing the brittle name guess.

---

## 3. The signal

A per-column **determinant strength** `d(col) ∈ [0, 1]`: how strongly `col`
functionally determines other columns. Derived from GoldenCheck's FD discovery:
- strict FD `col → X` (PR #793 `discover_functional_dependencies`) ⇒ confidence 1.0;
- approximate FD `col → X` at confidence `c` (`discover_approximate_fds`) ⇒ `c`.
- `d(col)` = max FD confidence over all `X` it determines (a column that exactly
  determines *anything* else is a strong anchor; take the strongest).

Fail-open: `d(col)=0` for every column when goldencheck is absent / clean.

---

## 4. Integration

```
promote_negative_evidence(config, df, column_priors)   # autoconfig_negative_evidence.py
  gate per column becomes:
    cardinality_ratio >= 0.5
    AND ( identity_score >= 0.75            # existing name/heuristic path
          OR d(col) >= D_ANCHOR )           # NEW data-driven path (e.g. 0.95)
    AND <existing exact-matchkey / not-in-fields / not-in-blocking gates unchanged>
  penalty (for FD-admitted fields): scale with d(col), capped at the existing
    default — a 99.5%-confidence determinant earns near-full penalty; a 95% one
    earns less.
```

- **Bridge:** `core/quality.py::fd_identity_scores(df) -> dict[str, float] | None`
  — fail-open, calls a new GoldenCheck API (see §5), returns `d(col)`. Mirrors
  `blocking_risk` / `compute_quality_scores`.
- **Gate flag:** env `GOLDENMATCH_FD_NEGATIVE_EVIDENCE=1` (default OFF, the #662
  pattern). No dead config field (same call-site reasoning as #795 §10).
- **Strictly additive to NE selection:** it can only *admit* a column the name
  heuristic missed; it never removes an existing NE field nor changes the
  existing identity_score path. With the flag off, NE selection is byte-identical.

---

## 5. Changes (by file)

GoldenCheck (NEW public API, parallels `cell_quality`):
- `goldencheck.functional_dependencies(df) -> list[FD]` where
  `FD = {determinant: str, dependents: list[str], confidence: float}` — wraps the
  existing strict + approximate FD discovery (reuses the native kernels). This is
  the clean contract; today FD discovery is only reachable via the profilers'
  `Finding` objects.

GoldenMatch:
- `core/quality.py::fd_identity_scores(df)` — bridge (fail-open).
- `core/autoconfig_negative_evidence.py` — blend `d(col)` into the admission gate
  + scale penalty; guarded by the env flag.
- Docs: this spec + CLAUDE.md note.

---

## 6. Test + measurement plan (BINDING — the gate)

NE is heavily tuned against DQbench (v1.12 composite **91.04**, T1 89.3 / T2 97.5
/ T3 85.5). Any change to NE selection must not regress it.

Unit / behaviour:
- `functional_dependencies` (goldencheck): strict + approx FDs, confidence, clean→[].
- `fd_identity_scores` fail-open (no goldencheck → None; no FDs → None).
- `promote_negative_evidence`: a high-cardinality determinant with a LOW
  `identity_score` is admitted as NE when the flag is on (and NOT when off);
  a high-card NON-determinant is NOT admitted; a low-card determinant is NOT
  admitted (cardinality gate). Penalty scales with confidence.
- Flag off ⇒ NE config byte-identical.

Accuracy (the real gate):
- **DQbench T1/T2/T3** (the NE tuning surface): composite **must not regress**
  below v1.12's 91.04; target a measurable T3 (collision/precision) gain on a
  fixture with an oddly-named identity anchor.
- **DBLP-ACM / Febrl3 / NCVR**: precision **non-decreasing**, recall
  **non-decreasing** (NE never removes matches, but verify no threshold
  interaction pushes a true pair below cut).

---

## 7. Default posture & kill criteria

- **Default OFF** until the §6 sweep shows DQbench composite ≥ 91.04 + no
  precision/recall regression on the three ER sets. Then ON + env kill-switch.
- **Kill criterion:** any DQbench tier or ER-set regression that isn't a fixable
  bug ⇒ ships OFF (opt-in), negative result recorded.

---

## 8. Risks & honest assessment

- **Overlap with a mature subsystem.** NE selection is finely tuned (exact-
  matchkey gate, collision demotion, v1.11/1.12 calibration). This is the
  spec's biggest risk: a new admission path can perturb the calibrated DQbench
  numbers. Mitigation: additive + flag + the must-not-regress gate above.
- **Marginal value is data-dependent.** The win only materializes on datasets
  with a high-cardinality identity anchor the *name* heuristic misses but that
  *also* has an exact-matchkey counterpart. Well-named person benchmarks
  (Febrl/NCVR) may show little — so this MUST be measured, not assumed; the
  fixture-with-oddly-named-key test is the honest demonstration.
- **FD on a sample.** Auto-config profiles a sample; FD confidence on a small
  sample is noisy. Require strict or high-confidence (≥0.95) FDs only.
- **New GoldenCheck API surface** (`functional_dependencies`) — small, mirrors
  `cell_quality`.

**Alternative worth weighing before building:** Door #5 (quality-gated review
routing — route high-score matches built on GoldenCheck-flagged cells to human
review) reuses `cell_quality` directly (no new API), touches the review queue
(not the tuned NE/DQbench surface), and may be a higher value/risk ratio. #3 is
the right call if *precision on auto-config without labels* is the priority and
the FD-kernel reuse is wanted; #5 if *trust/safety* is. Flagging so the choice is
explicit before implementation.

---

## 9. Scope boundary

In scope: FD-driven *admission + penalty scaling* in `promote_negative_evidence`,
behind a flag, fail-open.

Out of scope: changing the NE *scoring* mechanism in scorer.py; probabilistic
matchkeys (NE already skips them); the other doors (#2 transforms, #4 threshold,
#5 review, #6 provenance).

---

## 10. Implementation notes & deviations (as built, 2026-06-07)

Status: IMPLEMENTED (mechanism + unit tests). Accuracy sweep deferred to CI.

- **New GoldenCheck API:** `goldencheck.functional_dependencies(df, *,
  min_confidence=0.95) -> list[FunctionalDependency{determinant, dependents,
  confidence}]` — wraps the strict + approximate FD kernels into structured
  records (one per determinant, `confidence` = the strongest it supports).
  Exported from `goldencheck.__init__`.
- **Bridge:** `goldenmatch/core/quality.py::fd_identity_scores(df)` — fail-open,
  returns `{determinant: confidence}`.
- **Wiring:** `promote_negative_evidence` gate becomes `cardinality >= 0.5 AND
  (identity_score >= 0.75 OR fd_conf >= 0.95)`; FD-admitted fields scale the
  penalty by `fd_conf` (capped at the default 0.3). Env-gated
  `GOLDENMATCH_FD_NEGATIVE_EVIDENCE=1`, **default OFF**. No `QualityConfig` flag
  (same call-site reasoning as #795 — no dead API). Flag off ⇒ `fd_scores={}` ⇒
  gate reduces to the original `identity_score` check ⇒ **byte-identical**
  (58 NE/autoconfig regression tests pass).

- **KEY FINDING (limitation), surfaced while building:** FD discovery **excludes
  perfectly-unique determinants** (cardinality 1.0) as *trivial* (a unique key
  determines everything). So the *strongest* identity anchors — perfectly-unique
  oddly-named keys — are **NOT** caught by this door. It catches identity anchors
  with cardinality in **[0.5, 1.0)** that determine other columns (verified: an
  `acct` column at cardinality 0.6 determining `name` is admitted). This narrows
  the value vs the spec's framing but does not void it. The complement for
  perfectly-unique oddly-named keys is a **format/structure-consistency** signal
  (an ID has a regular shape) — a separate future door, NOT functional dependency.
  This is the honest answer to §8's "marginal value is data-dependent".

- **Measurement still pending (the gate):** the DQbench-non-regression +
  Febrl/DBLP-ACM/NCVR sweep (§6) runs in CI, not this sandbox. Default stays OFF
  until it passes.

- Tests: goldencheck `tests/test_functional_dependencies.py` (5); goldenmatch
  `tests/test_fd_negative_evidence.py` (5).
