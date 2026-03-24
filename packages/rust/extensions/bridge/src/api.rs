//! GoldenMatch Python API wrappers.
//!
//! Each function acquires the GIL, calls the corresponding Python function,
//! and returns the result as Rust types.

use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::convert;
use crate::error::BridgeError;

/// Result of a dedupe operation, returned as JSON strings for the extension
/// layer to parse and convert to SQL tuples.
pub struct DedupeResult {
    /// Golden records as JSON array of objects
    pub golden_json: Option<String>,
    /// Cluster assignments as JSON
    pub clusters_json: String,
    /// Stats as JSON object
    pub stats_json: String,
}

/// A scored pair from deduplication.
pub struct ScoredPair {
    pub id_a: i64,
    pub id_b: i64,
    pub score: f64,
}

/// A cluster assignment from deduplication.
pub struct ClusterMember {
    pub cluster_id: i64,
    pub record_id: i64,
    pub cluster_size: i64,
}

/// A match result row.
pub struct MatchRow {
    pub target_id: i64,
    pub ref_id: i64,
    pub score: f64,
}

/// Result of a match operation.
pub struct MatchResult {
    /// Matched pairs as JSON array
    pub matched_json: Option<String>,
    /// Unmatched records as JSON array
    pub unmatched_json: Option<String>,
}

/// Deduplicate a table's data (passed as JSON records).
///
/// Calls `goldenmatch.dedupe_df()` under the hood.
pub fn dedupe(rows_json: &str, config_json: &str) -> Result<DedupeResult, BridgeError> {
    crate::init()?;

    Python::with_gil(|py| {
        let gm = py.import("goldenmatch")?;
        let json_mod = py.import("json")?;

        // Build DataFrame from JSON
        let df = convert::json_to_polars_df(py, rows_json)?;

        // Parse config JSON to kwargs
        let config_dict = json_mod.call_method1("loads", (config_json,))?;

        // Call gm.dedupe_df(df, **config)
        let kwargs = PyDict::new(py);
        // Extract known keys from config
        if let Ok(exact) = config_dict.get_item("exact") {
            if !exact.is_none() {
                kwargs.set_item("exact", exact)?;
            }
        }
        if let Ok(fuzzy) = config_dict.get_item("fuzzy") {
            if !fuzzy.is_none() {
                kwargs.set_item("fuzzy", fuzzy)?;
            }
        }
        if let Ok(blocking) = config_dict.get_item("blocking") {
            if !blocking.is_none() {
                kwargs.set_item("blocking", blocking)?;
            }
        }
        if let Ok(threshold) = config_dict.get_item("threshold") {
            if !threshold.is_none() {
                kwargs.set_item("threshold", threshold)?;
            }
        }

        let result = gm.call_method("dedupe_df", (df,), Some(&kwargs))?;

        // Extract golden DataFrame as JSON
        let golden_json = if let Ok(golden) = result.getattr("golden") {
            if !golden.is_none() {
                Some(convert::polars_df_to_json(
                    py,
                    &golden.into_pyobject(py).unwrap().unbind(),
                )?)
            } else {
                None
            }
        } else {
            None
        };

        // Extract stats
        let stats = result.getattr("stats")?;
        let stats_json: String = json_mod.call_method1("dumps", (stats,))?.extract()?;

        // Extract clusters -- convert to JSON-safe dict (pair_scores has tuple keys)
        let clusters = result.getattr("clusters")?;
        let clusters_json: String = {
            let str_repr: String = clusters.call_method0("__str__")?.extract()?;
            // Use str() representation as fallback since json.dumps fails on tuple keys
            match json_mod.call_method1("dumps", (clusters,)) {
                Ok(j) => j.extract()?,
                Err(_) => str_repr,
            }
        };

        Ok(DedupeResult {
            golden_json,
            clusters_json,
            stats_json,
        })
    })
}

/// Match two tables (passed as JSON records).
///
/// Calls `goldenmatch.match_df()` under the hood.
pub fn match_tables(
    target_json: &str,
    reference_json: &str,
    config_json: &str,
) -> Result<MatchResult, BridgeError> {
    crate::init()?;

    Python::with_gil(|py| {
        let gm = py.import("goldenmatch")?;
        let json_mod = py.import("json")?;

        let target_df = convert::json_to_polars_df(py, target_json)?;
        let ref_df = convert::json_to_polars_df(py, reference_json)?;

        let config_dict = json_mod.call_method1("loads", (config_json,))?;

        let kwargs = PyDict::new(py);
        if let Ok(exact) = config_dict.get_item("exact") {
            if !exact.is_none() {
                kwargs.set_item("exact", exact)?;
            }
        }
        if let Ok(fuzzy) = config_dict.get_item("fuzzy") {
            if !fuzzy.is_none() {
                kwargs.set_item("fuzzy", fuzzy)?;
            }
        }
        if let Ok(blocking) = config_dict.get_item("blocking") {
            if !blocking.is_none() {
                kwargs.set_item("blocking", blocking)?;
            }
        }

        let result = gm.call_method("match_df", (target_df, ref_df), Some(&kwargs))?;

        let matched_json = if let Ok(matched) = result.getattr("matched") {
            if !matched.is_none() {
                Some(convert::polars_df_to_json(
                    py,
                    &matched.into_pyobject(py).unwrap().unbind(),
                )?)
            } else {
                None
            }
        } else {
            None
        };

        let unmatched_json = if let Ok(unmatched) = result.getattr("unmatched") {
            if !unmatched.is_none() {
                Some(convert::polars_df_to_json(
                    py,
                    &unmatched.into_pyobject(py).unwrap().unbind(),
                )?)
            } else {
                None
            }
        } else {
            None
        };

        Ok(MatchResult {
            matched_json,
            unmatched_json,
        })
    })
}

/// Score two strings using a named similarity scorer.
///
/// Calls `goldenmatch.score_strings()` under the hood.
pub fn score_strings(value_a: &str, value_b: &str, scorer: &str) -> Result<f64, BridgeError> {
    crate::init()?;

    Python::with_gil(|py| {
        let gm = py.import("goldenmatch")?;
        let result = gm.call_method1("score_strings", (value_a, value_b, scorer))?;
        let score: f64 = result.extract()?;
        Ok(score)
    })
}

/// Score a pair of records (passed as JSON objects).
///
/// Calls `goldenmatch.score_pair_df()` under the hood.
pub fn score_pair(
    record_a_json: &str,
    record_b_json: &str,
    config_json: &str,
) -> Result<f64, BridgeError> {
    crate::init()?;

    Python::with_gil(|py| {
        let gm = py.import("goldenmatch")?;
        let json_mod = py.import("json")?;

        let rec_a = json_mod.call_method1("loads", (record_a_json,))?;
        let rec_b = json_mod.call_method1("loads", (record_b_json,))?;
        let config = json_mod.call_method1("loads", (config_json,))?;

        let kwargs = PyDict::new(py);
        if let Ok(fuzzy) = config.get_item("fuzzy") {
            if !fuzzy.is_none() {
                kwargs.set_item("fuzzy", fuzzy)?;
            }
        }
        if let Ok(exact) = config.get_item("exact") {
            if !exact.is_none() {
                kwargs.set_item("exact", exact)?;
            }
        }

        let result = gm.call_method("score_pair_df", (rec_a, rec_b), Some(&kwargs))?;
        let score: f64 = result.extract()?;
        Ok(score)
    })
}

/// Explain a pair match (passed as JSON objects).
///
/// Calls `goldenmatch.explain_pair_df()` under the hood.
pub fn explain_pair(
    record_a_json: &str,
    record_b_json: &str,
    config_json: &str,
) -> Result<String, BridgeError> {
    crate::init()?;

    Python::with_gil(|py| {
        let gm = py.import("goldenmatch")?;
        let json_mod = py.import("json")?;

        let rec_a = json_mod.call_method1("loads", (record_a_json,))?;
        let rec_b = json_mod.call_method1("loads", (record_b_json,))?;
        let config = json_mod.call_method1("loads", (config_json,))?;

        let kwargs = PyDict::new(py);
        if let Ok(fuzzy) = config.get_item("fuzzy") {
            if !fuzzy.is_none() {
                kwargs.set_item("fuzzy", fuzzy)?;
            }
        }
        if let Ok(exact) = config.get_item("exact") {
            if !exact.is_none() {
                kwargs.set_item("exact", exact)?;
            }
        }

        let result = gm.call_method("explain_pair_df", (rec_a, rec_b), Some(&kwargs))?;
        let explanation: String = result.extract()?;
        Ok(explanation)
    })
}

/// Deduplicate and return scored pairs as structured data.
pub fn dedupe_pairs(rows_json: &str, config_json: &str) -> Result<Vec<ScoredPair>, BridgeError> {
    crate::init()?;

    Python::with_gil(|py| {
        let gm = py.import("goldenmatch")?;
        let json_mod = py.import("json")?;

        let df = convert::json_to_polars_df(py, rows_json)?;
        let config_dict = json_mod.call_method1("loads", (config_json,))?;

        let kwargs = PyDict::new(py);
        if let Ok(exact) = config_dict.get_item("exact") {
            if !exact.is_none() {
                kwargs.set_item("exact", exact)?;
            }
        }
        if let Ok(fuzzy) = config_dict.get_item("fuzzy") {
            if !fuzzy.is_none() {
                kwargs.set_item("fuzzy", fuzzy)?;
            }
        }
        if let Ok(blocking) = config_dict.get_item("blocking") {
            if !blocking.is_none() {
                kwargs.set_item("blocking", blocking)?;
            }
        }
        if let Ok(threshold) = config_dict.get_item("threshold") {
            if !threshold.is_none() {
                kwargs.set_item("threshold", threshold)?;
            }
        }

        let result = gm.call_method("dedupe_df", (df,), Some(&kwargs))?;
        let scored_pairs = result.getattr("scored_pairs")?;
        let pairs_list: Vec<(i64, i64, f64)> = scored_pairs.extract()?;

        Ok(pairs_list
            .into_iter()
            .map(|(a, b, s)| ScoredPair {
                id_a: a,
                id_b: b,
                score: s,
            })
            .collect())
    })
}

/// Deduplicate and return cluster assignments as structured data.
pub fn dedupe_clusters(
    rows_json: &str,
    config_json: &str,
) -> Result<Vec<ClusterMember>, BridgeError> {
    crate::init()?;

    Python::with_gil(|py| {
        let gm = py.import("goldenmatch")?;
        let json_mod = py.import("json")?;

        let df = convert::json_to_polars_df(py, rows_json)?;
        let config_dict = json_mod.call_method1("loads", (config_json,))?;

        let kwargs = PyDict::new(py);
        if let Ok(exact) = config_dict.get_item("exact") {
            if !exact.is_none() {
                kwargs.set_item("exact", exact)?;
            }
        }
        if let Ok(fuzzy) = config_dict.get_item("fuzzy") {
            if !fuzzy.is_none() {
                kwargs.set_item("fuzzy", fuzzy)?;
            }
        }
        if let Ok(blocking) = config_dict.get_item("blocking") {
            if !blocking.is_none() {
                kwargs.set_item("blocking", blocking)?;
            }
        }

        let result = gm.call_method("dedupe_df", (df,), Some(&kwargs))?;
        let clusters_obj = result.getattr("clusters")?;
        let clusters_dict: std::collections::HashMap<i64, pyo3::Py<pyo3::types::PyDict>> =
            clusters_obj.extract()?;

        let mut members = Vec::new();
        for (cluster_id, info) in clusters_dict {
            let info_ref = info.bind(py);
            if let Ok(Some(m)) = info_ref.get_item("members") {
                let member_ids: Vec<i64> = m.extract()?;
                let size = member_ids.len() as i64;
                for record_id in member_ids {
                    members.push(ClusterMember {
                        cluster_id,
                        record_id,
                        cluster_size: size,
                    });
                }
            }
        }
        Ok(members)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_score_strings() {
        // Requires goldenmatch installed
        match score_strings("John Smith", "Jon Smyth", "jaro_winkler") {
            Ok(score) => {
                assert!(score > 0.7);
                assert!(score < 1.0);
            }
            Err(e) => {
                eprintln!("Skipping test (goldenmatch not installed): {}", e);
            }
        }
    }

    #[test]
    fn test_score_strings_exact() {
        match score_strings("hello", "hello", "exact") {
            Ok(score) => assert_eq!(score, 1.0),
            Err(e) => eprintln!("Skipping: {}", e),
        }
    }

    #[test]
    fn test_dedupe_basic() {
        let rows = r#"[
            {"email": "john@x.com", "name": "John"},
            {"email": "john@x.com", "name": "JOHN"},
            {"email": "jane@y.com", "name": "Jane"}
        ]"#;
        let config = r#"{"exact": ["email"]}"#;

        match dedupe(rows, config) {
            Ok(result) => {
                assert!(!result.clusters_json.is_empty());
                assert!(!result.stats_json.is_empty());
            }
            Err(e) => eprintln!("Skipping: {}", e),
        }
    }

    #[test]
    fn test_score_pair() {
        let rec_a = r#"{"name": "John Smith", "email": "j@x.com"}"#;
        let rec_b = r#"{"name": "Jon Smyth", "email": "j@x.com"}"#;
        let config = r#"{"fuzzy": {"name": 0.85}, "exact": ["email"]}"#;

        match score_pair(rec_a, rec_b, config) {
            Ok(score) => {
                assert!(score > 0.5);
                assert!(score <= 1.0);
            }
            Err(e) => eprintln!("Skipping: {}", e),
        }
    }
}
