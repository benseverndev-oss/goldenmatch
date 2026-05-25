# Native acceleration: Rust vs C++ vs Python decision matrix

Date: 2026-05-25
Status: directional (living doc)
Companions: `2026-05-25-rust-acceleration-spec.md`, `2026-05-25-rust-acceleration-roadmap.md`
Provenance: extends an area/priority matrix with two binding decision gates and
the **measured** findings from the Phase 2 native-kernel work (PR #498). Where
the original matrix and the measurements disagree, the measurements win.

## TL;DR

- **Rust, not C++.** We already run one native toolchain (pgrx, bridge,
  `goldenmatch._native`). A second toolchain is a permanent build/CI/security
  tax. Reach for C++ only when a specific C++-only library is mandatory.
- **Never** put autoconfig, planner/routing, Ray orchestration, explainability,
  workflow surfaces, or product/MDM rules in native code — they change often and
  need to stay legible.
- **Every "Priority: High" below is a hypothesis to measure, not a mandate.**
  Static "this is a hot loop" reasoning is how you ship a regression here (see
  Evidence).

## The two gates — apply before any "Write Rust? = Yes"

The repo's own perf-audit lesson ("the audit ranked items by static counts; 3 of
3 measured items came in well under the framing — always measure wall-clock
first") and this session both show static reasoning is unreliable. A kernel must
clear **both** gates before it's worth writing:

**Gate 1 — Are you displacing Python, or an existing Rust library?**
If the current hot path is already Polars / rapidfuzz / Arrow (all Rust), a
hand-written kernel will *not* beat it and usually regresses. Native wins only
where it displaces *Python interpreter overhead* (loops, per-item dispatch,
object churn) — not where it re-implements a tuned native lib.

**Gate 2 — Can you cross the FFI boundary in bulk?**
One call over all the data, not per-item. Per-item Python↔Rust marshalling eats
the win. A "Yes" is really "Yes, as a single bulk call."

## Revised matrix

Priorities and verdicts below are edited from the original matrix to reflect the
two gates and the measured evidence. C++ column collapsed to "only if a
C++-only lib is required" per the Rust-not-C++ stance.

| Area | Native? | Priority | Gate check / rationale |
|---|---|---|---|
| Postgres pgrx extension | **Rust** | High | Already the path; Rust is the natural pgrx choice. Not a hot-loop call — it's the integration surface. |
| DuckDB extension wrappers | Maybe | Medium | Python UDF path works. Native only if you need DuckDB-native/vectorized perf — and that's the one place a C++-only dep might be justified. Measure the UDF overhead first. |
| **Pair canonicalization** `(min,max)` + score normalize | **Measure first** | ~~High~~ Low/Med | **Gate 1 risk.** It's an O(1) tuple op; the distributed path already dedups via Polars `groupby(max)` (Rust). The in-memory Python `set` loop is Polars-vectorizable *without* a kernel. Vectorize in Polars before considering Rust. |
| **Pair dedup max-score** (50M/100M streams) | **Measure first** | ~~High~~ Med | **Gate 1 risk.** Already a Polars `groupby` in the distributed path. At 50–100M the real cost is almost certainly Ray shuffle/serialization, which a Rust dedup kernel does **not** touch. Profile the shuffle before writing Rust. |
| **Union-Find / connected components** | **Rust** | High *(conditional)* | **Gate 2 critical.** Real Python-loop hot path at scale → genuine win, **but only as one bulk call over all edges/clusters**. Measured 1.6× *slower* today because it crosses FFI ~66K times (once per cluster). The fix is batching the boundary, not the algorithm. |
| Two-Phase WCC boundary merge | Rust | Med/High | Same as Union-Find: stable, perf-sensitive, but pass it the whole boundary super-graph in one call. Today it's columnar-Polars (already Rust) — Gate 1 says confirm Polars isn't already sufficient. |
| Block histogram / candidate-pair count | Rust | Med | Simple, stable, easy to bench. But check Gate 1 — Polars `group_by().len()` may already do this fast enough; reach for Rust only if the planner needs it at a scale Polars can't hit. |
| **Stable record hashing / ID generation** | **Rust** | High | **Exempt from the gates** — the rationale is *correctness + cross-surface portability*, not raw speed. One canonical impl that Python/pgrx/DuckDB/Node all call (via a C ABI later) is worth it on determinism grounds alone. |
| String normalization | Maybe | Low/Med | Gate 1: Polars/rapidfuzz already cover most of this. Native only for a profiled gap. |
| **Fuzzy scoring kernels** | Rust *(done right)* | Med→**proven** | The original "be careful, rapidfuzz is native" caution was correct. **The win is NOT a better scorer** — it's removing the per-pair Python loop + releasing the GIL + rayon, with **rapidfuzz-rs** doing the scoring. Done that way: 5× measured (see Evidence). Done the wrong way (hand-rolled scorers): 2× slower. |
| Arrow-native transforms | Maybe (C++ only if required) | Med | `arrow-rs` is good enough that Rust is the default; only the Arrow/DuckDB C++ ecosystem justifies C++, and only if a Rust path genuinely doesn't exist. |
| Ray distributed orchestration | **No** | Low | Keep in Python. Native here hurts iteration and doesn't fix scheduling/data-shape. |
| Auto-config controller | **No** | Low | Changes constantly; needs explainability. |
| Planner / routing logic | **No** | Low | Product logic, not hot-loop logic. |
| Golden-record business rules | **No** | Low | Needs user-defined extensibility. Keep Python/SQL. |
| Identity Graph Postgres bulk write | Maybe | Med | Rust can help validation/serialization, but Postgres `COPY`/indexes are the likely bottleneck — measure before assuming the serializer is the cost. |
| C ABI layer | Rust-exported, later | Later | Only once ≥2 surfaces (Python + Node/C#/DuckDB) need to reuse a kernel. Don't start here. |
| C# SDK | No | Later | Client/MDM SDK, not a perf layer. |

## Measured evidence (PR #498, 4-core box, 200K person-like + DQbench tiers)

| Kernel | Implementation | Result | Gate |
|---|---|---|---|
| block_scoring | hand-rolled Rust scorers | **2.2–2.3× slower** than Python+rapidfuzz | Gate 1 violated (reimplemented rapidfuzz) |
| block_scoring | rapidfuzz-rs + rayon + `allow_threads` | **5.1× faster** (big blocks); **~9.5×** on real `score_buckets` (tier3); 1.5× (tiny blocks) | passes both gates |
| clustering | hand-rolled, per-cluster FFI (~66K calls) | **1.6× slower** (many small clusters); ~on-par (few large) | Gate 2 violated (per-item FFI) |

Parity held throughout (rapidfuzz-rs matches Python rapidfuzz within the existing
`abs=1e-9`/`1e-12` test tolerances); DQbench ER composite unchanged at 92.03.
Correctness parity is necessary but **not** sufficient — it says nothing about
speed, which is exactly why the gates exist.

## Rust vs C++ stance

Default to **Rust**. The repo is already all-Rust for native work, and a second
toolchain multiplies build matrices, CI lanes, security review, and the
unsafe-FFI surface. C++ earns a place only when a required capability has **no
viable Rust path** — realistically: a DuckDB-native vectorized extension, or an
Arrow kernel where `arrow-rs` genuinely falls short. Treat that as the exception
to justify in a design doc, not a default.

## Recommended execution order

1. **Bank the block-scoring win** — a large-runner 5M confirmation. (The
   suspected thread-pool-vs-rayon oversubscription turned out to be a
   non-issue: rayon uses a *shared global pool*, so thread-pool workers feeding
   it don't multiply threads, and processing buckets concurrently beats
   sequential when pairs concentrate in a few buckets. Measured on the tier3
   bucket path: thread-pool+rayon 0.37s vs forced-sequential 0.97s — 2.6x
   faster. No integration change needed; the worker dispatch stays as-is. A
   self-applied instance of the "measure, don't assume" gate.)
2. **Clustering, done right** — convert the current per-cluster FFI (Gate 2
   violation, 1.6× slower) into a single bulk rayon-backed call; re-measure.
3. **Measure-first** on pair canonicalization / dedup — Polars-vectorize the
   in-memory canonicalization; profile the Ray shuffle at 50–100M before writing
   any dedup kernel. Likely no kernel needed.
4. **Stable hashing/IDs in Rust** — on portability grounds, when a second
   surface needs it (gated on a C ABI plan, not before).

Everything else stays Python until a profile says otherwise.
