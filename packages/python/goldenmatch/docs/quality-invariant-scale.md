# Quality-invariant scale validation

Issue: [#510](https://github.com/benseverndev-oss/goldenmatch/issues/510) — part of the
Native Runtime + Local/In-house Embedding epic ([#504](https://github.com/benseverndev-oss/goldenmatch/issues/504)).

**Thesis (#510):** match quality and clustering behavior are invariant across scale.

**Status (this document, v0.1):**

- ✅ **Pipeline quality IS scale-invariant** on the realistic-vocab fixture
  across **1 K → 100 K** (Pairwise F1 1.0000 → 1.0000 → 0.9998; total drift
  0.0002, well inside the ≤ 0.005 / ≤ 0.01 acceptance — across two orders of
  magnitude). 1 M → 200 M pending on the bench box.
- ❌ Zero-config does **not** behave identically across scale on the
  *Phase-5* fixture (Pairwise F1 0.91 → 0.03 from 1 K → 10 K) — but that is
  a fixture pathology (literal `name_<cid>` low-cardinality tokens), not a
  pipeline failure. Documented below as the failure mode that exposed the
  need for a fair fixture in the first place.

The quality harness (`scripts/quality_invariant_scale.py`) ships both fixtures
(`--shape realistic` default, `--shape phase5` for the adversarial case) so the
two findings are reproducible from a fresh clone.

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

## Realistic-vocab shape: quality IS invariant

The harness now ships a second fixture, `--shape realistic`. It uses the same
5-rows-per-cluster + 10%-typo noise model as Phase-5, but with **5-syllable
hash-derived names** (24⁵ ≈ 8 M-combo space, independent salts for first vs
last, so (first, last) tuple collisions across distinct clusters are
vanishingly rare) plus a realistic address/city/zip/birth_year vocab. The
generator is in-process and deterministic from the seed; no field has the
literal `name_<cid>` pathology.

| Rows (clusters) | Pairwise F1 | B-cubed F1 | Cluster F1 | P / R (pairwise) | FP | Wall | Peak RSS | Controller |
|---|---|---|---|---|---|---|---|---|
| **1 000** (200) | **1.0000** | **1.0000** | **1.0000** | 1.00 / 1.00 | 0 | 3.5 s | 12 MB | YELLOW — `POLICY_SATISFIED` |
| **10 000** (2 000) | **1.0000** | **1.0000** | **1.0000** | 1.00 / 1.00 | 0 | ~25 s | — | YELLOW — `POLICY_SATISFIED` |
| **100 000** (20 000) | **0.9998** | **0.9999** | **0.9997** | 0.9996 / 1.00 | 75 | 187 s | 280 MB | YELLOW — `POLICY_SATISFIED` |

**Delta across the 1 K → 100 K range: 0.0002 on Pairwise F1**, 0.0001 on
B-cubed, 0.0003 on Cluster F1 — every dimension comfortably inside #510's
≤ 0.005 / ≤ 0.01 acceptance, across **two orders of magnitude**. At 100 K the
20 000 GT clusters are nearly all recovered exactly (19 998 / 20 000), and the
75 false positives out of ~200 K predicted pairs (0.037 %) are birthday-
collision territory — a handful of clusters whose 5-syl names plus typo'd
emails happen to share a fuzzy-matchable token with another cluster, not a
scale-dependent failure. The controller stays YELLOW / `POLICY_SATISFIED` at
every rung — auto-config finds a viable config independent of scale on this
shape.

So the **pipeline itself** preserves quality across this range when the
fixture isn't pathological. The Phase-5 failure mode (zero-config drifting to a
degenerate RED config under low-cardinality literal-pattern inputs) is an
auto-config robustness issue surfaced by an adversarial fixture, **not** an
intrinsic scale problem.

---

## What's still needed to fully close #510

1. **Larger rungs (100 K / 1 M / 10 M / 25 M / 50 M / 100 M / 200 M)** on the
   realistic fixture. The harness's per-rung JSON shape is already
   ladder-compatible. Local box can do up through ~100 K; everything above
   wants a Railway one-shot job modelled on `Dockerfile.embprov` (the #506
   embedding-provider job's pattern: pip-install goldenmatch from PyPI, copy
   `scripts/`, run `quality_invariant_scale.py --rows N --shape realistic`,
   results land in deploy logs).
2. **Backend parity at scale** — native kernels vs pure-Python pipeline
   produce equal clusters/scores. The per-kernel level is locked
   (`tests/test_native_parity.py`); pending here is the at-scale pipeline-level
   parity rerun.
3. **Auto-config low-card robustness** — separately trackable as a
   #491 / #195 workstream. Phase-5's failure mode (the original `name_<cid>`
   fixture) tells us the bound on what zero-config currently handles
   gracefully; closing it would let the realistic claim apply to a wider class
   of real-world inputs.
4. **Pinned-config rerun** — optionally, run the same ladder under an explicit
   config (e.g., exact-on-email + NE on names) to separate "pipeline quality
   at scale" from "auto-config behavior at scale" cleanly.

---

## Reproducing this page

```bash
# Realistic shape (default — the fair fixture):
python scripts/quality_invariant_scale.py --rows 1000  --out qis_r_1k.json
python scripts/quality_invariant_scale.py --rows 10000 --out qis_r_10k.json

# Phase-5 shape (the throughput-only fixture, kept for the failure-mode story):
python scripts/quality_invariant_scale.py --rows 1000  --shape phase5 --out qis_p_1k.json
python scripts/quality_invariant_scale.py --rows 10000 --shape phase5 --out qis_p_10k.json
```

Each `qis_*.json` is the canonical per-rung shape; append rows to the table
above as larger rungs land.
