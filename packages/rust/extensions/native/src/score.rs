//! String scorers backed by the `rapidfuzz` Rust crate — the same algorithms
//! the Python `rapidfuzz` bindings use, for the Phase 2 native block-scorer.
//!
//! Replaces the earlier hand-rolled scorers, which were ~2x slower than
//! rapidfuzz on representative shapes (they allocated a `Vec<char>` per
//! comparison and used naive O(n*m) inner loops). rapidfuzz-rs uses the same
//! bit-parallel algorithms as rapidfuzz-cpp, so per-comparison cost matches the
//! Python path while the kernel removes the per-pair Python interpreter
//! overhead. Parity is asserted (within float tolerance) in
//! tests/test_native_parity.py. All functions operate on Unicode chars
//! (codepoints), matching rapidfuzz.
use arrow::array::{Array, ArrayData, Int64Array, LargeStringArray, StringArray};
use arrow::datatypes::DataType;
use arrow::pyarrow::PyArrowType;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rapidfuzz::distance::{jaro_winkler, levenshtein};
use rapidfuzz::fuzz;
use rayon::prelude::*;
use std::collections::HashSet;
use std::sync::Arc;

/// Shared exclude index for the bucket scorer. The Python caller used to pass
/// the exclude set as a fresh `Vec<(i64, i64)>` on EVERY native call -- at 10M
/// rows / 64 buckets that materialized + marshaled + Rust-rebuilt a 36M-tuple
/// set 64 times per dedupe call, dominating the kernel wall (~1170s of 1370s
/// observed bucket_score time in QIS 10M-v9 native).
///
/// `ExcludeSet` is a single Arc<HashSet> built once on the Python side via
/// `build_exclude_set` and passed by handle to each worker's
/// `score_block_pairs_arrow` call. Threads share it read-only; HashSet's
/// `contains` is safe across &self. Arc::clone is O(1) (refcount bump).
///
/// Wire format on the Python side: the legacy `Vec<(i64, i64)>` path is still
/// supported when no handle is supplied -- the kernel falls back to building a
/// fresh HashSet from the Vec, matching the prior contract bit-for-bit.
#[pyclass(module = "goldenmatch._native", name = "ExcludeSet")]
pub struct ExcludeSet {
    set: Arc<HashSet<(i64, i64)>>,
}

#[pymethods]
impl ExcludeSet {
    fn __len__(&self) -> usize {
        self.set.len()
    }

    fn __repr__(&self) -> String {
        format!("ExcludeSet(n_pairs={})", self.set.len())
    }
}

/// Build an `ExcludeSet` from `(id_a, id_b)` tuples. Canonicalizes each pair
/// to (min, max) so callers don't have to. Runs once per dedupe call; the
/// returned handle is then passed to every `score_block_pairs_arrow` call in
/// the bucket worker loop, amortizing the build cost from O(buckets * pairs)
/// to O(pairs).
#[pyfunction]
pub fn build_exclude_set(pairs: Vec<(i64, i64)>) -> ExcludeSet {
    let mut set: HashSet<(i64, i64)> = HashSet::with_capacity(pairs.len());
    for (a, b) in pairs {
        let key = if a < b { (a, b) } else { (b, a) };
        set.insert(key);
    }
    ExcludeSet { set: Arc::new(set) }
}

/// `rapidfuzz.fuzz.token_sort_ratio` preprocessing: split on whitespace, sort
/// the tokens, rejoin with a single space. (Then `fuzz::ratio` on the result.)
fn token_sort_string(s: &str) -> String {
    let mut toks: Vec<&str> = s.split_whitespace().collect();
    toks.sort_unstable();
    toks.join(" ")
}

// ---- PyO3 surface (scale matches score_buckets._resolve_score_pair_callable:
//      jaro_winkler/levenshtein on 0-1, token_sort_ratio on 0-100) ----

#[pyfunction]
pub fn jaro_winkler_similarity(a: &str, b: &str) -> f64 {
    // rapidfuzz JaroWinkler default prefix_weight = 0.1.
    jaro_winkler::normalized_similarity(a.chars(), b.chars())
}

#[pyfunction]
pub fn levenshtein_similarity(a: &str, b: &str) -> f64 {
    // rapidfuzz Levenshtein default uniform weights (1, 1, 1).
    levenshtein::normalized_similarity(a.chars(), b.chars())
}

/// token_sort_ratio on the 0-100 scale (score_field divides by 100).
#[pyfunction]
pub fn token_sort_ratio(a: &str, b: &str) -> f64 {
    let sa = token_sort_string(a);
    let sb = token_sort_string(b);
    // rapidfuzz-rs fuzz::ratio returns [0, 1]; Python fuzz.ratio is [0, 100].
    fuzz::ratio(sa.chars(), sb.chars()) * 100.0
}

/// Scorer dispatch matching `score_buckets._resolve_score_pair_callable`'s
/// fast-path scale, all on [0, 1]. ids: 0=jaro_winkler, 1=levenshtein,
/// 2=token_sort, 3=exact.
fn score_one(scorer_id: u8, a: &str, b: &str) -> f64 {
    match scorer_id {
        0 => jaro_winkler::normalized_similarity(a.chars(), b.chars()),
        1 => levenshtein::normalized_similarity(a.chars(), b.chars()),
        2 => {
            let sa = token_sort_string(a);
            let sb = token_sort_string(b);
            fuzz::ratio(sa.chars(), sb.chars())
        }
        3 => {
            if a == b {
                1.0
            } else {
                0.0
            }
        }
        _ => 0.0,
    }
}

/// Native port of `score_buckets._score_one_bucket_fast`'s per-pair loop.
///
/// `field_values[f][r]` is the (already transform-applied) value of field `f`
/// for row `r`, in the bucket's block-sorted order. `block_sizes` are the
/// consecutive block lengths in that same order. Emits canonical (min,max)
/// pairs whose weighted score (sum(score*weight) / total_weight, with None
/// values skipped) meets `threshold`, excluding `exclude`.
///
/// Blocks are scored in parallel with `rayon` under `allow_threads` (the GIL is
/// released for the whole compute — no Python objects are touched inside), so
/// the kernel uses every core instead of relying on the caller's per-bucket
/// thread pool. `par_iter().flat_map(...).collect()` preserves block order, so
/// output ordering matches the sequential Python loop.
///
/// The scorers are rapidfuzz-rs (same algorithms as Python rapidfuzz), so
/// scores match the Python path to within float tolerance, not bit-for-bit; the
/// emitted pair set could differ only for a pair whose weighted score sits
/// within that tolerance of `threshold`.
#[allow(clippy::too_many_arguments)]
#[pyfunction]
pub fn score_block_pairs(
    py: Python<'_>,
    row_ids: Vec<i64>,
    block_sizes: Vec<usize>,
    field_values: Vec<Vec<Option<String>>>,
    scorer_ids: Vec<u8>,
    weights: Vec<f64>,
    total_weight: f64,
    threshold: f64,
    exclude: Vec<(i64, i64)>,
) -> Vec<(i64, i64, f64)> {
    let exclude: HashSet<(i64, i64)> = exclude.into_iter().collect();
    let n_fields = scorer_ids.len();

    // Precompute each block's (offset, size) span so blocks are independent
    // units of parallel work.
    let mut spans: Vec<(usize, usize)> = Vec::with_capacity(block_sizes.len());
    let mut offset = 0usize;
    for &size in &block_sizes {
        spans.push((offset, size));
        offset += size;
    }

    py.allow_threads(|| {
        spans
            .par_iter()
            .flat_map_iter(|&(offset, size)| {
                let mut local: Vec<(i64, i64, f64)> = Vec::new();
                if size >= 2 {
                    let end = offset + size;
                    for i in offset..end - 1 {
                        let ri = row_ids[i];
                        for j in (i + 1)..end {
                            let rj = row_ids[j];
                            let pair_key = if ri < rj { (ri, rj) } else { (rj, ri) };
                            if exclude.contains(&pair_key) {
                                continue;
                            }
                            let mut score_sum = 0.0_f64;
                            let mut weight_sum = 0.0_f64;
                            for f in 0..n_fields {
                                if let (Some(a), Some(b)) =
                                    (&field_values[f][i], &field_values[f][j])
                                {
                                    score_sum += score_one(scorer_ids[f], a, b) * weights[f];
                                    weight_sum += weights[f];
                                }
                            }
                            if weight_sum > 0.0 {
                                let combined = score_sum / total_weight;
                                if combined >= threshold {
                                    local.push((pair_key.0, pair_key.1, combined));
                                }
                            }
                        }
                    }
                }
                local
            })
            .collect()
    })
}

/// A block-sorted Utf8 column read zero-copy from an Arrow buffer. Polars emits
/// `LargeUtf8` (i64 offsets) by default; plain pyarrow string arrays are `Utf8`
/// (i32). Both are owned (Arc-backed -> `Send + Sync`) so the rayon closure can
/// borrow them across `allow_threads`.
enum StrCol {
    Utf8(StringArray),
    Large(LargeStringArray),
}

impl StrCol {
    fn from_data(data: ArrayData) -> PyResult<Self> {
        match data.data_type() {
            DataType::Utf8 => Ok(StrCol::Utf8(StringArray::from(data))),
            DataType::LargeUtf8 => Ok(StrCol::Large(LargeStringArray::from(data))),
            other => Err(PyValueError::new_err(format!(
                "score_block_pairs_arrow: field column must be utf8/large_utf8, got {other:?}"
            ))),
        }
    }

    #[inline]
    fn get(&self, i: usize) -> Option<&str> {
        match self {
            StrCol::Utf8(a) => (!a.is_null(i)).then(|| a.value(i)),
            StrCol::Large(a) => (!a.is_null(i)).then(|| a.value(i)),
        }
    }
}

/// Arrow-native sibling of [`score_block_pairs`]: reads `row_ids` (Int64) and the
/// `field_arrays` (Utf8/LargeUtf8) directly from Arrow buffers via the C Data
/// Interface, so the caller skips the per-element `.to_list()` materialization +
/// PyO3 `Vec<Vec<Option<String>>>` clone that dominate the block-scoring stage
/// (measured ~58% of native wall at 1M rows; see scripts/bench_native_kernels.py).
///
/// Identical scoring to `score_block_pairs` — same scorers, same weighted-average
/// (None values skipped), same canonical (min,max) emission in block order — so
/// the two are diffed in tests/test_native_parity.py. `block_sizes` are the
/// consecutive block lengths in the (same) block-sorted row order.
#[allow(clippy::too_many_arguments)]
#[pyfunction]
#[pyo3(signature = (
    row_ids, field_arrays, block_sizes, scorer_ids, weights,
    total_weight, threshold, exclude=None, exclude_set=None,
))]
pub fn score_block_pairs_arrow(
    py: Python<'_>,
    row_ids: PyArrowType<ArrayData>,
    field_arrays: Vec<PyArrowType<ArrayData>>,
    block_sizes: Vec<usize>,
    scorer_ids: Vec<u8>,
    weights: Vec<f64>,
    total_weight: f64,
    threshold: f64,
    exclude: Option<Vec<(i64, i64)>>,
    exclude_set: Option<PyRef<'_, ExcludeSet>>,
) -> PyResult<Vec<(i64, i64, f64)>> {
    let row_data = row_ids.0;
    if row_data.data_type() != &DataType::Int64 {
        return Err(PyValueError::new_err(format!(
            "score_block_pairs_arrow: row_ids must be int64, got {:?}",
            row_data.data_type()
        )));
    }
    let row_ids = Int64Array::from(row_data);
    let fields: Vec<StrCol> = field_arrays
        .into_iter()
        .map(|p| StrCol::from_data(p.0))
        .collect::<PyResult<_>>()?;

    let n_rows = row_ids.len();
    for (f, col) in fields.iter().enumerate() {
        let col_len = match col {
            StrCol::Utf8(a) => a.len(),
            StrCol::Large(a) => a.len(),
        };
        if col_len != n_rows {
            return Err(PyValueError::new_err(format!(
                "score_block_pairs_arrow: field {f} length {col_len} != row count {n_rows}"
            )));
        }
    }

    // Exclude resolution: prefer the shared handle (Track 1 Fix B), fall back
    // to the legacy Vec-rebuilt-per-call path for callers that haven't been
    // updated. The handle path is O(1) Arc::clone; the legacy path is O(N)
    // HashSet build, run once per call (= 64 times per dedupe at 10M, the
    // measured wall hog).
    let local_set: HashSet<(i64, i64)>;
    let exclude_ref: &HashSet<(i64, i64)> = match (&exclude_set, exclude) {
        (Some(handle), _) => &handle.set,
        (None, Some(v)) => {
            local_set = v.into_iter().collect();
            &local_set
        }
        (None, None) => {
            local_set = HashSet::new();
            &local_set
        }
    };
    let n_fields = scorer_ids.len();

    let mut spans: Vec<(usize, usize)> = Vec::with_capacity(block_sizes.len());
    let mut offset = 0usize;
    for &size in &block_sizes {
        spans.push((offset, size));
        offset += size;
    }

    Ok(py.allow_threads(|| {
        spans
            .par_iter()
            .flat_map_iter(|&(offset, size)| {
                let mut local: Vec<(i64, i64, f64)> = Vec::new();
                if size >= 2 {
                    let end = offset + size;
                    for i in offset..end - 1 {
                        let ri = row_ids.value(i);
                        for j in (i + 1)..end {
                            let rj = row_ids.value(j);
                            let pair_key = if ri < rj { (ri, rj) } else { (rj, ri) };
                            if exclude_ref.contains(&pair_key) {
                                continue;
                            }
                            let mut score_sum = 0.0_f64;
                            let mut weight_sum = 0.0_f64;
                            for f in 0..n_fields {
                                if let (Some(a), Some(b)) = (fields[f].get(i), fields[f].get(j)) {
                                    score_sum += score_one(scorer_ids[f], a, b) * weights[f];
                                    weight_sum += weights[f];
                                }
                            }
                            if weight_sum > 0.0 {
                                let combined = score_sum / total_weight;
                                if combined >= threshold {
                                    local.push((pair_key.0, pair_key.1, combined));
                                }
                            }
                        }
                    }
                }
                local
            })
            .collect()
    }))
}
