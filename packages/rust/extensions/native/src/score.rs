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
use goldenmatch_fs_core::{
    given_name_aliased_sim, name_freq_weighted_sim, AliasTable, NameAliases, SurnameFreq,
    SurnameIdfTable, TfTable,
};
use goldenmatch_score_core::score_one;
use numpy::{IntoPyArray, PyArray1, PyArray2};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rayon::prelude::*;
use std::collections::HashSet;
use std::sync::{Arc, OnceLock, RwLock};

/// Process-level reference-data tables for the FS name scorers
/// (`name_freq_weighted_jw` / `given_name_aliased_jw`). Built ONCE per process
/// from the Python `refdata` state via [`set_name_reference_data`] and read by
/// both FS entry points — the design's "build the index once, inject a handle,
/// reuse across every block-scoring call" contract, so the ~10K census table is
/// NOT marshaled per bucket call. Mirrors the goldenembed model-dir cache.
pub struct NameRefData {
    pub surnames: SurnameIdfTable,
    pub aliases: AliasTable,
}

static NAME_REFDATA: OnceLock<RwLock<Option<Arc<NameRefData>>>> = OnceLock::new();

fn name_refdata_cell() -> &'static RwLock<Option<Arc<NameRefData>>> {
    NAME_REFDATA.get_or_init(|| RwLock::new(None))
}

/// Snapshot the registered name reference data (a cheap `Arc` clone), or `None`
/// if the host never registered it — in which case name-scorer fields degrade
/// to plain Jaro-Winkler.
fn current_name_refdata() -> Option<Arc<NameRefData>> {
    name_refdata_cell().read().ok().and_then(|g| g.clone())
}

/// Register the FS name-scorer reference tables for this process.
///
/// `surname_counts` = `(name, count)` census pairs — the idf is computed in Rust
/// via the exact `surnames.surname_idf` formula, single-sourcing the frequency
/// math. `alias_forms` = `(form, [canonical_ids])` from the given-name alias
/// classes (`given_names._state.canonicals`). The latest registration wins via
/// an atomic pointer swap; concurrent readers keep the prior snapshot.
#[pyfunction]
pub fn set_name_reference_data(
    surname_counts: Vec<(String, f64)>,
    alias_forms: Vec<(String, Vec<String>)>,
) -> PyResult<()> {
    let data = Arc::new(NameRefData {
        surnames: SurnameIdfTable::from_counts(surname_counts),
        aliases: AliasTable::from_forms(alias_forms),
    });
    if let Ok(mut g) = name_refdata_cell().write() {
        *g = Some(data);
    }
    Ok(())
}

/// Whether this process has FS name reference data registered (test/debug aid).
#[pyfunction]
pub fn has_name_reference_data() -> bool {
    current_name_refdata().is_some()
}

/// Build the per-field Winkler TF tables from the marshaled kwargs. `tf_freqs`
/// and `tf_collision` are per-field (indexed like `scorer_ids`); a field opts in
/// only when BOTH its `tf_freqs[f]` and `tf_collision[f]` are present. Absent /
/// `None` everywhere -> an empty Vec, so `score_fs_pair` does no TF work.
fn build_tf_tables(
    tf_freqs: Option<Vec<Option<std::collections::HashMap<String, f64>>>>,
    tf_collision: Option<Vec<Option<f64>>>,
    n_fields: usize,
) -> Vec<Option<TfTable>> {
    let (freqs, coll) = match (tf_freqs, tf_collision) {
        (Some(f), Some(c)) => (f, c),
        _ => return Vec::new(),
    };
    let mut out: Vec<Option<TfTable>> = Vec::with_capacity(n_fields);
    for f in 0..n_fields {
        let table = match (
            freqs.get(f).and_then(|o| o.as_ref()),
            coll.get(f).and_then(|o| *o),
        ) {
            (Some(fq), Some(c)) => Some(TfTable {
                freqs: fq.clone(),
                collision: c,
            }),
            _ => None,
        };
        out.push(table);
    }
    out
}

/// Validate + own the per-field embedding vectors for `FS_SCORER_EMBEDDING_COSINE`
/// (id 7) fields. `emb_vectors[f]` (when the host supplies it) is the ROW-MAJOR
/// `n_rows * emb_dims[f]` already-L2-normalized buffer for field `f`; `None` for
/// every non-embedding field. Returns owned buffers (kept alive by the caller,
/// which then hands `score_fs_pair` a borrowed `Vec<Option<&[f64]>>`) plus an
/// `emb_dims` padded to `n_fields`. Absent everywhere -> empty Vecs, so the kernel
/// does no embedding work. Every id-7 field MUST carry a correctly-sized vector
/// block (a caller bug otherwise, surfaced as a `ValueError`, never a hot-loop
/// panic).
// The `(Vec<Option<Vec<f64>>>, Vec<usize>)` return mirrors the pyo3 kwarg shapes
// (per-field optional flat vectors + per-field dims); a type alias would obscure
// more than it clarifies here.
#[allow(clippy::type_complexity)]
fn build_emb_vectors(
    emb_vectors: Option<Vec<Option<Vec<f64>>>>,
    emb_dims: Option<Vec<usize>>,
    scorer_ids: &[u8],
    n_rows: usize,
) -> PyResult<(Vec<Option<Vec<f64>>>, Vec<usize>)> {
    let n_fields = scorer_ids.len();
    let mut owned = emb_vectors.unwrap_or_default();
    let mut dims = emb_dims.unwrap_or_default();
    owned.resize_with(n_fields, || None);
    dims.resize(n_fields, 0);
    for f in 0..n_fields {
        if scorer_ids[f] == goldenmatch_fs_core::FS_SCORER_EMBEDDING_COSINE {
            let dim = dims[f];
            let ok = dim > 0 && owned[f].as_ref().is_some_and(|v| v.len() == n_rows * dim);
            if !ok {
                return Err(PyValueError::new_err(format!(
                    "score_block_pairs_fs: field {f} is embedding-cosine (id 7) but \
                     emb_vectors[{f}] is missing or not n_rows*dim ({n_rows}*{dim})"
                )));
            }
        }
    }
    Ok((owned, dims))
}

/// Resolve the injected provider handles for a scoring call. Borrows live as
/// long as the caller keeps the returned `Arc` alive.
#[inline]
fn name_providers(
    refdata: &Option<Arc<NameRefData>>,
) -> (
    Option<&(dyn SurnameFreq + Sync)>,
    Option<&(dyn NameAliases + Sync)>,
) {
    match refdata {
        Some(d) => (Some(&d.surnames), Some(&d.aliases)),
        None => (None, None),
    }
}

/// Weighted-BUCKET scorer ids for the two name scorers. `score_one` (score-core)
/// owns the bucket namespace 0..=14; these extend it for the reference-table-
/// backed name scorers, which `score_one` (stateless) cannot dispatch. DISTINCT
/// from the FS namespace (where `name_freq_weighted_jw`/`given_name_aliased_jw`
/// are ids 4/5) — that map never transfers here. Kept in lockstep with
/// `backends.score_buckets._NATIVE_SCORER_IDS` (and `core.fused_match`); a skew
/// is caught by the `native_symbols` gate + the wheel-drift advisory via the
/// capability flag `NATIVE_SUPPORTS_NAME_BUCKET_SCORERS`.
pub const NB_NAME_FREQ_WEIGHTED: u8 = 15;
pub const NB_GIVEN_NAME_ALIASED: u8 = 16;

/// Per-field similarity for the weighted bucket kernels. Intercepts the two
/// name-scorer bucket ids (15/16) and dispatches them through fs-core's
/// reference-table-backed sims using the process-global [`NameRefData`]; every
/// other id delegates to the stateless `score_one`. When the host never
/// registered name reference data (`name_data == None`) a name-scorer field
/// degrades to plain Jaro-Winkler (`score_one(0)`), matching the Python plugin's
/// `if not is_available(): return jw` — though the Python caller only routes
/// native once the tables are installed, so this is a defensive fallback.
#[inline]
fn score_bucket_field(
    scorer_id: u8,
    a: &str,
    b: &str,
    name_data: &Option<Arc<NameRefData>>,
) -> f64 {
    match scorer_id {
        NB_NAME_FREQ_WEIGHTED => match name_data {
            Some(d) => name_freq_weighted_sim(a, b, &d.surnames),
            None => score_one(0, a, b),
        },
        NB_GIVEN_NAME_ALIASED => match name_data {
            Some(d) => given_name_aliased_sim(a, b, &d.aliases),
            None => score_one(0, a, b),
        },
        id => score_one(id, a, b),
    }
}

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
/// Date-aware similarity (score-core id 4). Exposed as its own #[pyfunction] so
/// the Python caller can `hasattr(_native, "date_similarity")` to detect whether
/// the LOADED kernel understands the date scorer -- `score_one`/`score_block_*`
/// dispatch id 4 too, but a stale published wheel (pre-date) would silently
/// return 0.0 for id 4 (the catch-all), so the caller gates the native date
/// route on this symbol and falls back to the pure-Python mirror otherwise.
#[pyfunction]
pub fn date_similarity(a: &str, b: &str) -> f64 {
    goldenmatch_score_core::date_similarity(a, b)
}

/// Magnitude-aware date comparator (score-core id 17, FS domain comparators spec
/// 2026-07-23). Exposed as its own #[pyfunction] capability marker, exactly like
/// `date_similarity`: `score_one` / `score_block_pairs` dispatch id 17, but a
/// stale published wheel (pre-date_diff) would hit score_one's catch-all and
/// silently return 0.0 for id 17, so the Python caller gates the native route on
/// `hasattr(_native, "date_diff_similarity")` and falls back to the pure-Python
/// per-pair mirror (`_date_diff_similarity_py`) otherwise.
#[pyfunction]
pub fn date_diff_similarity(a: &str, b: &str) -> f64 {
    goldenmatch_score_core::date_diff_similarity(a, b)
}

/// Great-circle (haversine) distance comparator (score-core id 18, FS domain
/// comparators spec 2026-07-23). Own #[pyfunction] capability marker like
/// `date_diff_similarity`; a stale pre-geo wheel hits score_one's catch-all
/// (silent 0.0 for id 18), so the Python caller gates the native route on
/// `hasattr(_native, "geo_haversine_similarity")` and falls back to the
/// pure-Python per-pair mirror (`_geo_haversine_similarity_py`) otherwise.
#[pyfunction]
pub fn geo_haversine_similarity(a: &str, b: &str) -> f64 {
    goldenmatch_score_core::geo_haversine_similarity(a, b)
}

/// Character-trigram Jaccard (q-gram) similarity (score-core id 5). Exposed as
/// its own #[pyfunction] capability marker, exactly like `date_similarity`:
/// `score_one` / `score_block_pairs` dispatch id 5, but a stale published wheel
/// (pre-qgram) would hit score_one's catch-all and silently return 0.0 for id 5,
/// so the Python caller gates the native q-gram route on
/// `hasattr(_native, "qgram_similarity")` and falls back to the pure-Python
/// per-pair mirror (`_qgram_score_single`) otherwise.
#[pyfunction]
pub fn qgram_similarity(a: &str, b: &str) -> f64 {
    goldenmatch_score_core::qgram_similarity(a, b)
}

/// Soundex-match similarity (score-core id 6): binary 1.0/0.0 on soundex-code
/// equality. Exposed as its own #[pyfunction] capability marker, like
/// `date_similarity`/`qgram_similarity`: `score_one`/`score_block_pairs`
/// dispatch id 6, but a stale published wheel (pre-soundex-bucket) would hit
/// score_one's catch-all and silently return 0.0 for id 6, so the Python caller
/// gates the native soundex route on `hasattr(_native, "soundex_similarity")`
/// and falls back to the pure-Python per-pair jellyfish mirror otherwise.
#[pyfunction]
pub fn soundex_similarity(a: &str, b: &str) -> f64 {
    goldenmatch_score_core::score_one(6, a, b)
}

/// Install the host-shipped legal-form variant set (`entity_form_variants()`,
/// ~77 lowercase-normalized entries) into score-core's process-global table,
/// which `initialism_match` (score-core id 7) reads. `OnceLock` first-wins:
/// returns `True` only on the first call; later calls no-op (`False`). The
/// Python caller MUST call this once before routing `initialism_match` through
/// the native kernel, else id 7 scores against an empty legal-form set.
#[pyfunction]
pub fn set_legal_form_variants(forms: Vec<String>) -> bool {
    goldenmatch_score_core::set_legal_forms(forms.into_iter().collect())
}

/// Initialism-match similarity (score-core id 7): 1.0 iff either name is the
/// other's derived initialism (or the two initialisms are equal), against the
/// globally-installed legal-form set. Own #[pyfunction] capability marker like
/// `qgram_similarity`/`soundex_similarity`: a stale published wheel lacking this
/// symbol would hit score_one's catch-all (silent 0.0) for id 7, so the Python
/// caller gates the native route on `hasattr(_native, "initialism_similarity")`
/// AND a successful `set_legal_form_variants`, falling back to the pure path
/// (`_initialism_match_single`) otherwise.
#[pyfunction]
pub fn initialism_similarity(a: &str, b: &str) -> f64 {
    goldenmatch_score_core::score_one(7, a, b)
}

/// Install the business alias table for `alias_match` (score-core id 8): the
/// normalized legal-form variant list (rebuilt into the strip-legal-form regex)
/// + the surface->canonical alias map. `OnceLock` first-wins (returns `True`
/// only on the first call). Must be called before routing alias_match native.
#[pyfunction]
pub fn set_business_aliases(
    variants: Vec<String>,
    surface_to_canonical: Vec<(String, String)>,
) -> bool {
    goldenmatch_score_core::set_business_aliases(variants, surface_to_canonical)
}

/// Install the given-name canonical map for `alias_match`: `normalized ->
/// min(canonical set)` (lex-first resolution done host-side). `OnceLock`
/// first-wins. Must be called before routing alias_match native.
#[pyfunction]
pub fn set_given_name_canonicals(pairs: Vec<(String, String)>) -> bool {
    goldenmatch_score_core::set_given_name_canonicals(pairs)
}

/// Alias-match similarity (score-core id 8): 1.0 iff both values share a
/// non-empty business OR given-name canonical, against the globally-installed
/// tables. Own #[pyfunction] capability marker like the other bucket kernels: a
/// stale published wheel lacking this symbol would hit score_one's catch-all
/// (silent 0.0) for id 8, so the Python caller gates the native route on
/// `hasattr(_native, "alias_match_similarity")` AND a successful table install,
/// falling back to the pure `_alias_match_single` mirror otherwise.
#[pyfunction]
pub fn alias_match_similarity(a: &str, b: &str) -> f64 {
    goldenmatch_score_core::score_one(8, a, b)
}

/// Dice / Jaccard / phash bloom-hex similarities (score-core ids 9/10/11): integer
/// popcount over hex-decoded bloom filters / pHashes, byte-exact with the Python
/// `_dice_score_single` / `_jaccard_score_single` / `_phash_score_single`. Each is
/// its own #[pyfunction] capability marker like the other bucket kernels: a stale
/// wheel lacking the symbol hits score_one's catch-all (silent 0.0) for that id,
/// so the Python caller gates the native route on `hasattr(_native, "<name>")` and
/// falls back to the pure per-pair mirror otherwise.
#[pyfunction]
pub fn dice_similarity(a: &str, b: &str) -> f64 {
    goldenmatch_score_core::dice_similarity(a, b)
}

#[pyfunction]
pub fn jaccard_similarity(a: &str, b: &str) -> f64 {
    goldenmatch_score_core::jaccard_similarity(a, b)
}

#[pyfunction]
pub fn phash_similarity(a: &str, b: &str) -> f64 {
    goldenmatch_score_core::phash_similarity(a, b)
}

/// ensemble: max(jaro_winkler, unscaled token_sort, 0.8*soundex_match). Composes
/// score_one 0/2/6; byte-for-byte with the bucket per-pair mirror
/// `_ensemble_score_single` to the same tolerance jw/token_sort hold vs rapidfuzz.
#[pyfunction]
pub fn ensemble_similarity(a: &str, b: &str) -> f64 {
    goldenmatch_score_core::ensemble_similarity(a, b)
}

/// radial / audio_fp perceptual profile similarities (score-core ids 13/14):
/// hex-parse + alignment search, byte-exact with the Python `_radial_score_single`
/// / `_audio_fp_score_single`. Each is its own #[pyfunction] capability marker
/// like the other bucket kernels: a stale wheel lacking the symbol hits
/// score_one's catch-all (silent 0.0) for that id, so the Python caller gates the
/// native route on `hasattr(_native, "<name>")` and falls back to the pure per-pair
/// mirror otherwise.
#[pyfunction]
pub fn radial_similarity(a: &str, b: &str) -> f64 {
    goldenmatch_score_core::radial_similarity(a, b)
}

#[pyfunction]
pub fn audio_fp_similarity(a: &str, b: &str) -> f64 {
    goldenmatch_score_core::audio_fp_similarity(a, b)
}

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
#[allow(clippy::too_many_arguments, clippy::needless_range_loop)]
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
    // #weighted-null: `total_weight` is now unused -- scores renormalize by the
    // OBSERVED weight (weight_sum) instead. The parameter is KEPT so the
    // #[pyfunction] signature is unchanged: the Python caller passes it
    // positionally, and a published goldenmatch-native wheel would skew against a
    // changed signature (the #688 wheel/caller class). Drop it only in a change
    // that republishes the wheel and updates the caller together.
    let _ = total_weight;
    let exclude: HashSet<(i64, i64)> = exclude.into_iter().collect();
    let n_fields = scorer_ids.len();

    // Snapshot the process-global name reference data ONCE (a cheap Arc clone)
    // so the name-scorer bucket ids (15/16) can reach the census/alias tables
    // inside the parallel loop without re-locking per pair. `Arc<NameRefData>`
    // is Sync, so the rayon closure captures `&name_data` safely.
    let name_data = current_name_refdata();

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
                                    score_sum +=
                                        score_bucket_field(scorer_ids[f], a, b, &name_data)
                                            * weights[f];
                                    weight_sum += weights[f];
                                }
                            }
                            if weight_sum > 0.0 {
                                // #weighted-null: a null field is ABSENCE of evidence, not disagreement: renormalize by the OBSERVED weight (weight_sum), matching core/scorer.py::score_pair. Dividing by total_weight made any null field score as a disagreement, so an absolute threshold became unreachable (0.3/0.4/0.3 fields @0.85: a null dob caps the pair at 0.70 -- unmatchable however perfectly the names agree).
                                let combined = score_sum / weight_sum;
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

// Fellegi-Sunter leaf math (weight normalization + level banding) now lives in
// the pyo3-free `goldenmatch-fs-core` crate — the single source of truth shared
// with the WASM/TS surface (see the 2026-07-17 fs-core design). Re-exported here
// so `score_block_pairs_fs`, the fused `match_fused_fs`, and this module's tests
// keep their existing `fs_normalize` / `fs_level_from_sim` call sites unchanged.
pub(crate) use goldenmatch_fs_core::{fs_level_from_sim, fs_normalize};

/// Fellegi-Sunter sibling of [`score_block_pairs`]: instead of a weighted average
/// of raw similarities, each field's similarity is mapped to a comparison LEVEL
/// and the per-level match weight (log2(m/u), EM-trained) is summed, then turned
/// into a 0-1 score the same two ways `score_probabilistic_vectorized` does:
///
///   calibrated (posterior): 1 / (1 + 2^-(prior_w + W)), W clamped to [-60, 60]
///   linear:                 clamp((W - min_weight) / weight_range, 0, 1)
///
/// `field_values[f][r]` is the already-transform-applied value of field `f` for
/// row `r`, block-sorted. A null on EITHER side contributes no evidence,
/// matching `comparison_vector`'s unobserved sentinel. `match_weights[f]` has
/// one weight per level.
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
#[allow(clippy::too_many_arguments, clippy::needless_range_loop)]
#[pyfunction]
#[pyo3(signature = (
    row_ids, block_sizes, field_values, scorer_ids, levels, partial_thresholds,
    match_weights, calibrated, prior_w, min_weight, weight_range, threshold,
    exclude, level_thresholds=None,
    ne_values=None, ne_scorer_ids=None, ne_thresholds=None, ne_weights=None,
    exclude_set=None,
    tf_freqs=None, tf_collision=None,
    emb_vectors=None, emb_dims=None,
    require_positive_evidence=false,
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
    exclude_set: Option<PyRef<'_, ExcludeSet>>,
    tf_freqs: Option<Vec<Option<std::collections::HashMap<String, f64>>>>,
    tf_collision: Option<Vec<Option<f64>>>,
    emb_vectors: Option<Vec<Option<Vec<f64>>>>,
    emb_dims: Option<Vec<usize>>,
    require_positive_evidence: bool,
) -> PyResult<Vec<(i64, i64, f64)>> {
    // FS_SUPPORTS_EXCLUDE_SET: prefer the shared Arc handle (built once per
    // score_buckets call via `build_exclude_set`), fall back to the legacy
    // Vec-rebuilt-per-call path. Same resolution as score_block_pairs_arrow --
    // the per-call HashSet rebuild was the #552/#688 pathology, previously
    // fixed for weighted only.
    let local_set: HashSet<(i64, i64)>;
    let exclude: &HashSet<(i64, i64)> = match &exclude_set {
        Some(handle) => &handle.set,
        None => {
            local_set = exclude.into_iter().collect();
            &local_set
        }
    };
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
        for (k, &sid) in ns.iter().enumerate() {
            if sid > 3 && sid != goldenmatch_fs_core::FS_SCORER_ENSEMBLE {
                return Err(PyValueError::new_err(format!(
                    "score_block_pairs_fs: ne_scorer_ids[{k}]={sid} out of range (valid: 0..=3 or 6=ensemble)"
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
    let field_mins: Vec<f64> = match_weights
        .iter()
        .map(|w| w.iter().copied().fold(f64::INFINITY, f64::min))
        .collect();
    let field_maxs: Vec<f64> = match_weights
        .iter()
        .map(|w| w.iter().copied().fold(f64::NEG_INFINITY, f64::max))
        .collect();
    let regular_min: f64 = field_mins.iter().sum();
    let regular_max: f64 = field_maxs.iter().sum();
    let base_min = min_weight - regular_min;
    let base_max = min_weight + weight_range - regular_max;

    let mut spans: Vec<(usize, usize)> = Vec::with_capacity(block_sizes.len());
    let mut offset = 0usize;
    for &size in &block_sizes {
        spans.push((offset, size));
        offset += size;
    }

    // Process-level name reference data (census idf + given-name aliases),
    // registered once via `set_name_reference_data`. Snapshot the Arc so the
    // borrows below outlive the rayon closure; None -> name-scorer fields degrade
    // to plain JW.
    let tf_tables = build_tf_tables(tf_freqs, tf_collision, scorer_ids.len());
    let name_refdata = current_name_refdata();
    let (surname_freq, name_aliases) = name_providers(&name_refdata);

    // Embedding vectors for FS_SCORER_EMBEDDING_COSINE fields: row-major
    // n_rows*dim, already L2-normalized host-side (the model stays host-side).
    // `emb_owned` keeps the buffers alive; `emb_vectors` borrows into it.
    let (emb_owned, emb_dims) = build_emb_vectors(emb_vectors, emb_dims, &scorer_ids, n_rows)?;
    let emb_vectors: Vec<Option<&[f64]>> = emb_owned.iter().map(|o| o.as_deref()).collect();

    // Per-matchkey scoring constants, borrowed once and reused per pair. The
    // per-pair FS math itself lives in `goldenmatch-fs-core::score_fs_pair` (the
    // single cross-surface source of truth); this entry point owns only the
    // Vec-of-`String` field access, span iteration, and GIL release.
    let fs_params = goldenmatch_fs_core::FsPairParams {
        scorer_ids: &scorer_ids,
        levels: &levels,
        partial_thresholds: &partial_thresholds,
        field_thresholds: &field_thresholds,
        match_weights: &match_weights,
        field_mins: &field_mins,
        field_maxs: &field_maxs,
        base_min,
        base_max,
        ne_scorer_ids: ne_scorer_ids_v,
        ne_thresholds: ne_thresholds_v,
        ne_weights: ne_weights_v,
        calibrated,
        prior_w,
        surname_freq,
        name_aliases,
        tf_tables: &tf_tables,
        emb_vectors: &emb_vectors,
        emb_dims: &emb_dims,
        require_positive_evidence,
    };

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
                            let normalized = goldenmatch_fs_core::score_fs_pair(
                                i,
                                j,
                                &fs_params,
                                |f, row| field_values[f][row].as_deref(),
                                |k, row| ne_vals[k][row].as_deref(),
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
#[allow(clippy::too_many_arguments, clippy::needless_range_loop)]
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
    // #weighted-null: unused -- scores renormalize by weight_sum. Param KEPT so
    // the #[pyfunction] signature (and any published wheel) is unchanged.
    let _ = total_weight;
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

    // Snapshot the process-global name reference data ONCE (cheap Arc clone) so
    // the name-scorer bucket ids (15/16) reach the census/alias tables inside the
    // shared span closure. `Arc<NameRefData>` is Sync -> safe for the rayon path.
    let name_data = current_name_refdata();

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
                            score_sum +=
                                score_bucket_field(scorer_ids[f], a, b, &name_data) * weights[f];
                            weight_sum += weights[f];
                        }
                    }
                    if weight_sum > 0.0 {
                        // #weighted-null: renormalize by OBSERVED weight (see above).
                        let combined = score_sum / weight_sum;
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

/// Arrow-native sibling of [`score_block_pairs_fs`]: identical Fellegi-Sunter
/// scoring (levels / custom `level_thresholds` banding / negative evidence /
/// `fs_normalize`), but `row_ids` (Int64) and the field/NE columns
/// (Utf8/LargeUtf8) are read zero-copy from Arrow buffers via the C Data
/// Interface — skipping the per-element `.to_list()` materialization + PyO3
/// `Vec<Vec<Option<String>>>` clone the Vec entry pays (the same win
/// `score_block_pairs_arrow` gave the weighted path, ~58% of native wall at
/// 1M rows). Also takes the shared [`ExcludeSet`] handle and the issue #688
/// sequential-vs-rayon dispatch gate, both of which the Vec entry historically
/// lacked. Byte-identical output to the Vec entry for the same inputs (parity
/// asserted in tests/test_native_fs_ne.py).
#[allow(clippy::too_many_arguments, clippy::needless_range_loop)]
#[pyfunction]
#[pyo3(signature = (
    row_ids, field_arrays, block_sizes, scorer_ids, levels, partial_thresholds,
    match_weights, calibrated, prior_w, min_weight, weight_range, threshold,
    exclude=None, exclude_set=None, level_thresholds=None,
    ne_arrays=None, ne_scorer_ids=None, ne_thresholds=None, ne_weights=None,
    tf_freqs=None, tf_collision=None,
    emb_vectors=None, emb_dims=None,
    require_positive_evidence=false,
))]
pub fn score_block_pairs_fs_arrow(
    py: Python<'_>,
    row_ids: PyArrowType<ArrayData>,
    field_arrays: Vec<PyArrowType<ArrayData>>,
    block_sizes: Vec<usize>,
    scorer_ids: Vec<u8>,
    levels: Vec<u8>,
    partial_thresholds: Vec<f64>,
    match_weights: Vec<Vec<f64>>,
    calibrated: bool,
    prior_w: f64,
    min_weight: f64,
    weight_range: f64,
    threshold: f64,
    exclude: Option<Vec<(i64, i64)>>,
    exclude_set: Option<PyRef<'_, ExcludeSet>>,
    level_thresholds: Option<Vec<Option<Vec<f64>>>>,
    ne_arrays: Option<Vec<PyArrowType<ArrayData>>>,
    ne_scorer_ids: Option<Vec<u8>>,
    ne_thresholds: Option<Vec<f64>>,
    ne_weights: Option<Vec<f64>>,
    tf_freqs: Option<Vec<Option<std::collections::HashMap<String, f64>>>>,
    tf_collision: Option<Vec<Option<f64>>>,
    emb_vectors: Option<Vec<Option<Vec<f64>>>>,
    emb_dims: Option<Vec<usize>>,
    require_positive_evidence: bool,
) -> PyResult<Vec<(i64, i64, f64)>> {
    let row_data = row_ids.0;
    if row_data.data_type() != &DataType::Int64 {
        return Err(PyValueError::new_err(format!(
            "score_block_pairs_fs_arrow: row_ids must be int64, got {:?}",
            row_data.data_type()
        )));
    }
    let row_ids = Int64Array::from(row_data);
    let fields: Vec<StrCol> = field_arrays
        .into_iter()
        .map(|p| StrCol::from_data(p.0))
        .collect::<PyResult<_>>()?;
    let n_rows = row_ids.len();
    let n_fields = scorer_ids.len();
    for (f, col) in fields.iter().enumerate() {
        if col.len() != n_rows {
            return Err(PyValueError::new_err(format!(
                "score_block_pairs_fs_arrow: field {f} length {} != row count {n_rows}",
                col.len()
            )));
        }
    }

    // Custom-banding validation, verbatim from score_block_pairs_fs.
    if let Some(lt) = &level_thresholds {
        if lt.len() != n_fields {
            return Err(PyValueError::new_err(format!(
                "score_block_pairs_fs_arrow: level_thresholds length {} != field count {n_fields}",
                lt.len()
            )));
        }
        for (f, ts) in lt.iter().enumerate() {
            if let Some(ts) = ts {
                if match_weights[f].len() != ts.len() + 1 {
                    return Err(PyValueError::new_err(format!(
                        "score_block_pairs_fs_arrow: field {f} has {} match_weights but \
                         {} level_thresholds (need thresholds + 1 weights)",
                        match_weights[f].len(),
                        ts.len()
                    )));
                }
            }
        }
    }
    let field_thresholds: Vec<Option<&[f64]>> = match &level_thresholds {
        Some(lt) => lt.iter().map(|ts| ts.as_deref()).collect(),
        None => vec![None; n_fields],
    };

    // Negative-evidence kwargs: all four present or all four absent
    // (score_block_pairs_fs contract, arrow column type).
    let n_present = [
        ne_arrays.is_some(),
        ne_scorer_ids.is_some(),
        ne_thresholds.is_some(),
        ne_weights.is_some(),
    ]
    .iter()
    .filter(|&&p| p)
    .count();
    if n_present != 0 && n_present != 4 {
        return Err(PyValueError::new_err(
            "score_block_pairs_fs_arrow: ne_arrays, ne_scorer_ids, ne_thresholds \
             and ne_weights must be passed together (all four or none)",
        ));
    }
    let ne_cols: Vec<StrCol> = match ne_arrays {
        Some(arrays) => arrays
            .into_iter()
            .map(|p| StrCol::from_data(p.0))
            .collect::<PyResult<_>>()?,
        None => Vec::new(),
    };
    let ne_scorer_ids_v: &[u8] = ne_scorer_ids.as_deref().unwrap_or(&[]);
    let ne_thresholds_v: &[f64] = ne_thresholds.as_deref().unwrap_or(&[]);
    let ne_weights_v: &[f64] = ne_weights.as_deref().unwrap_or(&[]);
    let n_ne = ne_cols.len();
    if ne_scorer_ids_v.len() != n_ne || ne_thresholds_v.len() != n_ne || ne_weights_v.len() != n_ne
    {
        return Err(PyValueError::new_err(format!(
            "score_block_pairs_fs_arrow: ne_* lengths differ (ne_arrays {}, \
             ne_scorer_ids {}, ne_thresholds {}, ne_weights {})",
            n_ne,
            ne_scorer_ids_v.len(),
            ne_thresholds_v.len(),
            ne_weights_v.len()
        )));
    }
    for (k, col) in ne_cols.iter().enumerate() {
        if col.len() != n_rows {
            return Err(PyValueError::new_err(format!(
                "score_block_pairs_fs_arrow: ne_arrays[{k}] length {} != row count {n_rows}",
                col.len()
            )));
        }
    }
    for (k, &sid) in ne_scorer_ids_v.iter().enumerate() {
        if sid > 3 && sid != goldenmatch_fs_core::FS_SCORER_ENSEMBLE {
            return Err(PyValueError::new_err(format!(
                "score_block_pairs_fs_arrow: ne_scorer_ids[{k}]={sid} out of range (valid: 0..=3 or 6=ensemble)"
            )));
        }
    }

    // Exclude resolution: shared Arc handle preferred, legacy Vec fallback
    // (same as score_block_pairs_arrow).
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

    let mut spans: Vec<(usize, usize)> = Vec::with_capacity(block_sizes.len());
    let mut offset = 0usize;
    for &size in &block_sizes {
        spans.push((offset, size));
        offset += size;
    }

    let field_mins: Vec<f64> = match_weights
        .iter()
        .map(|w| w.iter().copied().fold(f64::INFINITY, f64::min))
        .collect();
    let field_maxs: Vec<f64> = match_weights
        .iter()
        .map(|w| w.iter().copied().fold(f64::NEG_INFINITY, f64::max))
        .collect();
    let regular_min: f64 = field_mins.iter().sum();
    let regular_max: f64 = field_maxs.iter().sum();
    let base_min = min_weight - regular_min;
    let base_max = min_weight + weight_range - regular_max;

    // Process-level name reference data (see the Vec entry). Snapshot the Arc so
    // the borrows outlive the rayon closure below.
    let tf_tables = build_tf_tables(tf_freqs, tf_collision, scorer_ids.len());
    let name_refdata = current_name_refdata();
    let (surname_freq, name_aliases) = name_providers(&name_refdata);

    // Embedding vectors (id 7) — same host-marshaled buffers as the Vec entry.
    let (emb_owned, emb_dims) = build_emb_vectors(emb_vectors, emb_dims, &scorer_ids, n_rows)?;
    let emb_vectors: Vec<Option<&[f64]>> = emb_owned.iter().map(|o| o.as_deref()).collect();

    // Per-matchkey scoring constants, borrowed once and reused per pair. The
    // per-pair FS math is `goldenmatch-fs-core::score_fs_pair` — identical to the
    // Vec entry point; this arrow entry differs only in the zero-copy field
    // access, span iteration, and #688 rayon dispatch below.
    let fs_params = goldenmatch_fs_core::FsPairParams {
        scorer_ids: &scorer_ids,
        levels: &levels,
        partial_thresholds: &partial_thresholds,
        field_thresholds: &field_thresholds,
        match_weights: &match_weights,
        field_mins: &field_mins,
        field_maxs: &field_maxs,
        base_min,
        base_max,
        ne_scorer_ids: ne_scorer_ids_v,
        ne_thresholds: ne_thresholds_v,
        ne_weights: ne_weights_v,
        calibrated,
        prior_w,
        surname_freq,
        name_aliases,
        tf_tables: &tf_tables,
        emb_vectors: &emb_vectors,
        emb_dims: &emb_dims,
        require_positive_evidence,
    };

    // Per-block FS scorer shared by the sequential and rayon paths (mirrors
    // score_block_pairs_arrow's score_span).
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
                    let normalized = goldenmatch_fs_core::score_fs_pair(
                        i,
                        j,
                        &fs_params,
                        |f, row| fields[f].get(row),
                        |k, row| ne_cols[k].get(row),
                    );
                    if normalized >= threshold {
                        local.push((pair_key.0, pair_key.1, normalized));
                    }
                }
            }
        }
        local
    };

    // Issue #688 dispatch gate (see score_block_pairs_arrow): score in the
    // calling thread unless a single call carries enough intra-call work that
    // rayon's dispatch beats the LockLatch-park risk. Both paths walk spans in
    // order, so the emitted (min,max) sequence is byte-identical either way.
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

// `soundex` moved to `goldenmatch-score-core` (the single reference for the
// soundex surfaces): the field-matrix path below and the bucket `score_one`
// path (id 6) now share `goldenmatch_score_core::soundex`. See score-core's
// `soundex` + its `soundex_matches_jellyfish_reference` test.
use goldenmatch_score_core::soundex;

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

    // soundex tests moved to `goldenmatch-score-core` (the impl now lives there;
    // native re-uses it via `use goldenmatch_score_core::soundex`).

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
