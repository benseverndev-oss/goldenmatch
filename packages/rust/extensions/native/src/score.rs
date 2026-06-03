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
use numpy::{IntoPyArray, PyArray2};
use goldenmatch_score_core::score_one;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
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

// ---- PyO3 surface (scale matches score_buckets._resolve_score_pair_callable:
//      jaro_winkler/levenshtein on 0-1, token_sort_ratio on 0-100) ----
//
// The scorers + token_sort preprocessing + `score_one` dispatch live in the
// pyo3-free `goldenmatch-score-core` crate (one source of truth shared with the
// DataFusion FFI UDFs). These are thin #[pyfunction] SHIMS delegating into it —
// a bare `use` of the core fns won't satisfy lib.rs's `wrap_pyfunction!`, so the
// shims are required. `score_one` is re-imported verbatim above (no shim: it's
// called only from Rust by score_block_pairs / score_block_pairs_arrow /
// score_field_matrix, which stay in this crate).

#[pyfunction]
pub fn jaro_winkler_similarity(a: &str, b: &str) -> f64 {
    goldenmatch_score_core::jaro_winkler_similarity(a, b)
}

#[pyfunction]
pub fn levenshtein_similarity(a: &str, b: &str) -> f64 {
    goldenmatch_score_core::levenshtein_similarity(a, b)
}

/// token_sort_ratio on the 0-100 scale (score_field divides by 100).
#[pyfunction]
pub fn token_sort_ratio(a: &str, b: &str) -> f64 {
    goldenmatch_score_core::token_sort_ratio(a, b)
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

    // Per-block scorer, shared by the sequential and rayon paths so the two can
    // never diverge. Borrows only Sync data (the arrow arrays, the exclude set,
    // the weight/scorer slices) and holds no mutable state, so it is safe to
    // call from rayon workers AND from a plain sequential loop.
    let score_span = |offset: usize, size: usize| -> Vec<(i64, i64, f64)> {
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
    };

    // issue #688: rayon's blocking `collect` parks the calling thread on a
    // `LockLatch` futex that makes near-zero forward progress on some Linux
    // runners (ubuntu-latest-xlarge / EPYC) -- a sub-second scoring job turned
    // into ~190s of futex wait with zero CPU. The kernel's internal rayon is
    // also redundant in the common case: the Python caller (`score_buckets`)
    // already fans buckets across a ThreadPoolExecutor with the GIL released
    // here, so each kernel call is one of N concurrent calls. So score
    // small/medium calls in the CALLING thread (no rayon, no latch), and only
    // dispatch to rayon when a single call carries enough intra-call work that
    // the parallel speedup outweighs the dispatch (the rare few-huge-buckets
    // shape). Gate on the candidate-pair count; tune/override via
    // GOLDENMATCH_NATIVE_RAYON_MIN_PAIRS (default 20M, 0 = always rayon, a very
    // large value = always sequential). Both paths walk spans in order, so the
    // emitted (min,max) pair sequence is byte-identical either way (parity
    // asserted in tests/test_native_block_seq_parity.py).
    let total_pairs: u128 = block_sizes
        .iter()
        .map(|&s| {
            let s = s as u128;
            if s >= 2 {
                s * (s - 1) / 2
            } else {
                0
            }
        })
        .sum();
    let rayon_min_pairs: u128 = std::env::var("GOLDENMATCH_NATIVE_RAYON_MIN_PAIRS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(20_000_000);

    Ok(py.allow_threads(|| {
        if total_pairs >= rayon_min_pairs {
            spans
                .par_iter()
                .flat_map_iter(|&(offset, size)| score_span(offset, size))
                .collect()
        } else {
            spans
                .iter()
                .flat_map(|&(offset, size)| score_span(offset, size))
                .collect()
        }
    }))
}

// ============================================================================
// Per-field score matrix kernel (slow-path NxN replacement)
// ----------------------------------------------------------------------------
// `score_field_matrix(values_a, values_b, scorer_id) -> np.ndarray<f32, (N, M)>`
// is the cdist-shaped primitive that unifies the slow path's
// `_fuzzy_score_matrix` / `_soundex_score_matrix` / `_dice_score_matrix` /
// `_jaccard_score_matrix` callers behind one Rust kernel. Zero-copy Arrow in,
// owned-numpy-buffer out. Self-cdist (values_a is values_b) only fills the
// upper triangle + mirrors -- saves ~half the work on symmetric calls.
// ----------------------------------------------------------------------------

/// Soundex code matching `jellyfish.soundex` byte-for-byte. ASCII letters
/// only; non-letter input returns the empty string (caller treats empty
/// codes as non-matching, mirroring Python's `_soundex_score_matrix`).
fn soundex(s: &str) -> String {
    // Take the first alphabetic char as the seed letter (uppercased).
    let mut iter = s.chars();
    let first = loop {
        match iter.next() {
            Some(c) if c.is_ascii_alphabetic() => break c.to_ascii_uppercase(),
            Some(_) => continue,
            None => return String::new(),
        }
    };
    let mut out = String::with_capacity(4);
    out.push(first);
    let mut last_code = soundex_code(first);
    for c in iter {
        if !c.is_ascii_alphabetic() {
            continue;
        }
        let code = soundex_code(c.to_ascii_uppercase());
        // Skip Hs and Ws between consonants (jellyfish ignores them but does
        // not reset last_code).
        if code == b'0' && (c.eq_ignore_ascii_case(&'H') || c.eq_ignore_ascii_case(&'W')) {
            continue;
        }
        if code != b'0' && code != last_code {
            out.push(code as char);
            if out.len() == 4 {
                break;
            }
        }
        last_code = code;
    }
    while out.len() < 4 {
        out.push('0');
    }
    out
}

fn soundex_code(c: char) -> u8 {
    match c {
        'B' | 'F' | 'P' | 'V' => b'1',
        'C' | 'G' | 'J' | 'K' | 'Q' | 'S' | 'X' | 'Z' => b'2',
        'D' | 'T' => b'3',
        'L' => b'4',
        'M' | 'N' => b'5',
        'R' => b'6',
        _ => b'0',
    }
}

/// Read an Arrow Utf8 / LargeUtf8 column into a Vec<String>, mapping null
/// entries to empty strings (matches the slow path's caller convention --
/// see `_fuzzy_score_matrix:349` and `_soundex_score_matrix:451`).
fn arrow_to_strings(data: ArrayData) -> PyResult<Vec<String>> {
    match data.data_type() {
        DataType::Utf8 => {
            let arr = StringArray::from(data);
            let mut out = Vec::with_capacity(arr.len());
            for i in 0..arr.len() {
                if arr.is_null(i) {
                    out.push(String::new());
                } else {
                    out.push(arr.value(i).to_string());
                }
            }
            Ok(out)
        }
        DataType::LargeUtf8 => {
            let arr = LargeStringArray::from(data);
            let mut out = Vec::with_capacity(arr.len());
            for i in 0..arr.len() {
                if arr.is_null(i) {
                    out.push(String::new());
                } else {
                    out.push(arr.value(i).to_string());
                }
            }
            Ok(out)
        }
        other => Err(PyValueError::new_err(format!(
            "score_field_matrix: expected Utf8 or LargeUtf8, got {other:?}"
        ))),
    }
}

/// Per-field score matrix.
///
/// `scorer_id`:
///   0 = jaro_winkler, 1 = levenshtein, 2 = token_sort (returns [0,1] —
///   matches the slow path's `_fuzzy_score_matrix` which divides ts by 100),
///   3 = exact, 4 = soundex_match (binary 0/1).
///
/// `symmetric=True` when the caller knows `values_a is values_b`. Skips the
/// lower triangle compute and mirrors. Symmetric output diagonal is 1.0 on
/// non-null pairs across all scorers; 0.0 on null/null per the empty-string
/// mapping `arrow_to_strings` applies.
///
/// Dice / Jaccard are intentionally absent. Goldenmatch's slow-path
/// `_dice_score_matrix` and `_jaccard_score_matrix` are bloom-filter (PPRL
/// hex) scorers, not char-bigram. A native PPRL kernel is a separate
/// design (hex parse + popcount) and would not share this kernel's
/// dispatch.
#[pyfunction]
#[pyo3(signature = (values_a, values_b, scorer_id, symmetric=false))]
pub fn score_field_matrix(
    py: Python<'_>,
    values_a: PyArrowType<ArrayData>,
    values_b: PyArrowType<ArrayData>,
    scorer_id: u8,
    symmetric: bool,
) -> PyResult<Py<PyArray2<f32>>> {
    let a = arrow_to_strings(values_a.0)?;
    let b = arrow_to_strings(values_b.0)?;
    let n = a.len();
    let m = b.len();

    if !(0u8..=4u8).contains(&scorer_id) {
        return Err(PyValueError::new_err(format!(
            "score_field_matrix: unknown scorer_id={scorer_id} (valid: 0..=4)"
        )));
    }

    // Compute under allow_threads; build the flat (n*m) vec there.
    let buf: Vec<f32> = py.allow_threads(|| match scorer_id {
        // score_one returns [0,1] for ids 0-3 already (id=2 calls
        // fuzz::ratio which is [0,1] in rapidfuzz-rs, NOT the *100 scale
        // the PyO3-exposed token_sort_ratio uses).
        0..=3 => compute_pairwise(&a, &b, symmetric, |x, y| {
            score_one(scorer_id, x, y) as f32
        }),
        _ => {
            // 4 = soundex_match. Precompute soundex codes once per string
            // -- N*M comparisons would otherwise re-soundex every row pair.
            let codes_a: Vec<String> = a.par_iter().map(|s| soundex(s)).collect();
            let codes_b: Vec<String> = if symmetric {
                codes_a.clone()
            } else {
                b.par_iter().map(|s| soundex(s)).collect()
            };
            compute_pairwise_precomputed(&codes_a, &codes_b, symmetric, |x, y| {
                if !x.is_empty() && x == y {
                    1.0
                } else {
                    0.0
                }
            })
        }
    });

    // Reshape (n*m) -> (n, m) and hand off to numpy. numpy re-exports
    // ndarray; use its Array2::from_shape_vec to construct.
    let arr = numpy::ndarray::Array2::from_shape_vec((n, m), buf)
        .map_err(|e| PyValueError::new_err(format!("score_field_matrix: shape error: {e}")))?;
    Ok(arr.into_pyarray(py).unbind())
}

/// Pairwise scorer over raw string slices; `score(a_i, b_j)` per cell.
/// Result is row-major flat: out[i*m + j].
fn compute_pairwise<F>(a: &[String], b: &[String], symmetric: bool, score: F) -> Vec<f32>
where
    F: Fn(&str, &str) -> f32 + Send + Sync,
{
    let n = a.len();
    let m = b.len();
    let mut out = vec![0.0f32; n * m];
    // Row-parallel; each row is independent.
    out.par_chunks_mut(m).enumerate().for_each(|(i, row)| {
        if symmetric {
            // Fill upper triangle including diagonal.
            for j in i..m {
                row[j] = score(&a[i], &b[j]);
            }
        } else {
            for j in 0..m {
                row[j] = score(&a[i], &b[j]);
            }
        }
    });
    if symmetric {
        // Mirror upper triangle to lower.
        for i in 0..n {
            for j in 0..i {
                out[i * m + j] = out[j * m + i];
            }
        }
    }
    out
}

/// Pairwise scorer over precomputed per-string artifacts (soundex codes,
/// bigram sets, etc). Same flat layout as `compute_pairwise`.
fn compute_pairwise_precomputed<T, F>(a: &[T], b: &[T], symmetric: bool, score: F) -> Vec<f32>
where
    T: Send + Sync,
    F: Fn(&T, &T) -> f32 + Send + Sync,
{
    let n = a.len();
    let m = b.len();
    let mut out = vec![0.0f32; n * m];
    out.par_chunks_mut(m).enumerate().for_each(|(i, row)| {
        if symmetric {
            for j in i..m {
                row[j] = score(&a[i], &b[j]);
            }
        } else {
            for j in 0..m {
                row[j] = score(&a[i], &b[j]);
            }
        }
    });
    if symmetric {
        for i in 0..n {
            for j in 0..i {
                out[i * m + j] = out[j * m + i];
            }
        }
    }
    out
}
