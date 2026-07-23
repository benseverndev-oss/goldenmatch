//! `goldenmatch._native` — native acceleration kernels (PyO3 extension module).
//!
//! Phase 1 (this module): clustering kernels mirroring `core/cluster.py`. Each
//! function is a behavior-exact replacement for a pure-Python hot loop; the
//! Python side selects it only when `GOLDENMATCH_NATIVE` opts in (default stays
//! Python until the parity + DQbench gates pass). Spec:
//! `packages/python/goldenmatch/docs/design/2026-05-25-rust-acceleration-spec.md`.
use pyo3::prelude::*;

mod autoconfig;
mod block;
mod bloom;
mod cluster;
mod documents;
mod featurize;
mod fused;
mod golden;
mod hash;
mod pairs;
mod perceptual;
mod score;
mod sketch;
mod suggest;

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    // Wheel-skew capability flag: Python's `_fs_native_eligible` gates
    // level_thresholds matchkeys on this (old wheels never see the kwarg).
    m.add("FS_SUPPORTS_LEVEL_THRESHOLDS", true)?;
    // Wheel-skew capability flag: Python's `_fs_native_eligible` (block scorer)
    // AND `match_fused_fs_ready` (core/fused_match.py) both gate negative-evidence
    // matchkeys on this (old wheels never see the ne_* kwargs). One const is
    // accurate: both kernels' NE landed in the same 0.1.15 wheel.
    m.add("FS_SUPPORTS_NE", true)?;
    // Regular-field nulls are unobserved evidence (schema v2), not level 0.
    // Python declines older wheels so their legacy scorer cannot silently run.
    m.add("FS_SUPPORTS_MISSING_NEUTRAL", true)?;
    // Wheel-skew capability flag: Python's `match_fused_fs_ready` gates custom
    // level_thresholds on this (old wheels never see the kwarg).
    m.add("FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS", true)?;
    // Wheel-skew capability flag: `score_block_pairs_fs` accepts the shared
    // `exclude_set=` Arc handle (the #552/#688 fix, FS side). Old wheels keep
    // the legacy Vec-per-call path (#1803).
    m.add("FS_SUPPORTS_EXCLUDE_SET", true)?;
    // Wheel-skew capability flag: the zero-copy `score_block_pairs_fs_arrow`
    // entry exists. Python's `_score_fs_native_frame` routes to it when set;
    // old wheels keep the Vec entry (#1803).
    m.add("FS_SUPPORTS_ARROW", true)?;
    // Wheel-skew capability flag: both FS entries dispatch the reference-data
    // name scorers (`name_freq_weighted_jw` id 4 / `given_name_aliased_jw` id 5)
    // to the process-registered census / alias tables. Python's
    // `_fs_native_eligible` admits those scorers only when this flag is present
    // AND `set_name_reference_data` has been called; old wheels lack the flag
    // and keep the numpy path for name-scorer matchkeys.
    m.add("FS_SUPPORTS_NAME_SCORERS", true)?;
    // Wheel-skew capability flag: both FS entries accept the per-field Winkler
    // `tf_freqs`/`tf_collision` kwargs and apply the term-frequency adjustment on
    // exact-equal top-level agreements. Python's `_fs_native_eligible` admits a
    // tf_adjustment field only when this flag is present; old wheels keep numpy.
    m.add("FS_SUPPORTS_TF_ADJUSTMENT", true)?;
    // Wheel-skew capability flag: both FS entries dispatch the `ensemble` scorer
    // (id 6 = max(jaro_winkler, token_sort, soundex*0.8)) as a regular AND a
    // negative-evidence scorer. Python's `_fs_native_eligible` admits ensemble
    // only when this flag is present; old wheels score id 6 as 0.0 (score_one's
    // catch-all), so they must keep the numpy path for ensemble matchkeys.
    m.add("FS_SUPPORTS_ENSEMBLE", true)?;
    // Wheel-skew capability flag: both FS entries accept the per-field
    // `emb_vectors`/`emb_dims` kwargs and score an `embedding` / `record_embedding`
    // field (id 7) as the cosine (dot) of the two rows' host-precomputed
    // L2-normalized vectors. Python's `_fs_native_eligible` admits an embedding
    // field only when this flag is present; old wheels keep the numpy path.
    m.add("FS_SUPPORTS_EMBEDDING", true)?;
    // Wheel-skew capability flag: both FS entries accept the `require_positive_evidence`
    // kwarg and drop net-zero-evidence pairs (linear mode, W <= 0). Python passes the
    // kwarg only when this flag is present, so an OLDER wheel degrades gracefully to the
    // legacy emit-at-neutral native behavior (the numpy fallback still filters).
    m.add("FS_SUPPORTS_REQUIRE_POSITIVE_EVIDENCE", true)?;
    // Wheel-skew capability flag: the WEIGHTED bucket entries (`score_block_pairs`
    // / `score_block_pairs_arrow`) dispatch the reference-data name scorers
    // (`name_freq_weighted_jw` bucket id 15 / `given_name_aliased_jw` id 16)
    // through the process-registered census / alias tables — DISTINCT from the FS
    // path's ids 4/5 (FS_SUPPORTS_NAME_SCORERS). Python's `_NATIVE_SCORER_IDS`
    // gate admits these two bucket scorers only when this flag is present AND
    // `set_name_reference_data` has been called; an old wheel lacks the flag and
    // keeps the pure-Python plugin path (score_one's catch-all would score 15/16
    // as 0.0 otherwise).
    m.add("NATIVE_SUPPORTS_NAME_BUCKET_SCORERS", true)?;
    m.add_function(wrap_pyfunction!(cluster::connected_components, m)?)?;
    m.add_function(wrap_pyfunction!(cluster::mst_split_components, m)?)?;
    m.add_function(wrap_pyfunction!(cluster::severe_bridge_count, m)?)?;
    m.add_function(wrap_pyfunction!(cluster::cluster_confidence, m)?)?;
    m.add_function(wrap_pyfunction!(cluster::build_clusters_native, m)?)?;
    m.add_function(wrap_pyfunction!(cluster::build_clusters_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(cluster::connected_components_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(pairs::canonicalize_pairs, m)?)?;
    m.add_function(wrap_pyfunction!(pairs::dedup_pairs_max_score, m)?)?;
    m.add_function(wrap_pyfunction!(pairs::dedup_pairs_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(pairs::candidate_pair_count, m)?)?;
    m.add_function(wrap_pyfunction!(pairs::block_histogram, m)?)?;
    m.add_function(wrap_pyfunction!(block::build_block_index_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(fused::match_fused, m)?)?;
    m.add_function(wrap_pyfunction!(fused::match_fused_fs, m)?)?;
    m.add_function(wrap_pyfunction!(golden::golden_fused, m)?)?;
    m.add_function(wrap_pyfunction!(featurize::char_ngram_features, m)?)?;
    m.add_function(wrap_pyfunction!(featurize::char_ngram_project, m)?)?;
    m.add_function(wrap_pyfunction!(score::jaro_winkler_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(score::levenshtein_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(score::token_sort_ratio, m)?)?;
    m.add_function(wrap_pyfunction!(score::date_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(score::date_diff_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(score::geo_haversine_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(score::qgram_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(score::soundex_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(score::set_legal_form_variants, m)?)?;
    m.add_function(wrap_pyfunction!(score::initialism_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(score::set_business_aliases, m)?)?;
    m.add_function(wrap_pyfunction!(score::set_given_name_canonicals, m)?)?;
    m.add_function(wrap_pyfunction!(score::alias_match_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(score::dice_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(score::jaccard_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(score::phash_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(score::ensemble_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(score::radial_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(score::audio_fp_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(score::score_block_pairs, m)?)?;
    m.add_function(wrap_pyfunction!(score::score_block_pairs_fs, m)?)?;
    m.add_function(wrap_pyfunction!(score::score_block_pairs_fs_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(score::score_block_pairs_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(score::score_field_matrix, m)?)?;
    m.add_function(wrap_pyfunction!(score::score_field_pairwise, m)?)?;
    m.add_function(wrap_pyfunction!(score::build_exclude_set, m)?)?;
    m.add_function(wrap_pyfunction!(score::set_name_reference_data, m)?)?;
    m.add_function(wrap_pyfunction!(score::has_name_reference_data, m)?)?;
    m.add_class::<score::ExcludeSet>()?;
    m.add_function(wrap_pyfunction!(hash::record_fingerprint, m)?)?;
    m.add_function(wrap_pyfunction!(hash::record_fingerprints_batch, m)?)?;
    m.add_function(wrap_pyfunction!(hash::record_fingerprints_batch_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(bloom::bloom_clk_batch, m)?)?;
    m.add_function(wrap_pyfunction!(sketch::sketch_band_hashes_batch, m)?)?;
    m.add_function(wrap_pyfunction!(sketch::sketch_signature_batch, m)?)?;
    m.add_function(wrap_pyfunction!(
        sketch::sketch_simhash_band_hashes_batch,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(perceptual::perceptual_phash_image, m)?)?;
    m.add_function(wrap_pyfunction!(perceptual::perceptual_phash_batch, m)?)?;
    m.add_function(wrap_pyfunction!(
        perceptual::perceptual_fingerprint_audio,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(perceptual::perceptual_radial_variance, m)?)?;
    m.add_function(wrap_pyfunction!(autoconfig::autoconfig_decide_plan, m)?)?;
    m.add_function(wrap_pyfunction!(
        autoconfig::autoconfig_classify_columns,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        autoconfig::autoconfig_extrapolate_pair_count,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        autoconfig::autoconfig_sparse_match_floor,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        autoconfig::autoconfig_exact_matchkey_floor,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        autoconfig::autoconfig_assemble_strong_id_union,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        autoconfig::autoconfig_finalize_strong_id_union,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(suggest::suggest_config, m)?)?;
    m.add_function(wrap_pyfunction!(documents::documents_schema_validate, m)?)?;
    m.add_function(wrap_pyfunction!(
        documents::documents_parse_message_text,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        documents::documents_extract_instruction,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(documents::documents_suggest_prompt, m)?)?;
    m.add_function(wrap_pyfunction!(documents::documents_normalize_record, m)?)?;
    m.add_function(wrap_pyfunction!(documents::documents_template, m)?)?;
    m.add_function(wrap_pyfunction!(documents::documents_template_list, m)?)?;
    m.add_function(wrap_pyfunction!(documents::documents_classify_prompt, m)?)?;
    m.add_function(wrap_pyfunction!(documents::documents_parse_classify, m)?)?;
    m.add_function(wrap_pyfunction!(documents::documents_parse_structured, m)?)?;
    Ok(())
}
