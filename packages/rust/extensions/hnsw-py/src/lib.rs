//! `goldenmatch_hnsw._hnsw` — native HNSW ANN index (PyO3 extension module).
//!
//! A thin pyo3 wrapper over the pyo3-free `goldenhnsw` crate so Python's
//! `goldenmatch.core.ann_blocker.ANNBlocker` gets a native `IndexHNSWFlat`
//! backend — sub-linear ANN with zero FAISS/C dependency. All of pyo3 is
//! confined to this wheel; `goldenhnsw` itself stays pyo3-free.
//!
//! Two ingest/query shapes are exposed:
//!   * convenient per-vector `add` / `search` (Python lists of floats), and
//!   * a fast bulk path (`add_batch` / `search_batch`) that takes the raw
//!     little-endian float32 buffer of a C-contiguous numpy array
//!     (`arr.astype('<f4').tobytes()`), avoiding per-element Python↔Rust
//!     marshaling on million-row corpora.
//!
//! Scores are the raw inner product, byte-for-byte with FAISS `IndexFlatIP`
//! (see the `goldenhnsw` crate docs for the metric contract).

use goldenhnsw::{HnswIndex, HnswParams};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// Reinterpret a little-endian float32 byte buffer as `Vec<f32>`.
fn bytes_to_f32(data: &[u8]) -> PyResult<Vec<f32>> {
    if !data.len().is_multiple_of(4) {
        return Err(PyValueError::new_err(
            "goldenmatch-hnsw: byte buffer length is not a multiple of 4 (expected float32)",
        ));
    }
    Ok(data
        .chunks_exact(4)
        .map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]))
        .collect())
}

/// Native HNSW index over `dim`-dimensional float32 vectors, inner-product
/// metric. Mirrors the surface `ANNBlocker` needs: incremental `add`, bulk
/// `build`/`add_batch`, single `search` and batched `search_batch`.
#[pyclass(name = "HnswIndex", module = "goldenmatch_hnsw._hnsw")]
pub struct PyHnswIndex {
    inner: HnswIndex,
    dim: usize,
}

#[pymethods]
impl PyHnswIndex {
    /// Create an empty index. `m`/`ef_construction`/`ef_search`/`seed` map onto
    /// the HNSW graph parameters (see `goldenhnsw::HnswParams`).
    #[new]
    #[pyo3(signature = (dim, m=16, ef_construction=200, ef_search=64, seed=0x9E3779B97F4A7C15))]
    fn new(
        dim: usize,
        m: usize,
        ef_construction: usize,
        ef_search: usize,
        seed: u64,
    ) -> PyResult<Self> {
        if dim == 0 {
            return Err(PyValueError::new_err("goldenmatch-hnsw: dim must be > 0"));
        }
        if m < 2 {
            return Err(PyValueError::new_err("goldenmatch-hnsw: m must be >= 2"));
        }
        let params = HnswParams {
            m,
            ef_construction,
            ef_search,
            seed,
        };
        Ok(Self {
            inner: HnswIndex::new(dim, params),
            dim,
        })
    }

    #[getter]
    fn dim(&self) -> usize {
        self.dim
    }

    /// Number of vectors currently indexed.
    fn __len__(&self) -> usize {
        self.inner.len()
    }

    #[getter]
    fn size(&self) -> usize {
        self.inner.len()
    }

    /// Add a single vector, returning its assigned id (`0..N`).
    fn add(&mut self, vec: Vec<f32>) -> PyResult<u32> {
        if vec.len() != self.dim {
            return Err(PyValueError::new_err(format!(
                "goldenmatch-hnsw: vector dim {} != index dim {}",
                vec.len(),
                self.dim
            )));
        }
        Ok(self.inner.add(&vec))
    }

    /// Add `n` vectors from a little-endian float32 buffer (`n * dim` floats,
    /// row-major). Returns the id of the first added vector.
    fn add_batch(&mut self, data: &[u8], n: usize) -> PyResult<u32> {
        let flat = bytes_to_f32(data)?;
        let expected = n
            .checked_mul(self.dim)
            .ok_or_else(|| PyValueError::new_err("goldenmatch-hnsw: n * dim overflows"))?;
        if flat.len() != expected {
            return Err(PyValueError::new_err(format!(
                "goldenmatch-hnsw: buffer holds {} floats, expected n*dim = {}",
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

    /// Query the `k` nearest neighbors of `query`, returned as
    /// `(id, inner_product)` sorted by descending inner product.
    fn search(&self, query: Vec<f32>, k: usize) -> PyResult<Vec<(u32, f32)>> {
        if query.len() != self.dim {
            return Err(PyValueError::new_err(format!(
                "goldenmatch-hnsw: query dim {} != index dim {}",
                query.len(),
                self.dim
            )));
        }
        Ok(self.inner.search(&query, k))
    }

    /// Query `n` vectors from a little-endian float32 buffer, returning one
    /// `(id, score)` list per query row (each sorted by descending score).
    fn search_batch(&self, data: &[u8], n: usize, k: usize) -> PyResult<Vec<Vec<(u32, f32)>>> {
        let flat = bytes_to_f32(data)?;
        let expected = n
            .checked_mul(self.dim)
            .ok_or_else(|| PyValueError::new_err("goldenmatch-hnsw: n * dim overflows"))?;
        if flat.len() != expected {
            return Err(PyValueError::new_err(format!(
                "goldenmatch-hnsw: buffer holds {} floats, expected n*dim = {}",
                flat.len(),
                expected
            )));
        }
        Ok(flat
            .chunks_exact(self.dim)
            .map(|q| self.inner.search(q, k))
            .collect())
    }
}

#[pymodule]
fn _hnsw(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_class::<PyHnswIndex>()?;
    Ok(())
}
