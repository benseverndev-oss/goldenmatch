# goldenembed-rs finish-line (#508) — design

**Date:** 2026-06-04
**Issue:** #508 — *goldenembed-rs: standalone Rust embedding runtime (featurize + ONNX inference)*
**Status:** approved (brainstorming), spec under review

## Context

The standalone Rust embedding crate already exists and is CI-tested:
`packages/rust/extensions/goldenembed/` (lib + `goldenembed` CLI, ONNX via
`ort` 2.0, dedicated CI lane), landed in PR #503. It loads a saved
`GoldenEmbedModel` directory (`config.json` + `model.onnx`), featurizes text
with a pure-Rust char-n-gram kernel byte-identical to the Python reference, and
runs the learned projection head through onnxruntime — no Python, no torch.

The issue's own audit comment (2026-06-04) narrowed the remaining acceptance
criteria to three independent pieces:

- **(a)** embedding cache — not implemented.
- **(b)** batch/streaming throughput bench vs the pyo3/native path — absent.
- **(c)** FFI wiring — nothing actually calls the crate yet; Python
  `provider="inhouse"` and the SQL embed UDF still embed via CPython.

This spec covers all three as **three independently-shippable stages** plus a
small parity sub-stage. Each stage is a separate PR-sized unit so review stays
tractable, matching the repo's staged-plan convention.

### Relevant existing surfaces (verified)

- **Python embed model** — `goldenmatch/embeddings/inhouse/model.py`.
  `GoldenEmbedModel.model_id` = `f"inhouse:d{dim}:{hex}"` where `hex` is
  `blake2b(self.weights.tobytes(), digest_size=8)`, with `bias.tobytes()`
  appended via `h.update(...)` when a bias is present. `save()` writes
  `weights.npz` (`np.savez`, uncompressed), `config.json`
  (`dim`, `use_bias`, `featurizer`), and `model.onnx`.
- **Python cache** — `goldenmatch/embeddings/cache.py`. `EmbeddingCache` is a
  two-tier mem + optional SQLite cache keyed `(model_id, text_hash)`; values
  are little-endian `float32` blobs (`<f4`) with `dim` stored alongside.
- **Cache-key derivation** — `goldenmatch/embeddings/__init__.py`.
  `normalize_text(text)` = `" ".join(str(text).split()).lower()` (collapse all
  whitespace runs to single spaces, then lowercase; `None` → `""`).
  `text_hash(text)` = `hashlib.sha256(text.encode("utf-8")).hexdigest()`. The
  cache is keyed on the hash of the **normalized** text.
- **Rust featurizer** — `goldenembed/src/featurizer.rs` already ports the
  featurizer prepare/lowercase/collapse logic, but its `prepare()` adds
  boundary chars and is gated on the featurizer `lowercase` config flag. The
  cache key uses `normalize_text`, which is **independent** of featurizer config
  — so the cache layer needs its own `normalize_text` port, not a reuse of
  `prepare()`.
- **Rust crate deps today** — `ort = "2.0.0-rc.10"`, `blake2`, `serde`,
  `serde_json`, `anyhow`. Standalone `[workspace]` (isolated from the pyo3
  crates).
- **datafusion-udf crate** — `packages/rust/extensions/datafusion-udf/`. A
  standalone-`[workspace]` cdylib (pyo3 `extension-module` + `abi3-py311`) that
  registers DataFusion FFI ScalarUDFs and already takes a pure-Rust crate
  (`goldenmatch-score-core`) by path dep. Adding `goldenembed` by path follows
  the identical pattern. `datafusion-* = 53`, `arrow = 58`.
- **goldenembed CI lane** — `.github/workflows/ci.yml` has a `goldenembed` job
  gated on `packages/rust/extensions/goldenembed/**` path filter
  (`cargo build` + `cargo test` in the crate dir), wired into the aggregate
  gate.

## Decisions (locked during brainstorming)

1. **Cache backend = redb** (pure-Rust embedded KV). Cross-language cache
   sharing with Python's SQLite is an explicit **non-goal** — this is an edge /
   airgapped runtime; a Rust-only cache file is acceptable and avoids a C
   sqlite dep.
2. **model_id parity via npz parse in Rust** — zero Python change. The crate
   reads `weights.npz`, reproduces the blake2b-8 digest, and formats the same
   `model_id` string. This keeps cache-namespace semantics identical to Python
   (a retrained model → fresh cache namespace) and lets the CLI/bench report a
   model_id consistent with Python.
3. **Piece (c) = datafusion-udf first**; `postgres gm_embed` is a follow-up
   issue (pgrx is Linux/CI-only, harder to validate locally; de-risk one
   surface first).
4. **Two-tier cache** (in-memory HashMap front + redb disk tier) to mirror the
   Python mem+disk shape.

## Stage A — redb embedding cache

**New module:** `goldenembed/src/cache.rs`.

**Dependencies added:** `redb` (latest 2.x), `sha2`.

**Parity helpers (exact ports):**

- `normalize_text(text: &str) -> String` — split on Unicode whitespace, join
  with single spaces, lowercase. Must match Python `normalize_text` on the
  cache-relevant inputs. Python uses `str.split()` (splits on runs of any
  whitespace, strips leading/trailing) then `.lower()`. Rust:
  `text.split_whitespace().collect::<Vec<_>>().join(" ").to_lowercase()`.
  (`split_whitespace` uses the Unicode `White_Space` property; Python `split()`
  uses `str.isspace`. These agree on all ASCII and the common Unicode
  whitespace set. Known divergence: Python `str.isspace()` treats the C0
  separators `\x1c`–`\x1f` and `\x85` as whitespace; Rust `split_whitespace`
  does not. A parity test pins agreement on a representative corpus and
  **documents which side wins** on those edge bytes rather than asserting
  universal equality. This is not correctness-critical — a key mismatch only
  forces a cache miss (re-embed), never a wrong vector — so the Rust behavior is
  acceptable as-is and only needs to be recorded.)
- `text_hash(normalized: &str) -> String` — `sha2::Sha256` hex digest of the
  UTF-8 bytes. Matches Python `text_hash`.

**`EmbedCache`:**

```
pub struct EmbedCache {
    mem: HashMap<(String, String), Vec<f32>>,   // (model_id, text_hash) -> vec
    db: Option<redb::Database>,                  // None => mem-only (ephemeral)
}
```

- `EmbedCache::in_memory()` — mem tier only.
- `EmbedCache::open(path)` — opens/creates a redb file with a single table
  `embeddings: (&str, &str) -> &[u8]` keyed by a composite
  `"{model_id}\u{0}{text_hash}"` string (redb supports tuple keys; a delimited
  string keeps the key type trivial and collision-free since `\u{0}` cannot
  appear in a hex hash or the model_id format). Value = `f32` slice as
  little-endian bytes; dim inferred from `bytes.len() / 4` on read.
- `get(model_id, text_hash) -> Option<Vec<f32>>` — mem first, then redb (and
  promote into mem on a disk hit, mirroring Python).
- `put(model_id, text_hash, vec)` — write-through to mem and redb.

**Embed integration:** new method on `GoldenEmbed`:

```
pub fn embed_cached(
    &mut self,
    texts: &[&str],
    cache: &mut EmbedCache,
) -> Result<Vec<Vec<f32>>>
```

Per text: `normalize_text` → `text_hash` → `cache.get(self.model_id(), h)`.
Collect the **unique misses keyed by `text_hash`** (not raw `&str`), run them
through a single batched `embed()` call (reusing the existing ONNX path) on the
normalized text, `put` each result, then re-stack the output in input order by
hash — exactly the Python dedup shape (`embeddings/__init__.py:110-128`).
Bounded
memory: cap the miss batch at a configurable chunk size (default e.g. 4096) and
loop, so a huge input never materializes one giant feature matrix.

**Tests:** mem-only round-trip; redb persistence across reopen; repeated-text
dedup; `normalize_text`/`text_hash` parity against committed Python-derived
digests; `embed_cached` output equals uncached `embed` output.

## Stage A′ — model_id parity (npz parse)

**New module:** `goldenembed/src/model_id.rs`. **Dependency added:** `zip`.

`np.savez` writes a ZIP archive with one **stored (uncompressed)** entry per
array: `weights.npy` (and `bias.npy` if `use_bias`). The `.npy` format is a
magic string + version + a header line (dict with `shape`, `descr`,
`fortran_order`) padded to a 64-byte boundary, then the raw C-order array
bytes. For a C-contiguous float32 array, those raw bytes are exactly
`arr.tobytes()` — the same bytes Python blake2b-hashes.

`compute_model_id(dir, dim) -> Result<String>`:

1. Open `<dir>/weights.npz` with the `zip` crate.
2. Read `weights.npy`, parse the header length, seek past the header to the
   data section, read the remaining bytes.
3. `blake2b` digest_size=8 over those bytes (reuse the existing `blake2` dep).
4. If `bias.npy` exists, read its data section and `update` the same hasher.
5. Return `format!("inhouse:d{dim}:{hex}")`.

Wire into `GoldenEmbed::load` so `model_id()` is available; the CLI prints it in
the no-input smoke path (`model loaded: dim=… model_id=…`). If `weights.npz` is
absent (ONNX-only deployment), `model_id()` returns
`Err`/`None` and `embed_cached` falls back to a **deterministic** placeholder
namespace = `format!("onnx:d{dim}:{hex}")` where `hex` is `blake2b`-8 over the
raw `model.onnx` bytes — so two ONNX-only deployments of the same model still
share a cache namespace. Documented, since this namespace will **not** match the
Python `inhouse:…` model_id (cache parity with Python isn't possible without the
weights), which is fine: Python isn't sharing this redb file anyway.

**Test:** commit a tiny fixture model dir (small dim, few features) generated by
the Python `save()`, record its Python-computed `model_id`, assert the Rust
`compute_model_id` matches exactly. Cover both the bias and no-bias cases.

## Stage B — throughput bench

**CLI subcommand:** extend `goldenembed/src/main.rs` to accept a `bench`
verb: `goldenembed bench --model <dir> [--rows N] [--batch B[,B,...]]`.
Generates `N` synthetic texts (or reads a file), times `embed()` throughput at
each batch size, prints rows/sec and p50/p95 per-batch latency. Streams in
chunks for bounded memory. Argument parsing stays hand-rolled (the crate has no
clap dep today; keep it dependency-light).

**Comparison harness:** `packages/python/goldenmatch/scripts/bench_goldenembed.py`
— times the Python `GoldenEmbedModel.embed(..., backend="auto"|"onnx")` path on
identical input, invokes the Rust `goldenembed bench` (or the CLI embed path)
on the same texts, and prints a side-by-side table (rows/sec per side) plus a
parity spot-check (cosine ≈ 1.0 on a sample of rows). Acceptance target from
the issue: the Rust path beats the pyo3 path on large batches; the script
reports the ratio rather than hard-asserting (env-dependent), and `log`s the
runner.

**CI:** a `workflow_dispatch`-only job (repo bench convention; default to
`large-new-64GB` per `feedback_bench_default_runner`), not in the default lane.
Builds the crate `--release`, runs `bench`, uploads the table as a job summary.

## Stage C — datafusion-udf embed UDF

**Dependency:** `datafusion-udf/Cargo.toml` gains
`goldenembed = { path = "../goldenembed" }`.

**New module:** `datafusion-udf/src/embed_udf.rs`.

`goldenmatch_embed(text: Utf8) -> FixedSizeList<Float32, dim>`:

- A `ScalarUDFImpl` struct holding the loaded model. Model directory from the
  `GOLDENEMBED_MODEL_DIR` env var, loaded once at UDF construction; `dim` from
  the model fixes the `FixedSizeList` field width in `return_type`.
- `invoke_batch`: downcast the arg to `StringArray`, collect `&str`s
  (null → empty string → zero vector, matching Python's None handling), call
  `embed`, build a `FixedSizeListArray<Float32>` of width `dim`.
- Registered alongside the existing scalar UDFs in `lib.rs`.

**Spike-first risk (must resolve before building the UDF out):**

1. `ort` 2.0-rc.10 `Session::run` receiver — `&self` vs `&mut self`. This
   decides whether the model is `Arc<GoldenEmbed>` (shared, lock-free) or
   `Arc<Mutex<GoldenEmbed>>` (serialized) under DataFusion's parallel batch
   execution. Resolve by reading the `ort` 2.0-rc.10 API; if `&mut`, use a
   `Mutex` (correctness over throughput for v1) and note the contention.
2. onnxruntime binary discovery inside a cdylib loaded by CPython/DataFusion —
   confirm the `ort` feature flags (e.g. `download-binaries`) the existing
   `goldenembed` crate relies on still resolve the runtime when the symbol
   lives in the `datafusion-udf` cdylib. The crate's existing CI lane proves
   the binary build; the spike confirms the loaded-into-Python case.

If either spike turns ugly, surface it to the user rather than forcing the UDF
in; Stage C ships only when the spike is green.

**Test:** a smoke test in the datafusion-udf lane — register the UDF against the
fixture model, embed a 2–3 row table, assert shape `(n, dim)` and that two
identical inputs produce identical vectors. Parity against the Python embed on
the same fixture (cosine ≈ 1.0).

## Non-goals

- **Cross-language cache sharing** — redb is Rust-only by deliberate choice.
- **Python pyo3 wrapper for `provider="inhouse"`** — `goldenmatch-native`
  already runs the full featurize+project in Rust via `char_ngram_project`, so
  a second Python↔Rust embed path is redundant.
- **postgres `gm_embed`** — explicit follow-up issue once the datafusion
  surface and the `ort` integration story are proven.

## Sequencing

A′ (model_id) is a prerequisite for A's cache key, so land **A′ → A → B → C**.
A′+A can share one PR (cache is inert without a stable model_id). B and C are
independent of each other and of A once A′ lands. C is gated on its spike.

## Acceptance (maps to issue)

- `goldenembed` produces vectors matching the Python path within float
  tolerance — **already met** (PR #503), re-asserted by the Stage B parity
  spot-check and the Stage C UDF parity test.
- Embedding cache keyed by `model_id + normalized_text_hash` — **Stage A**.
- Batch + streaming inference with bounded memory; throughput bench — **Stage B**.
- Something other than CPython calls the crate (SQL embed via the Rust UDF) —
  **Stage C**.
