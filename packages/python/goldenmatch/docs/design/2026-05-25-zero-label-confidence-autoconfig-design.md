# Zero-label confidence layer for auto-config — design

Date: 2026-05-25
Status: draft (design only; no code)
Relationship: this is the **shared unsupervised objective** referenced by the
agentic config-optimizer design (`2026-05-25-agentic-config-optimizer-design.md`).
The optimizer *searches* the lever space; this layer *scores* a config's
plausibility without labels. One signal, two consumers: (a) the controller's
commit selection, (b) the optimizer's search objective.

## 1. Problem statement

`AutoConfigController` runs sampled pipeline iterations, builds a
`ComplexityProfile`, applies heuristic repair rules, and `RunHistory.pick_committed()`
picks by `(health_rank, -mass_separation, iteration)`. That key is a thin
unsupervised objective: it commits the first config that *avoids pathologies*,
not the config whose latent ER structure is *most plausible*. The known failure
mode is already partially guarded (`precision_collapse_floor` demotes RED entries
with `mass_above_threshold > 0.9`) — this generalizes that guard into a principled,
ZeroER-inspired confidence score.

**Honesty note:** real ZeroER (Wu et al. 2020) is a GMM+EM over similarity-feature
vectors with transitivity as a constraint. This layer is a *lightweight,
dependency-free heuristic approximation* of the same idea (separation + overlap +
transitivity), computed from already-emitted profile signals. Where a true latent
separation estimate is wanted, reuse the **existing Fellegi-Sunter EM**
(`core/probabilistic.py`, `train_em`) — it already estimates label-free match /
non-match m/u distributions and is the closer cousin to ZeroER's method.

## 2. Current state

- `core/complexity_profile.py` — `ComplexityProfile` with sub-profiles. Relevant
  emitted fields (verified): `scoring.{score_histogram[20], dip_statistic,
  mass_above_threshold, mass_in_borderline, random_pair_above_threshold_rate (Optional),
  per_field_score_variance, n_pairs_scored}`; `cluster.{n_clusters, cluster_size_max,
  transitivity_rate, edge_confidence_min, oversized_cluster_count}`;
  `blocking.{n_blocks, block_sizes_max, singleton_block_count, oversized_block_count}`;
  `data.{identity_score, corruption_score}`.
- `core/autoconfig_history.py` — `RunHistory.pick_committed()` (lex key above) +
  `precision_collapse_floor=0.9`.
- `core/autoconfig_controller.py` — assembles the profile each iteration via
  `_assemble_profile`, runs the refit policy.
- `core/probabilistic.py` — Fellegi-Sunter EM (label-free separation).

## 3. Proposed architecture

A new pure-function module computes a `ZeroLabelConfidenceProfile` from an
already-assembled `ComplexityProfile` (+ the candidate config, + optional history).
It performs **zero** additional data access — it reads emitted aggregates only.
The controller attaches it to each iteration's profile right after
`_assemble_profile`. `pick_committed` consumes `overall_confidence` as a ranking
term behind an env flag (Phase 2), default-on later (Phase 3). The optimizer
(separate spec) calls the same `compute_zero_label_confidence` as its objective.

## 4. Data model changes

New dataclass in `core/complexity_profile.py` (co-located so serialization stays
in one place):

```python
@dataclass
class ZeroLabelConfidenceProfile:
    # Phase 1 — derivable from emitted signals TODAY:
    latent_separation: float          # 0..1, gap between the two score-histogram modes
    distribution_overlap: float       # 0..1, mass in the ambiguous middle (lower=better)
    score_entropy: float              # normalized Shannon entropy of score_histogram
    bimodality_or_dip_score: float    # from dip_statistic (+ histogram bimodality)
    random_pair_contamination: float  # from random_pair_above_threshold_rate (0 when None)
    transitive_coherence: float       # from transitivity_rate
    cluster_size_risk: float          # from cluster_size_max / oversized_cluster_count / n_clusters
    overall_confidence: float         # combined 0..1
    confidence_reasons: list[str]     # human-readable drivers (for reports/diagnostics)
    # Phase 2 — require NEW instrumentation or extra runs (None until then):
    cluster_bridge_risk: float | None = None      # needs cluster-graph articulation analysis
    perturbation_stability: float | None = None   # needs perturbation re-runs (env-gated)
    expected_precision_proxy: float | None = None # research; loud caveats
    expected_recall_proxy: float | None = None    # research; no label-free recall signal today
```

**Signal audit (the load-bearing part):**

| Signal | Source field(s) | Status |
|---|---|---|
| latent_separation | `scoring.score_histogram` (mode gap) — or F-S EM m/u means | Phase 1 |
| distribution_overlap | `scoring.mass_in_borderline` + histogram middle bins | Phase 1 |
| score_entropy | `scoring.score_histogram` | Phase 1 |
| bimodality_or_dip_score | `scoring.dip_statistic` | Phase 1 |
| random_pair_contamination | `scoring.random_pair_above_threshold_rate` (Optional → 0/skip when None) | Phase 1 |
| transitive_coherence | `cluster.transitivity_rate` | Phase 1 |
| cluster_size_risk | `cluster.{cluster_size_max, oversized_cluster_count, n_clusters}` | Phase 1 |
| cluster_bridge_risk | **not emitted** — needs articulation-point/bridge detection on the cluster graph | Phase 2 (new instrumentation) |
| perturbation_stability | **needs re-runs** on perturbed configs | Phase 2 (env-gated) |
| expected_precision_proxy | rough fn of contamination + mass; **easy to over-trust** | Phase 2 (research) |
| expected_recall_proxy | no label-free recall signal exists today | Phase 2 (research) |

Backcompat: the field is **optional** on `ComplexityProfile` (defaults `None`);
`to_legacy_dict()` adds a `zero_label` sub-dict only when present;
`normalized_signal_vector()` appends the Phase-1 scalars (stable order, appended
at the end so existing indices don't shift).

## 5. Algorithm details

`core/zero_label_confidence.py` (no heavy deps; `math`/stdlib only; sklearn only
behind a guarded import if ever needed):

- `score_distribution_confidence(scoring)` → latent_separation, distribution_overlap,
  score_entropy, bimodality_or_dip_score. From the 20-bin histogram: normalize to a
  pmf; entropy = `-Σ p log p / log n`; identify two modes and measure the
  normalized valley depth (separation) and middle-mass (overlap); fold in
  `dip_statistic`. (Alt path: if F-S EM ran, use trained m/u means directly.)
- `score_random_pair_contamination(scoring)` → from `random_pair_above_threshold_rate`;
  when `None`, mark `confidence_reasons += ["random-pair signal unavailable"]` and
  treat as neutral (do not penalize).
- `score_transitivity(cluster)` → `transitive_coherence = transitivity_rate`.
- `score_cluster_confidence(cluster, data)` → cluster_size_risk from oversized rate
  + max/median size skew; uses `edge_confidence_min` as a floor.
- `combine_zero_label_scores(...)` → weighted combine into `overall_confidence`,
  with **anti-degeneracy guards**: hard-cap confidence low when `mass_above_threshold`
  is extreme (everything-matches) or `n_clusters` collapses to ~1, regardless of a
  "clean-looking" histogram. Populates `confidence_reasons`.
- `compute_zero_label_confidence(profile, config, history=None)` → orchestrates the
  above; deterministic for a given `(profile, config, history)`.

Weighting is a tunable module constant (documented), not magic numbers inline.

## 6. Controller integration

- `_assemble_profile` (or immediately after it in `AutoConfigController.run`)
  calls `compute_zero_label_confidence` and attaches it to the `ComplexityProfile`.
- Pure + cheap (reads aggregates) → safe inside the iteration loop.
- Surfaced through `ComplexityProfile.to_legacy_dict()` / `normalized_signal_vector()`
  and `web/controller_telemetry.serialize_telemetry` (the single cross-surface serializer).

## 7. Commit selection

`RunHistory.pick_committed()` new lex key (Phase 2, env-gated):
`(health_rank, -overall_confidence, precision_collapse_guard, runtime_cost, iteration)`.
Penalize (inside `overall_confidence`, so the key stays simple): high
`mass_above_threshold`, high borderline mass, random-pair contamination, oversized
clusters, low transitivity, low dip/bimodality. **Crucially: never reward a lower
threshold purely because it inflates `mass_above_threshold`** — that's the exact
pathology. The existing `precision_collapse_floor` stays as a hard backstop.

## 8. Policy/rule integration (Phase 4, not now)

Keep rules as-is; use zero-label confidence only as a tie-breaker + diagnostic
initially. Future rules consuming sub-signals: `rule_low_latent_separation`,
`rule_high_random_pair_contamination`, `rule_cluster_bridge_risk`,
`rule_unstable_config`, `rule_precision_recall_proxy_conflict`.

## 9. Perturbation stability (Phase 2, env-gated)

`GOLDENMATCH_AUTOCONFIG_ZERO_LABEL_STABILITY=1`. For the committed (or top-k)
config only, re-run the **sample** under cheap perturbations: threshold ±0.05,
drop one weak field, alter a blocking pass, vary sample seed. Measure drift in
cluster count / max cluster size / edge count / score distribution / transitivity →
`perturbation_stability ∈ [0,1]`. Budget-gated; never on by default.

## 10. Public / API surfaces

- Python: add `zero_label` to `PostflightReport` / `dedupe_df` result telemetry.
- REST: serialized via `serialize_telemetry`; update `docs/rest-api.md`.
- MCP: surfaces through the existing `auto_configure`/telemetry tool payload (no new tool Phase 1).
- TUI: Controller tab shows `overall_confidence` + top `confidence_reasons`.
- TypeScript mirror (`packages/typescript/goldenmatch/src/core/autoconfig.ts`):
  mirror the Phase-1 scalar fields in the telemetry shape (it already mirrors
  `serialize_telemetry`). Tests in `tests/parity/`.
- Docs: `docs/python-api.md`, `docs/rest-api.md`, README auto-config section, CHANGELOG.
- `__init__.py`: export `ZeroLabelConfidenceProfile` + `compute_zero_label_confidence`.

## 11. Distributed / sampling constraints

Computes only from sampled-profile aggregates already emitted by the Phase-2
distributed controller (`take_sample_distributed` path). **No full-data
materialization.** `random_pair_above_threshold_rate` may be `None` on the
distributed path → handled as neutral (see §5).

## 12. Testing plan

- `tests/test_zero_label_confidence.py` — unit tests with synthetic
  `ComplexityProfile`s: clean-bimodal → high confidence; everything-matches →
  low (guard fires); no-matches → low; low-transitivity → low; `random_pair_rate=None`
  → neutral + reason logged. Determinism test (same input → same output).
- `tests/test_autoconfig_history.py` (extend) — `pick_committed` prefers a
  higher-confidence config over one with higher naive `mass_separation`;
  precision-collapse regression still demotes; no-matches regression.
- Distributed smoke: confidence computes from a sampled profile with `None` fields.
- Serialization/backcompat: a `ComplexityProfile` without the field round-trips
  (legacy dict has no `zero_label`); with it, `zero_label` present.
- TS parity: Phase-1 scalar fields present in the mirrored telemetry.
- **Pathology gate (acceptance):** the DQbench T1/T2/T3 adapter shapes — confirm
  ZeroER-commit does **not regress** the v1.12 composite (91.04) and improves
  commit choice on the everything-matches / low-transitivity shapes.

## 13. Rollout

- **Phase 1:** dataclass + `compute_zero_label_confidence` from existing signals;
  attach to `ComplexityProfile`; show in reports/telemetry. **No behavioral change**
  (commit key unchanged); optional logging.
- **Phase 2:** `pick_committed` uses confidence behind `GOLDENMATCH_AUTOCONFIG_ZERO_LABEL_COMMIT=1`;
  add perturbation stability behind `_STABILITY=1`; add bridge-risk instrumentation.
- **Phase 3:** enable confidence-commit by default after the pathology gate passes.
- **Phase 4:** new policy rules consume sub-signals.

## 14. Acceptance criteria

- No new required dependencies (sklearn only behind a guarded optional import).
- No data scans beyond existing emitted indicators.
- No breaking schema change without defaults (field optional; legacy dict additive).
- Deterministic for the same `(profile, config, history)`.
- Safe when the zero-label profile is absent (None) — all consumers null-guard.
- Measurable improvement over current commit selection on the pathology set,
  with no DQbench composite regression.

## 15. Exact file list to modify

- `core/complexity_profile.py` — add `ZeroLabelConfidenceProfile`; optional field on
  `ComplexityProfile`; `to_legacy_dict()` + `normalized_signal_vector()` additive.
- `core/zero_label_confidence.py` — **new**; the compute functions.
- `core/autoconfig_controller.py` — attach profile post-`_assemble_profile`.
- `core/autoconfig_history.py` — `pick_committed` env-gated key (Phase 2).
- `web/controller_telemetry.py` — serialize the Phase-1 scalars.
- `goldenmatch/__init__.py` — export the new type + fn.
- `packages/typescript/goldenmatch/src/core/autoconfig.ts` — mirror telemetry fields (Phase 1/3).
- Tests as in §12. Docs: `docs/python-api.md`, `docs/rest-api.md`, README, CHANGELOG.
- (Phase 2) `core/autoconfig_rules.py` — future rules; perturbation runner.

## 16. Open questions

- `overall_confidence` weighting — start equal-ish, then tune against the pathology
  set; expose as a module constant (and as the optimizer's pluggable `objective`).
- latent_separation: histogram-valley heuristic vs reuse F-S EM m/u means — prototype
  both in Phase 1, pick by the pathology gate.
- Do we need `cluster_bridge_risk` at all before measuring its lift? Defer until a
  shape demonstrates the gap (don't instrument speculatively).
</content>
