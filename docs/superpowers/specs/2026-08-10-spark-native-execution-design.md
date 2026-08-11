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

## 2a. P0 RESULT — the tier runs on real Spark Connect (2026-08-11)

Run [31496638072](https://github.com/benseverndev-oss/goldenmatch/actions/runs/31496638072),
`spark_connect` lane: pyspark 4 against Spark's own `local[*]` Connect server,
pysail asserted absent.

```
20 failed, 36 passed, 8 skipped in 408.49s
```

The control passed: the `sail` lane (pysail) was **green** on the same commit, so
the fixture extraction is behaviour-preserving and the delta is the backend.

### The headline: no Spark Connect API gaps were found

36 tests pass. Session creation, DataFrame ops, SQL, config validation, and the
whole `test_sail_r3_feature_gate.py` surface all work unmodified against real
Spark. **The "tier is backend-agnostic" inference is confirmed.** P1–P6 are not
reshaped.

### All 20 failures have ONE cause

```
ModuleNotFoundError: No module named 'goldenmatch'   (73x)
ModuleNotFoundError: No module named 'pandas'        (13x)
```

raised inside the **Python UDF worker**. The correlation is exact: every failing
test executes the pipeline (and therefore a `pandas_udf`); every passing test
does not. Failures cluster in `clustering_parity`, `determinism`,
`golden_parity`, `identity_incremental`, `score_parity`.

**Classification: (c) environment — NOT (a) an API gap, NOT (b) a Sail-ism.**

### What this proves, and it is the important part

**The pysail lane structurally cannot catch this.** Sail's Connect server runs
**in-process**, so its Python worker shares the client's interpreter and
`import goldenmatch` simply works. Real Spark **forks a separate Python worker**
with its own environment. Every "it works distributed" signal from S1–S5 CI was
produced by a harness that could not exercise the executor dependency path at
all. (The 2026-06-15 GKE run did exercise it — by baking the deps into the worker
image.)

So P1 is **not plumbing, it is the blocker**, and P0 has demonstrated that
empirically rather than by argument. `addArtifacts` is precisely the mechanism
these 20 failures need.

### Consequences

- **P1 is promoted** to the first real work item, with its exit criterion
  unchanged and now clearly load-bearing: assert the kernel *loaded on an
  executor*, do not infer it.
- **The `spark_connect` lane must stay red until P1 lands.** That is the correct
  state. It goes into `ci-required` at P2, once P1 makes it green.
- No spec change is needed to §3–§9: the architecture survives the experiment.

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

## 6. REVERSED — the f32/f64 gate: **Option B** (was A)

> **Decision (2026-08-11, delegated by Ben): Option A.**
> **REVERSED the same day by its own condition 2. Option B — the kernel must
> return f64.**

### The reversal, and the evidence

Condition 2 (the decision-stability test) failed on its first CI run
(run 31521331431, `python_goldenmatch` shard 3):

```
jaro_winkler @ threshold 0.95: 2 pair(s) changed decision
  ('Jonathan','Jonothan')  pure=0.950000000  native=0.949999988
  ('Anderson','Andersen')  pure=0.950000000  native=0.949999988
```

That is exactly the documented reversal trigger: **realistic threshold,
realistic data, membership moves.** Not a constructed adversarial case — two
ordinary surname pairs at 0.95, one of the most common thresholds in use.

**Why it is systematic rather than unlucky.** Jaro-Winkler produces exact
rational values, and those land on round thresholds far more often than intuition
suggests. Here the f64 floor is **exactly 0.95**; f32 cannot represent it, so the
nearest value is 0.949999988, and `>= 0.95` flips from True to False. Every
threshold JW can hit exactly — 0.85, 0.9, 0.95 — is a collision site. The
1e-6 tolerance was never the problem; the problem is that a threshold comparison
turns any epsilon into a binary difference.

This is the distinction the original decision leaned on and got wrong: a *stated*
tolerance is fine for a score you report, and not fine for a score you
**threshold**. Scoring is thresholded here, so f32 is not acceptable.

### What Option B requires

`score_field_pairwise` returns f64. That breaks the f32 convention shared with
`score_field_matrix` and the DataFusion FFI scorer, which was the argument
against B — and the argument is now outweighed: the convention buys FFI payload
size, and it costs decision reproducibility against the one-box engine.

Consider whether the convention itself should change for any scorer whose output
is thresholded, rather than special-casing this one call site (§6 option C's
two-return-width shape remains the thing to avoid).

### What stays

- The decision-stability test **stays and becomes B's acceptance gate.** It was
  written to catch exactly this and did so on its first run.
- Condition 1 (parity on the PUBLISHED wheel) still applies to B.
- `sail_scoring` stays in `_FALLBACK_ONLY` until B lands. The gate was never
  lifted, so nothing shipped on the wrong premise.

### Original reasoning for A, kept for the record

`sail_scoring` is `_FALLBACK_ONLY` because `score_field_pairwise` returns f32
where the pure floor returns f64. Until this resolves, **the native kernel does
not run under `auto`** and the goal of this spec is unmet by default.

### Why A

**The tolerance already exists and is already tested.**
`tests/test_sail_scorer_native_parity.py` asserts
`max(|native - pure|) < 1e-6` across every supported scorer, plus flag routing,
identical-string, and length-mismatch cases. This is not accepting an unknown
divergence; it is a bound that has been measured and pinned since the battery
was written.

**The code already states its own lift condition.** The `_FALLBACK_ONLY`
rationale in `core/_native_loader.py` says sail_scoring *"stays Python under
`auto` until its parity battery is green on the PUBLISHED wheel."* So the open
question was never whether f32 is acceptable — it was whether that stated
condition had been met. That is a task, not a philosophy.

**f32 is the convention, not the exception.** `score_field_matrix` and the
DataFusion FFI scorer already return f32. B breaks a repo-wide convention for one
call site; C creates two return widths in one kernel family — the dual-lane shape
the arrow migration spent months eliminating.

**The divergence class is already accepted elsewhere.** The same rationale notes
FS block scoring carries this exact class and is gated by its own
`GOLDENMATCH_FS_NATIVE` env var rather than being permanently off.

### The objection, addressed

Accepting a divergence in a codebase that has just spent a long arc killing
silent ones deserves scrutiny. The distinction is categorical: the #2462 bug
(`"col" not in tbl.columns` silently returning `True`) was **undetectable,
unbounded, and untested**. This is **bounded at 1e-6, asserted in CI, and written
down**. A stated tolerance is an engineering decision; a silent one is a defect.

### Conditions (both required before `sail_scoring` leaves `_FALLBACK_ONLY`)

1. **The parity battery runs against the PUBLISHED wheel**, not an in-tree build.
   This is the #688 lesson: an in-tree build masks symbol skew, and "we tested
   it" is hollow if the artifact users install differs.
2. **Add a threshold-boundary test.** The existing battery proves *score*
   equality within 1e-6; it does **not** prove *decision* stability. The
   user-visible risk is not score drift, it is a pair flipping across a threshold
   and changing cluster membership. Score-tolerance != decision-stability, and
   only the second is observable in output. This condition is not optional — A is
   under-tested without it in precisely the dimension that matters.

### What would reverse this

If the boundary test shows cluster membership moving at realistic thresholds on
real data — not a constructed adversarial case — then f32 buys throughput at the
cost of reproducibility and **B becomes correct despite the convention break**.
Re-open this section if that happens.

### The options as evaluated

| Option | Consequence |
|---|---|
| **A. Accept f32, state a tolerance** | Cheapest. Scores differ from the one-box engine in the ~1e-7 band, which can flip a pair at a threshold boundary. Needs an explicit documented tolerance and a parity battery asserting cluster-level equivalence, not score equality. |
| **B. Return f64 from the kernel** | Removes the divergence entirely. Costs a kernel change and roughly 2x the FFI payload; the scorer convention elsewhere (`score_field_matrix`, the DataFusion FFI scorer) is f32, so this breaks a repo-wide convention for one call site. |
| **C. f64 only on the Spark path** | Keeps the convention and removes the divergence where it matters. Two return widths in one kernel family — the dual-lane shape the arrow migration spent months removing. |

**Selected: A** (see the decision block above). B and C remain recorded so a
future reader can see what was weighed, and B is the documented fallback if the
boundary test in condition 2 comes back bad.

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

### P1 RESULT (2026-08-11) — dependency delivery works, 18 of 20 fixed

Run [31516855744](https://github.com/benseverndev-oss/goldenmatch/actions/runs/31516855744):

| | failed | passed | skipped |
|---|---|---|---|
| P0 (no deps shipped) | 20 | 36 | 8 |
| **P1** (venv via `addArtifact`) | **2** | **54** | 8 |

Every `ModuleNotFoundError` is gone. The packed archive is independently verified
by the `pack-executor-env` lane (run 31514938174): `goldenmatch`,
`goldenmatch.core.strsim`, `pandas`, `pyarrow` all import on the shipped
interpreter, and **`native kernel present: True`** — the abi3 wheel travels, so
P3 has a kernel to switch on.

Two corrections the work produced, both worth keeping:

- **rapidfuzz is not a goldenmatch dependency** (dev-only extra; the scorer floor
  is the owned `core.strsim`). `sail/scorers.py`'s docstring said otherwise and
  has been fixed — it cost a CI round trip.
- **pandas must be shipped explicitly.** goldenmatch does not depend on it, but
  `pandas_udf` requires it in the worker. Installing goldenmatch alone produces
  an archive that unpacks cleanly and cannot run a single UDF.

### P2a — WCC broadcast join OOMs on real Spark *(new, from P1)*

The 2 residual failures are `test_sail_clustering_parity`'s long-chain tests:

```
Not enough memory to build and broadcast the table to all worker nodes
OutOfMemoryError: Java heap space
```

Neither a dependency nor an API gap. `xfail`'d **non-strictly** on real Spark so a
larger runner passing does not fail the lane either.

**The open question, and it is not a config question.** Two readings:

- a small-CI-runner artifact (7 GB heap), fixable with
  `spark.sql.autoBroadcastJoinThreshold=-1` around the loop; or
- a genuine tier defect — an iterative join loop that relies on broadcast will
  hurt on any cluster.

**Lean the second.** This same WCC loop already wedged on Sail at 12K rows for a
*different* reason (no lineage truncation). A loop that fails on both backends for
different reasons is usually itself the problem. Setting the threshold to make CI
green would be exactly the "make the lane pass" move this programme keeps
refusing.

Resolve before P4/P5 put real workloads through it.

### P2 — Drop Sail, rename the tier

`goldenmatch/sail/` → `goldenmatch/spark/`; `SAIL_REMOTE` → `SPARK_REMOTE` (keep
the old name as a deprecated alias); remove the `[sail]` extra's `pysail` dep;
lift `pyspark[connect]<4`; delete `_truncate_lineage` once P0 confirms real
Spark's `localCheckpoint`; retire `deploy/sail-gke/` from the product docs;
amend ADR-0004.

### P3 — Make the kernel return f64, THEN take `sail_scoring` out of `_FALLBACK_ONLY`

§6 was decided as A and **reversed to B** by its own condition 2 on the first CI
run: `jaro_winkler` at 0.95 flips two ordinary surname pairs, because the f64
floor is exactly 0.95 and f32's nearest value is 0.949999988. A tolerance is fine
for a score you report and not fine for one you **threshold**.

So P3 is no longer "lift the gate"; it is "remove the divergence, then lift the
gate".

1. Add the threshold-boundary test (§6 condition 2): assert **cluster membership**
   is stable across the native/pure boundary at realistic thresholds, not merely
   that scores agree within 1e-6. This is the test that does not exist yet.
2. Run the parity battery against the **PUBLISHED** wheel, not an in-tree build
   (§6 condition 1, the #688 lesson).
3. Only then remove `"sail_scoring"` from `_FALLBACK_ONLY` in
   `core/_native_loader.py` and update its rationale comment to record that the
   stated lift condition was met, with the tolerance named.

**Exit:** native runs under `auto` on Spark; both conditions demonstrably met.

**Abort criterion:** if step 1 shows membership moving on real data, stop and
switch to §6 option B (f64 kernel). Do NOT widen the tolerance to make the test
pass — that converts a stated tolerance back into a silent one.

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
