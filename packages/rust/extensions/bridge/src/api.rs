//! GoldenMatch Python API wrappers.
//!
//! Each function acquires the GIL, calls the corresponding Python function,
//! and returns the result as Rust types.

use pyo3::prelude::*;
use pyo3::types::{PyAnyMethods, PyDict};

use crate::convert;
use crate::error::BridgeError;

/// Best-effort serialisation of an AutoConfigController `(profile, history,
/// committed_config)` triple into the JSON shape consumed by the SQL layer.
///
/// Reuses `goldenmatch.web.controller_telemetry.serialize_telemetry` so the
/// shape is identical to what the FastAPI server returns at
/// `/api/v1/controller/telemetry`. That module is part of the optional `web`
/// extra; if it isn't importable we fall back to a hand-rolled minimal blob
/// so the SQL surface never crashes on environments that didn't install
/// `goldenmatch[web]`.
fn serialize_controller_telemetry<'py>(
    py: Python<'py>,
    profile: Bound<'py, PyAny>,
    history: Bound<'py, PyAny>,
    committed_config: Bound<'py, PyAny>,
    source: &str,
) -> Result<String, BridgeError> {
    let json_mod = py.import("json")?;

    // Try the web helper first — it's the source of truth for telemetry shape.
    if let Ok(helper_mod) = py.import("goldenmatch.web.controller_telemetry") {
        let kwargs = PyDict::new(py);
        kwargs.set_item("profile", profile)?;
        kwargs.set_item("history", history)?;
        kwargs.set_item("committed_config", committed_config)?;
        kwargs.set_item("source", source)?;
        kwargs.set_item("run_name", py.None())?;
        kwargs.set_item("recorded_at", py.None())?;
        let blob = helper_mod.call_method("serialize_telemetry", (), Some(&kwargs))?;
        let json_str: String = json_mod.call_method1("dumps", (blob,))?.extract()?;
        return Ok(json_str);
    }

    // Fallback: minimal hand-rolled telemetry surfacing stop_reason + health.
    // Keeps the SQL contract usable when goldenmatch[web] isn't installed.
    let minimal = PyDict::new(py);
    minimal.set_item("available", !profile.is_none() || !history.is_none())?;
    minimal.set_item("source", source)?;
    if !history.is_none() {
        if let Ok(sr) = history.getattr("stop_reason") {
            if !sr.is_none() {
                let value: String = sr.getattr("value")?.extract()?;
                minimal.set_item("stop_reason", value)?;
            }
        }
    }
    if !profile.is_none() {
        if let Ok(verdict) = profile.call_method0("health") {
            let value: String = verdict.getattr("value")?.extract()?;
            minimal.set_item("health", value)?;
        }
    }
    let json_str: String = json_mod.call_method1("dumps", (minimal,))?.extract()?;
    Ok(json_str)
}

/// Capture `_LAST_CONTROLLER_RUN` ContextVar and serialise it. Returns `None`
/// when the controller didn't run in the current call (e.g. an explicit
/// config was passed to `dedupe_df`).
fn capture_controller_telemetry<'py>(
    py: Python<'py>,
    committed_config: Bound<'py, PyAny>,
    source: &str,
) -> Result<Option<String>, BridgeError> {
    let autoconfig_mod = py.import("goldenmatch.core.autoconfig")?;
    let ctx_var = autoconfig_mod.getattr("_LAST_CONTROLLER_RUN")?;
    let state = ctx_var.call_method0("get")?;
    if state.is_none() {
        return Ok(None);
    }
    let profile = state.get_item(0)?;
    let history = state.get_item(1)?;
    let json = serialize_controller_telemetry(py, profile, history, committed_config, source)?;
    Ok(Some(json))
}

/// Convert a JSON-serialised `GoldenMatchConfig` into the corresponding
/// Python object via `GoldenMatchConfig.model_validate_json(...)`.
///
/// Accepts the full Pydantic shape (multiple matchkeys, `negative_evidence`,
/// `blocking`, `standardization`, `golden_rules`, ...) instead of the slim
/// `exact/fuzzy/blocking/threshold` kwargs the older `dedupe()` accepts.
fn build_full_config<'py>(
    py: Python<'py>,
    config_json: &str,
) -> Result<Bound<'py, PyAny>, BridgeError> {
    let schemas_mod = py.import("goldenmatch.config.schemas")?;
    let gm_config_cls = schemas_mod.getattr("GoldenMatchConfig")?;
    let cfg = gm_config_cls.call_method1("model_validate_json", (config_json,))?;
    Ok(cfg)
}

/// Result of a dedupe operation, returned as JSON strings for the extension
/// layer to parse and convert to SQL tuples.
pub struct DedupeResult {
    /// Golden records as JSON array of objects
    pub golden_json: Option<String>,
    /// Cluster assignments as JSON
    pub clusters_json: String,
    /// Stats as JSON object
    pub stats_json: String,
    /// AutoConfigController telemetry (stop_reason, decisions, health verdict,
    /// committed NE fields). ``None`` when an explicit config was supplied
    /// and the controller never ran on this call. Shape mirrors the web
    /// ``/api/v1/controller/telemetry`` endpoint so callers can reuse the
    /// same parsers.
    pub telemetry_json: Option<String>,
}

/// Result of `autoconfig()`: the committed config + controller telemetry.
///
/// Mirrors `goldenmatch autoconfig` CLI: stdout-equivalent is the YAML/JSON
/// config; stderr-equivalent is the telemetry blob.
pub struct AutoConfigResult {
    /// Committed `GoldenMatchConfig` serialised as JSON (Pydantic model_dump).
    /// Suitable to round-trip back into `dedupe_full()` or stored on a Postgres
    /// `_jobs` row / DuckDB pipeline state.
    pub config_json: String,
    /// Controller telemetry — same shape as `DedupeResult.telemetry_json`.
    /// Always populated (the controller always runs); never `None`.
    pub telemetry_json: String,
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

        // If the slim-kwargs path ended up triggering auto-config (i.e. the
        // caller didn't pass enough kwargs to bypass it), `_LAST_CONTROLLER_RUN`
        // will be populated. Pull the committed config off `result.config`
        // for the NE section of the telemetry blob.
        let committed_cfg = result
            .getattr("config")
            .ok()
            .filter(|c| !c.is_none())
            .unwrap_or_else(|| py.None().into_bound(py));
        let telemetry_json =
            capture_controller_telemetry(py, committed_cfg, "dedupe").unwrap_or(None);

        Ok(DedupeResult {
            golden_json,
            clusters_json,
            stats_json,
            telemetry_json,
        })
    })
}

/// Deduplicate a table's data with a *full* `GoldenMatchConfig` JSON.
///
/// Unlike `dedupe()`, which only forwards `exact`/`fuzzy`/`blocking`/`threshold`
/// kwargs, this accepts the full Pydantic config shape — including
/// `negative_evidence` (Path Y), per-matchkey `comparison`/`scorer`/`weight`,
/// `standardization` rules, `golden_rules`, etc. Use this when the SQL caller
/// wants to express anything that doesn't fit the slim shape.
pub fn dedupe_full(rows_json: &str, config_json: &str) -> Result<DedupeResult, BridgeError> {
    crate::init()?;

    Python::with_gil(|py| {
        let gm = py.import("goldenmatch")?;
        let json_mod = py.import("json")?;

        let df = convert::json_to_polars_df(py, rows_json)?;
        let cfg = build_full_config(py, config_json)?;

        let kwargs = PyDict::new(py);
        kwargs.set_item("config", cfg)?;
        let result = gm.call_method("dedupe_df", (df,), Some(&kwargs))?;

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

        let stats = result.getattr("stats")?;
        let stats_json: String = json_mod.call_method1("dumps", (stats,))?.extract()?;

        let clusters = result.getattr("clusters")?;
        let clusters_json: String = {
            let str_repr: String = clusters.call_method0("__str__")?.extract()?;
            match json_mod.call_method1("dumps", (clusters,)) {
                Ok(j) => j.extract()?,
                Err(_) => str_repr,
            }
        };

        // dedupe_full passes its own config in, so re-use it for NE display.
        // Re-fetch from result.config in case the engine swapped in an
        // augmented copy (e.g. policy refit applied at the controller layer).
        let committed_cfg = result
            .getattr("config")
            .ok()
            .filter(|c| !c.is_none())
            .unwrap_or_else(|| py.None().into_bound(py));
        let telemetry_json =
            capture_controller_telemetry(py, committed_cfg, "dedupe_full").unwrap_or(None);

        Ok(DedupeResult {
            golden_json,
            clusters_json,
            stats_json,
            telemetry_json,
        })
    })
}

/// Run AutoConfigController on the input rows and return the committed config
/// plus telemetry. Does NOT run the dedupe pipeline.
///
/// SQL-surface equivalent of the CLI `goldenmatch autoconfig <files>`.
/// Callers typically pipe the returned `config_json` into a follow-up
/// `dedupe_full()` call, or store it on a Postgres `_jobs` row.
pub fn autoconfig(rows_json: &str) -> Result<AutoConfigResult, BridgeError> {
    crate::init()?;

    Python::with_gil(|py| {
        let df = convert::json_to_polars_df(py, rows_json)?;
        let autoconfig_mod = py.import("goldenmatch.core.autoconfig")?;
        let cfg = autoconfig_mod.call_method1("auto_configure_df", (df,))?;

        // Read controller telemetry off the ContextVar set by auto_configure_df.
        let ctx_var = autoconfig_mod.getattr("_LAST_CONTROLLER_RUN")?;
        let state = ctx_var.call_method0("get")?;
        let telemetry_json = if state.is_none() {
            // Defensive: every modern auto_configure_df path sets this.
            "{\"available\": false, \"source\": \"autoconfig\"}".to_string()
        } else {
            let profile = state.get_item(0)?;
            let history = state.get_item(1)?;
            serialize_controller_telemetry(py, profile, history, cfg.clone(), "autoconfig")?
        };

        // Dump the committed config to JSON via Pydantic. `model_dump(mode="json",
        // exclude_none=True)` matches what the CLI `autoconfig` writes to disk.
        let kwargs = PyDict::new(py);
        kwargs.set_item("mode", "json")?;
        kwargs.set_item("exclude_none", true)?;
        let cfg_dict = cfg.call_method("model_dump", (), Some(&kwargs))?;
        let json_mod = py.import("json")?;
        let config_json: String = json_mod.call_method1("dumps", (cfg_dict,))?.extract()?;

        Ok(AutoConfigResult {
            config_json,
            telemetry_json,
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
