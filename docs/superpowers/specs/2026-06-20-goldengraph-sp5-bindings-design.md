# goldengraph SP5 — WASM + C bindings — design

**Status:** Design draft 2026-06-20. SP5 of the program roadmap. **Decomposed** (WASM + C are two toolchains): this doc fully specs **SP5a (WASM)** and outlines **SP5b (C ABI)**. Independent of SP4 — binds the on-`main` core (SP1 resolve/retrieve + SP2 store + SP3 communities).

**Surface:** new pyo3-free binding crates over `goldengraph-core`. Mirrors the `score-wasm` (wasm-bindgen) + `native/src/hash.rs` (`extern "C"`) precedents. **The multi-language payoff** the pyo3-free core was built for.

---

## Motivation

The core was kept pyo3-free precisely so it could compile to WASM (browser / Node / Workers / Deno) and expose a C ABI (any language) — "every capability on every surface." SP5 delivers that: the full resolve → store → query → communities surface, byte-parity-gated against the Rust golden vectors so all surfaces agree.

## The boundary design (the key decision)

**Stateless functions over the canonical snapshot JSON.** The SP2 store's snapshot already IS the portable state, so the bindings don't need FFI object handles / lifetimes (a real cross-language footgun). Every op is `(snapshot_json, args...) -> json`:

- `build_graph(mentions_json, edges_json, resolution_json) -> graph_json` (SP1 resolve+merge).
- `neighborhood(graph_json, seeds_json, hops) -> subgraph_json`; `seeds_by_name(graph_json, name) -> ids_json`; `communities(graph_json) -> communities_json`.
- `store_append(snapshot_json_or_empty, batch_json) -> snapshot_json` (SP2 append; "" = empty store).
- `store_as_of(snapshot_json, valid_t, tx_t) -> graph_json`; `store_history(snapshot_json, id) -> events_json`.

This shape is identical for WASM and C (only the string marshaling differs), and it matches the score-wasm lesson: cross the boundary ONCE per call with a JSON string, never per element.

**Prerequisite — make the core graph model serde-backed (a small `goldengraph-core` change in SP5a, not just binding code).** Today only `store.rs` derives `Serialize`/`Deserialize`; the SP1/SP3 model types (`Graph`, `Subgraph`, `EntityNode`, `Edge`, `Community`, and the `build_graph` inputs `Mention`/`MentionEdge`) do **not** (the existing integration tests hand-roll their JSON precisely because of this). So `store_append`/`store_as_of`/`store_history` already have a serde path (the snapshot), but `build_graph`/`neighborhood`/`seeds_by_name`/`communities` do **not** — SP5a must first `#[derive(Serialize, Deserialize)]` on those model types. The derived field names (`entity_id`/`canonical_name`/`typ`/`members`/`surface_names`; `subj`/`predicate`/`obj`/`source_refs`; `Community{id,members}`) already match the golden fixtures' shapes by construction, so the parity test (below) holds. `resolution_json` for `build_graph` parses the two forms the pyo3 binding accepts — a `dict[int,int]` (Provided) or `["native", scorer_id, threshold]` (Native) — into `ResolutionMode` explicitly (no blanket derive on the enum). This core change is in scope for SP5a.

---

## SP5a — WASM binding (fully specified)

**Crate:** `packages/rust/extensions/goldengraph-wasm` (standalone `[workspace]`, path-dep `goldengraph-core`, `crate-type = ["cdylib", "rlib"]`, `wasm-bindgen` under `[target.'cfg(target_arch = "wasm32")'.dependencies]` — exact score-wasm shape). Adds `serde_json` (already a core dep; the wasm crate serializes the JSON boundary).

**Structure (mirrors score-wasm):**
- Pure `*_impl` fns (host-`rlib`-testable, no wasm): e.g. `fn store_append_impl(snapshot: &str, batch: &str) -> Result<String, String>` — `GraphStore::open(Some(snapshot))` (or empty) → `serde_json::from_str::<StoreBatch>(batch)` → `append` → `snapshot()`. `fn as_of_impl(snapshot, v, t) -> Result<String,String>`, `fn communities_impl(graph_json) -> Result<String,String>`, `fn build_graph_impl(...)`, etc. Errors are `Err(String)` (JSON parse / bad input).
- A `#[cfg(target_arch = "wasm32")] mod wasm` with `#[wasm_bindgen]` wrappers that call the `_impl` fns and map `Err` → a thrown JS error (`JsError`).

**Parity (the gate) — real but partial; be precise about why.** The `_impl` fns are `#[cfg]`-independent and compile into BOTH the host `rlib` (native target) and the wasm32 `cdylib` — **same source, separately compiled** (NOT "the same compiled artifact"). Host `rlib` tests reuse the existing golden fixtures (`goldengraph-core/tests/fixtures/{store_golden,community_golden}.json` + the SP1 differentiator) and assert the `_impl` output is **byte-identical** canonical JSON to the core. Once the model types are serde-backed (above), `_impl` is a thin core-call + serialize, so this pins the **source logic**; the separate `cargo build --target wasm32` step proves it **compiles** for wasm. What the gate does NOT do (honest, and weaker than the precedent): it runs **zero wasm bytes** — the `wasm_score`/`analysis_wasm` lanes execute the artifact via a TS `vitest` gate because a TS consumer exists; goldengraph has none yet (see non-goals), so wasm32 *runtime* divergence is out of scope. Residual risk is low and localized: store/community/retrieve output is `u64`/`u32`/`i64` + `String` + `BTreeMap` ordering + `serde_json` (all deterministic on wasm32); the one `f64` path is `resolve_native`'s scoring — the single place host-parity does not guarantee wasm-parity. Flag it; TS-runtime parity lands with the TS package.

**Build:** `build_wasm.sh` (mirror score-wasm): `rustup target add wasm32-unknown-unknown`, `cargo build --target wasm32-unknown-unknown --release`, install the **lockfile-pinned** `wasm-bindgen-cli` (the version-skew footgun — commit `goldengraph-wasm/Cargo.lock`), `wasm-bindgen --target web` → artifact + a base64 universal-loader JS, emitted to a new `packages/typescript/goldengraph/src/core/wasm/artifacts/` (matching score-wasm's in-`src/` artifact path; there's no TS consumer yet, so the dir is created fresh — the full TS host pipeline is out, see non-goals).

**CI:** new gated lane `goldengraph-wasm` in `ci.yml` (mirror `wasm_score`): paths-filter on **both** `goldengraph-wasm/**` AND `goldengraph-core/**` (a core change must re-run parity, exactly as `wasm_score` globs `score-core/**`); build the wasm32 artifact + run the host rlib parity tests. Per the repo's CI convention, adding the job requires the `changes`-job filter entry AND the `if:` gate. The wasm32 build succeeding + host parity green IS the gate (no wasm execution — see Parity above).

**SP5a tests (TDD):** per-op parity (`store_append`/`as_of`/`communities`/`build_graph` impl == core golden vectors); a bad-JSON `_impl` returns `Err`; the wasm32 target compiles (CI).

### SP5a non-goals
The C ABI (SP5b). A TS **host pipeline** (extraction/synthesis in TS — the TS analogue of SP4, a separate future program; SP5a ships the WASM-callable engine + artifacts, not an LLM pipeline). TS-side parity tests + npm packaging (follow-up once a TS `goldengraph` package exists; SP5a's gate is the wasm build + Rust-host parity).

---

## SP5b — C ABI (outline; own spec later)

- `extern "C"` functions mirroring `native/src/hash.rs`'s pattern: `#[no_mangle] pub extern "C" fn gg_store_append(snapshot: *const c_char, batch: *const c_char, out: *mut c_char, out_cap: usize) -> c_int` (JSON in via `*const c_char`; write JSON into a caller buffer; return code = bytes written / error). Same stateless-snapshot boundary as SP5a.
- A hand-written C header (`goldengraph.h`) — the surface is small + stable; cbindgen optional. Reuses the same `_impl` fns (so WASM + C share one tested core path).
- **Decision for its spec:** crate placement (a `goldengraph-cabi` crate vs `extern "C"` behind a feature in `goldengraph-wasm`'s rlib) + buffer-sizing convention (return required length when `out_cap` too small, the standard C two-call pattern). **Note the precedent does NOT cover this:** `hash.rs::gm_record_fingerprint` uses a fixed 65-byte buffer (SHA-256 is constant-length), so it needs no sizing protocol; graph JSON is variable-length, so SP5b's two-call sizing is genuinely new, not inherited.

---

## Cross-slice non-goals (SP5)

The TS host LLM pipeline (separate future). SP6 eval. Publishing the WASM/C artifacts to npm / a C package registry (later rollout). Embedding/vectors on any surface (SP4c-adjacent).

## Risks / open questions

- **Snapshot-in/out cost at scale:** stateless ops re-parse + re-serialize the whole snapshot per call — fine for SP5 correctness + typical KG sizes; a stateful wasm-bindgen class (handle) is the optimization if a large-graph caller needs it (note, not built — same "measure first" discipline as the core).
- **wasm-bindgen-cli version skew** (the documented footgun): `build_wasm.sh` installs the exact lockfile-pinned version; commit `goldengraph-wasm/Cargo.lock`.
- **Local wasm build on exFAT:** the SP4a `CARGO_TARGET_DIR=C:` workaround applies (exFAT corrupts the rust cache here); the wasm32 build is CI-validated regardless.
- **No TS package yet:** SP5a emits artifacts to a new dir but there's no TS consumer; the parity gate is Rust-host + wasm-build, not TS-runtime (honest — TS-runtime parity lands with the TS package).
