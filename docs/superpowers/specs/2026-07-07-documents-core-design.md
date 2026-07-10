# documents-core — Rust kernel for document-ingest (single source of truth)

**Goal.** Move the deterministic, drift-prone logic of `goldenmatch.documents` into a pyo3-free
Rust crate `goldenmatch-documents-core`, the single source of truth, exposed to Python (via the
aggregated `native/` extension) and TS (via a `documents-wasm` crate). Python keeps the shipped
pure-Python code as the **lossy fallback**, per the repo thesis (Rust is the reference;
`docs/design/2026-07-01-rust-is-the-reference-roadmap.md`).

**Why.** The document-ingest surfaces (Python, MCP, CLI, and coming REST / A2A / Web UI / TS) must
agree byte-for-byte on schema validation, response parsing, prompt text, and record normalization.
Reimplementing those per surface (especially in TS) invites drift. One Rust core + WASM gives TS
parity by construction instead of a parallel implementation.

**Scope rule (from `project_wasm_acceleration_fold`):** the core is KERNELS, not orchestration.
Only the four pure kernels below move to Rust. Rasterization (PIL/fitz), the VLM HTTP call, the
Polars table build in `assemble.py`, and all surface glue **stay Python** (I/O / orchestration).

## The four kernels (exact contracts = current Python behavior)

1. **schema** — `Field{name, kind="text", hint: Option<String>}`, `TargetSchema{fields}`.
   - `schema_to_json(schema) -> {"fields":[{"name","kind","hint"}]}` (always all three keys; `hint`
     may be null).
   - `schema_from_json(value) -> Result<TargetSchema>`: error if not an object with a `fields` list;
     error if any field item is **not an object** or lacks `name`; `kind` defaults to `"text"`,
     `hint` optional; **empty fields list → error**. Mirrors `schema_io.schema_from_dict` +
     `types.Field` defaults exactly.

2. **parse** — `parse_message_text(resp_json) -> Result<String>`. Mirrors `_openai.parse_message_text`:
   `resp["choices"][0]` missing/wrong-type → error "unexpected response envelope"; `finish_reason ==
   "length"` → error "response truncated (finish_reason=length); increase max_tokens"; content not a
   string → error "response has no message content"; trim; strip a leading ```` ``` ```` fence (drop
   the first line, then a trailing ```` ``` ````); return trimmed.

3. **prompt** — `extract_instruction(schema) -> String` (mirrors `vlm_backend._instruction`: the
   per-field lines + the JSON-shape instruction + the exact-keys line) and `suggest_prompt() ->
   &'static str` (the fixed `suggest._PROMPT`). Byte-identical text = identical VLM behavior across
   surfaces.

4. **normalize** — `normalize_record(values_json, confidence_json, schema) -> NormalizedRow` where
   `NormalizedRow{values: Map<col, Option<String>>, confidence: Map<col, f64>}`. Mirrors
   `ExtractedRow.from_partial`: for each schema column, `values[col]` present & non-null → its
   **string form**, else `None`; unknown keys dropped; `confidence[col]` else `0.0`. Also
   `row_confidence(row) -> f64` = min over confidence values (0.0 if empty), mirroring
   `row_confidence()`.
   - **Parity nuance (must be in the corpus):** the non-null → string coercion must match Python's
     `str()` for JSON scalars (`90210`→`"90210"`, `true`→`"True"`, `1.5`→`"1.5"`). Number and bool
     stringification are the likely divergence points; the corpus covers int/float/bool/null.

## Crate layout (matches `suggest-core` etc.)

`packages/rust/extensions/documents-core/` — standalone `[workspace]`, pyo3-free,
`[lib] name = goldenmatch_documents_core`, `serde` + `serde_json` deps only. Modules:
`schema.rs`, `parse.rs`, `prompt.rs`, `normalize.rs`, `lib.rs` (re-exports + a small error enum).
Rust unit tests live in each module.

## Bindings

- **Python (aggregated `native/`):** new `native/src/documents.rs` pyo3-wraps the four kernels;
  add `goldenmatch-documents-core = { path = "../documents-core" }` to `native/Cargo.toml`; add
  `mod documents;` + register the functions in the existing `#[pymodule]` (lib.rs). Reached via
  `goldenmatch._native` — **no separate `documents-native` crate** (same as score/suggest).
- **TS (WASM):** new `packages/rust/extensions/documents-wasm/` wasm-bindgen crate over the same
  core (mirrors `suggest-wasm`/`analysis-wasm`). Built now; TS **consumption** is deferred to the TS
  surface sub-project — this spec only ensures the WASM build exists and exports the four kernels.

## Python integration (fallback thesis)

Register a `documents` component in `_native_loader._COMPONENT_SYMBOLS` (the kernel symbol names).
Re-point the Python call sites to try native, else the existing pure-Python impl:
- `schema_io.schema_from_dict` / `schema_to_dict`
- `_openai.parse_message_text`
- `vlm_backend._instruction` + `suggest._PROMPT`
- `types.ExtractedRow.from_partial` / `row_confidence`

The pure-Python functions stay as the fallback body (unchanged), so `GOLDENMATCH_NATIVE=0` and a
missing wheel behave exactly as today. `GOLDENMATCH_NATIVE=1` (CI parity lane) requires native.

## Parity harness

`packages/python/goldenmatch/tests/parity/documents_corpus.jsonl` (inputs → expected outputs for
all four kernels, incl. the number/bool/null coercion cases and malformed-envelope cases) + a
`test_documents_parity.py` that runs BOTH the native kernel and the pure-Python fallback over the
corpus and asserts identical output. A `scripts/gen_documents_corpus.py` regenerates it from the
pure-Python impls. (Same pattern as the `identifiers_corpus` parity tests.) TS==Rust parity is
added when the TS surface lands.

## Testing

- Rust: `cargo test` in `documents-core` (per-kernel unit tests) — verify the crate builds and its
  own tests pass explicitly (`grep '^error'`, per `feedback_verify_rust_builds_explicitly`).
- Python: the parity corpus test (native vs pure), plus the existing `tests/documents` suite stays
  green with native present AND with `GOLDENMATCH_NATIVE=0`.
- WASM: `documents-wasm` compiles to `wasm32-unknown-unknown` and exports the four kernels (build
  check only; no TS consumer yet).
- **Worktree build note:** Rust on the exFAT `D:` worktree needs the toolchain on PATH +
  `CARGO_HOME=D:\.cargo` (see `reference_rustup_proxy_exfat_direct_binary`); the plan spells out the
  exact env.

## Scope (YAGNI)

**In:** `documents-core` (4 kernels) + `native/documents.rs` binding + `documents-wasm` build +
Python fallback wiring + parity corpus/test.
**Out (later sub-projects):** the REST / A2A / Web UI / TS surfaces; moving `assemble` (Polars) or
any I/O into Rust; TS consumption of the WASM.

## Open risks

- **str() parity** on JSON numbers/bools is the main correctness risk — the corpus must pin it, and
  if Rust can't match a Python `str()` edge case exactly, that kernel goes on `_FALLBACK_ONLY`
  (pure-Python stays the reference for it) rather than shipping a divergence.
- **Build surface:** adding a `-core` + `-wasm` + a `native/` module touches the Rust workspace and
  the native wheel build; the plan verifies each crate builds in isolation.
