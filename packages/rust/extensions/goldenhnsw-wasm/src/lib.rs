//! wasm-bindgen wrapper over `goldenhnsw` (native HNSW / `IndexHNSWFlat`), so
//! the JS/TS port runs the SAME kernel as the Python wheel and the Rust core —
//! byte-identical inner-product ranking, no FAISS, no `hnswlib-node` native
//! addon, edge-safe (pure wasm). Mirrors the sibling `autoconfig-wasm` shim.
//!
//! Data crosses the boundary as typed arrays (not JSON): vectors in as
//! `Float32Array` (`&[f32]`), search results out as a `Float64Array` of
//! interleaved `[id0, score0, id1, score1, …]`. f64 holds the u32 ids exactly
//! (< 2^53), so no precision is lost; the score is the raw inner product.

use goldenhnsw::{HnswIndex, HnswParams};
use wasm_bindgen::prelude::*;

/// A native HNSW index exposed to JS/TS. Construct with the graph parameters,
/// bulk-load with `add_batch` (or incrementally with `add`), then `search`.
#[wasm_bindgen]
pub struct WasmHnswIndex {
    inner: HnswIndex,
    dim: usize,
}

#[wasm_bindgen]
impl WasmHnswIndex {
    /// Create an empty index. All parameters are required (wasm-bindgen has no
    /// default args — the TS wrapper supplies the defaults). `seed` is a `u32`
    /// here (JS-number-safe); the Rust/Python surfaces use the wider `u64`.
    #[wasm_bindgen(constructor)]
    pub fn new(
        dim: usize,
        m: usize,
        ef_construction: usize,
        ef_search: usize,
        seed: u32,
    ) -> Result<WasmHnswIndex, JsError> {
        if dim == 0 {
            return Err(JsError::new("goldenhnsw-wasm: dim must be > 0"));
        }
        if m < 2 {
            return Err(JsError::new("goldenhnsw-wasm: m must be >= 2"));
        }
        let params = HnswParams {
            m,
            ef_construction,
            ef_search,
            seed: seed as u64,
        };
        Ok(WasmHnswIndex {
            inner: HnswIndex::new(dim, params),
            dim,
        })
    }

    /// Number of vectors currently indexed.
    #[wasm_bindgen(getter)]
    pub fn len(&self) -> usize {
        self.inner.len()
    }

    /// True when the index holds no vectors.
    #[wasm_bindgen(getter)]
    pub fn is_empty(&self) -> bool {
        self.inner.is_empty()
    }

    /// The vector dimensionality.
    #[wasm_bindgen(getter)]
    pub fn dim(&self) -> usize {
        self.dim
    }

    /// Add a single `dim`-length vector, returning its id (`0..N`).
    pub fn add(&mut self, vec: &[f32]) -> Result<u32, JsError> {
        if vec.len() != self.dim {
            return Err(JsError::new(&format!(
                "goldenhnsw-wasm: vector dim {} != index dim {}",
                vec.len(),
                self.dim
            )));
        }
        Ok(self.inner.add(vec))
    }

    /// Add `n` vectors from a flat row-major `Float32Array` (`n * dim` floats).
    /// Returns the id of the first added vector.
    pub fn add_batch(&mut self, flat: &[f32], n: usize) -> Result<u32, JsError> {
        let expected = n
            .checked_mul(self.dim)
            .ok_or_else(|| JsError::new("goldenhnsw-wasm: n * dim overflows"))?;
        if flat.len() != expected {
            return Err(JsError::new(&format!(
                "goldenhnsw-wasm: buffer holds {} floats, expected n*dim = {}",
                flat.len(),
                expected
            )));
        }
        let base = self.inner.len() as u32;
        for row in flat.chunks_exact(self.dim) {
            self.inner.add(row);
        }
        Ok(base)
    }

    /// Search for the `k` nearest neighbors of `query`. Returns a flat
    /// `Float64Array` of interleaved `[id0, score0, id1, score1, …]`, sorted by
    /// descending inner-product score. Length is `2 * min(k, len)`.
    pub fn search(&self, query: &[f32], k: usize) -> Result<Vec<f64>, JsError> {
        if query.len() != self.dim {
            return Err(JsError::new(&format!(
                "goldenhnsw-wasm: query dim {} != index dim {}",
                query.len(),
                self.dim
            )));
        }
        let hits = self.inner.search(query, k);
        let mut out = Vec::with_capacity(hits.len() * 2);
        for (id, score) in hits {
            out.push(id as f64);
            out.push(score as f64);
        }
        Ok(out)
    }
}
