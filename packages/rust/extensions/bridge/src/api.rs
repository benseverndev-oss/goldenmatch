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

        // Structured cluster output is served via `dedupe_clusters` (and the
        // pgrx `goldenmatch_dedupe_clusters` TableIterator); no JSON clusters
        // blob is carried here — the prior `clusters_json` field fell back to a
        // non-JSON `str()` repr whenever `pair_scores` had tuple keys.

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

        // Structured clusters are served via `dedupe_clusters`; no tuple-keyed
        // JSON clusters blob is carried here (see `dedupe`).

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

// ── Identity Graph (v2.0) ────────────────────────────────────────────────
//
// Thin wrappers over ``goldenmatch.identity.query.*`` so the Postgres and
// DuckDB extensions can serve the same JSON shape the Python/REST/MCP/A2A
// surfaces serve. Every function takes the path to the identity SQLite/PG
// store as an explicit argument; session-level settings are awkward to
// thread through pgrx + pyo3, and explicit args make the SQL contract
// obvious at the call site.

/// Resolve a `{source}:{source_pk}` style ``record_id`` to its identity
/// view JSON. Returns ``{"found": false}`` when no identity owns the record.
pub fn identity_resolve(record_id: &str, db_path: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let identity = py.import("goldenmatch.identity")?;
        let store_cls = identity.getattr("IdentityStore")?;
        let find_by_record = identity.getattr("find_by_record")?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("path", db_path)?;
        let store = store_cls.call((), Some(&kwargs))?;
        let view = find_by_record.call1((store.clone(), record_id))?;
        let _ = store.call_method0("close");
        let json_mod = py.import("json")?;
        if view.is_none() {
            return Ok("{\"found\": false}".to_string());
        }
        let dict = view.call_method0("to_dict")?;
        let dumps_kwargs = PyDict::new(py);
        dumps_kwargs.set_item("default", py.import("builtins")?.getattr("str")?)?;
        let s: String = json_mod
            .call_method("dumps", (dict,), Some(&dumps_kwargs))?
            .extract()?;
        Ok(s)
    })
}

/// Return the full identity view JSON keyed by ``entity_id``.
pub fn identity_view(entity_id: &str, db_path: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let identity = py.import("goldenmatch.identity")?;
        let store_cls = identity.getattr("IdentityStore")?;
        let get_entity = identity.getattr("get_entity")?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("path", db_path)?;
        let store = store_cls.call((), Some(&kwargs))?;
        let view = get_entity.call1((store.clone(), entity_id))?;
        let _ = store.call_method0("close");
        let json_mod = py.import("json")?;
        if view.is_none() {
            return Ok("{\"found\": false}".to_string());
        }
        let dict = view.call_method0("to_dict")?;
        let dumps_kwargs = PyDict::new(py);
        dumps_kwargs.set_item("default", py.import("builtins")?.getattr("str")?)?;
        let s: String = json_mod
            .call_method("dumps", (dict,), Some(&dumps_kwargs))?
            .extract()?;
        Ok(s)
    })
}

/// Return the temporal event log for an identity as a JSON array.
pub fn identity_history(entity_id: &str, db_path: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let identity = py.import("goldenmatch.identity")?;
        let store_cls = identity.getattr("IdentityStore")?;
        let history_fn = identity.getattr("history")?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("path", db_path)?;
        let store = store_cls.call((), Some(&kwargs))?;
        let events = history_fn.call1((store.clone(), entity_id))?;
        let _ = store.call_method0("close");
        let json_mod = py.import("json")?;
        let dumps_kwargs = PyDict::new(py);
        dumps_kwargs.set_item("default", py.import("builtins")?.getattr("str")?)?;
        let s: String = json_mod
            .call_method("dumps", (events,), Some(&dumps_kwargs))?
            .extract()?;
        Ok(s)
    })
}

/// List `conflicts_with` evidence edges as a JSON array. Empty ``dataset``
/// means "all datasets".
pub fn identity_conflicts(dataset: &str, db_path: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let identity = py.import("goldenmatch.identity")?;
        let store_cls = identity.getattr("IdentityStore")?;
        let find_conflicts = identity.getattr("find_conflicts")?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("path", db_path)?;
        let store = store_cls.call((), Some(&kwargs))?;
        let conflicts_kwargs = PyDict::new(py);
        if dataset.is_empty() {
            conflicts_kwargs.set_item("dataset", py.None())?;
        } else {
            conflicts_kwargs.set_item("dataset", dataset)?;
        }
        let edges =
            find_conflicts.call((store.clone(),), Some(&conflicts_kwargs))?;
        let _ = store.call_method0("close");
        let json_mod = py.import("json")?;
        let dumps_kwargs = PyDict::new(py);
        dumps_kwargs.set_item("default", py.import("builtins")?.getattr("str")?)?;
        let s: String = json_mod
            .call_method("dumps", (edges,), Some(&dumps_kwargs))?
            .extract()?;
        Ok(s)
    })
}

/// List identities filtered by dataset/status as a JSON array.
/// Empty strings = no filter on that dimension.
pub fn identity_list(
    dataset: &str,
    status: &str,
    db_path: &str,
) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let identity = py.import("goldenmatch.identity")?;
        let store_cls = identity.getattr("IdentityStore")?;
        let list_entities = identity.getattr("list_entities")?;
        let open_kwargs = PyDict::new(py);
        open_kwargs.set_item("path", db_path)?;
        let store = store_cls.call((), Some(&open_kwargs))?;
        let list_kwargs = PyDict::new(py);
        if dataset.is_empty() {
            list_kwargs.set_item("dataset", py.None())?;
        } else {
            list_kwargs.set_item("dataset", dataset)?;
        }
        if status.is_empty() {
            list_kwargs.set_item("status", py.None())?;
        } else {
            list_kwargs.set_item("status", status)?;
        }
        list_kwargs.set_item("limit", 500)?;
        let items = list_entities.call((store.clone(),), Some(&list_kwargs))?;
        let _ = store.call_method0("close");
        let json_mod = py.import("json")?;
        let dumps_kwargs = PyDict::new(py);
        dumps_kwargs.set_item("default", py.import("builtins")?.getattr("str")?)?;
        let s: String = json_mod
            .call_method("dumps", (items,), Some(&dumps_kwargs))?
            .extract()?;
        Ok(s)
    })
}

// ─── Correction CRUD (Phase 6A of #437 surface sync) ────────────────────
//
// File pair-level + field-level + cluster-decision corrections into the
// Python MemoryStore. The pgrx extension wraps these as
// `goldenmatch.correction_add(...)` etc. Spec:
// docs/superpowers/specs/2026-05-22-phase-6-sql-extensions-correction-crud-design.md

/// Args for `correction_add` -- supports the three Correction shapes
/// (pair-level, field-level, cluster-decision) auto-detected from
/// which fields are populated.
#[derive(Debug, Default)]
pub struct CorrectionAddArgs<'a> {
    pub decision: &'a str,
    pub dataset: &'a str,
    pub source: Option<&'a str>,
    pub memory_path: Option<&'a str>,
    pub reason: Option<&'a str>,
    pub matchkey_name: Option<&'a str>,
    // Pair-level (decision in {approve, reject}):
    pub id_a: Option<i64>,
    pub id_b: Option<i64>,
    pub original_score: Option<f64>,
    // Field-level (decision = field_correct):
    pub cluster_id: Option<i64>,
    pub field_name: Option<&'a str>,
    pub original_value: Option<&'a str>,
    pub corrected_value: Option<&'a str>,
    // Cluster-decision (decision = cluster_decision):
    pub cluster_score: Option<f64>,
    pub cluster_outcome: Option<&'a str>,
}

/// File a correction into MemoryStore. Returns the generated UUID.
///
/// Validates per-shape required fields and raises `BridgeError::Validation`
/// on missing combos (mirrors Python `_dispatch` in
/// `goldenmatch/mcp/memory_tools.py`).
///
/// Source defaults to "postgres" (trust=0.7, between agent 0.5 and steward
/// 1.0) when not provided.
pub fn correction_add(args: CorrectionAddArgs<'_>) -> Result<String, BridgeError> {
    crate::init()?;

    if args.dataset.is_empty() {
        return Err(BridgeError::Validation(
            "dataset is required and must be non-empty".into(),
        ));
    }
    if !matches!(args.decision, "approve" | "reject" | "field_correct" | "cluster_decision") {
        return Err(BridgeError::Validation(format!(
            "invalid decision {:?}; expected approve, reject, field_correct, or cluster_decision",
            args.decision,
        )));
    }

    Python::with_gil(|py| {
        let store_mod = py.import("goldenmatch.core.memory.store")?;
        let datetime_mod = py.import("datetime")?;
        let uuid_mod = py.import("uuid")?;

        let memory_path = args.memory_path.unwrap_or(".goldenmatch/memory.db");
        let source = args.source.unwrap_or("postgres");

        // trust_for_source dispatch -- module-level helper handles
        // steward / boost / unmerge -> 1.0; rest -> 0.8; duckdb -> 0.7;
        // postgres falls through to default 0.5 unless added. Add it here
        // as 0.7 (matches DuckDB tier) until the Python module bumps.
        let trust: f64 = if source == "postgres" {
            0.7
        } else {
            let trust_fn = store_mod.getattr("trust_for_source")?;
            trust_fn.call1((source,))?.extract()?
        };

        // Build Correction kwargs based on shape.
        let kwargs = PyDict::new(py);
        let new_id: String = uuid_mod.call_method0("uuid4")?
            .call_method0("__str__")?.extract()?;
        kwargs.set_item("id", &new_id)?;
        kwargs.set_item("source", source)?;
        kwargs.set_item("trust", trust)?;
        kwargs.set_item("field_hash", "")?;
        kwargs.set_item("record_hash", "")?;
        kwargs.set_item("dataset", args.dataset)?;
        kwargs.set_item("reason", args.reason)?;
        kwargs.set_item("matchkey_name", args.matchkey_name)?;
        kwargs.set_item("created_at", datetime_mod.call_method0("datetime")?
            .getattr("now")?.call0()?)?;
        kwargs.set_item("decision", args.decision)?;

        match args.decision {
            "field_correct" => {
                let field_name = args.field_name.ok_or_else(|| {
                    BridgeError::Validation(
                        "field_correct requires field_name".into(),
                    )
                })?;
                let corrected_value = args.corrected_value.ok_or_else(|| {
                    BridgeError::Validation(
                        "field_correct requires corrected_value".into(),
                    )
                })?;
                let cluster_id = args.cluster_id
                    .or(args.id_a)
                    .ok_or_else(|| BridgeError::Validation(
                        "field_correct requires cluster_id".into(),
                    ))?;
                kwargs.set_item("id_a", cluster_id)?;
                kwargs.set_item("id_b", 0i64)?;
                kwargs.set_item("original_score", 0.0f64)?;
                kwargs.set_item("field_name", field_name)?;
                kwargs.set_item("original_value", args.original_value)?;
                kwargs.set_item("corrected_value", corrected_value)?;
            }
            "cluster_decision" => {
                let score = args.cluster_score.ok_or_else(|| {
                    BridgeError::Validation(
                        "cluster_decision requires cluster_score".into(),
                    )
                })?;
                let outcome = args.cluster_outcome.ok_or_else(|| {
                    BridgeError::Validation(
                        "cluster_decision requires cluster_outcome".into(),
                    )
                })?;
                if !matches!(outcome, "approve" | "reject") {
                    return Err(BridgeError::Validation(format!(
                        "cluster_outcome must be approve or reject; got {:?}", outcome,
                    )));
                }
                if !(0.0..=1.0).contains(&score) {
                    return Err(BridgeError::Validation(format!(
                        "cluster_score must be in [0, 1]; got {}", score,
                    )));
                }
                let cluster_id = args.cluster_id
                    .or(args.id_a)
                    .ok_or_else(|| BridgeError::Validation(
                        "cluster_decision requires cluster_id".into(),
                    ))?;
                kwargs.set_item("id_a", cluster_id)?;
                kwargs.set_item("id_b", 0i64)?;
                kwargs.set_item("original_score", 0.0f64)?;
                kwargs.set_item("cluster_score", score)?;
                kwargs.set_item("cluster_outcome", outcome)?;
            }
            "approve" | "reject" => {
                let id_a = args.id_a.ok_or_else(|| BridgeError::Validation(
                    format!("{} requires id_a", args.decision),
                ))?;
                let id_b = args.id_b.ok_or_else(|| BridgeError::Validation(
                    format!("{} requires id_b", args.decision),
                ))?;
                kwargs.set_item("id_a", id_a)?;
                kwargs.set_item("id_b", id_b)?;
                kwargs.set_item("original_score", args.original_score.unwrap_or(0.0))?;
            }
            _ => unreachable!("decision validated above"),
        }

        let correction_cls = store_mod.getattr("Correction")?;
        let correction = correction_cls.call((), Some(&kwargs))?;

        let store_cls = store_mod.getattr("MemoryStore")?;
        let store_kwargs = PyDict::new(py);
        store_kwargs.set_item("backend", "sqlite")?;
        store_kwargs.set_item("path", memory_path)?;
        let store = store_cls.call((), Some(&store_kwargs))?;
        // Best-effort context-manager pattern: call add_correction then close.
        let result = store.call_method1("add_correction", (correction,));
        let _ = store.call_method0("close");
        result?;

        Ok(new_id)
    })
}

/// List corrections for a dataset (or all when None). Returns JSON array.
pub fn correction_list(
    dataset: Option<&str>,
    memory_path: Option<&str>,
) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let store_mod = py.import("goldenmatch.core.memory.store")?;
        let json_mod = py.import("json")?;
        let store_cls = store_mod.getattr("MemoryStore")?;
        let store_kwargs = PyDict::new(py);
        store_kwargs.set_item("backend", "sqlite")?;
        store_kwargs.set_item("path", memory_path.unwrap_or(".goldenmatch/memory.db"))?;
        let store = store_cls.call((), Some(&store_kwargs))?;

        let kw = PyDict::new(py);
        kw.set_item("dataset", dataset)?;
        let corrections = store.call_method("get_corrections", (), Some(&kw))?;

        // Serialize to JSON. Use a Python helper expression to convert
        // dataclasses to dicts in one shot.
        let dataclasses = py.import("dataclasses")?;
        let items = pyo3::types::PyList::empty(py);
        for c in corrections.try_iter()? {
            let c = c?;
            let d = dataclasses.call_method1("asdict", (c,))?;
            // Fix the datetime field for JSON serialization.
            if let Ok(dt) = d.get_item("created_at") {
                if !dt.is_none() {
                    let iso: String = dt.call_method0("isoformat")?.extract()?;
                    d.set_item("created_at", iso)?;
                }
            }
            items.append(d)?;
        }
        let _ = store.call_method0("close");
        let s: String = json_mod.call_method1("dumps", (items,))?.extract()?;
        Ok(s)
    })
}

/// Force a MemoryLearner pass over accumulated corrections. Returns a JSON
/// object `{ "count": N, "adjustments": [...] }`. Mirrors the Python MCP
/// `learn_thresholds` tool: needs >= 10 corrections per matchkey before
/// threshold tuning fires; otherwise returns an empty list.
pub fn memory_learn(
    matchkey_name: Option<&str>,
    memory_path: Option<&str>,
) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let store_mod = py.import("goldenmatch.core.memory.store")?;
        let learner_mod = py.import("goldenmatch.core.memory.learner")?;
        let json_mod = py.import("json")?;
        let dataclasses = py.import("dataclasses")?;

        let store_cls = store_mod.getattr("MemoryStore")?;
        let store_kwargs = PyDict::new(py);
        store_kwargs.set_item("backend", "sqlite")?;
        store_kwargs.set_item("path", memory_path.unwrap_or(".goldenmatch/memory.db"))?;
        let store = store_cls.call((), Some(&store_kwargs))?;

        let learner_cls = learner_mod.getattr("MemoryLearner")?;
        let learner = learner_cls.call1((store.clone(),))?;
        let adjustments = learner.call_method1("learn", (matchkey_name,))?;

        let items = pyo3::types::PyList::empty(py);
        for a in adjustments.try_iter()? {
            let d = dataclasses.call_method1("asdict", (a?,))?;
            if let Ok(dt) = d.get_item("learned_at") {
                if !dt.is_none() {
                    let iso: String = dt.call_method0("isoformat")?.extract()?;
                    d.set_item("learned_at", iso)?;
                }
            }
            items.append(d)?;
        }
        let _ = store.call_method0("close");

        let out = PyDict::new(py);
        out.set_item("count", items.len())?;
        out.set_item("adjustments", items)?;
        let s: String = json_mod.call_method1("dumps", (out,))?.extract()?;
        Ok(s)
    })
}

/// Learning-memory status as a JSON object
/// `{ "total_corrections": N, "last_learn_time": ISO|null, "adjustments": [...] }`.
/// Cheap; safe for status checks. Mirrors the Python MCP `memory_stats` tool.
pub fn memory_stats(memory_path: Option<&str>) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let store_mod = py.import("goldenmatch.core.memory.store")?;
        let json_mod = py.import("json")?;
        let dataclasses = py.import("dataclasses")?;

        let store_cls = store_mod.getattr("MemoryStore")?;
        let store_kwargs = PyDict::new(py);
        store_kwargs.set_item("backend", "sqlite")?;
        store_kwargs.set_item("path", memory_path.unwrap_or(".goldenmatch/memory.db"))?;
        let store = store_cls.call((), Some(&store_kwargs))?;

        let total: i64 = store.call_method0("count_corrections")?.extract()?;
        let last = store.call_method0("last_learn_time")?;
        let last_iso: Option<String> = if last.is_none() {
            None
        } else {
            Some(last.call_method0("isoformat")?.extract()?)
        };

        let items = pyo3::types::PyList::empty(py);
        for a in store.call_method0("get_all_adjustments")?.try_iter()? {
            let d = dataclasses.call_method1("asdict", (a?,))?;
            if let Ok(dt) = d.get_item("learned_at") {
                if !dt.is_none() {
                    let iso: String = dt.call_method0("isoformat")?.extract()?;
                    d.set_item("learned_at", iso)?;
                }
            }
            items.append(d)?;
        }
        let _ = store.call_method0("close");

        let out = PyDict::new(py);
        out.set_item("total_corrections", total)?;
        out.set_item("last_learn_time", last_iso)?;
        out.set_item("adjustments", items)?;
        let s: String = json_mod.call_method1("dumps", (out,))?.extract()?;
        Ok(s)
    })
}

// ─── Core-API parity (mirrors duckdb/core_apis.py) ───────────────────────
//
// Wrappers over goldenmatch's function-shaped core APIs so the Postgres
// extension reaches parity with the DuckDB UDFs registered in
// `goldenmatch_duckdb/core_apis.py`. The JSON in / JSON out contract is
// IDENTICAL to the DuckDB side so the two backends are interchangeable.
//
// Fail-soft semantics: like the DuckDB UDFs, optional-dep / bad-input
// failures return a `{"error": ...}` JSON object instead of raising, so a
// malformed call doesn't abort a whole SQL query. The bridge converts an
// internal `PyErr` into that JSON object rather than propagating a
// `BridgeError` (whereas a true *initialisation* failure -- goldenmatch
// not importable -- still surfaces as `BridgeError::PythonImport`).
//
// Table-input functions take a `rows_json` JSON array of record objects
// (the Postgres layer reads its table via `row_to_json` SPI, exactly like
// `goldenmatch_dedupe_table`). The DuckDB side reads the table itself; both
// converge on the same `json_to_polars_df` path inside goldenmatch.

/// Serialise an arbitrary Python object to a JSON string via `json.dumps`,
/// using `default=str` so dataclasses-converted dicts / datetimes / Paths
/// stay JSON-safe (mirrors the `default=str` in core_apis.py).
fn py_json_dumps<'py>(py: Python<'py>, obj: Bound<'py, PyAny>) -> Result<String, BridgeError> {
    let json_mod = py.import("json")?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("default", py.import("builtins")?.getattr("str")?)?;
    let s: String = json_mod
        .call_method("dumps", (obj,), Some(&kwargs))?
        .extract()?;
    Ok(s)
}

/// Build a `{"error": "<msg>"}` JSON string (fail-soft contract).
fn error_json(msg: &str) -> String {
    // Re-use serde-free hand assembly with minimal escaping for quotes and
    // backslashes so we never depend on a JSON lib in the error path.
    let escaped = msg.replace('\\', "\\\\").replace('"', "\\\"");
    format!("{{\"error\": \"{}\"}}", escaped)
}

/// Build a Polars DataFrame with the `__row_id__` column the probabilistic
/// `train_em` / `score_probabilistic` functions index pairs by. Mirrors
/// `core_apis._build_probabilistic_frame`: respects an existing
/// `__row_id__`, otherwise adds a 0-based Int64 row index.
fn build_probabilistic_frame<'py>(
    py: Python<'py>,
    rows_json: &str,
) -> Result<Bound<'py, PyAny>, BridgeError> {
    let pl = py.import("polars")?;
    let io = py.import("io")?;
    let string_io = io.call_method1("StringIO", (rows_json,))?;
    let df = pl.call_method1("read_json", (string_io,))?;
    let columns: Vec<String> = df.getattr("columns")?.extract()?;
    if columns.iter().any(|c| c == "__row_id__") {
        return Ok(df);
    }
    let df = df.call_method1("with_row_index", ("__row_id__",))?;
    let int64 = pl.getattr("Int64")?;
    let col = pl.call_method1("col", ("__row_id__",))?;
    let cast = col.call_method1("cast", (int64,))?;
    let df = df.call_method1("with_columns", (cast,))?;
    Ok(df)
}

/// Wrap `goldenmatch.profile_dataframe` -- comprehensive table profile.
///
/// `rows_json` is a JSON array of record objects. Returns the profile report
/// as a JSON object (or `{"error": ...}` on failure).
pub fn profile_table(rows_json: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let result: Result<String, BridgeError> = (|| {
            let gm = py.import("goldenmatch")?;
            let df = convert::json_to_polars_df(py, rows_json)?;
            let report = gm.call_method1("profile_dataframe", (df,))?;
            py_json_dumps(py, report)
        })();
        Ok(result.unwrap_or_else(|e| error_json(&e.to_string())))
    })
}

/// Wrap `goldenmatch.suggest_threshold` -- Otsu threshold over a JSON list of
/// scores. Returns `None` (SQL NULL) when the distribution is unimodal or
/// there are too few scores -- identical semantics to the core function and
/// the DuckDB `null_handling="special"` registration.
pub fn suggest_threshold(scores_json: &str) -> Result<Option<f64>, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let result: Result<Option<f64>, BridgeError> = (|| {
            let gm = py.import("goldenmatch")?;
            let json_mod = py.import("json")?;
            let parsed = if scores_json.is_empty() {
                py.import("builtins")?.call_method0("list")?
            } else {
                json_mod.call_method1("loads", (scores_json,))?
            };
            // Coerce each element to float (mirrors `[float(s) for s in ...]`).
            let builtins = py.import("builtins")?;
            let floats = pyo3::types::PyList::empty(py);
            for item in parsed.try_iter()? {
                let f = builtins.call_method1("float", (item?,))?;
                floats.append(f)?;
            }
            let out = gm.call_method1("suggest_threshold", (floats,))?;
            if out.is_none() {
                Ok(None)
            } else {
                Ok(Some(out.extract()?))
            }
        })();
        // Bad input -> NULL (matches core_apis: `except: return None`).
        Ok(result.unwrap_or(None))
    })
}

/// Wrap `goldenmatch.core.domain.detect_domain` -- domain profile for a JSON
/// list of column names. Returns the dataclass as a JSON object.
pub fn detect_domain(columns_json: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let result: Result<String, BridgeError> = (|| {
            let domain = py.import("goldenmatch.core.domain")?;
            let dataclasses = py.import("dataclasses")?;
            let json_mod = py.import("json")?;
            let builtins = py.import("builtins")?;
            let parsed = if columns_json.is_empty() {
                builtins.call_method0("list")?
            } else {
                json_mod.call_method1("loads", (columns_json,))?
            };
            // Coerce each element to str (mirrors `[str(c) for c in ...]`).
            let columns = pyo3::types::PyList::empty(py);
            for item in parsed.try_iter()? {
                let s = builtins.call_method1("str", (item?,))?;
                columns.append(s)?;
            }
            let profile = domain.call_method1("detect_domain", (columns,))?;
            let dict = dataclasses.call_method1("asdict", (profile,))?;
            py_json_dumps(py, dict)
        })();
        Ok(result.unwrap_or_else(|e| error_json(&e.to_string())))
    })
}

/// Wrap the three `extract_*_features` extractors. `kind` selects the
/// extractor: `"product"` / `"electronics"` / `""` -> product;
/// `"software"` -> software; `"biblio"` / `"bibliographic"` -> biblio.
/// Mirrors `core_apis._extract_features` exactly, including its
/// unknown-kind / missing-text error JSON.
pub fn extract_features(text: &str, kind: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let result: Result<String, BridgeError> = (|| {
            let domain = py.import("goldenmatch.core.domain")?;
            let dataclasses = py.import("dataclasses")?;
            let k = kind.trim().to_lowercase();
            match k.as_str() {
                "product" | "electronics" | "" => {
                    let feats = domain.call_method1("extract_product_features", (text,))?;
                    let dict = dataclasses.call_method1("asdict", (feats,))?;
                    py_json_dumps(py, dict)
                }
                "software" => {
                    let feats = domain.call_method1("extract_software_features", (text,))?;
                    let dict = dataclasses.call_method1("asdict", (feats,))?;
                    py_json_dumps(py, dict)
                }
                "biblio" | "bibliographic" => {
                    // extract_biblio_features already returns a plain dict.
                    let dict = domain.call_method1("extract_biblio_features", (text,))?;
                    py_json_dumps(py, dict)
                }
                _ => Ok(error_json(&format!(
                    "Unknown kind '{}'. Use 'product'/'electronics', 'software', or 'biblio'/'bibliographic'.",
                    kind
                ))),
            }
        })();
        Ok(result.unwrap_or_else(|e| error_json(&e.to_string())))
    })
}

/// Wrap `evaluate_pairs` / `evaluate_clusters`. The first argument
/// auto-selects by shape: a JSON array -> `evaluate_pairs`; a JSON object
/// (`{cluster_id: {"members": [...]}}`) -> `evaluate_clusters`.
/// `ground_truth_json` is a JSON array of `[a, b]` pairs. Returns the
/// `EvalResult.summary()` dict.
pub fn evaluate(pairs_json: &str, ground_truth_json: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let result: Result<String, BridgeError> = (|| {
            let gm = py.import("goldenmatch")?;
            let json_mod = py.import("json")?;
            let builtins = py.import("builtins")?;

            let predicted = if pairs_json.is_empty() {
                builtins.call_method0("list")?
            } else {
                json_mod.call_method1("loads", (pairs_json,))?
            };
            let gt_raw = if ground_truth_json.is_empty() {
                builtins.call_method0("list")?
            } else {
                json_mod.call_method1("loads", (ground_truth_json,))?
            };
            // ground_truth = {(p[0], p[1]) for p in gt_raw}
            let ground_truth = pyo3::types::PySet::empty(py)?;
            for p in gt_raw.try_iter()? {
                let p = p?;
                let tuple = pyo3::types::PyTuple::new(py, [p.get_item(0)?, p.get_item(1)?])?;
                ground_truth.add(tuple)?;
            }

            let is_dict = predicted.is_instance(&builtins.getattr("dict")?)?;
            let eval_result = if is_dict {
                // clusters = {int(k): v for k, v in predicted.items()}
                let clusters = PyDict::new(py);
                let items = predicted.call_method0("items")?;
                for kv in items.try_iter()? {
                    let kv = kv?;
                    let k = builtins.call_method1("int", (kv.get_item(0)?,))?;
                    clusters.set_item(k, kv.get_item(1)?)?;
                }
                gm.call_method1("evaluate_clusters", (clusters, ground_truth))?
            } else {
                // pairs = [(p[0], p[1], float(p[2]) if len(p) > 2 else 1.0) for p in predicted]
                let pairs = pyo3::types::PyList::empty(py);
                for p in predicted.try_iter()? {
                    let p = p?;
                    let len: usize = builtins.call_method1("len", (&p,))?.extract()?;
                    let score = if len > 2 {
                        builtins.call_method1("float", (p.get_item(2)?,))?
                    } else {
                        builtins.call_method1("float", (1.0f64,))?
                    };
                    let tuple =
                        pyo3::types::PyTuple::new(py, [p.get_item(0)?, p.get_item(1)?, score])?;
                    pairs.append(tuple)?;
                }
                gm.call_method1("evaluate_pairs", (pairs, ground_truth))?
            };
            let summary = eval_result.call_method0("summary")?;
            py_json_dumps(py, summary)
        })();
        Ok(result.unwrap_or_else(|e| error_json(&e.to_string())))
    })
}

/// Wrap `goldenmatch.compare_clusters` -- CCMS comparison of two clusterings.
/// Both args are JSON objects of `{cluster_id: {"members": [...]}}`. Returns
/// the `CompareResult.summary()` dict.
pub fn compare_clusters(a_json: &str, b_json: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let result: Result<String, BridgeError> = (|| {
            let gm = py.import("goldenmatch")?;
            let json_mod = py.import("json")?;
            let builtins = py.import("builtins")?;

            let parse_int_keyed = |raw_json: &str| -> Result<Bound<'_, PyAny>, BridgeError> {
                let parsed = if raw_json.is_empty() {
                    builtins.call_method0("dict")?
                } else {
                    json_mod.call_method1("loads", (raw_json,))?
                };
                let out = PyDict::new(py);
                let items = parsed.call_method0("items")?;
                for kv in items.try_iter()? {
                    let kv = kv?;
                    let k = builtins.call_method1("int", (kv.get_item(0)?,))?;
                    out.set_item(k, kv.get_item(1)?)?;
                }
                Ok(out.into_any())
            };

            let a = parse_int_keyed(a_json)?;
            let b = parse_int_keyed(b_json)?;
            let result = gm.call_method1("compare_clusters", (a, b))?;
            let summary = result.call_method0("summary")?;
            py_json_dumps(py, summary)
        })();
        Ok(result.unwrap_or_else(|e| error_json(&e.to_string())))
    })
}

/// Wrap `goldenmatch.core.validate.validate_dataframe` -- run validation
/// rules over a table. `rows_json` is the table's records; `rules_json` is a
/// JSON array of rule objects (`{"column", "rule_type", "params", "action"}`).
/// Returns `{report, valid_rows, quarantine_rows, quarantine}` JSON.
pub fn validate_table(rows_json: &str, rules_json: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let result: Result<String, BridgeError> = (|| {
            let validate_mod = py.import("goldenmatch.core.validate")?;
            let json_mod = py.import("json")?;
            let builtins = py.import("builtins")?;

            let df = convert::json_to_polars_df(py, rows_json)?;
            let rules_spec = if rules_json.is_empty() {
                builtins.call_method0("list")?
            } else {
                json_mod.call_method1("loads", (rules_json,))?
            };
            let rule_cls = validate_mod.getattr("ValidationRule")?;
            let rules = pyo3::types::PyList::empty(py);
            for r in rules_spec.try_iter()? {
                let r = r?;
                let kwargs = PyDict::new(py);
                kwargs.set_item("column", r.get_item("column")?)?;
                kwargs.set_item("rule_type", r.get_item("rule_type")?)?;
                let params = match r.call_method1("get", ("params",)) {
                    Ok(p) if !p.is_none() => p,
                    _ => PyDict::new(py).into_any(),
                };
                kwargs.set_item("params", params)?;
                let action = match r.call_method1("get", ("action",)) {
                    Ok(a) if !a.is_none() => a,
                    _ => pyo3::types::PyString::new(py, "flag").into_any(),
                };
                kwargs.set_item("action", action)?;
                rules.append(rule_cls.call((), Some(&kwargs))?)?;
            }

            let out = validate_mod.call_method1("validate_dataframe", (df, rules))?;
            let valid_df = out.get_item(0)?;
            let quarantine_df = out.get_item(1)?;
            let report = out.get_item(2)?;

            let result = PyDict::new(py);
            result.set_item("report", report)?;
            result.set_item("valid_rows", valid_df.getattr("height")?)?;
            result.set_item("quarantine_rows", quarantine_df.getattr("height")?)?;
            result.set_item("quarantine", quarantine_df.call_method0("to_dicts")?)?;
            py_json_dumps(py, result.into_any())
        })();
        Ok(result.unwrap_or_else(|e| error_json(&e.to_string())))
    })
}

/// Wrap `goldenmatch.auto_fix_dataframe` -- apply auto-fixes to a table.
/// `rows_json` is the table's records. Returns `{fixes, fixed_rows, rows}`
/// JSON.
pub fn autofix_table(rows_json: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let result: Result<String, BridgeError> = (|| {
            let gm = py.import("goldenmatch")?;
            let df = convert::json_to_polars_df(py, rows_json)?;
            let out = gm.call_method1("auto_fix_dataframe", (df,))?;
            let fixed_df = out.get_item(0)?;
            let fixes = out.get_item(1)?;
            let result = PyDict::new(py);
            result.set_item("fixes", fixes)?;
            result.set_item("fixed_rows", fixed_df.getattr("height")?)?;
            result.set_item("rows", fixed_df.call_method0("to_dicts")?)?;
            py_json_dumps(py, result.into_any())
        })();
        Ok(result.unwrap_or_else(|e| error_json(&e.to_string())))
    })
}

/// Wrap `goldenmatch.detect_anomalies` -- flag suspicious records in a table.
/// `rows_json` is the table's records; `sensitivity` is `"low"`/`"medium"`/
/// `"high"` (empty -> `"medium"`). Returns the JSON array of anomaly dicts.
pub fn detect_anomalies(rows_json: &str, sensitivity: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let result: Result<String, BridgeError> = (|| {
            let gm = py.import("goldenmatch")?;
            let df = convert::json_to_polars_df(py, rows_json)?;
            let sens = if sensitivity.is_empty() {
                "medium"
            } else {
                sensitivity
            };
            let kwargs = PyDict::new(py);
            kwargs.set_item("sensitivity", sens)?;
            let anomalies = gm.call_method("detect_anomalies", (df,), Some(&kwargs))?;
            py_json_dumps(py, anomalies)
        })();
        Ok(result.unwrap_or_else(|e| error_json(&e.to_string())))
    })
}

/// Wrap `goldenmatch.core.autoconfig_verify.preflight` -- validate
/// `(df, config)` before a run. `rows_json` is the table's records;
/// `config_json` is a full `GoldenMatchConfig` JSON. Returns
/// `{has_errors, config_was_modified, findings}` JSON.
pub fn preflight(rows_json: &str, config_json: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let result: Result<String, BridgeError> = (|| {
            let verify = py.import("goldenmatch.core.autoconfig_verify")?;
            let dataclasses = py.import("dataclasses")?;
            let df = convert::json_to_polars_df(py, rows_json)?;
            let config = build_full_config(py, config_json)?;
            let report = verify.call_method1("preflight", (df, config))?;

            let findings = pyo3::types::PyList::empty(py);
            for f in report.getattr("findings")?.try_iter()? {
                findings.append(dataclasses.call_method1("asdict", (f?,))?)?;
            }
            let result = PyDict::new(py);
            result.set_item("has_errors", report.getattr("has_errors")?)?;
            result.set_item("config_was_modified", report.getattr("config_was_modified")?)?;
            result.set_item("findings", findings)?;
            py_json_dumps(py, result.into_any())
        })();
        Ok(result.unwrap_or_else(|e| error_json(&e.to_string())))
    })
}

/// Wrap `goldenmatch.core.autoconfig_verify.postflight` -- post-run signal
/// report for `(df, config)`. `postflight` needs `pair_scores`, which aren't
/// in the table, so we derive them SQL-naturally: run `dedupe_df` on the
/// table with the given config and feed its `scored_pairs` to `postflight`
/// (identical to `core_apis._postflight`). Returns
/// `{signals, adjustments, advisories}` JSON.
pub fn postflight(rows_json: &str, config_json: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let result: Result<String, BridgeError> = (|| {
            let gm = py.import("goldenmatch")?;
            let verify = py.import("goldenmatch.core.autoconfig_verify")?;
            let dataclasses = py.import("dataclasses")?;
            let df = convert::json_to_polars_df(py, rows_json)?;
            let config = build_full_config(py, config_json)?;

            let dedupe_kwargs = PyDict::new(py);
            dedupe_kwargs.set_item("config", config.clone())?;
            let dedupe_result =
                gm.call_method("dedupe_df", (df.clone_ref(py),), Some(&dedupe_kwargs))?;
            let scored_pairs = dedupe_result.getattr("scored_pairs")?;

            let post_kwargs = PyDict::new(py);
            post_kwargs.set_item("pair_scores", scored_pairs)?;
            let report =
                verify.call_method("postflight", (df, config), Some(&post_kwargs))?;

            let adjustments = pyo3::types::PyList::empty(py);
            for a in report.getattr("adjustments")?.try_iter()? {
                adjustments.append(dataclasses.call_method1("asdict", (a?,))?)?;
            }
            let result = PyDict::new(py);
            result.set_item("signals", report.getattr("signals")?)?;
            result.set_item("adjustments", adjustments)?;
            result.set_item("advisories", report.getattr("advisories")?)?;
            py_json_dumps(py, result.into_any())
        })();
        Ok(result.unwrap_or_else(|e| error_json(&e.to_string())))
    })
}

/// Wrap Fellegi-Sunter `train_em`. `rows_json` is a JSON array of record
/// objects (a small training set); `matchkey_json` is a probabilistic
/// `MatchkeyConfig` JSON; `params_json` is an optional JSON object of
/// train_em kwargs (`n_sample_pairs`, `max_iterations`, `convergence`,
/// `seed`, `blocking_fields`; empty -> defaults). Returns the `EMResult` as
/// JSON -- pass it straight to `score_probabilistic`.
pub fn train_em(
    rows_json: &str,
    matchkey_json: &str,
    params_json: &str,
) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let result: Result<String, BridgeError> = (|| {
            let schemas = py.import("goldenmatch.config.schemas")?;
            let prob = py.import("goldenmatch.core.probabilistic")?;
            let dataclasses = py.import("dataclasses")?;
            let json_mod = py.import("json")?;
            let builtins = py.import("builtins")?;

            let df = build_probabilistic_frame(py, rows_json)?;
            let mk_cls = schemas.getattr("MatchkeyConfig")?;
            let mk = mk_cls.call_method1("model_validate_json", (matchkey_json,))?;

            let params = if params_json.is_empty() {
                builtins.call_method0("dict")?
            } else {
                json_mod.call_method1("loads", (params_json,))?
            };
            let allowed = [
                "n_sample_pairs",
                "max_iterations",
                "convergence",
                "seed",
                "blocking_fields",
            ];
            let kwargs = PyDict::new(py);
            for key in allowed {
                if let Ok(v) = params.get_item(key) {
                    if !v.is_none() {
                        kwargs.set_item(key, v)?;
                    }
                }
            }
            let em = prob.call_method("train_em", (df, mk), Some(&kwargs))?;
            let dict = dataclasses.call_method1("asdict", (em,))?;
            py_json_dumps(py, dict)
        })();
        Ok(result.unwrap_or_else(|e| error_json(&e.to_string())))
    })
}

/// Wrap Fellegi-Sunter `score_probabilistic`. `rows_json` is a JSON array of
/// record objects (the block to score); `matchkey_json` is the same
/// probabilistic `MatchkeyConfig` used for training; `em_result_json` is the
/// JSON produced by `train_em`. Returns a JSON array of `[a, b, score]`
/// triples for pairs above the link threshold.
pub fn score_probabilistic(
    rows_json: &str,
    matchkey_json: &str,
    em_result_json: &str,
) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let result: Result<String, BridgeError> = (|| {
            let schemas = py.import("goldenmatch.config.schemas")?;
            let prob = py.import("goldenmatch.core.probabilistic")?;
            let json_mod = py.import("json")?;

            let df = build_probabilistic_frame(py, rows_json)?;
            let mk_cls = schemas.getattr("MatchkeyConfig")?;
            let mk = mk_cls.call_method1("model_validate_json", (matchkey_json,))?;

            let em_cls = prob.getattr("EMResult")?;
            let em_dict = json_mod.call_method1("loads", (em_result_json,))?;
            let em_kwargs = em_dict.downcast::<PyDict>().map_err(|e| {
                BridgeError::PythonRuntime(format!("em_result_json must be a JSON object: {}", e))
            })?;
            let em = em_cls.call((), Some(em_kwargs))?;

            let pairs = prob.call_method1("score_probabilistic", (df, mk, em))?;
            // pairs is an iterable of (a, b, score) tuples -> JSON array of arrays.
            let out = pyo3::types::PyList::empty(py);
            for p in pairs.try_iter()? {
                let p = p?;
                let triple = pyo3::types::PyTuple::new(
                    py,
                    [p.get_item(0)?, p.get_item(1)?, p.get_item(2)?],
                )?;
                out.append(triple)?;
            }
            py_json_dumps(py, out.into_any())
        })();
        Ok(result.unwrap_or_else(|e| error_json(&e.to_string())))
    })
}

// ─── GoldenFlow transforms (mirrors duckdb/goldenflow.py) ────────────────
//
// Single-value wrappers over `goldenflow.transforms.get_transform`, exposing
// the same 8 transforms the DuckDB `goldenflow_*` UDFs register. The Postgres
// extension layers 8 fixed-name scalar functions on top of this one generic
// bridge fn.
//
// Fail-open contract (identical to the DuckDB UDF in
// `goldenmatch_duckdb/goldenflow.py::_wrap_series_transform`): if goldenflow
// (or polars) isn't importable, or the named transform is missing, or the
// transform errors on the value, we return the *input value unchanged*
// rather than raising. That keeps a `SELECT goldenflow_*(...)` query alive on
// environments that didn't `pip install goldenflow`.

/// Apply a single goldenflow Series-level transform to one string value.
///
/// `transform_name` is the underlying goldenflow registry key (e.g.
/// `email_normalize`); `value` is the single input. Builds a 1-element
/// `pl.Series`, dispatches through `goldenflow`'s transform registry, and
/// unboxes the result -- the cheapest path to byte-equality with the DuckDB
/// sibling and the Python transform itself.
///
/// **Fail-open**: returns `value` unchanged on ImportError / missing transform
/// / any transform error. Never raises a `BridgeError` for those cases.
pub fn goldenflow_transform(transform_name: &str, value: &str) -> Result<String, BridgeError> {
    crate::init()?;

    Python::with_gil(|py| {
        // fail-open closure: on any pyo3 error inside, fall back to the input.
        let applied: Option<String> = (|| -> Result<Option<String>, BridgeError> {
            // Lazy import: goldenflow + polars may be absent. Treat as
            // pass-through (mirror the DuckDB `except ImportError: return value`).
            let pl = match py.import("polars") {
                Ok(m) => m,
                Err(_) => return Ok(None),
            };
            let transforms = match py.import("goldenflow.transforms") {
                Ok(m) => m,
                Err(_) => return Ok(None),
            };

            let info = transforms.call_method1("get_transform", (transform_name,))?;
            if info.is_none() {
                return Ok(None); // unknown transform -> pass through
            }

            let mode: String = info.getattr("mode")?.extract()?;
            let func = info.getattr("func")?;

            // series = pl.Series([value])
            let values = pyo3::types::PyList::new(py, [value])?;
            let series = pl.call_method1("Series", (values,))?;

            let out = match mode.as_str() {
                "series" => func.call1((series,))?,
                "expr" => {
                    // expr-mode transforms take a column name; build a tiny
                    // 1-col frame, apply, extract the column (mirror DuckDB).
                    let frame_data = PyDict::new(py);
                    frame_data.set_item("v", series)?;
                    let df = pl.call_method1("DataFrame", (frame_data,))?;
                    let expr = func.call1(("v",))?;
                    let aliased = expr.call_method1("alias", ("v",))?;
                    let df = df.call_method1("with_columns", (aliased,))?;
                    df.get_item("v")?
                }
                // dataframe-mode -- not applicable to single-value calls.
                _ => return Ok(None),
            };

            let result = out.get_item(0)?;
            if result.is_none() {
                // goldenflow produced NULL for this value -> pass input through.
                // (Postgres side is STRICT, so a non-NULL in never maps to a
                // NULL out via this path; preserve the input to be safe.)
                return Ok(None);
            }
            let s: String = py.import("builtins")?.call_method1("str", (result,))?.extract()?;
            Ok(Some(s))
        })()
        .unwrap_or(None);

        Ok(applied.unwrap_or_else(|| value.to_string()))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Handle a failed bridge call. CI sets `GOLDENMATCH_BRIDGE_REQUIRE_PY=1` so
    /// a failure of the embedded-Python / goldenmatch call is a HARD test failure
    /// -- the bridge marshalling surface must actually be exercised, not silently
    /// skipped (these tests self-skipped before, so the whole bridge was
    /// effectively untested in CI). Locally the var is unset, so a missing
    /// `goldenmatch` package prints a skip notice and the test passes, keeping
    /// `cargo test` usable on a dev box without the package installed.
    fn require_or_skip(err: BridgeError, what: &str) {
        if std::env::var("GOLDENMATCH_BRIDGE_REQUIRE_PY").as_deref() == Ok("1") {
            panic!("{what}: goldenmatch required in CI but the bridge call failed: {err}");
        }
        eprintln!("Skipping {what} (goldenmatch not installed): {err}");
    }

    #[test]
    fn test_memory_stats_empty_store() {
        // Exercises the full pyo3 path: MemoryStore open, count_corrections,
        // last_learn_time, get_all_adjustments, JSON serialize. A fresh path
        // yields zero counts.
        let dir = std::env::temp_dir().join(format!("gm-mem-{}", std::process::id()));
        let path = dir.join("memory.db");
        let path_s = path.to_string_lossy();
        match memory_stats(Some(&path_s)) {
            Ok(json) => {
                assert!(json.contains("\"total_corrections\": 0"), "got: {}", json);
                assert!(json.contains("\"adjustments\": []"), "got: {}", json);
                assert!(json.contains("\"last_learn_time\": null"), "got: {}", json);
            }
            Err(e) => require_or_skip(e, "memory_stats_empty_store"),
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_memory_learn_below_threshold() {
        // No corrections -> learner emits an empty adjustments list.
        let dir = std::env::temp_dir().join(format!("gm-learn-{}", std::process::id()));
        let path = dir.join("memory.db");
        let path_s = path.to_string_lossy();
        match memory_learn(None, Some(&path_s)) {
            Ok(json) => {
                assert!(json.contains("\"count\": 0"), "got: {}", json);
                assert!(json.contains("\"adjustments\": []"), "got: {}", json);
            }
            Err(e) => require_or_skip(e, "memory_learn_below_threshold"),
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_score_strings() {
        match score_strings("John Smith", "Jon Smyth", "jaro_winkler") {
            Ok(score) => {
                assert!(score > 0.7);
                assert!(score < 1.0);
            }
            Err(e) => require_or_skip(e, "score_strings"),
        }
    }

    #[test]
    fn test_score_strings_exact() {
        match score_strings("hello", "hello", "exact") {
            Ok(score) => assert_eq!(score, 1.0),
            Err(e) => require_or_skip(e, "score_strings_exact"),
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
                assert!(!result.stats_json.is_empty());
                // Structured clusters come from `dedupe_clusters`, not a JSON blob here.
            }
            Err(e) => require_or_skip(e, "dedupe_basic"),
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
            Err(e) => require_or_skip(e, "score_pair"),
        }
    }
}
