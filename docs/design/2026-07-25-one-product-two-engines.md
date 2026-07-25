# GoldenMatch Architecture: One Product, Two Engines, Many Surfaces

Date: 2026-07-25
Status: direction proposed. Amends the "every capability must reach every surface"
commitment in `context-network/foundation/project-definition.md` (see §1.1) and extends
the `2026-07-01-rust-is-the-reference-roadmap.md` model from "Rust is the reference kernel"
to "one authoritative semantic owner per capability, across two engines."

## Summary

GoldenMatch is not converging on a single monolithic Arrow engine. It is one **product**
composed of two coupled systems with genuinely different concerns:

1. A high-performance **Identity Compute Engine** — throughput, memory efficiency,
   vectorized/batch execution, deterministic algorithms, portable runtime integration.
2. A durable **Identity Control Plane** — stable IDs, transactional correctness,
   incremental updates, merge/split semantics, provenance, audit, durable state.

Forcing every subsystem into one execution model produces misleading abstractions. Arrow is
the preferred contract for *bulk compute*; it is not the universal contract for *stateful
identity management*. Rust is the authoritative implementation for shared core semantics
where practical; it is not the only implementation that may exist, and it is not by itself
the definition of correctness.

**One product. Two engines. Many surfaces.** The implementation may be distributed across
runtimes; the semantics must not be.

## 1. Principles

1. One **authoritative semantic owner** per capability.
2. **Specification + conformance** define correctness (not a binary, not a fixture set alone).
3. **Rust** is the preferred implementation for shared core semantics.
4. **Arrow** is the bulk data-plane contract; small calls may use simpler primitives.
5. **Compute and control** stay architecturally distinct.
6. **Kernelization is justified by measurement**, not aesthetics.
7. **Product surfaces are first-class** without becoming independent sources of truth.
8. **Execution engines are replaceable.**
9. **The simple user experience is the product.**

### 1.1 Reconciliation with the North Star

`project-definition.md` commitment 3 previously read: *"Every capability must reach every
surface … not stranded on one."* Read literally, that mandates identical exposure everywhere
— which is precisely the pressure that produced the multi-runtime parity tax this document
seeks to retire. **This document amends commitment 3 to:**

> *Shared capabilities must conform across surfaces. Surface-specific capabilities are
> permitted when they are explicit, justified, and declared. A capability may be richer on
> its primary surface, but where a behavior is shared it must be semantically consistent —
> no surface silently invents its own answer.*

The foundation doc is updated in lockstep with this change. The other four commitments are
unaffected; this one moves from "identical everywhere" to "consistent where shared, honest
about gaps."

## 2. One authoritative implementation, not one binary

"One implementation" is directionally right but too absolute. GoldenMatch runs where a
native wheel, a compiler, or a browser-suitable binary may be unavailable, and a
compiled-only architecture would break the zero-config accessibility goal. The accurate
principle:

> Each capability has **one authoritative semantic implementation**. Bindings are thin
> wherever the environment allows. **Scoped fallbacks** are permitted where a hard runtime
> constraint prevents use of the authoritative core.

### 2.1 Rust as primary

Rust owns capabilities that are shared across surfaces, deterministic, computationally
significant, semantically sensitive, and expose a clean primitive boundary: string
similarity, normalization, blocking-key generation, candidate-pair ops, hashing,
fingerprints, sketches, clustering primitives, deterministic planning decisions, and
identity-transition logic where a single source is valuable.

Rust is the *primary* implementation. It is **not** the *definition* of correctness (§3).

### 2.2 Scoped fallbacks

Fallbacks (pure Python without a wheel; TypeScript where WASM is unsuitable; host logic for
control-heavy workflows; scalar boundaries where FFI/Arrow overhead exceeds the work) must
be explicitly scoped, classified by fidelity (§3.3), conformance-tested, and prevented from
silently gaining independent semantics. *Authoritative implementation* and *supported
fallback* are different roles and must be labeled as such.

## 3. The contract is specification + conformance

Neither the Rust binary nor the fixtures independently define correctness. A Rust bug must
not become correct because Rust is "the reference"; a missing test must not license any
behavior the fixtures happen not to reject. The hierarchy:

```
Semantic specification        (what correctness means)
        ↓
Conformance fixtures & invariants   (executable evidence — the LIVING spec today)
        ↓
Primary Rust implementation
        ↓
Bindings, fallbacks, execution backends
```

**Honest note on maturity:** today the middle layer (fixtures / JSON oracles / parity gates)
is what actually exists and is maintained; prose specs are sparse. The pragmatic policy is
**the conformance suite IS the executable spec**, with prose written only for the
semantically load-bearing decisions where fixtures under-determine intent — tie-breaking,
null handling, ordering, float expectations, stable-ID/merge/split rules. We do not commit to
a comprehensive prose spec that would rot; we commit to prose exactly where ambiguity is
dangerous.

### 3.1 What a load-bearing spec entry covers

Accepted inputs, canonicalization, null behavior, ordering, tie-breaking, float
expectations, failure semantics, stable identifiers, merge/split behavior, idempotency, and
externally observable output.

### 3.2 Conformance suite

Golden vectors, adversarial cases, cross-language fixtures, backend parity checks, stable
JSON shapes, boundary cases, property tests, fuzzing where useful, regression fixtures, and
cross-surface API manifests. **A new backend or binding proves itself by passing the suite —
not by calling the same library.**

### 3.3 Conformance levels

| Level | Guarantee | Suitable for |
|---|---|---|
| **Exact** | Byte-identical output | fingerprints, hashes, stable IDs, deterministic config, serialized identity views, integer pair ops, event representations |
| **Numerically equivalent** | Differs within a declared tolerance that cannot change a downstream decision | float similarity, vector reductions, platform/SIMD numeric execution |
| **Semantically equivalent** | Internal representation may differ; externally observable *decisions* match | cluster membership, reconciliation, merge-winner selection, worklist classification, routing |
| **Intentionally divergent** | A runtime cannot match due to an environmental constraint; declared, justified, tested, documented, prevented from expanding silently | documented ASCII-vs-Unicode edges, platform-specific limits |

This makes compatibility explicit rather than rhetorical.

## 4. Arrow: bulk contract, not universal calling convention

Arrow is the preferred **data-plane** boundary for bulk/columnar work. Anything that
produces Arrow can feed GoldenMatch; anything that consumes Arrow can consume its results
(Python, Polars, DuckDB, Spark, DataFusion, Sail, Ballista, Java, Scala, Flight,
Parquet-backed systems). The benefit is a stable, columnar, language-neutral memory
contract — not brand loyalty.

Arrow should **not** be forced into every function boundary. For small/scalar ops, array
construction, marshaling, FFI, ownership conversion, and runtime init can exceed the work
itself. Use the smallest stable primitive elsewhere: strings, numeric slices, lists, tuples,
compact structs, scalar config, serialized messages. (Several kernels already take plain
`list[str]` for exactly this reason — domain detection, name scorers.)

> Arrow at bulk/columnar/interop boundaries. Smallest stable primitive everywhere else.

## 5. Separate compute from control

**Compute** is deterministic, parallelizable, data-oriented, batch-shaped,
throughput-sensitive: normalization, phonetic transforms, blocking-key generation, pair
generation, edit distance, Jaro-Winkler, token comparison, feature extraction, hashing, pair
scoring, connected components, cluster statistics. Natural Rust-kernel candidates.

**Control** is orchestration-heavy, policy-driven, branch-heavy, state-aware,
correctness- and explainability-sensitive: auto-config, confidence calibration, refusal,
LLM arbitration, workflow routing, experiment management, stewardship, quality analysis,
remediation, product-level planning.

Some control logic may still live in Rust for single-sourcing — but the justification
(performance vs. semantic authority vs. portability vs. safety vs. maintainability) must be
stated, and moving control logic into Rust does **not** make it a compute kernel.

## 6. Kernelization is evidence-based

Architectural elegance does not predict system performance. Real bottlenecks have included
allocator behavior, memory residency, batch materialization, serialization, scheduler
contention, thread parking, cache locality, DB write throughput, object retention, and
network latency — none of which a faster inner loop addresses.

Before any kernelization, measure **the entire current path vs. the entire proposed path**
(including conversion and orchestration) on realistic workload shapes: wall-clock, peak RSS,
allocations, serialization, FFI overhead, conversion time, scheduler overhead, end-to-end
latency. The comparison is never "Rust loop vs Python loop."

A migration may be justified without a speedup, for: **performance** (measured hotspot),
**semantic single-sourcing** (drift-prone duplicate behavior), **portability** (must run via
PyO3/WASM/SQL), **safety** (types/ownership), or **maintainability** (a clean boundary
retiring real duplication). Each migration states which reason applies.

## 7. The Identity Compute Engine

Flow: `records → normalization → blocking → candidate pairs → features → pair scoring →
clustering → clusters + evidence`. Owns: normalization primitives, scorers, blocking
transforms, candidate generation, pair canonicalization, features, fingerprints, sketches,
vectorized scoring, clustering primitives, evidence production, batch quality telemetry.

Optimizes for throughput, predictable memory, zero-copy where valuable, vectorization,
portable kernels, deterministic execution, backend independence, streaming where possible,
and **measurement-driven** fusion.

**Execution backends** (local Rust, Python host orchestration, Ray, DataFusion, Sail, Spark
integrations, Ballista, SQL extensions, WASM, Flight services) are environments — none is
synonymous with GoldenMatch.

### 7.1 DataFusion, Ray, Sail, Ballista, Spark

- **DataFusion** is highly aligned (Arrow-native, planning, pushdown, streaming, SQL,
  extensibility) and may host GoldenMatch expressions/operators. It remains **one backend**.
  The relationship is `DataFusion → GoldenMatch Rust cores`, *not* `GoldenMatch semantics =
  DataFusion operator model` — so a change in DataFusion's APIs or cadence cannot strand the
  core. (The `datafusion-udf` crate pinned to arrow 58 while the rest moved to 59 is already
  a live instance of this coupling cost.)
- **Ray** is a supported backend for Python/notebook/ML/distributed workflows; it does not
  define compute semantics. (It is what serves real 100M scale today via distributed-WCC.)
- **Sail** is an execution option (Spark-compatible, Arrow-oriented), evaluated on maturity,
  compatibility, reliability, measured performance, and integration cost — not an
  architectural commitment.
- **Ballista** is a research path / compatibility experiment; it must not displace working
  production backends for architectural purity.
- **Spark/Comet** is a strategically important product surface and execution environment.
  Direction is `Spark → Arrow-compatible boundary → GoldenMatch core`; do **not** duplicate
  algorithms in Scala to participate. Comet may accelerate supported Spark operators, but
  arbitrary GoldenMatch-specific operators flowing through Comet is **engineering work to be
  validated, not a solved consequence of Arrow compatibility.**

## 8. The Identity Control Plane

Begins where batch clustering ends. Input: prior identity state + new observations +
clusters + evidence + accepted steward decisions. Output: updated durable identities +
ownership changes + append-only events + audit history + stewardship state. This is **not**
a columnar compute problem — it is a **state-transition system**.

Owns: stable entity IDs, record ownership, identity creation, record absorption, merges,
splits, retirements, aliases, conflict resolution, claims, evidence retention, append-only
events, audit sealing, lineage, worklists, stewardship, incremental reconciliation,
rollback/compensation, temporal identity history.

**Primary contract:** given the same prior state, observations, evidence, configuration, and
accepted decisions, identity transitions must be **deterministic, durable, idempotent, and
auditable.** Correctness is defined by state transitions, transaction behavior, stable
identifiers, mutation ordering, conflict handling, history preservation, and recovery — not
by Arrow layout.

### 8.1 Transaction-native, not Arrow-native

The compute engine may be Arrow-native; the control plane is **transaction-native**. It must
reason about transaction boundaries, concurrent updates, retries, partial failures,
idempotency keys, ordering, merge/split races, stale reads, history retention, audit
integrity, and backend migration. Arrow RecordBatches are useful *at its edges* (bulk
ingestion, exporting identity views, analytical queries, moving evidence, batch
reconciliation input) but do not define identity transaction semantics.

### 8.2 Storage backends are part of the design, not a passive box

Backends (SQLite, Postgres, future distributed/append-only/analytical stores) must conform
to the same externally observable identity semantics, but differ materially in locking,
concurrency, write throughput, batching, isolation, indexing, bulk loading, and operational
ceilings. SQLite: local/zero-config/embedded/small-medium. Postgres: shared/concurrent/large
/bulk-write/team. Storage behavior is a first-class control-plane concern.

## 9. The seam between the engines (highest-risk interface)

The two engines communicate through an **explicit, versioned contract** — a *resolution
batch*: source records, fingerprints, clusters, candidate evidence, pair scores, matchkey
metadata, controller telemetry, run identifier, model/config version, provenance. The
control plane applies `resolution batch + existing durable state → reconciliation →
identity-transition set → transactional commit`.

Arrow may be one bulk *representation* of the batch, but the **semantic contract is specified
independently of any in-memory object model** and is versioned.

**This seam gets the most design rigor, because it leaks in practice.** The recently-shipped
SQLite bulk-write fast-path (#2132) surfaced two ways the clean two-box picture is
idealized: (a) the *payload-drop trap* — routing new clusters through a bulk path silently
dropped per-row payloads the row path carried, i.e. the boundary must specify *what evidence
crosses it*, not just cluster IDs; and (b) *frame-residency coupling* — control-plane write
memory stacks on top of the compute engine's `emit_singletons` prep floor, so the two boxes
share a memory budget the diagram hides. The versioned batch contract must make evidence
payload carriage explicit and must document the shared residency budget across the seam.

### 9.1 OPEN: incremental resolution belongs to neither box as drawn

§7 calls the compute engine "primarily stateless run-to-run" and §8 puts all state in the
control plane. Real **incremental** ER punctures this: when 1,000 new records arrive you must
block and score them **against a persisted index of prior identities**, not re-block the
whole corpus. That is *compute that reads durable state* — a third concern the two-box model
does not home. **Decision required:** does incremental candidate-generation-against-a-
persisted-blocking-index live as (i) a stateful extension of the compute engine, (ii) a
control-plane-owned index the compute engine queries, or (iii) an explicit third seam? This
is where the product gets most valuable and is deferred to the control-plane manifesto (§11,
Tier 5), but it must not be left implicit.

## 10. Planning, surfaces, and "one core"

**Planning/orchestration** sits above both engines: what runs, which backend, which kernels
are available, whether native acceleration applies, blocking config, when to refuse/escalate
/require review, whether to persist. It optimizes for correctness, explainability,
predictable defaults, safe refusal, backend/resource awareness, minimal config, and
reproducibility. Backend selection is **automatic by default, explicit when needed,
observable always** — not invisible (users need overrides, diagnostics, reproducibility).

**Product surfaces** (Python, TypeScript, CLI, REST, MCP, A2A, SQL, Spark, DuckDB, Polars,
Java, Scala, web UI, Flight) are first-class: documented, supported, tested, versioned,
designed for the host ecosystem. First-class does **not** mean identical low-level APIs,
identical install models, or independent algorithm stacks. Python may remain the richest
workflow surface; that does not demote the others in their intended roles. Shared
capabilities conform (§1.1); surface-specific gaps are permitted when declared.

**"One core" means one authoritative semantic owner per capability, not one giant crate:**

| Capability | Authoritative owner |
|---|---|
| Similarity / blocking / pair / clustering primitives, fingerprints, sketches | Rust compute core |
| Deterministic auto-config decisions | Rust planning core |
| Python ergonomics | Python surface |
| Browser / Node integration | TypeScript surface |
| SQL integration | SQL adapter layer |
| Workflow orchestration | Appropriate host layer |
| Identity-transition semantics | Control-plane core |
| Persistence behavior | Storage backend implementation |
| Cross-surface correctness | Specification + conformance suite |

## 11. Decisions & roadmap

**Decisions.** (1) Rust is authoritative where a clean shared core exists.
(2) Pure Python is a *supported fallback*, not a co-equal source of truth — **and this has a
user-visible cost to price:** today a no-wheel `pip install goldenmatch` gets full-fidelity
pure Python; reclassifying it changes what that install *guarantees*, so the deprecation
contract (which fallbacks stay exact indefinitely vs. become reduced/temporary) must be
stated, not implied. (3) TypeScript is progressively **WASM-first** for shared deterministic
algorithms; host-specific orchestration stays in TS. (4) Arrow is the bulk boundary; simpler
types for small calls. (5) The control plane is **outside** the Arrow-core thesis and needs
its own architecture. (6) DataFusion is one backend. (7) Kernelization requires measurement
(state the bottleneck, workload, effect, and motivation class).

**Roadmap.**
- **Tier 1 — Authority & conformance:** define authoritative owners for major capabilities;
  classify fallbacks (§3.3); centralize golden/adversarial fixtures; version cross-engine
  contracts; **surface current undeclared divergences** (audit, don't assume none).
- **Tier 2 — Compute engine:** continue Rust single-sourcing of shared kernels; improve
  Arrow bulk boundaries; profile end-to-end shapes; optimize memory residency; reduce
  materialization; fuse only measured hotspots; keep backends replaceable.
- **Tier 3 — TypeScript migration:** inventory duplicated algorithms; classify each
  retain/wrap/migrate; prioritize semantically risky duplication; measure WASM startup +
  package size; preserve browser/Node ergonomics; retire parity gates made unnecessary by
  single-sourcing.
- **Tier 4 — Python fallback policy:** inventory pure-Python impls; classify exact /
  tolerance / semantic / reduced / temporary; decide which stay indefinitely; block new
  duplicated semantics by default; preserve zero-friction install.
- **Tier 5 — Control-plane manifesto (its own doc):** stable-ID semantics, reconciliation,
  merge/split rules, idempotency, concurrency, transaction boundaries, event history, audit,
  stewardship, rollback, backend contracts, distributed resolution — **and the §9.1
  incremental-resolution seam.**

## 12. What not to do

- Do **not** make DataFusion the definition of GoldenMatch — it is a backend.
- Do **not** make Arrow mandatory for scalar operations.
- Do **not** migrate to Rust for aesthetic consistency — require a semantic/perf/portability
  /safety/maintenance reason.
- Do **not** call Python and Rust equal references — choose one authority, support the
  fallback honestly.
- Do **not** treat first-class TypeScript as requiring an independent algorithm stack.
- Do **not** model the identity store as a passive storage layer — it is a stateful subsystem.
- Do **not** let parity infrastructure become permanent justification for duplication — parity
  gates protect *migrations* and *intentional* multi-runtime behavior; retire them where
  single-sourcing becomes practical.

## 13. Final architecture

```
                         User Surfaces
             Python · TS · SQL · CLI · MCP · A2A · UI
                           │
                           ▼
                Planning and Orchestration
          defaults · routing · confidence · refusal · policy
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│              Identity Compute Engine                 │
│  Rust authoritative cores · Arrow bulk boundaries    │
│  measured kernels · blocking/scoring/clustering/      │
│  evidence · replaceable execution backends           │
└────────────────────────┬─────────────────────────────┘
                         │ versioned resolution batch
                         │ (evidence payloads explicit;
                         │  shared residency budget)
                         ▼
┌─────────────────────────────────────────────────────┐
│              Identity Control Plane                  │
│  deterministic state transitions · stable IDs        │
│  merge/split/claim/conflict resolution               │
│  provenance · audit · stewardship                    │
│  transactional SQLite / Postgres persistence         │
└─────────────────────────────────────────────────────┘
        ▲ (OPEN §9.1: incremental compute reads persisted state)
```

**North Star, restated:** *Think in contracts instead of frameworks. Assign one semantic
owner to each capability. Use Arrow for bulk compute, transactions for durable identity, and
conformance to keep every surface honest.* The implementation may be distributed across
runtimes; the semantics must not be.

---
**Status:** direction proposed • **Last updated:** 2026-07-25
