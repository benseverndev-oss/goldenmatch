# Blocking-key selection in `autoconfig-core` (shared cross-surface)

**Date:** 2026-07-19
**Closes (via increment 2):** #1317 — TS `buildBlocking` missing the #1207 per-identifier union
**Related:** `docs/superpowers/specs/2026-06-20-autoconfig-native-core-design.md` (the planner/classifier core this extends)

## Problem

Auto-config's **blocking-key selection** (`build_blocking` — which columns to
block on, `static` vs `multi_pass`, the #1207 strong-identifier union) is the
one remaining piece of the auto-config decision surface that is **hand-written
twice**: `build_blocking` in Python `core/autoconfig.py`, and a separate
hand-ported `buildBlocking` in TS `src/core/autoconfig.ts`. The planner and
classifier were already unified into `autoconfig-core` (Rust → wasm for TS,
native wheel for Python); blocking selection never was.

The concrete cost: #1207 landed the strong-identifier blocking union in Python
(`_build_strong_identifier_union`) but the TS twin was never updated, so TS
zero-config on null-sparse multi-source strong-id data emits a name-only
`multi_pass` and silently loses recall vs Python. That is exactly the
parallel-logic drift the shared-core strategy exists to prevent.

## The obstacle blocking selection posed, and the split that resolves it

The planner/classifier are **pure functions of column profiles** (aggregates),
so they moved into the core cleanly. Blocking selection is **data-dependent**:
the union's gates need row-level signals the profile doesn't carry —
- **OR-coverage** of a candidate pass set (fraction of rows non-null on ≥1
  pass's fields; a multi-field pass needs ALL its fields non-null), and
- **scale-safety** per pass (a strong-id singleton is gated on its NON-NULL
  projected block size; name/geo passes on the standard bounded gate).

Resolution — the **smart-core / dumb-measurement split** the codebase already
uses for the planner (which consumes the host-measured `ComplexityProfile`):
the **host measures** the row-level signals; the **core decides**. Measurement
is inherently per-surface (polars in Python, JS loops in TS); the *decision*
(assembly, transforms, every threshold and gate) lives once in Rust.

Because coverage/scale-safety are measured over the passes the core assembles,
the flow is naturally two-phase (mirroring Python's helper-then-call-site
structure):

```
          ┌── core: assemble_strong_id_union(columns) ──► candidate passes | None
host ─────┤   (pure: profiles + classify_by_name; ≥1 strong-id, ≥2 passes)
          │
          ├── host measures: OR-coverage(passes) + per-pass scale-safety bool
          │
          └── core: finalize_strong_id_union(passes, coverage, survives[], …) ──► BlockingConfig | None
              (pure: coverage gate, survivor filter, re-gate, config emission)
```

## Core API (`autoconfig-core/src/select_blocking.rs`, serde JSON boundary)

Faithful port of `_build_strong_identifier_union` (autoconfig.py:1583) + the
`build_blocking` union call-site survivor filtering (autoconfig.py:3145). All
thresholds/branch-orders reproduced from the Python source.

```rust
pub struct BlockingColumnInput { name, col_type: ColType, null_rate, cardinality_ratio }
pub struct UnionPass { fields: Vec<String>, transforms: Vec<String>, is_strong_id: bool }

/// Phase 1 — pure assembly from profiles. None unless ≥1 strong-id pass AND
/// ≥2 distinct passes. (Coverage gate is applied in phase 2, once measured.)
pub fn assemble_strong_id_union(cols: &[BlockingColumnInput]) -> Option<Vec<UnionPass>>

pub struct UnionFinalizeInput { passes, coverage, pass_survives: Vec<bool>,
                                coverage_target, max_safe_block }
pub struct BlockingConfigOut { strategy, keys, passes, max_block_size, skip_oversized }

/// Phase 2 — pure gates: coverage ≥ target, then survivor filter (host-measured
/// `pass_survives`), then re-gate (≥1 surviving strong-id AND ≥2 survivors).
pub fn finalize_strong_id_union(input: &UnionFinalizeInput) -> Option<BlockingConfigOut>
```

Ported constants (from autoconfig.py): `_STRONG_EXACT_TYPES = {identifier,
email, phone}`, `_UNION_PASS_MIN_NONNULL = 0.02`, `_BLOCKING_UNION_COVERAGE_TARGET
= 0.95`, `#876` surrogate guard `cardinality_ratio >= 1.0` excluded. name/geo
passes: `[first,last]`, `[last,geo]` where first/last come from a name-classified
column whose name contains `first` / `last|surname`, geo is a `zip`/`geo`
col_type. Transforms: email → `[lowercase, strip]`, else `[strip]`.

**Assembly detail — name classification:** the core reuses its own
`classify::classify_by_name` for the name-column detection (Python uses
`_classify_by_name(p.name) == "name"`), so the boundary stays `(name, col_type,
null_rate, cardinality_ratio)` — no extra host input.

## Increment plan

1. **This PR — core only.** `select_blocking.rs` (both phases) + `lib.rs`
   re-exports + Rust golden tests + a golden fixture JSON. Pure addition to
   `autoconfig-core`; **no surface behavior changes**, so zero regression risk.
   Fully built/tested with `cargo test` (no wasm/maturin needed).
2. **TS surface — split into 2a (done) + 2b (deferred) after a build-time
   finding.**
   - **2a (shipped):** expose the core via wasm shims (`autoconfig_assemble_/
     finalize_strong_id_union`) + rebuild the committed embed; port the pure
     DECISION logic to `src/core/blockingUnion.ts`; cross-surface parity test
     asserting **TS-pure == wasm == the golden fixture** on assemble + finalize.
   - **2b (SHIPPED — the always-on `buildBlocking` reroute that closes #1317):**
     wiring the union into the always-on `buildBlocking` surfaced that the core's
     `assemble` derives name-column detection from **`classify_by_name`** (a
     name-*pattern*-only classifier: bare `first`/`last` are NOT names, only
     `first_name`/`surname` are), which differs from TS's data-aware
     `classifyColumn`. Feeding TS's classifier made the union **over-fire** vs
     Python (it fired on a bare-`first`/`last` dataset where Python returns the
     name fallback — caught by `controller-stoppoint.parity.test.ts::mixed_blocking`).
     2b makes the core's `classify_by_name` the name-classification authority on
     the TS surface via a **faithful, fixture-pinned pure-TS port** (`classifyByName.ts`
     + the `autoconfig_classify_by_name` wasm shim as the cross-surface oracle;
     `classify_by_name_vectors.json` checked by the Rust golden test AND a TS
     parity test — TS-pure == wasm == Rust == Python, so this parallel logic
     cannot drift). The union's name detection now calls `classifyByName`, so it
     no longer over-fires. The **host-measurement half** (`blockingUnionMeasure.ts`
     — OR-coverage + per-pass scale-safety over rows) feeds the core's finalize
     phase; `buildBlocking` takes the rows and attempts the union on the
     exact-pool fall-through (matching Python's `build_blocking` union call-site
     ordering), emitting it before the name fallback. Proven by
     `blocking-union.parity.test.ts` (Python-oracle fixture): TS emits the SAME
     union as Python on the null-sparse multi-source case, and the SAME name
     fallback on the bare-`first`/`last` case. **#1317 closed.**
3. **Python reroute.** Route `_build_strong_identifier_union` + the call-site
   survivor filtering through the core (native when the wheel carries the symbol,
   pure-Python fallback matching the core otherwise). Equivalence test
   Python == core golden. Wheel republish is CI's job.

Later increments can migrate the rest of `build_blocking` (exact-pool pick,
compound fallback, name fallback) into the core the same way; the union is first
because it is where the surfaces have actually drifted (#1317).

## Testing

The Rust golden fixture (`autoconfig-core/tests/golden/select_blocking/*.json`)
is the cross-surface oracle: increment-1 Rust `tests/golden.rs` reads it;
increments 2/3 assert the wasm/TS and Python paths reproduce the SAME
assemble/finalize outputs on the SAME inputs (the autoconfig-core parity-gate
pattern). Fixtures use small datasets where Python's full-N block-size
projection is a no-op, so the host-measured `pass_survives`/`coverage` are exact.
