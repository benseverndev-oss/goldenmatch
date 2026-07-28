//! `goldenphonetic._goldenphonetic` — PyO3 extension module for the goldenphonetic
//! phonetic encoders. A thin wrapper over the pyo3-free `goldenphonetic-core`
//! crate: byte-identical to the Python `jellyfish` package on soundex / metaphone
//! / nysiis / match_rating. All of pyo3 is confined to this wheel; the core stays
//! pyo3-free.
//!
//! The error semantics mirror `jellyfish` exactly (its own pyo3 binding,
//! `rustyfish.rs`): `match_rating_codex` RAISES `ValueError` on non-alphabetic
//! input, while `match_rating_comparison` never raises — it returns `None` when the
//! two codices can't be compared (a length difference of 3 or more, or either side
//! rejected by the codex).

use goldenphonetic_core as gp;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// American Soundex (== `jellyfish.soundex`).
#[pyfunction]
fn soundex(s: &str) -> String {
    gp::soundex(s)
}

/// Original Metaphone, Philips 1990 (== `jellyfish.metaphone`).
#[pyfunction]
fn metaphone(s: &str) -> String {
    gp::metaphone(s)
}

/// NYSIIS phonetic encoding (== `jellyfish.nysiis`).
#[pyfunction]
fn nysiis(s: &str) -> String {
    gp::nysiis(s)
}

/// Match Rating Approach codex (== `jellyfish.match_rating_codex`). Raises
/// `ValueError` when the input contains a non-alphabetic, non-space character.
#[pyfunction]
fn match_rating_codex(s: &str) -> PyResult<String> {
    gp::match_rating_codex(s).map_err(PyValueError::new_err)
}

/// Match Rating Approach comparison (== `jellyfish.match_rating_comparison`).
/// Returns `None` (never raises) when the two codices can't be compared — a
/// codex-length difference of 3 or more, or either input rejected as non-alpha.
#[pyfunction]
fn match_rating_comparison(s1: &str, s2: &str) -> Option<bool> {
    gp::match_rating_comparison(s1, s2)
}

#[pymodule]
fn _goldenphonetic(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(soundex, m)?)?;
    m.add_function(wrap_pyfunction!(metaphone, m)?)?;
    m.add_function(wrap_pyfunction!(nysiis, m)?)?;
    m.add_function(wrap_pyfunction!(match_rating_codex, m)?)?;
    m.add_function(wrap_pyfunction!(match_rating_comparison, m)?)?;
    Ok(())
}
