# Native ER core — scoping (decouple SQL extensions from embedded CPython)

Date: 2026-05-25
Status: scoping (no code; decision-enabling)
Companions: `2026-05-25-rust-acceleration-roadmap.md` (Phase 4.2), `2026-05-25-native-acceleration-decision-matrix.md`

## 1. Goal

Let the SQL extensions (`packages/rust/extensions/postgres` pgrx + `duckdb` UDFs)
run entity resolution **without embedding a CPython interpreter** — i.e. call a
Rust ER core directly instead of routing every call through
`packages/rust/extensions/bridge` (pyo3 `auto-initialize`, which imports the
`goldenmatch` Python package and runs the pipeline under the GIL).

Motivation is **operational, not throughput** (the heavy compute is already
native — Polars + rapidfuzz). The documented CPython-in-Postgres fragility:
`typing_extensions` system-package clashes, pyo3 version coupling, the fail-soft
JSON wrappers, and shipping a full Python runtime inside a PG extension.

## 2. Current state (measured)

- **bridge** (Rust): ~30 public fns in `api.rs`, each `Python::with_gil { import goldenmatch…; call; convert }`. Zero ER compute in Rust — it marshals JSON in/out. pyo3 pinned `>=0.23.3,<0.24` (separate from the `_native` crate).
- **`goldenmatch._native`** (Rust): the only native ER compute today — `connected_components` / `severe_bridge_count` / `cluster_confidence` (clustering) + `score_block_pairs` + 3 scorers. Built this session; clustering on-par, block-scoring 5× / 16.5s at 5M.
- **The pipeline it would replace**: ~80 modules in `core/`. Key sizes: `pipeline.py` 2035 LOC, `scorer.py` 1053, `golden.py` 787, `standardize.py` 403, config `schemas.py` 802 LOC / **27 Pydantic models**, auto-config **13 modules**.
- **No `polars-rs` Rust dependency** anywhere — the foundation a native core would build on does not yet exist in the Rust tree.

## 3. What a native core must cover

The ~30 bridge fns split into three tiers by portability:

| Tier | Functions | Portability |
|---|---|---|
| **Deterministic execution path** | `dedupe`/`dedupe_full`/`dedupe_pairs`/`dedupe_clusters`, `match_tables`, `score_strings`/`score_pair`/`explain_pair` | **Portable.** Mostly Polars (block-key, joins, group-by) + the 2 native kernels. The realistic target. |
| **Heavy / stateful / model-backed** | `autoconfig` (the introspective controller — 13 modules, fast-evolving), `detect_domain`/`extract_features` (domain packs), `train_em`/`score_probabilistic` (FS-EM), `profile_table`/`suggest_threshold`/`detect_anomalies`, `evaluate`/`compare_clusters`, `validate_table`/`autofix_table` (GoldenCheck), `goldenflow_transform` | **Stay Python.** The matrix's "No" rows: auto-config changes often + needs explainability; the rest are separate packages (GoldenCheck/GoldenFlow) or model-backed. |
| **DB-bound** | `identity_*` (SQLite/PG reads), `correction_*`/`memory_*` (Learning Memory) | Already thin DB I/O; little Rust value. |

## 4. The constraints that make this XL

1. **Auto-config can't be ported, and it's the zero-config selling point.** The controller (`autoconfig_*` × 13, v1.8–v1.12, rapidly evolving, explainability-required) is explicitly a "keep in Python" row. So a native core only ever serves the **explicit-config** path — and the SQL surface exposes `goldenmatch_autoconfig_telemetry`. **CPython cannot be fully removed** while the extensions offer zero-config. Decoupling is therefore *partial* at best.
2. **Config port.** 27 Pydantic models + validators → Rust serde structs, kept in lockstep with the Python schema forever. High parity-drift surface.
3. **Behaviour-parity burden, ×the whole pipeline.** Exact-match NE post-filter, weighted-matchkey negative evidence, oversized-cluster MST split, weak-cluster downgrade, golden survivorship strategies (787 LOC), standardizers. This session showed how a *single* kernel drifts (1-ULP, over-merge edge cases); replicating the full pipeline in Rust with tolerance-parity is that risk ×20, plus a permanent tax as the Python pipeline evolves.
4. **Gate-1 check (matrix):** much of the execution path is **already Polars** (Rust). A Rust core built on `polars-rs` would move *orchestration* into Rust and drop the embedded interpreter + per-call GIL/JSON marshalling — it would **not** make the compute faster. The value is decoupling + per-call latency, not throughput.

## 5. Options

| Option | What | Effort | Verdict |
|---|---|---|---|
| **A. Full native ER core** | Re-port the whole SQL-exposed surface to Rust | XXL (months), perpetual parity tax | **No.** Re-implements the product; auto-config stays Python anyway so CPython isn't eliminated. |
| **B. Narrow native execution core** | Rust core on `polars-rs` + the 2 existing kernels covering **explicit-config** exact+fuzzy dedupe → cluster → golden. SQL extensions get a "native fast path"; auto-config + the long tail stay on the Python bridge. | L (config port + golden + parity harness) | **Only with a concrete driver.** Bounded, but still a real project. |
| **C. Fragility-hardening (no Rust core)** | Fix the *documented* CPython-in-PG pains via packaging: pin/vendor the interpreter, freeze deps, harden the bridge's fail-soft paths. | S–M | **Default.** Captures most of the operational benefit (stability) at a fraction of cost, full feature parity, no parity tax. |

## 6. The gating question — is there a driver?

B is speculative without one of:
- a **target environment that forbids embedding CPython** (locked-down Postgres, an edge/WASM target);
- a **per-call latency / GIL-contention** requirement the embedded-Python bridge measurably can't meet (needs a bench: bridge round-trip vs a native call);
- a **distribution-size / supply-chain** constraint (no Python runtime inside the extension artifact).

No driver → C, and revisit when one lands.

## 7. Recommended path (if a driver exists)

Incremental, measure-gated — do **not** start by porting config + golden:

1. **Prove the premise first.** Bench the bridge's per-call cost (JSON marshal + GIL acquire + Python dispatch) vs a trivial native call, on the SQL workloads that matter. If the embedded-Python overhead isn't the measured bottleneck, stop — it's option C.
2. **Add `polars-rs` + a minimal `goldenmatch-er-core` crate.** Cover the narrowest useful slice: explicit-config **exact-only** dedupe (Polars self-join + the clustering kernel + a basic golden survivorship). Diff cluster output against the Python pipeline on a fixture battery.
3. **Extend to fuzzy** (reuse `score_block_pairs`), then golden survivorship strategies — each behind a parity gate (pair-set + cluster membership identical to Python within tolerance).
4. **Wire one SQL surface** (DuckDB first — pure-Python UDF host, easiest to A/B) to call the native core for explicit-config dedupe; keep the Python bridge for everything else. Measure the decoupling/latency win end-to-end before touching pgrx.

Each step is independently shippable and falls back to the Python bridge.

## 8. Recommendation

**Do not build the full native ER core (A).** It re-ports the product and doesn't even eliminate CPython (auto-config stays Python). Default to **C (fragility-hardening)** now — it addresses the actual documented pain cheaply.

Pursue **B (narrow execution core on `polars-rs`)** only behind a concrete driver (§6) and only after step-1 proves the embedded-Python overhead is the real cost. The two kernels this session already shipped (clustering, block-scoring) are the reusable seed of B; the rest of B is config-port + golden + a parity harness, which is where the cost and risk live.
