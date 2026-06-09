# Layer 2 (Columnar / Frames Orchestration) — Verdict-First Decision Roadmap

**Date:** 2026-06-09
**Status:** Approved shape (brainstorm), pending user review of this doc
**Owner:** Ben
**Type:** Decision roadmap (resolves carrying-cost / decision-debt, not a feature build)

---

## Problem

The "Layer 2" columnar/frames work shipped as three opt-in env gates, all
**default-OFF**, none ever flipped or removed:

- `GOLDENMATCH_COLUMNAR_PIPELINE` — Phase A columnar *scorer* (single fuzzy matchkey)
- `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` — SP1 internal columnar build that still
  materializes the dict
- `GOLDENMATCH_CLUSTER_FRAMES_OUT` — SP-A/B/C frames cutover (`build_cluster_frames`
  → golden-from-frames → identity-from-frames)

Gated-forever is the worst state: we pay for two code paths (maintenance, parity
suites, cognitive load — it made a recent audit disagree with itself about what was
even wired) and get the benefit of neither. This roadmap **resolves each gate** to
one of {default-on, deleted} via a measured verdict, so Layer 2 stops being decision
debt.

**Disposition (decided):** verdict-first. Run the binding bench, then the data
decides flip-vs-delete.
**Win metric (decided):** measure all three signals — completes-where-dict-OOMs,
wall ratio, peak RSS — and read them as a decision matrix.
**Scale scope (decided):** 25M on the 64GB box is the deciding run; 100M is a
flagged feasibility spike that must not gate the decision on infra that may not exist.

---

## The reframe: three gates, three evidence states (not one verdict)

Treating Layer 2 as a single decision is what kept it stuck. The gates carry
*different* evidence and deserve *different* fates:

| Gate | What it is | Prior evidence | Disposition |
|---|---|---|---|
| `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` (SP1) | internal columnar build that **still materializes the dict** | already measured a **loss**: 0.77x@1M, 0.82x@5M, ~2x RSS | strictly dominated → **delete, no new bench** |
| `GOLDENMATCH_CLUSTER_FRAMES_OUT` (SP-A/B/C) | the real frames cutover, **now complete** through identity | SP4 was +30% RSS / +6% wall, but that was measured **before SP-C** removed the identity dict-rebuild | **the actual verdict** → verify, bench, decide |
| `GOLDENMATCH_COLUMNAR_PIPELINE` (Phase A scorer) | single-fuzzy-matchkey columnar *scorer* (not clustering) | ~38% faster in isolation (0.63 ratio), never default | **separate axis** → keep-and-document vs delete |

### Correction baked in (2026-06-09): SP-C already landed

The original approved shape assumed Phase 2 = *build* SP-C (identity-from-frames).
Verification against current code shows SP-C is **implemented**:

- `identity/resolve.py:263` — `resolve_clusters(cluster_frames=..., pair_score_view=...)`
- `core/cluster_pairscores.py:89` — `ClusterPairScores.from_frames(...)`
- `core/pipeline.py:401-414` — frames-out path calls `resolve_clusters(cluster_frames=...)`
  with `clusters=None`
- `core/pipeline.py:2075` — identity pair-score view built via `from_frames` on the
  frames path

So the **complete A+B+C frames path runs cluster→golden→identity dict-free** behind
`GOLDENMATCH_CLUSTER_FRAMES_OUT` today. Phase 2 shrinks from "build" to "verify +
characterize the residual." (My standing project memory for `663-arrow-kernels` is
stale on this point and should be updated.)

---

## The invariant that survives every branch

`build_cluster_frames`, `build_golden_records_from_frames`, `cluster_frames_to_dict`,
and `ClusterPairScores.from_frames` are **imported and used by the DataFusion spine
backend** (`backends/datafusion_spine.py:336-398`) and by `distributed/identity.py:58`.

So **"delete Layer 2" never means deleting those primitives** — it means deleting the
*in-process pipeline gates and default-off branches* that route through them. The
frames primitives stay; the DataFusion spine is their permanent home. Every delete task
in this roadmap is scoped to gates + pipeline branches + in-process parity tests, never
the shared functions.

---

## The decision gate (3-metric matrix)

The bench captures all three signals at 25M (and 100M if feasible). The gate reads them:

- **Clear FLIP** — frames path completes at a scale where the dict default OOMs/SIGTERMs,
  AND wall ≤ ~1.2x dict where both complete, AND peak RSS not worse. ("Finishes where
  dict can't" + RSS headroom is the story; wall parity is sufficient.)
- **Slam-dunk FLIP** — frames is faster end-to-end AND lower RSS. (Unlikely given UF is
  sequential + the build_clusters 1.09x dict-floor, but it's the easy case.)
- **Clear DELETE** — SP4-shaped: +RSS and +wall at a scale the dict also handles, and no
  new scale is unlocked. Dict-as-default is the answer.
- **Ambiguous middle** — surface the three numbers + a recommendation; Ben makes the call.

The matrix is what makes this roadmap *terminate*: every outcome maps to flip, delete, or
an explicit human decision — never "leave it gated."

---

## Phases

### Phase 0 — Decision-debt ledger (cheap, no infra)
Produce a precise, file:line-cited ledger for each of the three gates: code paths, the
parity tests that guard them, the consumers, the prior measured result, and the external
deps. Resolve any "is it even wired" ambiguity (the thing that bit the audit). Also
enumerate the residual `_clusters_dict()` rebuild sites (`pipeline.py:1667`) and classify
each as **hot-path** (would defeat the RSS measurement) vs **output-only / by-design**
(`results["clusters"]`, output_clusters rows, lineage, adaptive refiner — legitimate when
the caller asked for cluster output).
**Deliverable:** the ledger (this table, made exact). **Gate:** none; pure inventory.

### Phase 1 — Retire the dominated gate (`COLUMNAR_CLUSTER_BUILD` / SP1)
SP1 already measured a loss (0.77x@1M, ~2x RSS) and is dominated by the frames-out path
(which at least drops the eager `pair_scores` dict). Delete the gate, `_columnar_cluster_build_enabled`,
the `build_clusters` branch (`cluster.py:520-521`), `_build_clusters_via_frames`
(`cluster.py:1044`), and its parity tests — **after** verifying the one known consumer at
`pipeline.py:2080` (the identity pair-score-view `from_pairs` fallback that exists because
SP1 emits `pair_scores={}`) collapses cleanly when the gate is gone.
**Risk:** low — clear-delete quadrant, no fresh bench needed.
**Gate:** full suite green + byte-identical default output (the dict path is unchanged).

### Phase 2 — Close the SP-C residual / confirm the complete dict-free path
SP-C is implemented (see correction above), so this is **verify-and-close, not build**:
1. Confirm the frames-out path runs cluster→golden→identity dict-free for a bench-shaped
   config (identity ON, cluster-output OFF), with no hot-path `_clusters_dict()` rebuild.
2. If Phase 0 surfaced a hot-path rebuild that defeats RSS, fix it; otherwise this phase
   is a test + a bench-config harness, and may fold into Phase 3.
3. Lock a parity test asserting the complete frames path == dict default on entity
   partition (members-as-set, UUIDv7), `edges_added ≥ 1` anti-vacuous guard, and
   byte-identical fingerprints (entity-id durability).
**Gate:** complete-path parity green (native lane in CI; native cluster path is CI-only
validatable per the recurring local-build gotcha).

### Phase 3 — Complete-path verdict bench (the binding measurement)
Run the FULL pipeline (ingest→score→cluster→golden→identity), frames-out gate ON vs the
dict default, **at 25M on the 64GB box**, capturing all three metrics with per-phase RSS
markers (per the RSS-as-tracked-workstream constraint). Wire 100M as a `workflow_dispatch`
spike that logs-and-skips if the driver-side materialization wall blocks it (it wedged the
head node even on a 4-node Ray cluster — that's an infra story, not a frames-vs-dict story).
**Deliverable:** the 3-metric table at 25M (+100M if it ran). **Gate:** the bench completes
and the matrix yields a verdict.

### Phase 4 — The branch (flip OR delete), driven by the matrix
- **FLIP:** default-on `GOLDENMATCH_CLUSTER_FRAMES_OUT`; one-release deprecation window for
  the dict path; retire the dict path in N+1. SP-C means identity already consumes frames,
  so no further durability re-validation. Update docs + CHANGELOG.
- **DELETE:** remove the in-process gate, the `_cluster_frames_out_enabled` branch in the
  pipeline, and the in-process parity tests — **keep** the frames primitives for the spine.
  Document "dict wins" with the evidence table.
- **AMBIGUOUS:** surface the three numbers + a recommendation; Ben decides.
**Gate:** Layer 2's main gate is now either default-on-with-deprecation or gone.

### Phase 5 — The columnar scorer gate (`COLUMNAR_PIPELINE`, separate axis)
Decide independently of the frames verdict. Lightweight check: does the ~38% isolation win
survive end-to-end on an eligible single-fuzzy-matchkey shape at a realistic size? Then
either **keep-and-document it as a supported tuning knob** (stop treating it as dead and
give it a doc entry + the eligibility constraints) or **delete** if the eligible surface is
too narrow to justify the carrying cost.
**Gate:** the scorer gate is documented-and-kept or deleted.

---

## Risks & known gotchas (carried from prior Layer 2 work)

- **Native cluster path is CI-only validatable** — local `_native.pyd` lacks
  `build_clusters_arrow`/`mst_split_components` and can't be rebuilt (pinned toolchain).
  Parity for the frames path must run in CI's fresh-native lane; budget slow loops.
- **Intermediate small-N RSS is a guaranteed false negative** — every half-cutover pays for
  both frames and dict; Arrow fixed overhead dominates before the dict's per-object cost
  scales. The binding RSS verdict is only meaningful on the **complete** path at scale,
  which is exactly why Phase 2 (complete + dict-free) precedes Phase 3.
- **Full-box OOM SIGTERMs the whole GH job** (exit 143), not a catchable per-child signal —
  the bench harness must treat a host-shutdown as "dict OOM'd here" data, not a crash.
- **Members-as-set, not list** — columnar UF and the dict path differ in member list order
  (PR #598); all frames parity compares members as a frozenset, everything else strict.
- **Zombie-python box hazard** — do not run pytest/import locally to validate; use
  ruff + py_compile + CI (the established posture for this codebase).

## Out of scope

- Layer 1 (the wire-Arrow FFI kernels) — patchwork there is the correct measure-first
  policy; not this roadmap.
- The orphan kernels (`dedup_pairs_arrow_data_utf8`, etc.) — tracked separately.
- DataFusion spine and Sail tier behavior — they consume the frames primitives but their
  own default/experimental status is a different decision.
- The `goldenanalysis` native-dispatch discrepancy flagged in the audit — separate follow-up.

## Related
- `docs/superpowers/specs/2026-05-31-arrow-native-roadmap.md` (Phase-2 amendment)
- `docs/superpowers/specs/2026-06-02-columnar-finalize-tail-sp-a-design.md`
- SP-A #684, SP-B #685; SP1-SP4 history in the `663-arrow-kernels` project memory
- `feedback_rss_optimization_constraint`, `project_scale_driver_bottleneck` (the 100M wall)
