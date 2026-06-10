# goldenembed-rs finish-line (#508) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish issue #508 by giving the standalone `goldenembed` Rust crate a persistent embedding cache, a throughput bench, and a real non-CPython caller (a DataFusion SQL embed UDF).

**Architecture:** Three independently-shippable stages on top of the existing `packages/rust/extensions/goldenembed/` crate. A redb-backed two-tier cache keyed by a Python-parity `model_id + sha256(normalize_text)`; a `bench` CLI verb plus a Python side-by-side comparison harness; and a `goldenmatch_embed` DataFusion FFI ScalarUDF in the sibling `datafusion-udf` crate that depends on `goldenembed` by Cargo path (no FFI boundary). model_id parity is reproduced in Rust by parsing `weights.npz` — zero Python change.

**Tech Stack:** Rust (`ort` 2.0-rc.10 onnxruntime, `redb`, `sha2`, `zip`, `blake2`), DataFusion 53 / Arrow 58 FFI ScalarUDFs (pyo3 abi3), Python (numpy) for fixtures + bench comparison.

**Spec:** `docs/superpowers/specs/2026-06-04-goldenembed-rs-finish-line-design.md`

**Sequencing:** PR 1 = Stage A′ + A (model_id + cache). PR 2 = Stage B (bench). PR 3 = Stage C (UDF, spike-gated). B and C are independent once A′ lands.

**Branch/auth note:** This work is in `packages/rust/extensions/` which uses the **personal** `benzsevern` GitHub account. Per repo SOP, branch `feature/goldenembed-finish-508`, squash-merge via PR, `gh auth switch --user benzsevern` before push and switch back after. `docs/superpowers/` is gitignored — do NOT `git add` the spec or this plan.

**Rust bash preamble (prepend to every cargo command):**
```bash
export PATH="/c/Users/bsevern/.cargo/bin:$PATH" && export RUSTUP_HOME="C:/Users/bsevern/.rustup" && export CARGO_HOME="C:/Users/bsevern/.cargo"
```

**Crate dir for Stage A/B:** `packages/rust/extensions/goldenembed`
**Crate dir for Stage C:** `packages/rust/extensions/datafusion-udf`

---

## Stage A′ — model_id parity (npz parse)

### Task 1: Add deps + hex helper + model_id module skeleton

**Files:**
- Modify: `packages/rust/extensions/goldenembed/Cargo.toml`
- Create: `packages/rust/extensions/goldenembed/src/model_id.rs`
- Modify: `packages/rust/extensions/goldenembed/src/lib.rs`

- [ ] **Step 1: Add dependencies**

In `Cargo.toml` `[dependencies]`, add:
```toml
redb = "2"
sha2 = "0.10"
zip = { version = "2", default-features = false, features = ["deflate"] }
```
(`zip` `deflate` feature covers both stored and deflated entries; `np.savez` uses stored, but enabling deflate is harmless and future-proofs against `savez_compressed`.)

- [ ] **Step 2: Write the failing test for model_id npz parsing**

Append to `src/model_id.rs`:
```rust
//! Reproduce the Python `GoldenEmbedModel.model_id` from a saved model dir,
//! by hashing the raw array bytes inside `weights.npz` exactly as numpy's
//! `ndarray.tobytes()` would — so the Rust runtime computes the same cache
//! namespace as Python without a Python dependency.
use std::io::Read;
use std::path::Path;

use anyhow::{anyhow, Context, Result};
use blake2::digest::{Update, VariableOutput};
use blake2::Blake2bVar;

/// Lowercase hex of a byte slice (matches Python `hexdigest()`).
fn hex(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

/// Extract the raw data section of a `.npy` blob (skip magic + header).
fn npy_data(buf: &[u8]) -> Result<&[u8]> {
    if buf.len() < 10 || &buf[0..6] != b"\x93NUMPY" {
        return Err(anyhow!("not a .npy buffer"));
    }
    let major = buf[6];
    let data_start = if major >= 2 {
        if buf.len() < 12 {
            return Err(anyhow!("truncated v2 .npy header"));
        }
        12 + u32::from_le_bytes([buf[8], buf[9], buf[10], buf[11]]) as usize
    } else {
        10 + u16::from_le_bytes([buf[8], buf[9]]) as usize
    };
    buf.get(data_start..).ok_or_else(|| anyhow!("truncated .npy"))
}

fn read_zip_entry(zip_path: &Path, name: &str) -> Result<Option<Vec<u8>>> {
    let file = std::fs::File::open(zip_path)
        .with_context(|| format!("opening {}", zip_path.display()))?;
    let mut archive = zip::ZipArchive::new(file)?;
    match archive.by_name(name) {
        Ok(mut entry) => {
            let mut buf = Vec::new();
            entry.read_to_end(&mut buf)?;
            Ok(Some(buf))
        }
        Err(zip::result::ZipError::FileNotFound) => Ok(None),
        Err(e) => Err(e.into()),
    }
}

/// Reproduce `inhouse:d{dim}:{blake2b8(weights[+bias])}` from `<dir>/weights.npz`.
pub fn compute_model_id(dir: &Path, dim: usize) -> Result<String> {
    let zip_path = dir.join("weights.npz");
    let weights = read_zip_entry(&zip_path, "weights.npy")?
        .ok_or_else(|| anyhow!("weights.npy missing from {}", zip_path.display()))?;
    let mut hasher = Blake2bVar::new(8).expect("blake2b-8 is valid");
    hasher.update(npy_data(&weights)?);
    if let Some(bias) = read_zip_entry(&zip_path, "bias.npy")? {
        hasher.update(npy_data(&bias)?);
    }
    let mut out = [0u8; 8];
    hasher.finalize_variable(&mut out).expect("8-byte output fits");
    Ok(format!("inhouse:d{dim}:{}", hex(&out)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn fixture(name: &str) -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures")
            .join(name)
    }

    #[test]
    fn model_id_matches_python_no_bias() {
        // EXPECTED_NO_BIAS is the model_id printed by the Python fixture
        // generator (see Task 2). Paste the real value there.
        let got = compute_model_id(&fixture("tiny_model"), 8).unwrap();
        assert_eq!(got, EXPECTED_NO_BIAS);
    }

    #[test]
    fn model_id_matches_python_with_bias() {
        let got = compute_model_id(&fixture("tiny_model_bias"), 8).unwrap();
        assert_eq!(got, EXPECTED_BIAS);
    }

    const EXPECTED_NO_BIAS: &str = "PLACEHOLDER_NO_BIAS";
    const EXPECTED_BIAS: &str = "PLACEHOLDER_BIAS";
}
```

Register the module in `src/lib.rs` after the `mod featurizer;` line:
```rust
pub mod model_id;
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
cargo test -p goldenembed model_id --manifest-path packages/rust/extensions/goldenembed/Cargo.toml
```
Expected: FAIL — fixtures don't exist yet / PLACEHOLDER mismatch.

### Task 2: Generate the fixture models + lock expected model_ids

**Files:**
- Create: `packages/rust/extensions/goldenembed/tests/fixtures/tiny_model/{config.json,weights.npz,model.onnx}`
- Create: `packages/rust/extensions/goldenembed/tests/fixtures/tiny_model_bias/{config.json,weights.npz,model.onnx}`
- Modify: `packages/rust/extensions/goldenembed/src/model_id.rs` (paste real expected ids)

- [ ] **Step 1: Generate the fixtures with Python**

> Run with the repo's Python that has `goldenmatch` + numpy installed. Set
> `POLARS_SKIP_CPU_CHECK=1` is not needed (no polars import here). This imports
> only numpy + the featurizer (no torch/polars).

```bash
python - <<'PY'
import numpy as np
from pathlib import Path
from goldenmatch.embeddings.inhouse.model import GoldenEmbedModel, EmbedModelConfig
from goldenmatch.embeddings.inhouse.featurizer import FeaturizerConfig

base = Path("packages/rust/extensions/goldenembed/tests/fixtures")
fc = FeaturizerConfig(n_features=256, ngram_min=2, ngram_max=3, lowercase=True, boundary="", seed=0)

# no-bias model, dim=8, deterministic seed
m = GoldenEmbedModel(EmbedModelConfig(dim=8, use_bias=False, featurizer=fc), seed=7)
m.save(base / "tiny_model")
print("EXPECTED_NO_BIAS =", m.model_id)

# bias model, dim=8
mb = GoldenEmbedModel(EmbedModelConfig(dim=8, use_bias=True, featurizer=fc), seed=7)
mb.save(base / "tiny_model_bias")
print("EXPECTED_BIAS =", mb.model_id)
PY
```

- [ ] **Step 2: Paste the printed ids into `model_id.rs`**

Replace `PLACEHOLDER_NO_BIAS` / `PLACEHOLDER_BIAS` with the two printed values.

- [ ] **Step 3: Run the test to verify it passes**

```bash
cargo test -p goldenembed model_id --manifest-path packages/rust/extensions/goldenembed/Cargo.toml
```
Expected: PASS (both cases). If FAIL, the npy header parse or hash order is wrong — verify `npy_data` offset and that weights-then-bias matches `model.py:82-85`.

- [ ] **Step 4: Commit**

```bash
git add packages/rust/extensions/goldenembed/Cargo.toml \
        packages/rust/extensions/goldenembed/src/model_id.rs \
        packages/rust/extensions/goldenembed/src/lib.rs \
        packages/rust/extensions/goldenembed/tests/fixtures
git commit -m "feat(goldenembed): reproduce Python model_id from weights.npz"
```

### Task 3: Wire model_id into GoldenEmbed::load + CLI

**Files:**
- Modify: `packages/rust/extensions/goldenembed/src/lib.rs`
- Modify: `packages/rust/extensions/goldenembed/src/main.rs`

- [ ] **Step 1: Add a `model_id` field + accessor to `GoldenEmbed`**

In `lib.rs`, add `model_id: Option<String>` to the struct. In `load`, after reading the config, compute it (non-fatal — ONNX-only dirs have no npz):
```rust
let model_id = crate::model_id::compute_model_id(dir, cfg.dim).ok();
```
Store it; add:
```rust
/// The Python-parity cache namespace, or an onnx-bytes fallback when
/// `weights.npz` is absent (see `model_id_or_fallback`).
pub fn model_id(&self) -> Option<&str> {
    self.model_id.as_deref()
}
```

- [ ] **Step 2: Print model_id in the CLI smoke path**

In `main.rs`, the no-input branch currently prints `model loaded: dim={}`. Change to also print the id:
```rust
println!(
    "model loaded: dim={} model_id={}",
    model.dim(),
    model.model_id().unwrap_or("<onnx-only>")
);
```

- [ ] **Step 3: Build to verify**

```bash
cargo build -p goldenembed --manifest-path packages/rust/extensions/goldenembed/Cargo.toml
```
Expected: clean build.

- [ ] **Step 4: Commit**

```bash
git add packages/rust/extensions/goldenembed/src/lib.rs packages/rust/extensions/goldenembed/src/main.rs
git commit -m "feat(goldenembed): expose model_id on load + CLI smoke output"
```

---

## Stage A — redb embedding cache

### Task 4: normalize_text + text_hash parity helpers

**Files:**
- Create: `packages/rust/extensions/goldenembed/src/cache.rs`
- Modify: `packages/rust/extensions/goldenembed/src/lib.rs` (add `pub mod cache;`)

- [ ] **Step 1: Write the failing parity tests**

Create `src/cache.rs`:
```rust
//! redb-backed two-tier embedding cache keyed by Python-parity
//! `(model_id, text_hash)`. Rust-only on-disk format by design (edge runtime);
//! cross-language sharing with the Python SQLite cache is a non-goal.
use sha2::{Digest, Sha256};

/// Port of Python `goldenmatch.embeddings.normalize_text`:
/// collapse all whitespace runs to single spaces, then lowercase.
/// `split_whitespace` uses Unicode `White_Space`; Python `str.split` uses
/// `str.isspace` — they agree on ASCII + common Unicode whitespace. Known
/// divergence (documented, non-critical: a mismatch only forces a cache miss):
/// Python treats C0 separators `\x1c`–`\x1f` and `\x85` as whitespace; Rust
/// does not.
pub fn normalize_text(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ").to_lowercase()
}

/// Port of Python `text_hash`: lowercase hex SHA-256 of the UTF-8 bytes.
pub fn text_hash(normalized: &str) -> String {
    let digest = Sha256::digest(normalized.as_bytes());
    let mut s = String::with_capacity(64);
    for b in digest {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_collapses_and_lowercases() {
        assert_eq!(normalize_text("Acme  Corp"), "acme corp");
        assert_eq!(normalize_text("  John\tSmith\n"), "john smith");
        assert_eq!(normalize_text(""), "");
    }

    #[test]
    fn text_hash_matches_python_sha256() {
        // Digests computed independently via `printf '<t>' | sha256sum`.
        assert_eq!(
            text_hash("acme corp"),
            "ea6f9c07a2f95c788a1645cf557f58aa63c5fa3ad7d749b9db4fce435deef64e"
        );
        assert_eq!(
            text_hash("john smith"),
            "32ddaf65cc3aa8d3e6eda3ca2da7c18b71e169e9aa444cccb479c9ca759dd095"
        );
        assert_eq!(
            text_hash(""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
    }
}
```
Add `pub mod cache;` to `lib.rs`.

- [ ] **Step 2: Run tests to verify they pass**

```bash
cargo test -p goldenembed cache::tests --manifest-path packages/rust/extensions/goldenembed/Cargo.toml
```
Expected: PASS (these helpers are self-contained; this task is parity-locking, not red-green).

- [ ] **Step 3: Commit**

```bash
git add packages/rust/extensions/goldenembed/src/cache.rs packages/rust/extensions/goldenembed/src/lib.rs
git commit -m "feat(goldenembed): normalize_text + text_hash cache-key parity"
```

### Task 5: EmbedCache (mem + redb tiers)

**Files:**
- Modify: `packages/rust/extensions/goldenembed/src/cache.rs`

- [ ] **Step 1: Write the failing tests**

Append to `cache.rs` (above the existing `tests` mod, add the impl; add cases to `tests`):
```rust
use std::collections::HashMap;
use std::path::Path;

use anyhow::Result;
use redb::{Database, ReadableTable, TableDefinition};

const TABLE: TableDefinition<&str, &[u8]> = TableDefinition::new("embeddings");

/// Two-tier cache: in-memory HashMap front + optional redb disk tier.
pub struct EmbedCache {
    mem: HashMap<String, Vec<f32>>,
    db: Option<Database>,
}

fn key(model_id: &str, text_hash: &str) -> String {
    // `\u{0}` cannot appear in a hex hash or the model_id format, so this is a
    // collision-free composite key.
    format!("{model_id}\u{0}{text_hash}")
}

fn to_bytes(vec: &[f32]) -> Vec<u8> {
    let mut out = Vec::with_capacity(vec.len() * 4);
    for v in vec {
        out.extend_from_slice(&v.to_le_bytes());
    }
    out
}

fn from_bytes(bytes: &[u8]) -> Vec<f32> {
    bytes
        .chunks_exact(4)
        .map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]))
        .collect()
}

impl EmbedCache {
    /// Ephemeral mem-only cache.
    pub fn in_memory() -> Self {
        Self { mem: HashMap::new(), db: None }
    }

    /// Open/create a redb-backed cache at `path`.
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let db = Database::create(path.as_ref())?;
        // Ensure the table exists so first-read on a fresh db doesn't error.
        let w = db.begin_write()?;
        { let _ = w.open_table(TABLE)?; }
        w.commit()?;
        Ok(Self { mem: HashMap::new(), db: Some(db) })
    }

    pub fn get(&mut self, model_id: &str, text_hash: &str) -> Option<Vec<f32>> {
        let k = key(model_id, text_hash);
        if let Some(hit) = self.mem.get(&k) {
            return Some(hit.clone());
        }
        let db = self.db.as_ref()?;
        let r = db.begin_read().ok()?;
        let t = r.open_table(TABLE).ok()?;
        let v = t.get(k.as_str()).ok().flatten()?;
        let vec = from_bytes(v.value());
        self.mem.insert(k, vec.clone()); // promote to mem tier
        Some(vec)
    }

    pub fn put(&mut self, model_id: &str, text_hash: &str, vec: Vec<f32>) -> Result<()> {
        let k = key(model_id, text_hash);
        if let Some(db) = self.db.as_ref() {
            let w = db.begin_write()?;
            { let mut t = w.open_table(TABLE)?; t.insert(k.as_str(), to_bytes(&vec).as_slice())?; }
            w.commit()?;
        }
        self.mem.insert(k, vec);
        Ok(())
    }
}
```
Add tests to the `tests` mod:
```rust
    #[test]
    fn mem_roundtrip() {
        let mut c = EmbedCache::in_memory();
        assert!(c.get("m", "h").is_none());
        c.put("m", "h", vec![1.0, 2.0, 3.0]).unwrap();
        assert_eq!(c.get("m", "h"), Some(vec![1.0, 2.0, 3.0]));
    }

    #[test]
    fn redb_persists_across_reopen() {
        let dir = std::env::temp_dir().join(format!("gec_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("cache.redb");
        {
            let mut c = EmbedCache::open(&path).unwrap();
            c.put("m", "h", vec![0.5, -0.5]).unwrap();
        }
        let mut c2 = EmbedCache::open(&path).unwrap();
        assert_eq!(c2.get("m", "h"), Some(vec![0.5, -0.5]));
        std::fs::remove_dir_all(&dir).ok();
    }
```

- [ ] **Step 2: Run the tests to verify they pass**

```bash
cargo test -p goldenembed cache --manifest-path packages/rust/extensions/goldenembed/Cargo.toml
```
Expected: PASS. (redb API: if `ReadableTable`/method names differ in the resolved 2.x, fix imports — `table.get` returns `Result<Option<AccessGuard>>`, `.value()` yields `&[u8]`.)

- [ ] **Step 3: Commit**

```bash
git add packages/rust/extensions/goldenembed/src/cache.rs
git commit -m "feat(goldenembed): EmbedCache mem+redb two-tier store"
```

### Task 6: GoldenEmbed::embed_cached

**Files:**
- Modify: `packages/rust/extensions/goldenembed/src/lib.rs`

- [ ] **Step 1: Write the failing test**

Add a `#[cfg(test)] mod tests` to `lib.rs` (or extend if present):
```rust
#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn tiny() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/tiny_model")
    }

    #[test]
    fn embed_cached_equals_uncached() {
        let mut m = GoldenEmbed::load(tiny()).unwrap();
        let texts = ["Acme Corp", "acme  corp", "Zebra Inc"];
        let direct = m.embed(&texts).unwrap();
        let mut cache = crate::cache::EmbedCache::in_memory();
        let cached = m.embed_cached(&texts, &mut cache).unwrap();
        assert_eq!(direct.len(), cached.len());
        for (a, b) in direct.iter().zip(&cached) {
            for (x, y) in a.iter().zip(b) {
                assert!((x - y).abs() < 1e-6, "{x} vs {y}");
            }
        }
        // "Acme Corp" and "acme  corp" normalize identically -> one cache entry.
        assert_eq!(cache.len(), 2);
    }
}
```
(Add a `pub fn len(&self) -> usize { self.mem.len() }` to `EmbedCache` for the assertion.)

- [ ] **Step 2: Run to verify it fails**

```bash
cargo test -p goldenembed embed_cached --manifest-path packages/rust/extensions/goldenembed/Cargo.toml
```
Expected: FAIL — `embed_cached` / `len` not defined.

- [ ] **Step 3: Implement `embed_cached`**

Add to `impl GoldenEmbed` in `lib.rs`:
```rust
/// Embed `texts` with cache lookups keyed by `(model_id, sha256(normalize_text))`.
/// Unique misses (deduped by text_hash) are embedded in one batched ONNX run,
/// chunked to bound memory. Output is in input order.
pub fn embed_cached(
    &mut self,
    texts: &[&str],
    cache: &mut crate::cache::EmbedCache,
) -> anyhow::Result<Vec<Vec<f32>>> {
    use crate::cache::{normalize_text, text_hash};
    const MISS_CHUNK: usize = 4096;

    let model_id = self
        .model_id()
        .map(str::to_owned)
        .unwrap_or_else(|| self.onnx_fallback_namespace());

    let normalized: Vec<String> = texts.iter().map(|t| normalize_text(t)).collect();
    let hashes: Vec<String> = normalized.iter().map(|n| text_hash(n)).collect();

    // Resolve hits; collect unique miss hashes preserving first-seen normalized text.
    let mut resolved: std::collections::HashMap<String, Vec<f32>> = std::collections::HashMap::new();
    let mut miss_order: Vec<String> = Vec::new();
    let mut miss_text: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    for (norm, h) in normalized.iter().zip(&hashes) {
        if resolved.contains_key(h) || miss_text.contains_key(h) {
            continue;
        }
        if let Some(v) = cache.get(&model_id, h) {
            resolved.insert(h.clone(), v);
        } else {
            miss_order.push(h.clone());
            miss_text.insert(h.clone(), norm.clone());
        }
    }

    // Embed misses in bounded chunks.
    for chunk in miss_order.chunks(MISS_CHUNK) {
        let batch_texts: Vec<&str> = chunk.iter().map(|h| miss_text[h].as_str()).collect();
        let vecs = self.embed(&batch_texts)?;
        for (h, vec) in chunk.iter().zip(vecs) {
            cache.put(&model_id, h, vec.clone())?;
            resolved.insert(h.clone(), vec);
        }
    }

    Ok(hashes.iter().map(|h| resolved[h].clone()).collect())
}

/// Deterministic cache namespace when `weights.npz` is absent: blake2b-8 over
/// the raw `model.onnx` bytes. Will NOT match Python's `inhouse:…` id (parity
/// needs the weights) — fine, since Python doesn't share this redb file.
fn onnx_fallback_namespace(&self) -> String {
    format!("onnx:d{}:{}", self.dim, self.onnx_digest.clone())
}
```
To support the fallback deterministically, in `load` compute and store an
`onnx_digest: String` field = blake2b-8 hex over the bytes read from
`<dir>/model.onnx` (read them once; you already pass the path to
`commit_from_file`). Reuse the `hex` helper pattern from `model_id.rs` (make it
`pub(crate)` there and import, to stay DRY).

- [ ] **Step 4: Run to verify it passes**

```bash
cargo test -p goldenembed --manifest-path packages/rust/extensions/goldenembed/Cargo.toml
```
Expected: PASS (whole crate green).

- [ ] **Step 5: Commit**

```bash
git add packages/rust/extensions/goldenembed/src/lib.rs packages/rust/extensions/goldenembed/src/model_id.rs
git commit -m "feat(goldenembed): embed_cached with deduped batched misses"
```

### Task 7: fmt/clippy + open PR 1

- [ ] **Step 1: Format + lint**

```bash
cd packages/rust/extensions/goldenembed && cargo fmt && cargo clippy --all-targets -- -D warnings
```
Expected: clean. Fix any clippy findings.

- [ ] **Step 2: Push + PR** (auth dance — see header)

```bash
gh auth switch --user benzsevern
git push -u origin feature/goldenembed-finish-508
GH_TOKEN=$(gh auth token --user benzsevern) gh pr create --repo benseverndev-oss/goldenmatch \
  --title "feat(goldenembed): model_id parity + redb embedding cache (#508 a)" \
  --body "Stage A′+A of #508. Reproduces Python model_id from weights.npz; adds redb two-tier embedding cache keyed by model_id + sha256(normalize_text). Non-goal: Python cache sharing."
gh auth switch --user benzsevern-mjh
```

- [ ] **Step 3: Watch CI green, then merge** (see root CLAUDE.md poll-loop + merge patterns)

---

## Stage B — throughput bench

### Task 8: `goldenembed bench` CLI verb

**Files:**
- Modify: `packages/rust/extensions/goldenembed/src/main.rs`

- [ ] **Step 1: Add the bench subcommand (hand-rolled arg parse, no new deps)**

Detect a leading `bench` verb in `main`. Implement:
```rust
fn run_bench(model_dir: &str, rows: usize, batches: &[usize]) -> anyhow::Result<()> {
    use std::time::Instant;
    let mut model = goldenembed::GoldenEmbed::load(model_dir)?;
    // Deterministic synthetic corpus (vary by index; no RNG dep).
    let corpus: Vec<String> = (0..rows).map(|i| format!("record number {i} acme corp")).collect();
    println!("rows={rows} dim={} model_id={}", model.dim(),
             model.model_id().unwrap_or("<onnx-only>"));
    for &b in batches {
        let refs: Vec<&str> = corpus.iter().map(String::as_str).collect();
        let mut latencies: Vec<f64> = Vec::new();
        let start = Instant::now();
        for chunk in refs.chunks(b) {
            let t = Instant::now();
            let _ = model.embed(chunk)?;
            latencies.push(t.elapsed().as_secs_f64() * 1000.0);
        }
        let wall = start.elapsed().as_secs_f64();
        latencies.sort_by(|a, c| a.partial_cmp(c).unwrap());
        let p = |q: f64| latencies[((latencies.len() as f64 * q) as usize).min(latencies.len() - 1)];
        println!(
            "batch={b:>6} rows/sec={:>10.0} p50={:>7.2}ms p95={:>7.2}ms",
            rows as f64 / wall, p(0.50), p(0.95)
        );
    }
    Ok(())
}
```
Parse `--rows` (default 50_000) and `--batch` (comma list, default `64,256,1024,4096`). Keep the existing embed/no-input paths intact.

- [ ] **Step 2: Build + smoke-run against a fixture**

```bash
cargo build --release -p goldenembed --manifest-path packages/rust/extensions/goldenembed/Cargo.toml
./packages/rust/extensions/goldenembed/target/release/goldenembed bench \
  --model packages/rust/extensions/goldenembed/tests/fixtures/tiny_model --rows 2000 --batch 64,512
```
Expected: prints a rows/sec table, no panic.

- [ ] **Step 3: Commit**

```bash
git add packages/rust/extensions/goldenembed/src/main.rs
git commit -m "feat(goldenembed): bench CLI verb (rows/sec + p50/p95 by batch)"
```

### Task 9: Python side-by-side comparison harness

**Files:**
- Create: `packages/python/goldenmatch/scripts/bench_goldenembed.py`

- [ ] **Step 1: Write the harness**

```python
"""Side-by-side throughput: Python GoldenEmbedModel.embed vs the Rust
`goldenembed bench` CLI, on identical synthetic text. Prints rows/sec per side
+ a cosine parity spot-check. Reports the ratio (env-dependent), does not assert.
"""
from __future__ import annotations
import argparse, subprocess, time
from pathlib import Path
import numpy as np
from goldenmatch.embeddings.inhouse.model import GoldenEmbedModel


def synthetic(rows: int) -> list[str]:
    return [f"record number {i} acme corp" for i in range(rows)]


def py_throughput(model_dir: str, rows: int, batch: int, backend: str) -> float:
    m = GoldenEmbedModel.load(model_dir)
    texts = synthetic(rows)
    start = time.perf_counter()
    for i in range(0, rows, batch):
        m.embed(texts[i : i + batch], backend=backend)
    return rows / (time.perf_counter() - start)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--rows", type=int, default=50_000)
    ap.add_argument("--batch", type=int, default=1024)
    ap.add_argument("--rust-bin", default="goldenembed")
    args = ap.parse_args()

    for backend in ("auto", "onnx"):
        rps = py_throughput(args.model, args.rows, args.batch, backend)
        print(f"python[{backend}] rows/sec={rps:,.0f}")

    print("--- rust ---")
    subprocess.run(
        [args.rust_bin, "bench", "--model", args.model,
         "--rows", str(args.rows), "--batch", str(args.batch)],
        check=True,
    )

    # Parity spot-check on a small sample.
    m = GoldenEmbedModel.load(args.model)
    sample = synthetic(8)
    v = m.embed(sample, backend="auto")
    # cosine of each row with itself via the onnx backend == ~1.0
    v2 = m.embed(sample, backend="onnx")
    cos = (v * v2).sum(1) / (np.linalg.norm(v, axis=1) * np.linalg.norm(v2, axis=1) + 1e-9)
    print(f"parity cosine min={cos.min():.6f} (expect ~1.0)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-run**

```bash
python packages/python/goldenmatch/scripts/bench_goldenembed.py \
  --model packages/rust/extensions/goldenembed/tests/fixtures/tiny_model \
  --rows 2000 --batch 512 \
  --rust-bin packages/rust/extensions/goldenembed/target/release/goldenembed
```
Expected: prints python + rust rows/sec and a parity cosine ~1.0.

- [ ] **Step 3: Commit**

```bash
git add packages/python/goldenmatch/scripts/bench_goldenembed.py
git commit -m "feat(goldenembed): python<->rust throughput comparison harness"
```

### Task 10: workflow_dispatch bench CI job

**Files:**
- Create: `.github/workflows/bench-goldenembed.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: bench-goldenembed
on:
  workflow_dispatch:
    inputs:
      rows:
        description: "rows to embed"
        default: "200000"
      runner:
        description: "runner label"
        default: "large-new-64GB"
jobs:
  bench:
    runs-on: ${{ github.event.inputs.runner }}
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - name: Build goldenembed (release)
        working-directory: packages/rust/extensions/goldenembed
        run: cargo build --release
      - name: Bench
        working-directory: packages/rust/extensions/goldenembed
        run: |
          ./target/release/goldenembed bench \
            --model tests/fixtures/tiny_model \
            --rows ${{ github.event.inputs.rows }} \
            --batch 64,256,1024,4096 | tee bench.txt
      - name: Summary
        working-directory: packages/rust/extensions/goldenembed
        run: |
          echo '```' >> $GITHUB_STEP_SUMMARY
          cat bench.txt >> $GITHUB_STEP_SUMMARY
          echo '```' >> $GITHUB_STEP_SUMMARY
```
(Per `feedback_bench_default_runner`: default to `large-new-64GB`. This job is dispatch-only — NOT added to the default `needs:` aggregate gate.)

- [ ] **Step 2: Commit + push + dispatch a smoke run**

```bash
git add .github/workflows/bench-goldenembed.yml
git commit -m "ci(goldenembed): workflow_dispatch throughput bench job"
# after merge to main (workflow must exist on the ref):
# gh workflow run bench-goldenembed.yml --ref main -f rows=50000
```

- [ ] **Step 3: Open PR 2, watch CI, merge** (auth dance + poll-loop as in Task 7)

PR title: `feat(goldenembed): throughput bench + comparison harness (#508 b)`

---

## Stage C — datafusion-udf embed UDF (spike-gated)

### Task 11: SPIKE — ort Session thread-safety + onnxruntime-in-cdylib

**Files:** none committed (investigation). Record findings in the PR description.

- [ ] **Step 1: Determine the `ort` 2.0-rc.10 `Session::run` receiver**

```bash
grep -rn "fn run" ~/.cargo/registry/src/*/ort-2.0.0-rc.10/src/session/ 2>/dev/null | head
```
Or read docs.rs for `ort` 2.0.0-rc.10 `Session`. Record: is `run` `&self` or `&mut self`?
- If `&self` → model can be `Arc<GoldenEmbed>` (lock-free). BUT `GoldenEmbed::embed` currently takes `&mut self`; refactor `embed` to `&self` if the underlying `session.run` allows, else keep `&mut` and use a `Mutex`.
- If `&mut self` → wrap as `Arc<Mutex<GoldenEmbed>>` in the UDF struct (correctness over throughput for v1; note contention in a comment).

> **Expected outcome:** `ort` 2.x `Session::run` is `&mut self`, and
> `GoldenEmbed::embed` is already `&mut self` (`lib.rs:54`), so the
> `Arc<Mutex<GoldenEmbed>>` branch in Task 12 is almost certainly the survivor.
> Don't over-invest in the lock-free path unless the spike surprises you.

- [ ] **Step 2: Confirm onnxruntime binary loads from within an abi3 cdylib**

The `datafusion-udf` cdylib is imported by CPython. Confirm `ort`'s default
`download-binaries` (or chosen feature) resolves the onnxruntime shared lib at
load time in that context. Quick check: add `goldenembed` as a path dep
(Task 12 step 1) and write a throwaway `#[test]` in the datafusion-udf crate
that does `goldenembed::GoldenEmbed::load(fixture)?.embed(&["x"])?` and run
`cargo test`. If onnxruntime fails to load, evaluate `ort` features
(`load-dynamic` + a bundled lib path) before proceeding.

- [ ] **Step 3: Decision gate**

Write the verdict in the PR/issue: GREEN (proceed with Arc or Arc<Mutex>) or
RED (surface to the user; do not force the UDF). Only continue to Task 12 on
GREEN.

### Task 12: goldenmatch_embed ScalarUDF

**Files:**
- Modify: `packages/rust/extensions/datafusion-udf/Cargo.toml`
- Create: `packages/rust/extensions/datafusion-udf/src/embed_udf.rs`
- Modify: `packages/rust/extensions/datafusion-udf/src/lib.rs`

- [ ] **Step 1: Add the path dep**

In `datafusion-udf/Cargo.toml` `[dependencies]`:
```toml
goldenembed = { path = "../goldenembed" }
```
No other Cargo change needed — `arrow-array 58` already provides the
`FixedSizeListBuilder` / `Float32Builder` used below. (This dep pulls `ort` +
`redb`/`zip`/`sha2` into the cdylib; the Task 11 spike confirms onnxruntime
loads in that context before you rely on this.)

- [ ] **Step 2: Write the UDF (shape mirrors scalar_udf.rs, single-arg)**

Create `embed_udf.rs`:
```rust
//! goldenmatch_embed(text: Utf8) -> FixedSizeList<Float32, dim>. Loads a saved
//! GoldenEmbedModel from GOLDENEMBED_MODEL_DIR once at construction and runs the
//! pure-Rust featurize + ONNX projection per batch — a zero-CPython SQL embed
//! path. NULL Utf8 -> empty string -> zero-then-normalized vector (matches the
//! Python None convention).
use std::any::Any;
use std::sync::{Arc, Mutex};   // drop Mutex if the spike says Session::run is &self

use arrow_array::builder::{FixedSizeListBuilder, Float32Builder};
use arrow_array::cast::AsArray;
use arrow_schema::{DataType, Field};
use datafusion_common::error::{DataFusionError, Result as DataFusionResult};
use datafusion_expr::{
    ColumnarValue, ScalarFunctionArgs, ScalarUDF, ScalarUDFImpl, Signature, Volatility,
};
use datafusion_ffi::udf::FFI_ScalarUDF;
use goldenembed::GoldenEmbed;
use pyo3::types::PyCapsule;
use pyo3::{Bound, PyResult, Python, pyclass, pymethods};

#[pyclass(from_py_object, name = "EmbedUDF", module = "goldenmatch_datafusion_udf", subclass)]
#[derive(Clone)]
pub(crate) struct EmbedUDF {
    signature: Signature,
    dim: i32,
    model: Arc<Mutex<GoldenEmbed>>,
}

// ScalarUDFImpl requires Eq/Hash/PartialEq; compare on dim + model_id only.
impl PartialEq for EmbedUDF {
    fn eq(&self, other: &Self) -> bool { self.dim == other.dim }
}
impl Eq for EmbedUDF {}
impl std::hash::Hash for EmbedUDF {
    fn hash<H: std::hash::Hasher>(&self, s: &mut H) { self.dim.hash(s); }
}
impl std::fmt::Debug for EmbedUDF {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "EmbedUDF{{dim:{}}}", self.dim)
    }
}

#[pymethods]
impl EmbedUDF {
    #[new]
    fn new() -> PyResult<Self> {
        let dir = std::env::var("GOLDENEMBED_MODEL_DIR").map_err(|_| {
            pyo3::exceptions::PyRuntimeError::new_err("GOLDENEMBED_MODEL_DIR not set")
        })?;
        let model = GoldenEmbed::load(&dir)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        let dim = model.dim() as i32;
        Ok(Self {
            signature: Signature::exact(vec![DataType::Utf8], Volatility::Immutable),
            dim,
            model: Arc::new(Mutex::new(model)),
        })
    }

    fn __datafusion_scalar_udf__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyCapsule>> {
        let name = cr"datafusion_scalar_udf".into();
        let func = Arc::new(ScalarUDF::from(self.clone()));
        let provider = FFI_ScalarUDF::from(func);
        PyCapsule::new(py, provider, Some(name))
    }
}

impl ScalarUDFImpl for EmbedUDF {
    fn as_any(&self) -> &dyn Any { self }
    fn name(&self) -> &str { "goldenmatch_embed" }
    fn signature(&self) -> &Signature { &self.signature }
    fn return_type(&self, _: &[DataType]) -> DataFusionResult<DataType> {
        Ok(DataType::FixedSizeList(
            Arc::new(Field::new("item", DataType::Float32, false)),
            self.dim,
        ))
    }
    fn invoke_with_args(&self, args: ScalarFunctionArgs) -> DataFusionResult<ColumnarValue> {
        let arrs = ColumnarValue::values_to_arrays(&args.args)?;
        let texts = arrs[0].as_string::<i32>();
        let texts_ref: Vec<&str> = texts.iter().map(|o| o.unwrap_or("")).collect();
        let vecs = {
            let mut m = self.model.lock().unwrap();
            m.embed(&texts_ref).map_err(|e| DataFusionError::Execution(e.to_string()))?
        };
        let mut b = FixedSizeListBuilder::new(Float32Builder::new(), self.dim);
        for row in vecs {
            b.values().append_slice(&row);
            b.append(true);
        }
        Ok(ColumnarValue::Array(Arc::new(b.finish())))
    }
}
```

Register in `lib.rs`:
```rust
pub(crate) mod embed_udf;
use crate::embed_udf::EmbedUDF;
// inside #[pymodule]:
m.add_class::<EmbedUDF>()?;
```

- [ ] **Step 3: Build**

```bash
cargo build -p goldenmatch-datafusion-udf --manifest-path packages/rust/extensions/datafusion-udf/Cargo.toml
```
Expected: clean (adjust FixedSizeList builder/Field API to the resolved arrow 58 if names differ).

- [ ] **Step 4: Commit**

```bash
git add packages/rust/extensions/datafusion-udf/Cargo.toml \
        packages/rust/extensions/datafusion-udf/src/embed_udf.rs \
        packages/rust/extensions/datafusion-udf/src/lib.rs
git commit -m "feat(datafusion-udf): goldenmatch_embed ScalarUDF over goldenembed"
```

### Task 13: Python smoke + parity test for the UDF

**Files:**
- Create: `packages/python/goldenmatch/tests/test_embed_udf.py`

> **Where this runs:** the `datafusion-udf` crate is built into the goldenmatch
> `.venv` by the existing CI step (`ci.yml:277-288`, `matrix.pkg == 'goldenmatch'`)
> and the FFI tests live in `packages/python/goldenmatch/tests/`. So the embed
> test goes **there**, next to `test_datafusion_ffi_udf.py`, and is collected by
> the existing goldenmatch pytest lane automatically — **no new CI job and no
> `ci.yml` edit** (the crate already builds the new `EmbedUDF` class into the
> same wheel; `GOLDENEMBED_MODEL_DIR` is set by the test via `monkeypatch`).
> Registration API is `ctx.register_udf(udf(EmbedUDF()))` — copied verbatim from
> `test_datafusion_ffi_udf.py:46-52`.

- [ ] **Step 1: Write the test**

```python
"""goldenmatch_embed FFI UDF: shape + determinism + parity vs Python embed.

Mirrors test_datafusion_ffi_udf.py: pyarrow/datafusion are soft deps; the crate
is a HARD import (CI builds it into .venv). GOLDENEMBED_MODEL_DIR points at the
committed tiny fixture, set before EmbedUDF() is constructed (the Rust ctor reads
the env var at construction).
"""
from pathlib import Path

import numpy as np
import pytest

pa = pytest.importorskip("pyarrow")
datafusion = pytest.importorskip("datafusion")
import goldenmatch_datafusion_udf  # noqa: E402,F401  HARD import (loud guard)
from goldenmatch.embeddings.inhouse.model import GoldenEmbedModel  # noqa: E402

# tests -> goldenmatch -> python -> packages -> <repo root>
_REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURE = _REPO_ROOT / "packages/rust/extensions/goldenembed/tests/fixtures/tiny_model"


def test_embed_udf_shape_and_parity(monkeypatch):
    monkeypatch.setenv("GOLDENEMBED_MODEL_DIR", str(FIXTURE))
    from datafusion import SessionContext, udf
    from goldenmatch_datafusion_udf import EmbedUDF

    ctx = SessionContext()
    ctx.register_udf(udf(EmbedUDF()))
    ctx.from_arrow(
        pa.table({"t": pa.array(["acme corp", "acme corp"], pa.string())}), name="rows"
    )
    batches = ctx.sql("SELECT goldenmatch_embed(t) AS e FROM rows").collect()
    out = batches[0].column(0).to_pylist()

    assert len(out) == 2
    assert len(out[0]) == 8  # dim
    assert out[0] == out[1]  # determinism: identical input -> identical vector

    py = GoldenEmbedModel.load(str(FIXTURE)).embed(["acme corp"], backend="onnx")[0]
    v = np.asarray(out[0], dtype=np.float32)
    cos = float(np.dot(v, py) / (np.linalg.norm(v) * np.linalg.norm(py) + 1e-9))
    assert cos > 0.999, f"UDF vs python embed cosine {cos}"
```

- [ ] **Step 2: Build the crate into .venv (mirror CI) + run the test locally**

`maturin develop` installs into an ephemeral overlay env, NOT the project `.venv`
(see the `ci.yml:281-288` comment) — so build a wheel and install it, exactly as
CI does:
```bash
cd packages/python/goldenmatch
uv pip install 'datafusion>=53,<54'
uv run --with maturin maturin build --release \
  --manifest-path ../../rust/extensions/datafusion-udf/Cargo.toml --out dist-dfudf
uv pip install dist-dfudf/*.whl --reinstall
uv run pytest tests/test_embed_udf.py -v
```
Expected: PASS. If onnxruntime load fails here, that's the Task 11 spike failing
in practice — stop and surface to the user.

- [ ] **Step 3: Commit**

```bash
git add packages/python/goldenmatch/tests/test_embed_udf.py
git commit -m "test(goldenmatch): goldenmatch_embed UDF shape + parity smoke"
```

### Task 14: fmt/clippy + open PR 3

- [ ] **Step 1: Format + lint both crates**

```bash
cd packages/rust/extensions/datafusion-udf && cargo fmt && cargo clippy --all-targets -- -D warnings
cd ../goldenembed && cargo fmt --check
```

- [ ] **Step 2: PR 3** (auth dance)

PR title: `feat(datafusion-udf): SQL embed UDF over goldenembed (#508 c)`
Body: include the Task 11 spike verdict (ort receiver, onnxruntime-load result).

- [ ] **Step 3: Watch CI, merge.**

---

## Close-out

- [ ] **Update issue #508** with a comment: cache (Stage A), bench (Stage B), and the DataFusion SQL embed UDF (Stage C) shipped; postgres `gm_embed` tracked as the remaining follow-up. Close #508 if the follow-up is filed separately.
- [ ] **File the postgres follow-up issue**: `gm_embed(text) -> float4[]` pgrx pg_extern over `goldenembed` (Linux/CI-only).
- [ ] **Memory:** update `project_arrow_native_finish_line` or add a note that the goldenembed crate now has a cache + a real SQL caller, if it materially changes the roadmap state.
```
