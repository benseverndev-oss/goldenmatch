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
mod hash;
mod pairs;
mod perceptual;
mod score;
mod sketch;
mod suggest;

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
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
    m.add_function(wrap_pyfunction!(featurize::char_ngram_features, m)?)?;
    m.add_function(wrap_pyfunction!(featurize::char_ngram_project, m)?)?;
    m.add_function(wrap_pyfunction!(score::jaro_winkler_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(score::levenshtein_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(score::token_sort_ratio, m)?)?;
    m.add_function(wrap_pyfunction!(score::score_block_pairs, m)?)?;
    m.add_function(wrap_pyfunction!(score::score_block_pairs_fs, m)?)?;
    m.add_function(wrap_pyfunction!(score::score_block_pairs_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(score::score_field_matrix, m)?)?;
    m.add_function(wrap_pyfunction!(score::score_field_pairwise, m)?)?;
    m.add_function(wrap_pyfunction!(score::build_exclude_set, m)?)?;
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
    m.add_function(wrap_pyfunction!(perceptual::perceptual_fingerprint_audio, m)?)?;
    m.add_function(wrap_pyfunction!(perceptual::perceptual_radial_variance, m)?)?;
    m.add_function(wrap_pyfunction!(autoconfig::autoconfig_decide_plan, m)?)?;
    m.add_function(wrap_pyfunction!(autoconfig::autoconfig_classify_columns, m)?)?;
    m.add_function(wrap_pyfunction!(autoconfig::autoconfig_extrapolate_pair_count, m)?)?;
    m.add_function(wrap_pyfunction!(autoconfig::autoconfig_sparse_match_floor, m)?)?;
    m.add_function(wrap_pyfunction!(autoconfig::autoconfig_exact_matchkey_floor, m)?)?;
    m.add_function(wrap_pyfunction!(suggest::suggest_config, m)?)?;
    m.add_function(wrap_pyfunction!(documents::documents_schema_validate, m)?)?;
    m.add_function(wrap_pyfunction!(documents::documents_parse_message_text, m)?)?;
    m.add_function(wrap_pyfunction!(documents::documents_extract_instruction, m)?)?;
    m.add_function(wrap_pyfunction!(documents::documents_suggest_prompt, m)?)?;
    m.add_function(wrap_pyfunction!(documents::documents_normalize_record, m)?)?;
    m.add_function(wrap_pyfunction!(documents::documents_template, m)?)?;
    m.add_function(wrap_pyfunction!(documents::documents_template_list, m)?)?;
    m.add_function(wrap_pyfunction!(documents::documents_classify_prompt, m)?)?;
    m.add_function(wrap_pyfunction!(documents::documents_parse_classify, m)?)?;
    Ok(())
}
