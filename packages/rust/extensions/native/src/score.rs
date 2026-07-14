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
use goldenmatch_score_core::score_one;
use numpy::{IntoPyArray, PyArray1, PyArray2};
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
/// Blocks are scored in parallel with `rayon` under `detach` (the GIL is
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

    py.detach(|| {
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

/// Normalize a summed Fellegi-Sunter match weight to a [0,1] score. Shared by
/// `score_block_pairs_fs` and the fused `match_fused_fs` so the two cannot drift.
/// `calibrated` = posterior probability `1/(1+2^-(prior_w+w))`; else linear
/// min-max over the observed weight range (0.5 when the range is degenerate).
pub(crate) fn fs_normalize(
    total_weight: f64,
    calibrated: bool,
    prior_w: f64,
    min_weight: f64,
    weight_range: f64,
) -> f64 {
    if calibrated {
        let logodds = (prior_w + total_weight).clamp(-60.0, 60.0);
        1.0 / (1.0 + 2.0_f64.powf(-logodds))
    } else if weight_range > 0.0 {
        ((total_weight - min_weight) / weight_range).clamp(0.0, 1.0)
    } else {
        0.5
    }
}

/// Map a per-field similarity to a comparison level, matching
/// `core/probabilistic._levels_from_similarity`:
///   custom `level_thresholds`: level = count of thresholds t with sim >= t
///     (inclusive, order-independent — mirrors the Python custom branch
///     byte-for-byte; len(thresholds)+1 levels total)
///   2 levels: 1 if sim >= partial_threshold else 0
///   3 levels: 2 if sim >= 0.95, elif sim >= partial_threshold -> 1, else 0
///   N levels: count of k in 1..N with sim >= k/N (even spacing)
#[inline]
pub(crate) fn fs_level_from_sim(
    sim: f64,
    n_levels: u8,
    partial_threshold: f64,
    level_thresholds: Option<&[f64]>,
) -> usize {
    if let Some(ts) = level_thresholds {
        return ts.iter().filter(|&&t| sim >= t).count();
    }
    match n_levels {
        2 => usize::from(sim >= partial_threshold),
        3 => {
            if sim >= 0.95 {
                2
            } else if sim >= partial_threshold {
                1
            } else {
                0
            }
        }
        n => {
            let n = n as usize;
            let mut lvl = 0usize;
            for k in 1..n {
                if sim >= (k as f64) / (n as f64) {
                    lvl += 1;
                }
            }
            lvl
        }
    }
}

/// Fellegi-Sunter sibling of [`score_block_pairs`]: instead of a weighted average
/// of raw similarities, each field's similarity is mapped to a comparison LEVEL
/// and the per-level match weight (log2(m/u), EM-trained) is summed, then turned
/// into a 0-1 score the same two ways `score_probabilistic_vectorized` does:
///
///   calibrated (posterior): 1 / (1 + 2^-(prior_w + W)), W clamped to [-60, 60]
///   linear:                 clamp((W - min_weight) / weight_range, 0, 1)
///
/// `field_values[f][r]` is the already-transform-applied value of field `f` for
/// row `r`, block-sorted. A null on EITHER side maps to level 0 (disagree),
/// matching `comparison_vector`. `match_weights[f]` has one weight per level.
/// Scorers are score_one ids 0..=3 (jaro_winkler/levenshtein/token_sort/exact);
/// soundex/embedding fields aren't native-eligible (caller falls back to numpy).
///
/// `level_thresholds` (optional, one entry per field) carries a field's custom
/// similarity->level banding (PR #1749's `level_thresholds`): when
/// `Some(ts)` for field `f`, its level is the count of thresholds `t` with
/// `sim >= t` (inclusive), and `match_weights[f]` must have `ts.len() + 1`
/// entries. `None` (whole kwarg or per field) keeps the legacy 2/3/N-even
/// banding. Old wheels never see this kwarg (Python gates on the
/// `FS_SUPPORTS_LEVEL_THRESHOLDS` capability flag).
///
/// The `ne_*` kwargs (optional, all-or-none) carry Fellegi-Sunter negative
/// evidence: `ne_values[k][r]` is NE field `k`'s POST-transform value for row
/// `r`, `ne_scorer_ids`/`ne_thresholds`/`ne_weights` its scorer, firing
/// threshold, and resolved fired-weight (normally negative). Firing follows
/// `_ne_fired` (core/probabilistic.py:466) byte-for-byte: fires iff BOTH
/// values are present AND non-empty (empty string = inconclusive — the
/// deliberate NE null-handling that differs from regular fields'
/// null -> level-0) AND similarity is STRICTLY below the threshold; a fired
/// field adds `ne_weights[k]` to the pair's summed weight, otherwise it
/// contributes exactly 0. `fs_normalize` is unchanged — the caller passes
/// NE-aware `min_weight`/`weight_range`. Old wheels never see these kwargs
/// (Python gates on the `FS_SUPPORTS_NE` capability flag).
///
/// Parity with the numpy path is within rapidfuzz tolerance (same as the
/// weighted kernel) — a pair could differ only if its normalized score sits
/// within that tolerance of `threshold`. Asserted in tests/test_native_parity.py.
#[allow(clippy::too_many_arguments)]
#[pyfunction]
#[pyo3(signature = (
    row_ids, block_sizes, field_values, scorer_ids, levels, partial_thresholds,
    match_weights, calibrated, prior_w, min_weight, weight_range, threshold,
    exclude, level_thresholds=None,
    ne_values=None, ne_scorer_ids=None, ne_thresholds=None, ne_weights=None,
))]
pub fn score_block_pairs_fs(
    py: Python<'_>,
    row_ids: Vec<i64>,
    block_sizes: Vec<usize>,
    field_values: Vec<Vec<Option<String>>>,
    scorer_ids: Vec<u8>,
    levels: Vec<u8>,
    partial_thresholds: Vec<f64>,
    match_weights: Vec<Vec<f64>>,
    calibrated: bool,
    prior_w: f64,
    min_weight: f64,
    weight_range: f64,
    threshold: f64,
    exclude: Vec<(i64, i64)>,
    level_thresholds: Option<Vec<Option<Vec<f64>>>>,
    ne_values: Option<Vec<Vec<Option<String>>>>,
    ne_scorer_ids: Option<Vec<u8>>,
    ne_thresholds: Option<Vec<f64>>,
    ne_weights: Option<Vec<f64>>,
) -> PyResult<Vec<(i64, i64, f64)>> {
    let exclude: HashSet<(i64, i64)> = exclude.into_iter().collect();
    let n_fields = scorer_ids.len();

    if let Some(lt) = &level_thresholds {
        if lt.len() != n_fields {
            return Err(PyValueError::new_err(format!(
                "score_block_pairs_fs: level_thresholds length {} != field count {n_fields}",
                lt.len()
            )));
        }
        for (f, ts) in lt.iter().enumerate() {
            if let Some(ts) = ts {
                if match_weights[f].len() != ts.len() + 1 {
                    return Err(PyValueError::new_err(format!(
                        "score_block_pairs_fs: field {f} has {} match_weights but \
                         {} level_thresholds (need thresholds + 1 weights)",
                        match_weights[f].len(),
                        ts.len()
                    )));
                }
            }
        }
    }
    // Per-field threshold slices hoisted out of the per-pair-per-field hot loop
    // (no Option chasing / re-borrowing inside the rayon closure).
    let field_thresholds: Vec<Option<&[f64]>> = match &level_thresholds {
        Some(lt) => lt.iter().map(|ts| ts.as_deref()).collect(),
        None => vec![None; n_fields],
    };

    // Negative-evidence kwargs: all four present or all four absent.
    let n_present = [
        ne_values.is_some(),
        ne_scorer_ids.is_some(),
        ne_thresholds.is_some(),
        ne_weights.is_some(),
    ]
    .iter()
    .filter(|&&p| p)
    .count();
    if n_present != 0 && n_present != 4 {
        return Err(PyValueError::new_err(
            "score_block_pairs_fs: ne_values, ne_scorer_ids, ne_thresholds and \
             ne_weights must be passed together (all four or none)",
        ));
    }
    let n_rows = row_ids.len();
    if let (Some(nv), Some(ns), Some(nt), Some(nw)) =
        (&ne_values, &ne_scorer_ids, &ne_thresholds, &ne_weights)
    {
        let n_ne = nv.len();
        if ns.len() != n_ne || nt.len() != n_ne || nw.len() != n_ne {
            return Err(PyValueError::new_err(format!(
                "score_block_pairs_fs: ne_* lengths differ (ne_values {}, \
                 ne_scorer_ids {}, ne_thresholds {}, ne_weights {})",
                n_ne,
                ns.len(),
                nt.len(),
                nw.len()
            )));
        }
        for (k, vals) in nv.iter().enumerate() {
            if vals.len() != n_rows {
                return Err(PyValueError::new_err(format!(
                    "score_block_pairs_fs: ne_values[{k}] length {} != row count {n_rows}",
                    vals.len()
                )));
            }
        }
    }
    // NE slices hoisted out of the rayon hot loop (same rationale as
    // `field_thresholds` above); empty slices when NE is absent.
    let ne_vals: &[Vec<Option<String>>] = ne_values.as_deref().unwrap_or(&[]);
    let ne_scorer_ids_v: &[u8] = ne_scorer_ids.as_deref().unwrap_or(&[]);
    let ne_thresholds_v: &[f64] = ne_thresholds.as_deref().unwrap_or(&[]);
    let ne_weights_v: &[f64] = ne_weights.as_deref().unwrap_or(&[]);
    let n_ne = ne_vals.len();

    let mut spans: Vec<(usize, usize)> = Vec::with_capacity(block_sizes.len());
    let mut offset = 0usize;
    for &size in &block_sizes {
        spans.push((offset, size));
        offset += size;
    }

    let result = py.detach(|| {
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
                            let mut total_weight = 0.0_f64;
                            for f in 0..n_fields {
                                let level = match (&field_values[f][i], &field_values[f][j]) {
                                    (Some(a), Some(b)) => {
                                        let sim = score_one(scorer_ids[f], a, b);
                                        fs_level_from_sim(
                                            sim,
                                            levels[f],
                                            partial_thresholds[f],
                                            field_thresholds[f],
                                        )
                                    }
                                    // Null on either side -> disagree (level 0).
                                    _ => 0,
                                };
                                total_weight += match_weights[f][level];
                            }
                            // Negative evidence: exact `_ne_fired` semantics
                            // (core/probabilistic.py:466) — fires iff both
                            // values present AND non-empty AND similarity
                            // STRICTLY below the threshold; contributes
                            // exactly 0 otherwise.
                            for k in 0..n_ne {
                                if let (Some(a), Some(b)) = (&ne_vals[k][i], &ne_vals[k][j]) {
                                    if !a.is_empty()
                                        && !b.is_empty()
                                        && score_one(ne_scorer_ids_v[k], a, b) < ne_thresholds_v[k]
                                    {
                                        total_weight += ne_weights_v[k];
                                    }
                                }
                            }
                            let normalized = fs_normalize(
                                total_weight,
                                calibrated,
                                prior_w,
                                min_weight,
                                weight_range,
                            );
                            if normalized >= threshold {
                                local.push((pair_key.0, pair_key.1, normalized));
                            }
                        }
                    }
                }
                local
            })
            .collect()
    });
    Ok(result)
}

/// A block-sorted Utf8 column read zero-copy from an Arrow buffer. Polars emits
/// `LargeUtf8` (i64 offsets) by default; plain pyarrow string arrays are `Utf8`
/// (i32). Both are owned (Arc-backed -> `Send + Sync`) so the rayon closure can
/// borrow them across `detach`.
pub(crate) enum StrCol {
    Utf8(StringArray),
    Large(LargeStringArray),
}

impl StrCol {
    pub(crate) fn from_data(data: ArrayData) -> PyResult<Self> {
        match data.data_type() {
            DataType::Utf8 => Ok(StrCol::Utf8(StringArray::from(data))),
            DataType::LargeUtf8 => Ok(StrCol::Large(LargeStringArray::from(data))),
            other => Err(PyValueError::new_err(format!(
                "field column must be utf8/large_utf8, got {other:?}"
            ))),
        }
    }

    #[inline]
    pub(crate) fn get(&self, i: usize) -> Option<&str> {
        match self {
            StrCol::Utf8(a) => (!a.is_null(i)).then(|| a.value(i)),
            StrCol::Large(a) => (!a.is_null(i)).then(|| a.value(i)),
        }
    }

    #[inline]
    pub(crate) fn len(&self) -> usize {
        match self {
            StrCol::Utf8(a) => a.len(),
            StrCol::Large(a) => a.len(),
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

    Ok(py.detach(|| {
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

    // Compute under detach; build the flat (n*m) vec there.
    let buf: Vec<f32> = py.detach(|| match scorer_id {
        // score_one returns [0,1] for ids 0-3 already (id=2 calls
        // fuzz::ratio which is [0,1] in rapidfuzz-rs, NOT the *100 scale
        // the PyO3-exposed token_sort_ratio uses).
        0..=3 => compute_pairwise(&a, &b, symmetric, |x, y| score_one(scorer_id, x, y) as f32),
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

/// Elementwise pairwise scorer: `out[i] = score(a[i], b[i])` for two equal-length
/// Arrow string arrays. The Sail-tier (Spark Connect) vectorized Arrow UDF target --
/// one FFI crossing per batch, no per-element Python loop, no N*N matrix. Returns a
/// 1-D float32 numpy array in [0, 1]. Nulls are treated as "" (arrow_to_strings
/// maps null -> empty), matching the pure-Python rapidfuzz floor. scorer_id mirrors
/// score_field_matrix ids 0..=3 (jaro_winkler / levenshtein / token_sort / exact);
/// soundex (4) is excluded -- pairwise has no precompute amortization.
#[pyfunction]
#[pyo3(signature = (values_a, values_b, scorer_id))]
pub fn score_field_pairwise(
    py: Python<'_>,
    values_a: PyArrowType<ArrayData>,
    values_b: PyArrowType<ArrayData>,
    scorer_id: u8,
) -> PyResult<Py<PyArray1<f32>>> {
    let a = arrow_to_strings(values_a.0)?;
    let b = arrow_to_strings(values_b.0)?;
    if a.len() != b.len() {
        return Err(PyValueError::new_err(format!(
            "score_field_pairwise: length mismatch a={} b={}",
            a.len(),
            b.len()
        )));
    }
    if !(0u8..=3u8).contains(&scorer_id) {
        return Err(PyValueError::new_err(format!(
            "score_field_pairwise: unknown scorer_id={scorer_id} (valid: 0..=3; \
             4=soundex is matrix-only)"
        )));
    }
    let n = a.len();
    // Score under detach, row-parallel (each pair independent).
    let buf: Vec<f32> = py.detach(|| {
        let mut out = vec![0.0f32; n];
        out.par_iter_mut().enumerate().for_each(|(i, slot)| {
            *slot = score_one(scorer_id, &a[i], &b[i]) as f32;
        });
        out
    });
    Ok(buf.into_pyarray(py).unbind())
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn soundex_characterizes_implementation_output() {
        assert_eq!(soundex("Robert"), "R163");
        assert_eq!(soundex("Ashcraft"), "A261");
        assert_eq!(soundex("Tymczak"), "T522");
    }

    #[test]
    fn soundex_pads_short_codes_to_four() {
        assert_eq!(soundex("Lee").len(), 4);
        assert_eq!(soundex("A"), "A000");
    }

    #[test]
    fn soundex_non_alpha_is_empty() {
        assert_eq!(soundex("123"), "");
        assert_eq!(soundex(""), "");
    }

    #[test]
    fn soundex_code_table() {
        assert_eq!(soundex_code('B'), b'1'); // B F P V
        assert_eq!(soundex_code('C'), b'2'); // C G J K Q S X Z
        assert_eq!(soundex_code('D'), b'3'); // D T
        assert_eq!(soundex_code('L'), b'4'); // L
        assert_eq!(soundex_code('M'), b'5'); // M N
        assert_eq!(soundex_code('R'), b'6'); // R
        assert_eq!(soundex_code('A'), b'0'); // vowels / other
    }

    #[test]
    fn fs_level_from_sim_custom_thresholds_count_inclusive() {
        // level = count of thresholds t with sim >= t (inclusive), matching
        // Python _levels_from_similarity's custom branch byte-for-byte.
        let ts = [1.0, 0.92, 0.88];
        let sims = [1.0, 0.95, 0.90, 0.5, 0.88];
        let expected = [3usize, 2, 1, 0, 1];
        for (sim, want) in sims.iter().zip(expected.iter()) {
            assert_eq!(fs_level_from_sim(*sim, 4, 0.8, Some(&ts)), *want);
        }
    }

    #[test]
    fn fs_level_from_sim_none_keeps_legacy_banding() {
        // 2-level: 1 if sim >= partial_threshold else 0.
        assert_eq!(fs_level_from_sim(0.9, 2, 0.8, None), 1);
        assert_eq!(fs_level_from_sim(0.7, 2, 0.8, None), 0);
        // 3-level: 0.95 exact band, partial band, else 0.
        assert_eq!(fs_level_from_sim(1.0, 3, 0.8, None), 2);
        assert_eq!(fs_level_from_sim(0.9, 3, 0.8, None), 1);
        assert_eq!(fs_level_from_sim(0.5, 3, 0.8, None), 0);
        // N-even (5 levels): count of k in 1..5 with sim >= k/5.
        assert_eq!(fs_level_from_sim(0.85, 5, 0.8, None), 4);
        assert_eq!(fs_level_from_sim(0.4, 5, 0.8, None), 2);
    }

    #[test]
    fn compute_pairwise_symmetric_mirrors_upper_triangle() {
        let a = vec!["x".to_string(), "y".to_string()];
        let out = compute_pairwise(&a, &a, true, |p, q| if p == q { 1.0 } else { 0.3 });
        assert_eq!(out, vec![1.0, 0.3, 0.3, 1.0]);
    }
}
