# Quality-invariant scale validation

Issue: [#510](https://github.com/benseverndev-oss/goldenmatch/issues/510) — part of the
Native Runtime + Local/In-house Embedding epic ([#504](https://github.com/benseverndev-oss/goldenmatch/issues/504)).

**Thesis (#510):** match quality and clustering behavior are invariant across scale.

**Status (this document, v0):** *not yet validated* — the existing scale infra
measures **throughput** (wall, peak RSS) but not **quality**. This page introduces
the quality harness (`scripts/quality_invariant_scale.py`), the methodology, and the
first two rungs. It also flags the concrete obstacle the small-rung evidence
already surfaces: zero-config on the Phase-5 synthetic does **not** behave
identically across scale, so the established "scale envelope" claims need a
proper quality fixture before they can be reissued as quality claims.

---

## Methodology

### Dataset

`scripts/quality_invariant_scale.py::generate_with_gt(n_rows, seed)` reproduces the
[Phase-5 generator](../scripts/generate_phase5_dataset.py) in-process but keeps
the cluster id so we have ground truth:

- **5 rows per cluster** (so `n_rows = 5 × n_clusters`).
- Fields: `first_name = "name_<cid>"`, `last_name = "sur_<cid>"`,
  `email = "<first>.<last>@example.com"`, `zip = cid % 100000` (5-digit).
- **10% typo injection** on `first_name` (`a` → `@`). The same character flip
  carries into `email` (because `email` is built from the post-typo `first_name`).
- Deterministic from `seed`.

The shipped CLI generator drops `__cid__` from the parquet (we need it for
scoring), and `ProcessPoolExecutor.as_completed` doesn't preserve chunk order, so
reconstructing GT from row position on a pre-generated parquet isn't reliable.
The in-process generator avoids both problems.

### Pipeline

Zero-config: `goldenmatch.dedupe_df(df)`. We deliberately use the same auto-config
path real users hit. The committed `ComplexityProfile.health`, `StopReason`, and
the controller's refit decision trail are captured per rung so config drift across
scales is visible alongside the metrics.

`GOLDENMATCH_AUTOCONFIG_MEMORY=0` — disables cross-run memory so each rung is
reproducible from scratch.

### Metrics

| Metric | What it measures |
|---|---|
| **Pairwise F1** | `TP/FP/FN` over the set of within-cluster pairs (canonical `(min, max)`). |
| **B-cubed F1** | Per-record `precision = |true ∩ predicted| / |predicted|`, `recall = |true ∩ predicted| / |true|`, averaged across all `N` rows. The classic Bagga & Baldwin (1998) cluster-evaluation metric — robust to cluster-count differences. |
| **Cluster F1** | Strict exact-set match: a predicted multi-member cluster scores a true positive only if its row set equals some ground-truth cluster's row set exactly. |

Plus: wall, peak RSS, `multi_member_clusters`, `predicted_clusters`, GT cluster
count, and the committed controller state (`health`, `stop_reason`, fired refit
decisions).

### Acceptance bar (per #510)

- Pairwise F1 / B-cubed F1 delta across rungs ≤ **0.005**.
- Cluster F1 delta across rungs ≤ **0.01**.
- Backend parity: native kernels vs pure-Python produce equal clusters/scores
  (separate workstream — `tests/test_native_parity.py` already locks this for the
  per-kernel level; pending here is the at-scale pipeline parity rerun).

---

## Results so far

| Rows (clusters) | Pairwise F1 | B-cubed F1 | Cluster F1 | P / R (pairwise) | FP | Wall | Controller |
|---|---|---|---|---|---|---|---|
| **1 000** (200) | **0.9107** | **0.9298** | 0.6039 | 1.00 / 0.836 | 0 | 3.5 s | **RED** — `POLICY_SATISFIED` / `failing_subprofile=scoring` |
| **10 000** (2 000) | **0.0289** | **0.1411** | 0.0080 | 0.015 / 0.884 | **1 186 180** | 147 s | **RED** — `BUDGET_TIME` / `failing_subprofile=blocking` + `auto-split edge-work budget (5 000 000) exhausted; 7 oversized clusters excluded` |

**Pairwise F1 collapses 0.91 → 0.03 from 1 K to 10 K**, and `fp` explodes from
**0 to 1.18 M**. Quality is *very* much not invariant on this fixture.

### Why this is a fixture finding, not a pipeline finding

The Phase-5 generator was built for **throughput** (`scripts/bench_phase5_*`,
`bench-dataset-v1`). Its `name_<cid>` / `sur_<cid>` literal-encoded fields have
two properties that are fine for throughput stress but adversarial for ER
quality:

1. **Low cardinality by construction** (`cardinality_ratio ≈ 1/5` for every
   field): no column passes the `≥ 0.5` exact-matchkey guard, so auto-config
   falls back to fuzzy-only — exactly the family of pathology #538 / #541
   were created to mitigate, but the composites need a DOB/year anchor which
   this fixture doesn't have.
2. **High inter-cluster token similarity**: `name_0` vs `name_1` is one
   character apart. At 2 K clusters this drives an enormous mass of borderline
   fuzzy pairs over threshold, and the controller (RED, `BUDGET_TIME` exhausted)
   commits a config it knows is degenerate.

At 1 K the same shape commits RED too — but the 200-cluster cardinality keeps
borderline pair count below the over-merge threshold, so precision *happens* to
stay at 1.0. That's accidental, not invariant, which is why the 10 K rung is the
informative one.

So the published claim has to be honest: **zero-config quality on the Phase-5
synthetic is not scale-invariant, and the existing scale-envelope numbers can't
be reissued as quality numbers without a different fixture**.

---

## What's needed to actually close #510

A proper quality-invariance result needs at least one of:

1. **A realistic synthetic** (varied syllable-based vocabs, like the one
   `scripts/bench_embedding_providers.py::run_synthetic` already uses for #506)
   so inter-cluster token similarity isn't pathological. That isolates whether
   the *pipeline* (not the fixture) preserves quality across scale.
2. **A pinned config** (e.g., exact-on-email with negative evidence on names,
   or a fixed weighted matchkey) so the at-scale metric measures the pipeline,
   not auto-config drift. Drift itself is worth reporting alongside.
3. **Auto-config improvements** for low-cardinality literal-pattern shapes
   (#491 lever-coverage / #195 controller behavior at low budgets) — a real,
   separately-trackable workstream that would let zero-config quality be
   invariant on a wider class of inputs.

Bigger rungs (1 M / 10 M / 25 M / 50 M / 100 M / 200 M) need the Railway
`goldenmatch-bench-gen` box (or a sibling one-shot job modeled on
`Dockerfile.embprov`). The harness's per-rung JSON shape is already
ladder-compatible — wiring the bench-box runs and appending them to this table
is the next deliverable once a meaningful fixture is in place.

---

## Reproducing this page

```bash
# One rung end-to-end, locally:
python scripts/quality_invariant_scale.py --rows 1000  --out qis_1k.json
python scripts/quality_invariant_scale.py --rows 10000 --out qis_10k.json
```

Each `qis_*.json` is the canonical per-rung shape; append rows to the table
above as larger rungs land.
