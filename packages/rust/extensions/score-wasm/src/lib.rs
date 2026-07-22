//! wasm-bindgen wrapper over `goldenmatch-score-core`. The TS analogue of the
//! `native` pyo3 crate: thin shims delegating to `score-core` so the scorers
//! are byte-identical across Python, the FFI UDFs, and TS WASM.
//!
//! Covered scorer ids (must match the TS backend): 0=jaro_winkler,
//! 1=levenshtein, 2=token_sort, 3=exact, 4=date. id=2 routes through score-core's
//! `token_sort_normalized_ratio` (the TS-parity lowercase+strip normalize), NOT
//! the un-normalized `score_one(2)` (which the FFI/native path depends on); every
//! other id (incl. 4=date, the #1858 date-aware scorer) delegates to `score_one`.
//! ids 20/21 are the score-wasm-only name scorers over `fs-core` (`given_name_-
//! aliased_jw` / `name_freq_weighted_jw`), scoring against reference-data tables
//! the TS loader injects once at `enableWasm()`.
//!
//! Boundary design: the batch `score_matrix` entry crosses the JS<->WASM boundary
//! ONCE per NxN block (values arrive as one separator-joined string), never per
//! pair -- per the perf-audit lesson that boundary cost dwarfs a single scorer.

use goldenmatch_fs_core::{
    given_name_aliased_sim, name_freq_weighted_sim, AliasTable, SurnameIdfTable,
};
use goldenmatch_score_core::{score_one, token_sort_normalized_ratio};
use std::sync::OnceLock;

// Injected reference-data tables for the two name scorers (ids 20/21). The TS
// loader passes the SAME census / alias data the pure path uses into the setters
// once at `enableWasm()`; fs-core bundles no data, so nothing is embedded in the
// wasm bundle. `OnceLock` first-wins; the data is deterministic.
static SURNAME_IDF: OnceLock<SurnameIdfTable> = OnceLock::new();
static NAME_ALIASES: OnceLock<AliasTable> = OnceLock::new();

// Name-scorer ids, distinct from `score_one`'s 0..=8 (>= 20 leaves headroom for
// score_one growth) so `score_matrix_impl` can branch on them.
const ID_GIVEN_NAME_ALIASED_JW: u8 = 20;
const ID_NAME_FREQ_WEIGHTED_JW: u8 = 21;

/// Install the surname-IDF table from host-shipped census `(name, count)` pairs
/// (`SurnameIdfTable::from_counts` computes the idf with the shared
/// `surnames.surname_idf` formula). Shared by the wasm setter + native tests.
pub fn install_surname_idf(names: Vec<String>, counts: Vec<f64>) {
    let _ = SURNAME_IDF.set(SurnameIdfTable::from_counts(names.into_iter().zip(counts)));
}

/// Install the given-name alias table from parallel `(form, canonical)` EDGE
/// arrays (`forms[i]` is a member of canonical `canonicals[i]`) -- grouped by
/// form into `AliasTable::from_forms`. Two flat arrays keep the wasm-bindgen
/// boundary simple (no nested Vec).
pub fn install_name_aliases(forms: Vec<String>, canonicals: Vec<String>) {
    let mut grouped: std::collections::HashMap<String, Vec<String>> =
        std::collections::HashMap::new();
    for (form, canon) in forms.into_iter().zip(canonicals) {
        grouped.entry(form).or_default().push(canon);
    }
    let _ = NAME_ALIASES.set(AliasTable::from_forms(grouped));
}

/// Full row-major NxN similarity matrix for `values` under `scorer_id`.
/// Diagonal = 0.0 and the matrix is symmetric, matching the pure-TS
/// `scoreMatrix` (which fills the upper triangle, mirrors it, and leaves the
/// diagonal 0). NULL handling is done JS-side (this sees only strings).
pub fn score_matrix_impl(values: &[&str], scorer_id: u8) -> Vec<f64> {
    let n = values.len();
    let mut out = vec![0.0_f64; n * n];
    for i in 0..n {
        for j in (i + 1)..n {
            // id=2 (token_sort) uses the TS-parity normalized path (lowercase +
            // strip + token-sort), NOT score_one(2)'s un-normalized fuzz::ratio.
            // ids 20/21 = the fs-core name scorers over the installed tables; if a
            // table isn't installed the sim degrades to plain JW -- the same
            // table-absent fallback the pure TS path takes.
            let s = match scorer_id {
                2 => token_sort_normalized_ratio(values[i], values[j]),
                // id=12 (ensemble) mirrors id=2's override: score_one(12)'s
                // `ensemble_similarity` maxes over the UN-normalized score_one(2),
                // but the pure-TS `ensembleScore` uses the normalized `tokenSortRatio`
                // -- so the WASM arm recomposes ensemble with the TS-parity normalized
                // token_sort (jw + normalized token_sort + 0.8*soundex) to stay 4dp
                // parity with the pure-TS fallback (soundex byte-exact; jw/token_sort
                // to 4dp, the same bar those scorers hold individually vs rapidfuzz).
                12 => {
                    let jw = score_one(0, values[i], values[j]);
                    let ts = token_sort_normalized_ratio(values[i], values[j]);
                    let sx = score_one(6, values[i], values[j]);
                    jw.max(ts).max(0.8 * sx)
                }
                ID_GIVEN_NAME_ALIASED_JW => match NAME_ALIASES.get() {
                    Some(t) => given_name_aliased_sim(values[i], values[j], t),
                    None => score_one(0, values[i], values[j]),
                },
                ID_NAME_FREQ_WEIGHTED_JW => match SURNAME_IDF.get() {
                    Some(t) => name_freq_weighted_sim(values[i], values[j], t),
                    None => score_one(0, values[i], values[j]),
                },
                _ => score_one(scorer_id, values[i], values[j]),
            };
            out[i * n + j] = s;
            out[j * n + i] = s;
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matrix_is_symmetric_zero_diagonal() {
        // jaro_winkler id=0. "abc"/"abc" on the diagonal stays 0 (diagonal is
        // never scored); off-diagonal is the real score and mirrored.
        let vals = ["abc", "abd", "xyz"];
        let m = score_matrix_impl(&vals, 0);
        assert_eq!(m.len(), 9);
        assert_eq!(m[0], 0.0); // diagonal
        assert_eq!(m[1], m[3]); // symmetric (0,1)==(1,0)
        assert!(m[1] > 0.0 && m[1] < 1.0); // abc~abd is a partial match
    }

    #[test]
    fn exact_id3_is_one_or_zero() {
        let vals = ["a", "a", "b"];
        let m = score_matrix_impl(&vals, 3);
        assert_eq!(m[1], 1.0); // (0,1) a==a
        assert_eq!(m[2], 0.0); // (0,2) a!=b
    }

    #[test]
    fn date_id4_separates_typo_from_unrelated() {
        // #1858: id=4 delegates to score_one -> date_similarity. Unrelated ISO
        // dates -> 0.0 (jaro_winkler would give 0.80+); a 1-digit typo stays high.
        let vals = ["1980-01-01", "1980-01-02", "1975-11-30"];
        let m = score_matrix_impl(&vals, 4);
        assert!((m[1] - 0.90).abs() < 1e-9); // (0,1) typo
        assert_eq!(m[2], 0.0); // (0,2) unrelated
    }

    #[test]
    fn name_scorer_ids_20_21_dispatch_to_fs_core() {
        // The ONLY test that installs the process-global name-scorer tables
        // (OnceLock first-wins), so it never races a different-content setter.
        install_name_aliases(
            vec![
                "william".into(), "bill".into(), "robert".into(), "bob".into(),
            ],
            vec![
                "william".into(), "william".into(), "robert".into(), "robert".into(),
            ],
        );
        // Tiny census table: smith common, smyth rare, jones mid.
        install_surname_idf(
            vec!["smith".into(), "smyth".into(), "jones".into()],
            vec![2_000_000.0, 100.0, 500_000.0],
        );

        // id 20 = given_name_aliased_jw: William/Bill share a canonical -> 1.0.
        let m = score_matrix_impl(&["William", "Bill"], ID_GIVEN_NAME_ALIASED_JW);
        assert_eq!(m[1], 1.0);
        // Non-alias falls back to plain JW.
        let m2 = score_matrix_impl(&["William", "Walter"], ID_GIVEN_NAME_ALIASED_JW);
        assert_eq!(m2[1], score_one(0, "William", "Walter"));

        // id 21 = name_freq_weighted_jw: dispatches to the fs-core sim over the
        // installed table (byte-identical to calling the sim directly).
        let table = SURNAME_IDF.get().unwrap();
        let m3 = score_matrix_impl(&["smith", "smyth"], ID_NAME_FREQ_WEIGHTED_JW);
        assert_eq!(m3[1], name_freq_weighted_sim("smith", "smyth", table));
    }

    #[test]
    fn token_sort_id2_normalizes_and_is_order_invariant() {
        // id=2 must use the TS-parity normalized path: order-invariant + case/
        // punctuation-insensitive. "John SMITH" vs "smith john" -> 1.0.
        let vals = ["John SMITH", "smith john"];
        let m = score_matrix_impl(&vals, 2);
        assert!((m[1] - 1.0).abs() < 1e-9);
        // The UN-normalized score_one(2) would NOT be 1.0 here (case +
        // token-order differ before normalization).
        let raw = goldenmatch_score_core::score_one(2, "John SMITH", "smith john");
        assert!(raw < 1.0);
    }

    #[test]
    fn ensemble_id12_uses_normalized_token_sort() {
        use goldenmatch_score_core::{score_one, token_sort_normalized_ratio};
        // id=12 must recompose ensemble with the NORMALIZED token_sort (not the
        // un-normalized one score_one(12)/ensemble_similarity uses), so it stays
        // parity with the pure-TS `ensembleScore`.
        for (a, b) in [
            ("John SMITH", "smith john"), // token-sort dominates -> 1.0
            ("Robert", "Rupert"),         // soundex R163 -> 0.8 bonus
            ("MARTHA", "MARHTA"),         // jw dominates
            ("", ""),                     // empty -> soundex guard 0.0, jw/ts 1.0
        ] {
            let m = score_matrix_impl(&[a, b], 12);
            let want = score_one(0, a, b)
                .max(token_sort_normalized_ratio(a, b))
                .max(0.8 * score_one(6, a, b));
            assert!((m[1] - want).abs() < 1e-12, "ensemble {a:?}/{b:?}: {} vs {want}", m[1]);
        }
        // The normalized token_sort makes "John SMITH"/"smith john" -> 1.0, where
        // score_one(12) (un-normalized token_sort) would score lower.
        let m = score_matrix_impl(&["John SMITH", "smith john"], 12);
        assert!((m[1] - 1.0).abs() < 1e-9);
        assert!(score_one(12, "John SMITH", "smith john") < 1.0);
    }
}

#[cfg(target_arch = "wasm32")]
mod wasm {
    use super::{install_name_aliases, install_surname_idf, score_matrix_impl};
    use wasm_bindgen::prelude::*;

    /// JS entry: `values` is one string with fields joined by `sep` (a 1-char
    /// separator the caller guarantees is absent from the data, e.g. U+001E).
    /// Returns the flat row-major NxN matrix as a Float64Array.
    #[wasm_bindgen]
    pub fn score_matrix(values: &str, sep: &str, scorer_id: u8) -> Vec<f64> {
        let parts: Vec<&str> = if values.is_empty() {
            Vec::new()
        } else {
            values.split(sep).collect()
        };
        score_matrix_impl(&parts, scorer_id)
    }

    /// Install the surname-IDF table for `name_freq_weighted_jw` (id 21). Called
    /// once by the TS loader at `enableWasm()` with the census `(name, count)`
    /// data the pure path uses (`censusSurnames.ts`).
    #[wasm_bindgen]
    pub fn set_surname_idf(names: Vec<String>, counts: Vec<f64>) {
        install_surname_idf(names, counts);
    }

    /// Install the given-name alias table for `given_name_aliased_jw` (id 20) from
    /// parallel `(form, canonical)` edge arrays.
    #[wasm_bindgen]
    pub fn set_name_aliases(forms: Vec<String>, canonicals: Vec<String>) {
        install_name_aliases(forms, canonicals);
    }
}
