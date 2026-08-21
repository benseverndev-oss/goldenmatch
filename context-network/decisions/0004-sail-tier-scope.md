# 0004 — Sail tier scope (amended twice; SUPERSEDED 2026-08-11 by the Spark-native target)

**Status:** superseded (2026-08-11) • accepted 2026-06-03 • amended 2026-06-15
• **Superseding spec:** `docs/superpowers/specs/2026-08-10-spark-native-execution-design.md`
• **Original spec:** `docs/superpowers/specs/2026-06-03-sail-tier-design.md`

## Amendment 2 (2026-08-11, Ben) — the axis was wrong; the target is Apache Spark

Both this decision and its first amendment argue about **Sail versus Ray**. That is
the wrong axis, and reframing it is what P0–P3 of the superseding spec acted on.

**The goal is to run GoldenMatch's native Rust kernels inside a Spark cluster the
customer already operates** — so a Splink-on-Spark team adopts GM with no new
infrastructure. Against that goal:

- **Ray never competed.** It is the distributed path for users who are not on
  Spark. Nothing here retires or diminishes it.
- **Sail was the test harness, not the product.** The tier is
  `SparkSession.builder.remote(url)` with **zero** Sail-specific calls, so it
  always spoke generic Spark Connect. Sail is one server implementation; Apache
  Spark is another, and it is the one customers already run.
- **The customer's cluster is the asset.** Handing them a new engine reinstates
  the friction the whole exercise removes.

### What the reframing produced, in evidence rather than argument

| | |
|---|---|
| P0 (run 31496638072) | the tier runs on real Spark Connect with **no API gaps** — 36 tests pass unmodified. All 20 failures were one cause: the executor's Python worker could not import goldenmatch. |
| P1 (run 31516855744) | `addArtifact` + `spark.sql.execution.pyspark.python` ships the client venv to executors: **20 failures → 2**, no cluster-side install. |
| P2a | the residual 2 were a **tier defect**, not a runner artifact: the WCC loop never truncated its plan. Sail wedged (recompute); Spark OOM'd (broadcast). Same cause, and the diagnosis was already written in `_truncate_lineage`'s own docstring. |
| P3 | the native scorer narrowed to f32 at the FFI boundary, which **changed match decisions** at round thresholds. Fixed at the source (f64 kernel, `goldenmatch-native 0.1.21`) and the gate lifted. |

**The decisive practical difference:** `localCheckpoint` and `addArtifacts` both
exist on Apache Spark and are missing or unproven on Sail. The first is what fixes
the WCC wedge; the second is what makes the zero-install cutover true. Sail's
`pysail` dependency also pins `pyspark[connect]<4`, capping the very users this
targets.

**Sail's remaining role** is the no-cluster dev/CI server — and even that is
replaceable by `SparkSession.builder.remote("local[*]")`, at the cost of a JVM.

Read everything below as historical record. The S1–S5 work was not wasted: it
built a tier that turned out to be backend-agnostic, which is exactly why
retargeting it cost days rather than a rewrite.


## Amendment 1 (2026-06-15, Ben) — Sail is additive; Ray stays
The original decision framed Sail as **replacing** the Ray distributed stack (a one-release
deprecation window after S4). **That is revised:** Ray clustering is effective and stays the
default distributed substrate indefinitely. Sail is an **additive** scale-out option that can
be *supercharged* (the R1 native Arrow UDF, etc.), not a retirement target. Concretely:
- **No Ray retirement.** Drop the "replace the Ray distributed stack" / deprecation-window
  language below. Ray remains a first-class, supported, default path.
- **R5 reframes** from "Ray retirement + wiring" to "add `backend="sail"` as an *additional*
  opt-in surface" — Ray is untouched.
- **S4 still binds**, but its verdict is "Sail proven as an additive multi-node option"
  (completes where one-box can't, per-node RSS bounded, wall scales with nodes), NOT "Ray is
  now removable."
- The `mode` default-flip question is unchanged (still gated on its own evidence; unrelated to
  Ray's status).

Everything below is the original 2026-06-03 record, kept for the audit trail; read it through
this amendment.

## Context
After the Stage E honest-null ([0003](0003-stage-e-spill-honest-null.md)), the distributed
(Sail) path is the real value. Three scoping forks were put to Ben; he chose the ambitious
option on all three.

## Decision
- **Nature:** a **buildable implementation spec now** (not a spike-first, not paper-only).
  Mitigation: the build is staged S1→S4 with the load-bearing WCC de-risk as S1+S2, so the
  riskiest unknown is proven before the rest — the spike is folded into the build, not skipped.
- **Vs Ray:** **Sail-native everything, replace the Ray distributed stack** — including a
  Sail-native connected-components (no native graph in Sail → port two-phase WCC). Ray is
  NOT retired until S4's binding 100M+ bench passes (one-release deprecation window).
- **Scope:** **full spine-on-Sail** — load → block → score → dedup → WCC → golden (incl.
  custom field-rules) → identity.

## Consequences / honest flags
- "Buildable now" is real, but Sail is **Spark Connect**, so this is a re-expression (new
  `goldenmatch.sail` code), not a port of `run_spine`. Every stage self-parity-gates vs the
  one-box spine.
- **WCC-on-Sail (S2) is the gate.** If it can't be made native + correct, the
  Sail-native-everything premise is in question and we escalate; Ray stays.
- No `mode` default-flip and no Ray retirement on faith — both gated on S4.

## Alternatives not taken
- Spike-first / design-on-paper (declined — folded the de-risk into S1+S2 instead).
- Sail owns relational + Ray keeps the UF holdout (declined — chose full Sail-native).
- Minimal binding proof only (declined — chose full spine scope).

---
**Classification:** decision/accepted • **Last updated:** 2026-06-15 (amended)
