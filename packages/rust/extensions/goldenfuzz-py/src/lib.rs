//! `goldenfuzz._goldenfuzz` — PyO3 extension module for the goldenfuzz fuzzy-string
//! scorers. A thin wrapper over the pyo3-free `goldenfuzz-core` crate: byte-identical
//! to rapidfuzz on jaro-winkler / levenshtein / indel, with a one-vs-many
//! `extract` / `cdist` / `BatchComparator` that builds the query bitmap once.
//! All of pyo3 is confined to this wheel; the core stays pyo3-free.

use goldenfuzz_core as gf;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// Map a scorer name to the core enum. Accepts the canonical names plus a couple
/// of short aliases so `extract(..., scorer="jw")` reads naturally.
fn parse_scorer(s: &str) -> PyResult<gf::Scorer> {
    match s {
        "jaro_winkler" | "jaro" | "jw" => Ok(gf::Scorer::JaroWinkler),
        "levenshtein" | "lev" => Ok(gf::Scorer::Levenshtein),
        "indel" | "ratio" => Ok(gf::Scorer::Indel),
        other => Err(PyValueError::new_err(format!(
            "unknown scorer {other:?}; expected one of jaro_winkler | levenshtein | indel"
        ))),
    }
}

/// Jaro-Winkler normalized similarity in `[0, 1]` (== rapidfuzz).
#[pyfunction]
fn jaro_winkler(a: &str, b: &str) -> f64 {
    gf::jaro_winkler(a, b)
}

/// Levenshtein normalized similarity in `[0, 1]` (== rapidfuzz).
#[pyfunction]
fn levenshtein(a: &str, b: &str) -> f64 {
    gf::levenshtein_normalized_similarity(a, b)
}

/// Indel normalized similarity in `[0, 1]` (== rapidfuzz `fuzz.ratio`).
#[pyfunction]
fn indel(a: &str, b: &str) -> f64 {
    gf::indel_ratio(a, b)
}

/// One-vs-many top-k: score `query` against every choice (query bitmap built
/// once), keep scores `>= score_cutoff`, return up to `limit` `(index, score)`
/// pairs best-first. `limit == 0` means no limit.
#[pyfunction]
#[pyo3(signature = (query, choices, scorer="jaro_winkler", score_cutoff=0.0, limit=0))]
fn extract(
    query: &str,
    choices: Vec<String>,
    scorer: &str,
    score_cutoff: f64,
    limit: usize,
) -> PyResult<Vec<(usize, f64)>> {
    let sc = parse_scorer(scorer)?;
    let refs: Vec<&str> = choices.iter().map(String::as_str).collect();
    Ok(gf::extract(query, &refs, sc, score_cutoff, limit))
}

/// Full one-vs-many score matrix `out[i][j] = scorer(queries[i], choices[j])`;
/// each query's bitmap is built once.
#[pyfunction]
#[pyo3(signature = (queries, choices, scorer="jaro_winkler"))]
fn cdist(queries: Vec<String>, choices: Vec<String>, scorer: &str) -> PyResult<Vec<Vec<f64>>> {
    let sc = parse_scorer(scorer)?;
    let qr: Vec<&str> = queries.iter().map(String::as_str).collect();
    let cr: Vec<&str> = choices.iter().map(String::as_str).collect();
    Ok(gf::cdist(&qr, &cr, sc))
}

/// A query prepared for one-vs-many scoring: build once, score many choices.
#[pyclass(name = "BatchComparator", module = "goldenfuzz._goldenfuzz")]
struct PyBatchComparator {
    inner: gf::BatchComparator,
}

#[pymethods]
impl PyBatchComparator {
    #[new]
    fn new(query: &str) -> Self {
        PyBatchComparator {
            inner: gf::BatchComparator::new(query),
        }
    }

    fn jaro_winkler(&self, choice: &str) -> f64 {
        self.inner.jaro_winkler(choice)
    }

    fn levenshtein(&self, choice: &str) -> f64 {
        self.inner.levenshtein(choice)
    }

    fn indel(&self, choice: &str) -> f64 {
        self.inner.indel(choice)
    }

    #[pyo3(signature = (choice, scorer="jaro_winkler"))]
    fn score(&self, choice: &str, scorer: &str) -> PyResult<f64> {
        Ok(self.inner.score(choice, parse_scorer(scorer)?))
    }
}

#[pymodule]
fn _goldenfuzz(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(jaro_winkler, m)?)?;
    m.add_function(wrap_pyfunction!(levenshtein, m)?)?;
    m.add_function(wrap_pyfunction!(indel, m)?)?;
    m.add_function(wrap_pyfunction!(extract, m)?)?;
    m.add_function(wrap_pyfunction!(cdist, m)?)?;
    m.add_class::<PyBatchComparator>()?;
    Ok(())
}
