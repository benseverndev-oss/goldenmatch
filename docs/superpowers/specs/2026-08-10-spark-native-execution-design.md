# Spark-native execution — GM's Rust kernels on the customer's Spark cluster

**Status:** proposed (2026-08-10, Ben) • **Supersedes the framing of**
[`decisions/0004-sail-tier-scope`](../../../context-network/decisions/0004-sail-tier-scope.md)

**Goal:** run GoldenMatch's native optimized kernels **inside a Spark cluster the
customer already operates**, so a Splink-on-Spark team can adopt GM without new
infrastructure.

---

## 1. Why this, and why it is not the other three things

The goal was arrived at by elimination, and the discarded framings are recorded
here because each one resurfaces naturally.

| Framing | Verdict |
|---|---|
| "Sail instead of Ray" | Wrong axis. Ray is a different distributed path for non-Spark users; nothing about Spark competes with it. |
| "Match Splink's output" | Explicitly **not** the goal (Ben, 2026-08-10). Cutover means adopting GM's matching, seeded from their config — sold on quality, not equivalence. |
| "Port Spark to Rust ourselves" | Optimises the wrong variable. The customer's value is that they *already run Spark*; handing them a new engine reinstates the friction we are removing. See §9. |
| **"Run GM's native kernels on their Spark"** | **This spec.** |

**The differentiator.** Splink generates **Spark SQL** — the engine performs the
comparisons. GM would ship **vectorized Rust executing over Arrow batches** to
the same executors: one FFI crossing per batch, zero-copy, row-parallel. Same
cluster, same data, materially different per-core throughput.

That is a reason to switch that survives "our Splink model already works". Parity
arguments do not.

---

## 2. What exists today (verified against `origin/main`, 2026-08-10)

Inventory, because the gap is smaller in some places and much larger in others
than the docs suggest.

### Already built

- **The Arrow → Rust bridge.** `goldenmatch/sail/scorers.py::_native_scores`
  calls `score_field_pairwise(aa, bb, scorer_id)` on `pa.large_string` arrays
  inside a `pandas_udf("double")`. This is the mechanism the whole goal needs,
  and it is written.
- **Linux executor wheels.** `publish-goldenmatch-native.yml` builds
  **manylinux 2_28, x86_64 + aarch64, abi3 (3.11+)** — exactly the shape a Spark
  executor needs.
- **Dependency delivery.** `SparkSession.addArtifacts(*path, pyfile=…,
  archive=…, file=…)` ships Python deps from the client at session time;
  archives are unpacked executor-side automatically. **This API is Spark
  Connect-only** — it raises on a classic session. See §10 sources.
- **The Splink on-ramp.** `import-splink` (settings/trained-model JSON →
  `GoldenMatchConfig`), `migrate-splink` (convert, verify, run),
  `config/from_splink.py`, `splink_upgrade*.py`, ADR-0008 FS/Splink parity, and
  a published bakeoff.
- **The tier is not Sail-coupled.** `goldenmatch/sail/session.py` is
  `SparkSession.builder.remote(url).getOrCreate()` and a grep of
  `goldenmatch/sail/` finds **zero** Sail-specific API calls. `SAIL_REMOTE` is
  only an env var name.

### Built but switched off

`_native_loader.py:199`:

```python
_FALLBACK_ONLY: frozenset[str] = frozenset({"sail_scoring"})
```

with the rationale at `:194` — `score_field_pairwise` returns **f32** against
the pure **f64** floor. So under `GOLDENMATCH_NATIVE=auto` the Spark tier
**never runs the Rust kernel**; it silently takes pure rapidfuzz. Native engages
only under explicit `GOLDENMATCH_NATIVE=1`.

The headline capability is gated off by a float-width decision. See §6.

### Not built

- **Fellegi-Sunter on Spark.** No `m_prob`, `u_prob`, `match_weight`, or `log2`
  anywhere in `goldenmatch/sail/`. `scoring.py::score_and_dedup` is a single
  rapidfuzz similarity, threshold-filtered, deduped by `GROUP BY max`.
- **Config-driven execution.** `run_sail_pipeline(source_df, *, id_col,
  block_col, value_col, golden_cols, scorer_name, threshold, strategy, …)` takes
  scalars. **`GoldenMatchConfig` never reaches the tier.** One blocking column,
  one value column, one scorer, one threshold.
- **Any wiring into the product.** The public `backend` parameter accepts
  `None`, `"ray"`, `"duckdb"` — there is no `"spark"`. `run_sail_pipeline`'s only
  callers are `scripts/bench_sail_100m.py`, `deploy/sail-gke/run_smoke.py`, and
  its own tests.
- **Native coverage beyond the field scorer.** Blocking, WCC, golden-record
  building are PySpark DataFrame ops.

**Summary:** the Spark tier is a proof that the plan compiles and distributes —
which is precisely what S1–S5 set out to show. It is not a foundation a customer
can be pointed at.

---

## 3. Decision: Apache Spark Connect is the target; Sail is removed

**Sail's only remaining job is replaceable by Spark itself.**
`SparkSession.builder.remote("local")` (or `local[*]` / `local-cluster[*]`)
starts a local Spark Connect server with no cluster — which is exactly what the
in-process Sail server provides for CI today.

**What removing Sail costs:** the in-process test server becomes a **JVM**
instead of a Rust binary. CI runners ship Java; this is slower startup and more
memory, not a blocker. `deploy/sail-gke/` stops being product-relevant (its WCC
findings stand). **No tier code is lost** — it was never Sail-coupled.

**What removing Sail buys:**

1. **The only observed scaling failure disappears.** The real GKE run wedged at
   12K rows (2.9s @1.5K → 22.4s @4K → wedged) because Sail's `cache` is a no-op
   and `persist` / `localCheckpoint` / `checkpoint` are unimplemented
   ([lakehq/sail#482](https://github.com/lakehq/sail/issues/482)), so the WCC
   loop grows the Spark Connect plan unbounded. **Real Spark has all three.**
   `clustering.py::_truncate_lineage`'s parquet write/read barrier becomes
   unnecessary overhead to delete.
2. **Spark 4 unblocks.** `pyspark[connect]>=3.5,<4` is pinned *because of
   pysail* — the pyproject comment says to revisit "when pysail ships a
   Spark-4-Connect-compatible release". Dropping pysail drops the ceiling.
3. **`addArtifacts` becomes guaranteed** by the Spark Connect contract rather
   than an open question about another implementation. Sail's UDF and deployment
   docs do not mention dependency shipping at all, and the GKE Dockerfile bundles
   `goldenmatch[sail,native]` into the worker image — which is what you do when
   artifact transfer is unavailable.
4. **No upstream roadmap dependency.** #482 is "planned".
5. **The name stops hiding the product.** A Spark shop searching the repo for
   "Spark" currently finds nothing: real Spark, Databricks, and EMR appear
   **nowhere** in the sail docs or ADR-0004.

**Ray is untouched.** It remains the distributed path for non-Spark users. This
spec does not revisit it.

---

## 4. Non-goals

- Splink output parity (§1).
- Byte-identical results against the one-box engine at scale — see §6, the f32
  decision may make this deliberately false.
- Replacing Ray.
- Building a distributed engine (§9).
- Supporting classic (non-Connect) Spark. `addArtifacts` is Connect-only and it
  is load-bearing for the zero-install story.

---

## 5. Architecture

```
client (laptop / CI / notebook)
  │  GoldenMatchConfig  ──────────────────┐
  │  addArtifacts(archive=venv-linux.tgz) │   ships goldenmatch + rapidfuzz
  │                                       │   + the abi3 native wheel
  └── sc://their-cluster:15002 ───────────┘
                │
        their Spark cluster (scheduler, shuffle, spill — theirs)
                │
        executors: pandas_udf → pa.large_string → score_field_pairwise (Rust)
```

Spark owns orchestration, shuffle, and fault tolerance. GM owns compute. The
seam is one Arrow FFI crossing per batch.

---

## 6. OPEN DECISION — the f32/f64 gate

`sail_scoring` is `_FALLBACK_ONLY` because `score_field_pairwise` returns f32
where the pure floor returns f64. Until this resolves, **the native kernel does
not run under `auto`** and the goal of this spec is unmet by default.

Three ways out:

| Option | Consequence |
|---|---|
| **A. Accept f32, state a tolerance** | Cheapest. Scores differ from the one-box engine in the ~1e-7 band, which can flip a pair at a threshold boundary. Needs an explicit documented tolerance and a parity battery asserting cluster-level equivalence, not score equality. |
| **B. Return f64 from the kernel** | Removes the divergence entirely. Costs a kernel change and roughly 2x the FFI payload; the scorer convention elsewhere (`score_field_matrix`, the DataFusion FFI scorer) is f32, so this breaks a repo-wide convention for one call site. |
| **C. f64 only on the Spark path** | Keeps the convention and removes the divergence where it matters. Two return widths in one kernel family — the dual-lane shape the arrow migration spent months removing. |

**Recommendation: A**, with the tolerance written down and a threshold-boundary
test. This session's work was largely about *silent* divergence; a stated,
tested tolerance is a different thing from an unnoticed one. **This is Ben's
call and should be settled before Phase 3.**

---

## 7. Phases

Dependency-ordered. Each is independently shippable.

### P0 — Prove it on real Spark Connect *(load-bearing; do first)*

Everything else rests on an assumption currently supported only by "no
Sail-specific calls in the source". Add a CI lane that stands up Apache Spark
3.5 Connect (container or `builder.remote("local[*]")`) and runs the existing
`tests/test_sail_*.py` against it.

**Exit:** the existing tier is green on real Spark, or the incompatibilities are
enumerated. A red here reshapes every later phase.

### P1 — Executor delivery, proven not assumed

Package a Linux venv (`venv-pack` / PEX / `uv`) carrying `goldenmatch` +
`rapidfuzz` + the abi3 wheel; ship via `addArtifacts(archive=…)`. Document the
cluster-libraries path (Databricks/EMR) as the alternative.

**Exit:** a test asserts the kernel **actually loaded on an executor** —
`native_dispatch_report` shows native, not the pure fallback. A silent fallback
here looks like success and delivers none of the value.

**Known trap:** a venv packed on macOS/Windows carries no working Linux kernel.
The pure fallback masks it. The assertion above is the guard.

### P2 — Drop Sail, rename the tier

`goldenmatch/sail/` → `goldenmatch/spark/`; `SAIL_REMOTE` → `SPARK_REMOTE` (keep
the old name as a deprecated alias); remove the `[sail]` extra's `pysail` dep;
lift `pyspark[connect]<4`; delete `_truncate_lineage` once P0 confirms real
Spark's `localCheckpoint`; retire `deploy/sail-gke/` from the product docs;
amend ADR-0004.

### P3 — Resolve the f32 gate, take `sail_scoring` out of `_FALLBACK_ONLY`

Per §6. **Blocked on Ben's decision.**

**Exit:** native runs under `auto` on Spark, with a parity battery on the
**published** wheel (per the loader's own rule, and the #688 symbol-skew
lesson).

### P4 — Config-driven execution + `backend="spark"`

Make the tier consume a `GoldenMatchConfig`: multi-matchkey, multi-blocking-rule,
golden rules. Wire `backend="spark"` into the public API so `import-splink`
output runs unchanged.

### P5 — Fellegi-Sunter on Spark

The largest piece and the one that makes a Splink user's model executable at all.
`fs-core` exists; the model must be re-expressed against the DataFrame API — the
same shape as the DataFusion spine work, not a research problem.

### P6 — Zero-config on Spark

Distributed auto-config, so `backend="spark"` supports both driver modes (Ben,
2026-08-10: "both, user picks per run").

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| Spark Connect API coverage differs from what the tier uses | P0, before anything is built on it |
| Silent pure-Python fallback on executors → all the infra, none of the speed | P1's dispatch assertion |
| f32 divergence flips a boundary pair | §6, stated tolerance + boundary test |
| Scope: P4–P6 are large | Each independently shippable; P0–P2 deliver a working, honest tier alone |
| Locked-down clusters forbid arbitrary artifact upload | Cluster-libraries path documented in P1 |
| A packed venv is platform-specific | Build in CI on Linux, never on a dev laptop |

---

## 9. Why not build our own engine

Considered and rejected (Ben raised it 2026-08-10).

**The necessary elements are already ported.** `score-core`, `cluster-core`,
`fs-core`, `graph-core`, `datafusion-udf`, and the DataFusion spine (stages A–E,
with spill) are the Rust port of what GM needs from a query engine. What is
missing is *distribution*, and per #955 the surface that genuinely distributes is
narrow: scoring fan-out and WCC.

**It does not serve the goal.** The customer's Spark cluster is the asset. A new
engine reinstates exactly the adoption friction being removed.

**Sail already is that port** — Rust, DataFusion-backed, Apache-2.0, with a team.
Its gap (#482) is a *feature* gap; a benchmark has already been contributed to
that thread, which is the expensive half of an upstream fix.

**The hard part is shuffle**, not the DataFrame API — plus fault tolerance,
scheduling, spill, stragglers. #957 is the evidence: GM runs on Ray, a mature
distributed runtime, and concurrency/backpressure tuning is *still* unfinished
after months. Operating someone else's solved shuffle costs that much; writing
one costs an order more.

**If the itch persists**, the adjacent path is **Ballista** (DataFusion's
distributed scheduler) over the existing spine — not a Spark reimplementation.

---

## 10. Sources

- [`SparkSession.addArtifacts`](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.SparkSession.addArtifacts.html) — Connect-only; `pyfile` / `archive` / `file`
- [SPARK-50718](https://www.mail-archive.com/commits@spark.apache.org/msg71171.html) — `addArtifact(s)` for PySpark
- [Application Development with Spark Connect](https://spark.apache.org/docs/latest/app-dev-spark-connect.html) — `spark.remote=local[...]` starts a local Connect server
- [lakehq/sail#482](https://github.com/lakehq/sail/issues/482) — lineage truncation, planned
- `deploy/sail-gke/README.md` — the 2026-06-15 GKE smoke proof and the WCC wedge
