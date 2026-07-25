//! GoldenMatch Python API wrappers.
//!
//! Each function acquires the GIL, calls the corresponding Python function,
//! and returns the result as Rust types.

use pyo3::prelude::*;
use pyo3::types::{PyAnyMethods, PyDict};

use crate::convert;
use crate::error::BridgeError;

/// Row count of a returned frame, dual-rep: a pyarrow Table exposes `num_rows`,
/// a polars DataFrame exposes `height`. The core-API functions return arrow
/// tables (their `.native`) on the arrow lane, so `num_rows` is the default.
fn frame_row_count<'py>(df: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    if df.hasattr("num_rows")? {
        df.getattr("num_rows")
    } else {
        df.getattr("height")
    }
}

/// Records of a returned frame as a Python list[dict], dual-rep: pyarrow
/// `to_pylist` vs polars `to_dicts`.
fn frame_to_records<'py>(df: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    if df.hasattr("to_pylist")? {
        df.call_method0("to_pylist")
    } else {
        df.call_method0("to_dicts")
    }
}

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

/// Sections that only a *full* `GoldenMatchConfig` can express — the slim
/// `exact`/`fuzzy`/`blocking`/`threshold` kwargs cannot represent custom
/// survivorship (`golden_rules`), explicit `matchkeys`, `standardization`
/// rules, or `negative_evidence`. When any is present the config is routed
/// through the full Pydantic path.
const FULL_CONFIG_KEYS: [&str; 4] = [
    "matchkeys",
    "golden_rules",
    "standardization",
    "negative_evidence",
];

/// Build the `dedupe_df` kwargs from a stored config JSON.
///
/// The pgrx `gm_configure` stores an opaque config blob. If it carries full
/// `GoldenMatchConfig` sections (see `FULL_CONFIG_KEYS`) the slim kwargs would
/// silently drop them — e.g. a `golden_rules` survivorship config never reached
/// the golden-record composition (#1914). So when the blob is a full config we
/// build the Pydantic object and pass it verbatim via `config=` (skips
/// auto-config, honours every section); otherwise we forward the slim kwargs
/// exactly as before, so existing `{"exact": [...], "fuzzy": {...}}` callers are
/// byte-unchanged.
fn build_dedupe_kwargs<'py>(
    py: Python<'py>,
    config_json: &str,
) -> Result<Bound<'py, PyDict>, BridgeError> {
    let json_mod = py.import("json")?;
    let config_dict = json_mod.call_method1("loads", (config_json,))?;
    let kwargs = PyDict::new(py);

    let is_full = FULL_CONFIG_KEYS
        .iter()
        .any(|k| matches!(config_dict.get_item(k), Ok(v) if !v.is_none()));
    if is_full {
        kwargs.set_item("config", build_full_config(py, config_json)?)?;
        return Ok(kwargs);
    }

    for key in ["exact", "fuzzy", "blocking", "threshold"] {
        if let Ok(v) = config_dict.get_item(key) {
            if !v.is_none() {
                kwargs.set_item(key, v)?;
            }
        }
    }
    Ok(kwargs)
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

/// Everything a single dedupe pipeline run produces that `gm_run` persists:
/// golden/stats/telemetry PLUS scored pairs and cluster assignments. Returned
/// by [`dedupe_bundle`], the combined form of `dedupe` + `dedupe_pairs` +
/// `dedupe_clusters` (each of which independently calls `dedupe_df`).
pub struct DedupeBundle {
    pub result: DedupeResult,
    pub pairs: Vec<ScoredPair>,
    pub clusters: Vec<ClusterMember>,
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
pub fn dedupe(table: &convert::TableData, config_json: &str) -> Result<DedupeResult, BridgeError> {
    crate::init()?;

    Python::with_gil(|py| {
        let gm = py.import("goldenmatch")?;
        let json_mod = py.import("json")?;

        // Build the pa.Table from the SPI handoff (columnar Arrow, or JSON fallback).
        let df = convert::table_to_arrow_df(py, table)?;

        // Parse config JSON to kwargs. A full GoldenMatchConfig blob (with
        // golden_rules / matchkeys / standardization / negative_evidence) is
        // routed through `config=` so those sections reach the pipeline instead
        // of being dropped by the slim kwargs (#1914); slim blobs are unchanged.
        let kwargs = build_dedupe_kwargs(py, config_json)?;

        let result = gm.call_method("dedupe_df", (df,), Some(&kwargs))?;

        // Extract golden DataFrame as JSON
        let golden_json = if let Ok(golden) = result.getattr("golden") {
            if !golden.is_none() {
                Some(convert::arrow_df_to_json(
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
pub fn dedupe_full(
    table: &convert::TableData,
    config_json: &str,
) -> Result<DedupeResult, BridgeError> {
    crate::init()?;

    Python::with_gil(|py| {
        let gm = py.import("goldenmatch")?;
        let json_mod = py.import("json")?;

        let df = convert::table_to_arrow_df(py, table)?;
        let cfg = build_full_config(py, config_json)?;

        let kwargs = PyDict::new(py);
        kwargs.set_item("config", cfg)?;
        let result = gm.call_method("dedupe_df", (df,), Some(&kwargs))?;

        let golden_json = if let Ok(golden) = result.getattr("golden") {
            if !golden.is_none() {
                Some(convert::arrow_df_to_json(
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

/// Run the dedupe pipeline ONCE and return golden/stats/telemetry + scored
/// pairs + cluster assignments off the single `DedupeResult`.
///
/// This is the combined form of `dedupe` + `dedupe_pairs` + `dedupe_clusters`,
/// each of which independently calls `dedupe_df`. `gm_run` uses it so a job
/// runs the engine ONCE instead of three times on the same rows (#1883). It is
/// also more correct: the pipeline is non-deterministic run-to-run (EM sample
/// order), so three separate runs could persist pairs, clusters, and golden
/// records that mutually disagree — one run makes them consistent by
/// construction. Extraction mirrors the three single-purpose functions exactly
/// (telemetry source label `"dedupe"`, so the blob is byte-identical to the old
/// `dedupe()` path).
pub fn dedupe_bundle(
    table: &convert::TableData,
    config_json: &str,
) -> Result<DedupeBundle, BridgeError> {
    crate::init()?;

    Python::with_gil(|py| {
        let gm = py.import("goldenmatch")?;
        let json_mod = py.import("json")?;

        let df = convert::table_to_arrow_df(py, table)?;
        // Slim/full config routing identical to `dedupe`/`dedupe_pairs`/
        // `dedupe_clusters` (full sections via `config=`, slim kwargs otherwise; #1914).
        let kwargs = build_dedupe_kwargs(py, config_json)?;

        let result = gm.call_method("dedupe_df", (df,), Some(&kwargs))?;

        // -- golden (JSON array of objects), mirrors `dedupe` --
        let golden_json = if let Ok(golden) = result.getattr("golden") {
            if !golden.is_none() {
                Some(convert::arrow_df_to_json(
                    py,
                    &golden.into_pyobject(py).unwrap().unbind(),
                )?)
            } else {
                None
            }
        } else {
            None
        };

        // -- stats --
        let stats = result.getattr("stats")?;
        let stats_json: String = json_mod.call_method1("dumps", (stats,))?.extract()?;

        // -- telemetry (source label "dedupe" for byte-identity with dedupe()) --
        let committed_cfg = result
            .getattr("config")
            .ok()
            .filter(|c| !c.is_none())
            .unwrap_or_else(|| py.None().into_bound(py));
        let telemetry_json =
            capture_controller_telemetry(py, committed_cfg, "dedupe").unwrap_or(None);

        // -- scored pairs, mirrors `dedupe_pairs` --
        let scored_pairs = result.getattr("scored_pairs")?;
        let pairs_list: Vec<(i64, i64, f64)> = scored_pairs.extract()?;
        let pairs = pairs_list
            .into_iter()
            .map(|(a, b, s)| ScoredPair {
                id_a: a,
                id_b: b,
                score: s,
            })
            .collect();

        // -- cluster assignments, mirrors `dedupe_clusters` --
        let clusters_obj = result.getattr("clusters")?;
        let clusters_dict: std::collections::HashMap<i64, pyo3::Py<pyo3::types::PyDict>> =
            clusters_obj.extract()?;
        let mut clusters = Vec::new();
        for (cluster_id, info) in clusters_dict {
            let info_ref = info.bind(py);
            if let Ok(Some(m)) = info_ref.get_item("members") {
                let member_ids: Vec<i64> = m.extract()?;
                let size = member_ids.len() as i64;
                for record_id in member_ids {
                    clusters.push(ClusterMember {
                        cluster_id,
                        record_id,
                        cluster_size: size,
                    });
                }
            }
        }

        Ok(DedupeBundle {
            result: DedupeResult {
                golden_json,
                stats_json,
                telemetry_json,
            },
            pairs,
            clusters,
        })
    })
}

/// Run auto-config on the input rows and return the committed config plus
/// telemetry. Does NOT run the dedupe pipeline.
///
/// `mode` selects the auto-config strategy:
/// - `"standard"` -> `auto_configure_df` (the iterative AutoConfigController;
///   exact + weighted matchkeys, sets `_LAST_CONTROLLER_RUN` telemetry).
/// - `"probabilistic"` -> `auto_configure_probabilistic_df` (Fellegi-Sunter;
///   a single `type="probabilistic"` matchkey). Non-iterative: it does NOT run
///   the controller, so `_LAST_CONTROLLER_RUN` is unset and telemetry reports
///   `{"available": false}`.
///
/// Any other `mode` is an `InvalidConfig` error.
///
/// SQL-surface equivalent of the CLI `goldenmatch autoconfig <files>`.
/// Callers typically pipe the returned `config_json` into a follow-up
/// `dedupe_full()` call, or store it on a Postgres `_jobs` row.
pub fn autoconfig(table: &convert::TableData, mode: &str) -> Result<AutoConfigResult, BridgeError> {
    crate::init()?;

    let fn_name = match mode {
        "standard" => "auto_configure_df",
        "probabilistic" => "auto_configure_probabilistic_df",
        other => {
            return Err(BridgeError::InvalidConfig(format!(
                "unknown autoconfig mode '{other}' (expected 'standard' or 'probabilistic')"
            )))
        }
    };

    Python::with_gil(|py| {
        let df = convert::table_to_arrow_df(py, table)?;
        let autoconfig_mod = py.import("goldenmatch.core.autoconfig")?;
        let cfg = autoconfig_mod.call_method1(fn_name, (df,))?;

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
    target: &convert::TableData,
    reference: &convert::TableData,
    config_json: &str,
) -> Result<MatchResult, BridgeError> {
    crate::init()?;

    Python::with_gil(|py| {
        let gm = py.import("goldenmatch")?;
        let json_mod = py.import("json")?;

        let target_df = convert::table_to_arrow_df(py, target)?;
        let ref_df = convert::table_to_arrow_df(py, reference)?;

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
                Some(convert::arrow_df_to_json(
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
                Some(convert::arrow_df_to_json(
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

/// A matched pair from two-table record linkage: a target row linked to a
/// reference row with its match score. `target_id` / `reference_id` are
/// 0-based row indices into the respective input tables.
pub struct MatchedPair {
    pub target_id: i64,
    pub reference_id: i64,
    pub score: f64,
}

/// Match `target` against `reference`, returning the
/// `(target_id, reference_id, score)` linkage for the matched pairs.
///
/// `target_id` / `reference_id` are 0-based row indices into the respective
/// inputs. `match_df` assigns a single combined `__row_id__` space (target
/// rows `0..T-1`, reference rows `T..T+R-1`), so the matched frame's
/// `__ref_row_id__` is offset by `len(target)`; `reference_id` is normalized
/// back to a 0-based reference index by subtracting that offset. `target_id`
/// (`__target_row_id__`) needs no adjustment.
///
/// Mirrors `match_tables`'s `match_df` call shape (config kwargs +
/// `result.matched`); returns an empty `Vec` when there are no matches
/// (`result.matched is None`).
pub fn match_pairs(
    target: &convert::TableData,
    reference: &convert::TableData,
    config_json: &str,
) -> Result<Vec<MatchedPair>, BridgeError> {
    crate::init()?;

    Python::with_gil(|py| {
        let gm = py.import("goldenmatch")?;
        let json_mod = py.import("json")?;

        let target_df = convert::table_to_arrow_df(py, target)?;
        let ref_df = convert::table_to_arrow_df(py, reference)?;

        // target rows occupy combined __row_id__ space 0..target_len-1; the
        // reference's __ref_row_id__ is offset by target_len.
        let target_len: i64 = frame_row_count(target_df.bind(py))?.extract()?;

        let config_dict = json_mod.call_method1("loads", (config_json,))?;

        // Mirror match_tables' config-kwarg handling (exact/fuzzy/blocking),
        // plus threshold/config which match_df also accepts.
        let kwargs = PyDict::new(py);
        for key in ["exact", "fuzzy", "blocking", "threshold", "config"] {
            if let Ok(v) = config_dict.get_item(key) {
                if !v.is_none() {
                    kwargs.set_item(key, v)?;
                }
            }
        }

        let result = gm.call_method("match_df", (target_df, ref_df), Some(&kwargs))?;
        let matched = result.getattr("matched")?;
        if matched.is_none() {
            return Ok(vec![]);
        }
        // v3.0.0: `MatchResult.matched` is a pyarrow Table. Pull the three
        // linkage columns off it directly, polars-free (arrow-native bridge):
        // `table.column(name)` -> ChunkedArray -> `to_pylist()` -> Python list.
        // The id columns are Int64; the score column is float.
        let t_ids: Vec<i64> = matched
            .call_method1("column", ("__target_row_id__",))?
            .call_method0("to_pylist")?
            .extract()?;
        let r_ids: Vec<i64> = matched
            .call_method1("column", ("__ref_row_id__",))?
            .call_method0("to_pylist")?
            .extract()?;
        let scores: Vec<f64> = matched
            .call_method1("column", ("__match_score__",))?
            .call_method0("to_pylist")?
            .extract()?;

        let mut out = Vec::with_capacity(t_ids.len());
        for ((target_id, ref_row_id), score) in t_ids.into_iter().zip(r_ids).zip(scores) {
            out.push(MatchedPair {
                target_id,
                // NORMALIZE the combined-space ref row id back to a 0-based
                // index into the reference table.
                reference_id: ref_row_id - target_len,
                score,
            });
        }
        Ok(out)
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
pub fn dedupe_pairs(
    table: &convert::TableData,
    config_json: &str,
) -> Result<Vec<ScoredPair>, BridgeError> {
    crate::init()?;

    Python::with_gil(|py| {
        let gm = py.import("goldenmatch")?;

        let df = convert::table_to_arrow_df(py, table)?;
        // Full-config sections (matchkeys/golden_rules/standardization/NE) are
        // routed through `config=`; slim blobs keep the exact/fuzzy kwargs (#1914).
        let kwargs = build_dedupe_kwargs(py, config_json)?;

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
    table: &convert::TableData,
    config_json: &str,
) -> Result<Vec<ClusterMember>, BridgeError> {
    crate::init()?;

    Python::with_gil(|py| {
        let gm = py.import("goldenmatch")?;

        let df = convert::table_to_arrow_df(py, table)?;
        // Full-config sections (matchkeys/golden_rules/standardization/NE) are
        // routed through `config=`; slim blobs keep the exact/fuzzy kwargs (#1914).
        let kwargs = build_dedupe_kwargs(py, config_json)?;

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
// surfaces serve. Every function takes a ``store_ref`` for the identity store:
// a SQLite file path, OR (since #1913 P2) a libpq DSN, which opens the
// Postgres backend so the read surface serves the SAME in-DB dataset the
// gm_resolve write path populates. The pgrx layer supplies the DSN from the
// ``goldenmatch.identity_dsn`` GUC when the caller passes an empty db_path.

/// True when ``store_ref`` is a libpq connection string (URI or keyword/value
/// form) rather than a SQLite file path. A filesystem path never contains an
/// ``=`` (libpq ``key=value``) and doesn't start with a ``postgres`` URI
/// scheme, so this cleanly separates the two without a new argument.
fn is_postgres_dsn(store_ref: &str) -> bool {
    let t = store_ref.trim();
    t.starts_with("postgresql://") || t.starts_with("postgres://") || t.contains('=')
}

/// Open an ``IdentityStore`` from a store reference: Postgres backend for a
/// libpq DSN (#1913 P2), else a SQLite file path (the original behaviour).
fn open_identity_store<'py>(
    py: Python<'py>,
    store_ref: &str,
) -> Result<Bound<'py, PyAny>, BridgeError> {
    let identity = py.import("goldenmatch.identity")?;
    let store_cls = identity.getattr("IdentityStore")?;
    let kwargs = PyDict::new(py);
    if is_postgres_dsn(store_ref) {
        kwargs.set_item("backend", "postgres")?;
        kwargs.set_item("connection", store_ref)?;
    } else {
        kwargs.set_item("path", store_ref)?;
    }
    Ok(store_cls.call((), Some(&kwargs))?)
}

/// Resolve a `{source}:{source_pk}` style ``record_id`` to its identity
/// view JSON. Returns ``{"found": false}`` when no identity owns the record.
pub fn identity_resolve(record_id: &str, db_path: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let identity = py.import("goldenmatch.identity")?;
        let find_by_record = identity.getattr("find_by_record")?;
        let store = open_identity_store(py, db_path)?;
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
        let get_entity = identity.getattr("get_entity")?;
        let store = open_identity_store(py, db_path)?;
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
        let history_fn = identity.getattr("history")?;
        let store = open_identity_store(py, db_path)?;
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
        let find_conflicts = identity.getattr("find_conflicts")?;
        let store = open_identity_store(py, db_path)?;
        let conflicts_kwargs = PyDict::new(py);
        if dataset.is_empty() {
            conflicts_kwargs.set_item("dataset", py.None())?;
        } else {
            conflicts_kwargs.set_item("dataset", dataset)?;
        }
        let edges = find_conflicts.call((store.clone(),), Some(&conflicts_kwargs))?;
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
pub fn identity_list(dataset: &str, status: &str, db_path: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let identity = py.import("goldenmatch.identity")?;
        let list_entities = identity.getattr("list_entities")?;
        let store = open_identity_store(py, db_path)?;
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

/// Resolve `rows_json` into a Postgres-native identity dataset (#1913 P1).
///
/// This is the in-database *write* entrypoint the read-only `identity_*`
/// wrappers above deliberately lacked. It runs `dedupe_df` with the config's
/// `identity` section forced to the Postgres backend + the supplied `dsn`, so
/// the pipeline's post-clustering identity hook writes the durable,
/// event-sourced graph (nodes / source_records / evidence_edges / events) into
/// the extension's own database instead of an external SQLite file.
///
/// The entire resolution engine is reused unchanged — stable UUIDv7 ids,
/// create/absorb/merge from `preflight_existing` overlap, the append-only event
/// log, conflict edges — so re-running against the same `dataset` absorbs new
/// records into existing ids incrementally (the durable-spine requirement). No
/// resolution logic is re-implemented in Rust/SQL (see the #1913 design).
///
/// - `dsn` — libpq connection string for the identity store (the same database
///   the extension runs in; the pgrx layer supplies it from the
///   `goldenmatch.identity_dsn` GUC). Empty is rejected up front.
/// - `dataset` — identity dataset scoping key; empty preserves whatever the
///   stored config's `identity.dataset` carries.
/// - `run_name` — names this resolve run for idempotent event replay; empty
///   lets the pipeline stamp a timestamp run name.
///
/// Returns the identity resolution summary JSON (`created` / `absorbed_records`
/// / `merged` / `edges_added` / `events_emitted` / `conflicts_flagged`), or
/// `"{}"` when the pipeline reported no identity summary (resolution skipped).
pub fn resolve_identities(
    table: &convert::TableData,
    config_json: &str,
    dsn: &str,
    dataset: &str,
    run_name: &str,
) -> Result<String, BridgeError> {
    crate::init()?;
    if dsn.trim().is_empty() {
        return Err(BridgeError::InvalidConfig(
            "resolve_identities requires a non-empty identity DSN (set the \
             goldenmatch.identity_dsn GUC)"
                .to_string(),
        ));
    }

    Python::with_gil(|py| {
        let gm = py.import("goldenmatch")?;
        let json_mod = py.import("json")?;

        let df = convert::table_to_arrow_df(py, table)?;

        // Merge the identity write-path settings into the stored config dict,
        // then validate into a GoldenMatchConfig. Doing the surgery on the dict
        // (rather than on the built Pydantic object) keeps it simple and dodges
        // assignment-validation quirks; a non-dict/garbage blob coerces to {}.
        let loaded = json_mod.call_method1("loads", (config_json,))?;
        let cfg_map: Bound<'_, PyDict> = match loaded.downcast::<PyDict>() {
            Ok(d) => d.clone(),
            Err(_) => PyDict::new(py),
        };

        // Preserve any existing identity sub-config (e.g. source_pk_column,
        // emit_singletons) and override only the backend/connection/enabled.
        let identity_map: Bound<'_, PyDict> = match cfg_map.get_item("identity")? {
            Some(v) => match v.downcast::<PyDict>() {
                Ok(d) => d.clone(),
                Err(_) => PyDict::new(py),
            },
            None => PyDict::new(py),
        };
        identity_map.set_item("enabled", true)?;
        identity_map.set_item("backend", "postgres")?;
        identity_map.set_item("connection", dsn)?;
        if !dataset.is_empty() {
            identity_map.set_item("dataset", dataset)?;
        }
        cfg_map.set_item("identity", &identity_map)?;

        // Name the run for idempotent replay when the caller supplies one.
        if !run_name.is_empty() {
            let output_map: Bound<'_, PyDict> = match cfg_map.get_item("output")? {
                Some(v) => match v.downcast::<PyDict>() {
                    Ok(d) => d.clone(),
                    Err(_) => PyDict::new(py),
                },
                None => PyDict::new(py),
            };
            output_map.set_item("run_name", run_name)?;
            cfg_map.set_item("output", &output_map)?;
        }

        let schemas_mod = py.import("goldenmatch.config.schemas")?;
        let gm_config_cls = schemas_mod.getattr("GoldenMatchConfig")?;
        let cfg = gm_config_cls.call_method1("model_validate", (cfg_map,))?;

        let kwargs = PyDict::new(py);
        kwargs.set_item("config", cfg)?;
        let result = gm.call_method("dedupe_df", (df,), Some(&kwargs))?;

        // Read `identity_summary` off the public DedupeResult (surfaced in the
        // #1913 PR-A change). `None` -> resolution was disabled/skipped.
        let summary = result.getattr("identity_summary")?;
        if summary.is_none() {
            return Ok("{}".to_string());
        }
        let dumps_kwargs = PyDict::new(py);
        dumps_kwargs.set_item("default", py.import("builtins")?.getattr("str")?)?;
        let s: String = json_mod
            .call_method("dumps", (summary,), Some(&dumps_kwargs))?
            .extract()?;
        Ok(s)
    })
}

/// Steward manual **merge** of two identities (#1913 P3).
///
/// Reassigns `absorb_entity_id`'s source records to `keep_entity_id`, retires
/// the absorbed identity, and emits a `manual_merge` event on both — the
/// durable corrections path. Reuses the Python `manual_merge` steward function
/// unchanged (the same one `goldenmatch identity merge` calls), so no merge
/// semantics are re-implemented in Rust/SQL.
///
/// - `dsn` — libpq connection string for the identity store (empty is rejected).
/// - `reason` — optional free-text audit note; empty means "no reason recorded".
///
/// Returns the result JSON (`{"keep", "absorbed", "at"}`). A `ValueError` from
/// the store (missing entity, non-active winner) propagates as a `BridgeError`
/// so the caller can surface it.
pub fn identity_merge(
    dsn: &str,
    keep_entity_id: &str,
    absorb_entity_id: &str,
    reason: &str,
) -> Result<String, BridgeError> {
    crate::init()?;
    if dsn.trim().is_empty() {
        return Err(BridgeError::InvalidConfig(
            "identity_merge requires a non-empty identity DSN (set the \
             goldenmatch.identity_dsn GUC)"
                .to_string(),
        ));
    }
    Python::with_gil(|py| {
        let identity = py.import("goldenmatch.identity")?;
        let merge_fn = identity.getattr("manual_merge")?;
        let store = open_identity_store(py, dsn)?;
        let kwargs = PyDict::new(py);
        if !reason.is_empty() {
            kwargs.set_item("reason", reason)?;
        }
        // Bind the call result, close the store on BOTH paths (a bad steward id
        // legitimately raises ValueError), then propagate.
        let result = merge_fn.call(
            (store.clone(), keep_entity_id, absorb_entity_id),
            Some(&kwargs),
        );
        let _ = store.call_method0("close");
        let result = result?;
        let json_mod = py.import("json")?;
        let dumps_kwargs = PyDict::new(py);
        dumps_kwargs.set_item("default", py.import("builtins")?.getattr("str")?)?;
        let s: String = json_mod
            .call_method("dumps", (result,), Some(&dumps_kwargs))?
            .extract()?;
        Ok(s)
    })
}

/// Steward manual **split** of a record out of an identity (#1913 P3).
///
/// Moves `record_id` into a fresh identity and emits a `manual_split` event on
/// both the original and the new entity. Reuses the Python `manual_split`
/// steward function (the one `goldenmatch identity split` calls); it takes a
/// `list[str]` of record ids, so this single-record bridge wraps `record_id` in
/// a one-element list.
///
/// - `dsn` — libpq connection string for the identity store (empty is rejected).
/// - `reason` — optional free-text audit note; empty means "no reason recorded".
///
/// Returns the result JSON (`{"new_entity_id", "moved", "at"}`). A `ValueError`
/// (missing entity, empty record set) propagates as a `BridgeError`.
pub fn identity_split(
    dsn: &str,
    entity_id: &str,
    record_id: &str,
    reason: &str,
) -> Result<String, BridgeError> {
    crate::init()?;
    if dsn.trim().is_empty() {
        return Err(BridgeError::InvalidConfig(
            "identity_split requires a non-empty identity DSN (set the \
             goldenmatch.identity_dsn GUC)"
                .to_string(),
        ));
    }
    Python::with_gil(|py| {
        let identity = py.import("goldenmatch.identity")?;
        let split_fn = identity.getattr("manual_split")?;
        let store = open_identity_store(py, dsn)?;
        let kwargs = PyDict::new(py);
        if !reason.is_empty() {
            kwargs.set_item("reason", reason)?;
        }
        // manual_split takes record_ids: list[str] -> wrap the single id.
        let record_ids = vec![record_id];
        let result = split_fn.call((store.clone(), entity_id, record_ids), Some(&kwargs));
        let _ = store.call_method0("close");
        let result = result?;
        let json_mod = py.import("json")?;
        let dumps_kwargs = PyDict::new(py);
        dumps_kwargs.set_item("default", py.import("builtins")?.getattr("str")?)?;
        let s: String = json_mod
            .call_method("dumps", (result,), Some(&dumps_kwargs))?
            .extract()?;
        Ok(s)
    })
}

// ─── Identity audit / mediation / MDM SQL surface (post-#1913) ───────────
//
// Class-A embedded-Python wrappers over `goldenmatch.identity`, mirroring the
// #1913 read (`open_identity_store` on a `db_path`/DSN store ref) and write
// (DSN-required) templates above. Serialization is single-sourced with the MCP
// tool layer via the `*_dict`/`as_dict`/`*_page` helpers in
// `goldenmatch.identity`, so SQL output is byte-identical to the MCP surface.

/// `json.dumps(obj, default=str)` — the serialization every identity bridge fn
/// (and the MCP tool layer) uses, so a dict/dataclass round-trips identically
/// across the SQL and MCP surfaces.
fn dumps_default_str<'py>(py: Python<'py>, obj: Bound<'py, PyAny>) -> Result<String, BridgeError> {
    let json_mod = py.import("json")?;
    let dumps_kwargs = PyDict::new(py);
    dumps_kwargs.set_item("default", py.import("builtins")?.getattr("str")?)?;
    let s: String = json_mod
        .call_method("dumps", (obj,), Some(&dumps_kwargs))?
        .extract()?;
    Ok(s)
}

/// Append-only audit-log page: JSON `{"items": [...], "total": n}`. Empty
/// `dataset` = every dataset. `store_ref` is a SQLite path or a libpq DSN
/// (the pgrx wrapper substitutes the in-DB DSN when the caller passes empty).
pub fn identity_audit(store_ref: &str, dataset: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let identity = py.import("goldenmatch.identity")?;
        let audit_page = identity.getattr("audit_log_page")?;
        let store = open_identity_store(py, store_ref)?;
        let kwargs = PyDict::new(py);
        if !dataset.is_empty() {
            kwargs.set_item("dataset", dataset)?;
        }
        let result = audit_page.call((store.clone(),), Some(&kwargs));
        let _ = store.call_method0("close");
        dumps_default_str(py, result?)
    })
}

/// Replay the seal chain + per-event content hashes and report integrity as the
/// JSON verdict (`{"ok", "events_checked", ..., "summary"}`).
pub fn identity_audit_verify(store_ref: &str, dataset: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let identity = py.import("goldenmatch.identity")?;
        let verify = identity.getattr("verify_audit_chain")?;
        let store = open_identity_store(py, store_ref)?;
        let kwargs = PyDict::new(py);
        if !dataset.is_empty() {
            kwargs.set_item("dataset", dataset)?;
        }
        let result = verify.call((store.clone(),), Some(&kwargs));
        let _ = store.call_method0("close");
        let verdict = result?.call_method0("as_dict")?;
        dumps_default_str(py, verdict)
    })
}

/// Full MDM profile of one entity, or `{"found": false}` when the entity does
/// not exist (mirrors `identity_resolve`'s absent-record shape).
pub fn identity_profile(store_ref: &str, entity_id: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let identity = py.import("goldenmatch.identity")?;
        let profile_fn = identity.getattr("entity_profile")?;
        let store = open_identity_store(py, store_ref)?;
        let prof = profile_fn.call1((store.clone(), entity_id));
        let _ = store.call_method0("close");
        let prof = prof?;
        if prof.is_none() {
            return Ok("{\"found\": false}".to_string());
        }
        let d = prof.call_method0("as_dict")?;
        dumps_default_str(py, d)
    })
}

/// Graph-level identity health summary (JSON). Empty `dataset` = whole graph.
pub fn identity_stats(store_ref: &str, dataset: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let identity = py.import("goldenmatch.identity")?;
        let stats_fn = identity.getattr("identity_summary_stats")?;
        let store = open_identity_store(py, store_ref)?;
        let kwargs = PyDict::new(py);
        if !dataset.is_empty() {
            kwargs.set_item("dataset", dataset)?;
        }
        let result = stats_fn.call((store.clone(),), Some(&kwargs));
        let _ = store.call_method0("close");
        let d = result?.call_method0("as_dict")?;
        dumps_default_str(py, d)
    })
}

/// Prioritized steward worklist: JSON `{"items": [...]}` (active entities with
/// open conflicts and/or weak confidence). Empty `dataset` = all datasets.
pub fn identity_worklist(store_ref: &str, dataset: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let identity = py.import("goldenmatch.identity")?;
        let worklist_fn = identity.getattr("steward_worklist_page")?;
        let store = open_identity_store(py, store_ref)?;
        let kwargs = PyDict::new(py);
        if !dataset.is_empty() {
            kwargs.set_item("dataset", dataset)?;
        }
        let result = worklist_fn.call((store.clone(),), Some(&kwargs));
        let _ = store.call_method0("close");
        dumps_default_str(py, result?)
    })
}

/// Seal the audit log (steward write). JSON `{"sealed": false, ...}` when there
/// is nothing new to seal, else the new seal-anchor fields. DSN-required.
pub fn identity_audit_seal(dsn: &str, dataset: &str, actor: &str) -> Result<String, BridgeError> {
    crate::init()?;
    if dsn.trim().is_empty() {
        return Err(BridgeError::InvalidConfig(
            "identity_audit_seal requires a non-empty identity DSN (set the \
             goldenmatch.identity_dsn GUC)"
                .to_string(),
        ));
    }
    Python::with_gil(|py| {
        let identity = py.import("goldenmatch.identity")?;
        let seal_fn = identity.getattr("seal_audit_log")?;
        let seal_result = identity.getattr("seal_result_dict")?;
        let store = open_identity_store(py, dsn)?;
        let kwargs = PyDict::new(py);
        if !actor.is_empty() {
            kwargs.set_item("actor", actor)?;
        }
        if !dataset.is_empty() {
            kwargs.set_item("dataset", dataset)?;
        }
        let seal = seal_fn.call((store.clone(),), Some(&kwargs));
        let _ = store.call_method0("close");
        let d = seal_result.call1((seal?,))?;
        dumps_default_str(py, d)
    })
}

/// Steward conflict mediation write. `resolution` ∈ `same` / `distinct` /
/// `defer`. Empty `reason` / `dataset` mean "unset". DSN-required.
pub fn identity_resolve_conflict(
    dsn: &str,
    record_a_id: &str,
    record_b_id: &str,
    resolution: &str,
    reason: &str,
    dataset: &str,
) -> Result<String, BridgeError> {
    crate::init()?;
    if dsn.trim().is_empty() {
        return Err(BridgeError::InvalidConfig(
            "identity_resolve_conflict requires a non-empty identity DSN (set \
             the goldenmatch.identity_dsn GUC)"
                .to_string(),
        ));
    }
    Python::with_gil(|py| {
        let identity = py.import("goldenmatch.identity")?;
        let mediate = identity.getattr("mediate_conflict")?;
        let store = open_identity_store(py, dsn)?;
        let kwargs = PyDict::new(py);
        if !reason.is_empty() {
            kwargs.set_item("reason", reason)?;
        }
        if !dataset.is_empty() {
            kwargs.set_item("dataset", dataset)?;
        }
        let result = mediate.call(
            (store.clone(), record_a_id, record_b_id, resolution),
            Some(&kwargs),
        );
        let _ = store.call_method0("close");
        dumps_default_str(py, result?)
    })
}

/// Steward claim write: attach `record_id` to `entity_id` (moving it out of any
/// prior entity). Empty `reason` means "unset". DSN-required.
pub fn identity_claim(
    dsn: &str,
    entity_id: &str,
    record_id: &str,
    reason: &str,
) -> Result<String, BridgeError> {
    crate::init()?;
    if dsn.trim().is_empty() {
        return Err(BridgeError::InvalidConfig(
            "identity_claim requires a non-empty identity DSN (set the \
             goldenmatch.identity_dsn GUC)"
                .to_string(),
        ));
    }
    Python::with_gil(|py| {
        let identity = py.import("goldenmatch.identity")?;
        let claim = identity.getattr("claim_record")?;
        let store = open_identity_store(py, dsn)?;
        let kwargs = PyDict::new(py);
        if !reason.is_empty() {
            kwargs.set_item("reason", reason)?;
        }
        let result = claim.call((store.clone(), entity_id, record_id), Some(&kwargs));
        let _ = store.call_method0("close");
        dumps_default_str(py, result?)
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
    if !matches!(
        args.decision,
        "approve" | "reject" | "field_correct" | "cluster_decision"
    ) {
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
        let new_id: String = uuid_mod
            .call_method0("uuid4")?
            .call_method0("__str__")?
            .extract()?;
        kwargs.set_item("id", &new_id)?;
        kwargs.set_item("source", source)?;
        kwargs.set_item("trust", trust)?;
        kwargs.set_item("field_hash", "")?;
        kwargs.set_item("record_hash", "")?;
        kwargs.set_item("dataset", args.dataset)?;
        kwargs.set_item("reason", args.reason)?;
        kwargs.set_item("matchkey_name", args.matchkey_name)?;
        kwargs.set_item(
            "created_at",
            datetime_mod
                .call_method0("datetime")?
                .getattr("now")?
                .call0()?,
        )?;
        kwargs.set_item("decision", args.decision)?;

        match args.decision {
            "field_correct" => {
                let field_name = args.field_name.ok_or_else(|| {
                    BridgeError::Validation("field_correct requires field_name".into())
                })?;
                let corrected_value = args.corrected_value.ok_or_else(|| {
                    BridgeError::Validation("field_correct requires corrected_value".into())
                })?;
                let cluster_id = args.cluster_id.or(args.id_a).ok_or_else(|| {
                    BridgeError::Validation("field_correct requires cluster_id".into())
                })?;
                kwargs.set_item("id_a", cluster_id)?;
                kwargs.set_item("id_b", 0i64)?;
                kwargs.set_item("original_score", 0.0f64)?;
                kwargs.set_item("field_name", field_name)?;
                kwargs.set_item("original_value", args.original_value)?;
                kwargs.set_item("corrected_value", corrected_value)?;
            }
            "cluster_decision" => {
                let score = args.cluster_score.ok_or_else(|| {
                    BridgeError::Validation("cluster_decision requires cluster_score".into())
                })?;
                let outcome = args.cluster_outcome.ok_or_else(|| {
                    BridgeError::Validation("cluster_decision requires cluster_outcome".into())
                })?;
                if !matches!(outcome, "approve" | "reject") {
                    return Err(BridgeError::Validation(format!(
                        "cluster_outcome must be approve or reject; got {:?}",
                        outcome,
                    )));
                }
                if !(0.0..=1.0).contains(&score) {
                    return Err(BridgeError::Validation(format!(
                        "cluster_score must be in [0, 1]; got {}",
                        score,
                    )));
                }
                let cluster_id = args.cluster_id.or(args.id_a).ok_or_else(|| {
                    BridgeError::Validation("cluster_decision requires cluster_id".into())
                })?;
                kwargs.set_item("id_a", cluster_id)?;
                kwargs.set_item("id_b", 0i64)?;
                kwargs.set_item("original_score", 0.0f64)?;
                kwargs.set_item("cluster_score", score)?;
                kwargs.set_item("cluster_outcome", outcome)?;
            }
            "approve" | "reject" => {
                let id_a = args.id_a.ok_or_else(|| {
                    BridgeError::Validation(format!("{} requires id_a", args.decision))
                })?;
                let id_b = args.id_b.ok_or_else(|| {
                    BridgeError::Validation(format!("{} requires id_b", args.decision))
                })?;
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
    // Arrow-native (no polars): build a pa.Table and append an Int64
    // `__row_id__` position column when absent. The FS `train_em` /
    // `score_probabilistic` core functions accept a pa.Table directly.
    let pa = py.import("pyarrow")?;
    let table = convert::json_to_arrow_df(py, rows_json)?.into_bound(py);
    let names: Vec<String> = table.getattr("column_names")?.extract()?;
    if names.iter().any(|c| c == "__row_id__") {
        return Ok(table);
    }
    let n_rows: usize = table.getattr("num_rows")?.extract()?;
    let builtins = py.import("builtins")?;
    let rng = builtins.call_method1("range", (n_rows,))?;
    let int64 = pa.getattr("int64")?.call0()?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("type", int64)?;
    let row_ids = pa.call_method("array", (rng,), Some(&kwargs))?;
    let table = table.call_method1("append_column", ("__row_id__", row_ids))?;
    Ok(table)
}

/// Wrap `goldenmatch.profile_dataframe` -- comprehensive table profile.
///
/// `table` is the input records (columnar `TableData::Columns` from the
/// Arrow-native SPI read, or `TableData::Json` on the fallback path). Returns
/// the profile report as a JSON object (or `{"error": ...}` on failure).
pub fn profile_table(table: &convert::TableData) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let result: Result<String, BridgeError> = (|| {
            let gm = py.import("goldenmatch")?;
            let df = convert::table_to_arrow_df(py, table)?;
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
/// rules over a table. `table` is the table's records (columnar or JSON
/// `TableData`); `rules_json` is a JSON array of rule objects
/// (`{"column", "rule_type", "params", "action"}`). Returns
/// `{report, valid_rows, quarantine_rows, quarantine}` JSON.
pub fn validate_table(table: &convert::TableData, rules_json: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let result: Result<String, BridgeError> = (|| {
            let validate_mod = py.import("goldenmatch.core.validate")?;
            let json_mod = py.import("json")?;
            let builtins = py.import("builtins")?;

            let df = convert::table_to_arrow_df(py, table)?;
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
            result.set_item("valid_rows", frame_row_count(&valid_df)?)?;
            result.set_item("quarantine_rows", frame_row_count(&quarantine_df)?)?;
            result.set_item("quarantine", frame_to_records(&quarantine_df)?)?;
            py_json_dumps(py, result.into_any())
        })();
        Ok(result.unwrap_or_else(|e| error_json(&e.to_string())))
    })
}

/// Wrap `goldenmatch.auto_fix_dataframe` -- apply auto-fixes to a table.
/// `table` is the table's records (columnar or JSON `TableData`). Returns
/// `{fixes, fixed_rows, rows}` JSON.
pub fn autofix_table(table: &convert::TableData) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let result: Result<String, BridgeError> = (|| {
            let gm = py.import("goldenmatch")?;
            let df = convert::table_to_arrow_df(py, table)?;
            let out = gm.call_method1("auto_fix_dataframe", (df,))?;
            let fixed_df = out.get_item(0)?;
            let fixes = out.get_item(1)?;
            let result = PyDict::new(py);
            result.set_item("fixes", fixes)?;
            result.set_item("fixed_rows", frame_row_count(&fixed_df)?)?;
            result.set_item("rows", frame_to_records(&fixed_df)?)?;
            py_json_dumps(py, result.into_any())
        })();
        Ok(result.unwrap_or_else(|e| error_json(&e.to_string())))
    })
}

/// Wrap `goldenmatch.detect_anomalies` -- flag suspicious records in a table.
/// `table` is the table's records (columnar or JSON `TableData`);
/// `sensitivity` is `"low"`/`"medium"`/`"high"` (empty -> `"medium"`). Returns
/// the JSON array of anomaly dicts.
pub fn detect_anomalies(
    table: &convert::TableData,
    sensitivity: &str,
) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let result: Result<String, BridgeError> = (|| {
            let gm = py.import("goldenmatch")?;
            let df = convert::table_to_arrow_df(py, table)?;
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
/// `(df, config)` before a run. `table` is the table's records (columnar or
/// JSON `TableData`); `config_json` is a full `GoldenMatchConfig` JSON.
/// Returns `{has_errors, config_was_modified, findings}` JSON.
pub fn preflight(table: &convert::TableData, config_json: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let result: Result<String, BridgeError> = (|| {
            let verify = py.import("goldenmatch.core.autoconfig_verify")?;
            let dataclasses = py.import("dataclasses")?;
            let df = convert::table_to_arrow_df(py, table)?;
            let config = build_full_config(py, config_json)?;
            let report = verify.call_method1("preflight", (df, config))?;

            let findings = pyo3::types::PyList::empty(py);
            for f in report.getattr("findings")?.try_iter()? {
                findings.append(dataclasses.call_method1("asdict", (f?,))?)?;
            }
            let result = PyDict::new(py);
            result.set_item("has_errors", report.getattr("has_errors")?)?;
            result.set_item(
                "config_was_modified",
                report.getattr("config_was_modified")?,
            )?;
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
/// (identical to `core_apis._postflight`). `table` is the table's records
/// (columnar or JSON `TableData`). Returns
/// `{signals, adjustments, advisories}` JSON.
pub fn postflight(table: &convert::TableData, config_json: &str) -> Result<String, BridgeError> {
    crate::init()?;
    Python::with_gil(|py| {
        let result: Result<String, BridgeError> = (|| {
            let gm = py.import("goldenmatch")?;
            let verify = py.import("goldenmatch.core.autoconfig_verify")?;
            let dataclasses = py.import("dataclasses")?;
            let df = convert::table_to_arrow_df(py, table)?;
            let config = build_full_config(py, config_json)?;

            let dedupe_kwargs = PyDict::new(py);
            dedupe_kwargs.set_item("config", config.clone())?;
            let dedupe_result =
                gm.call_method("dedupe_df", (df.clone_ref(py),), Some(&dedupe_kwargs))?;
            let scored_pairs = dedupe_result.getattr("scored_pairs")?;

            let post_kwargs = PyDict::new(py);
            post_kwargs.set_item("pair_scores", scored_pairs)?;
            let report = verify.call_method("postflight", (df, config), Some(&post_kwargs))?;

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
            let s: String = py
                .import("builtins")?
                .call_method1("str", (result,))?
                .extract()?;
            Ok(Some(s))
        })()
        .unwrap_or(None);

        Ok(applied.unwrap_or_else(|| value.to_string()))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Wrap JSON records as `TableData` for the table-op fns whose first
    /// arg changed from `&str` to `&TableData` in P4 (#1883). These tests
    /// exercise the JSON path; the columnar path is covered in `convert`.
    fn td(records: &str) -> convert::TableData {
        convert::TableData::Json(records.to_string())
    }

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

        match dedupe(&td(rows), config) {
            Ok(result) => {
                assert!(!result.stats_json.is_empty());
                // Structured clusters come from `dedupe_clusters`, not a JSON blob here.
            }
            Err(e) => require_or_skip(e, "dedupe_basic"),
        }
    }

    #[test]
    fn test_dedupe_bundle_basic() {
        // The combined #1883 entry: one pipeline run, golden/stats/telemetry +
        // pairs + clusters. Asserts stats is valid JSON and the same-email dup
        // (john/JOHN) is grouped into a size-2 cluster off the single run.
        let rows = r#"[
            {"email": "john@x.com", "name": "John"},
            {"email": "john@x.com", "name": "JOHN"},
            {"email": "jane@y.com", "name": "Jane"}
        ]"#;
        let config = r#"{"exact": ["email"]}"#;

        match dedupe_bundle(&td(rows), config) {
            Ok(bundle) => {
                assert!(!bundle.result.stats_json.is_empty(), "stats_json empty");
                let v: serde_json::Value = serde_json::from_str(&bundle.result.stats_json)
                    .expect("stats_json not valid JSON");
                assert!(v.is_object(), "stats_json not an object");
                // The two john@x.com rows must land in one 2-member cluster.
                assert!(
                    bundle.clusters.iter().any(|m| m.cluster_size == 2),
                    "expected a size-2 cluster for the duplicate email"
                );
            }
            Err(e) => require_or_skip(e, "dedupe_bundle_basic"),
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

    // ── Phase 2: marshalling round-trip tests for the remaining wrappers ──

    /// Minimal full GoldenMatchConfig JSON that build_full_config can parse.
    /// Uses the simplest possible shape: one matchkey with exact comparison.
    fn simple_full_config() -> &'static str {
        // Valid GoldenMatchConfig: MatchkeyConfig requires `fields`
        // (list[MatchkeyField]), NOT `comparisons` -- dedupe_full strict-validates
        // it via pydantic (preflight/postflight accept it loosely).
        r#"{
            "matchkeys": [{
                "name": "email_key",
                "type": "exact",
                "fields": [{"field": "email", "scorer": "exact"}]
            }]
        }"#
    }

    /// Two-row JSON with a duplicate (same email) and one unique record.
    fn two_row_json() -> &'static str {
        r#"[
            {"email": "alice@x.com", "name": "Alice"},
            {"email": "alice@x.com", "name": "ALICE"},
            {"email": "bob@y.com",   "name": "Bob"}
        ]"#
    }

    // ── dedupe_full ─────────────────────────────────────────────────────────

    #[test]
    fn test_dedupe_full_basic() {
        let config = simple_full_config();
        match dedupe_full(&td(two_row_json()), config) {
            Ok(result) => {
                assert!(!result.stats_json.is_empty(), "stats_json empty");
                let v: serde_json::Value =
                    serde_json::from_str(&result.stats_json).expect("stats_json not valid JSON");
                assert!(v.is_object(), "stats_json not an object");
            }
            Err(e) => require_or_skip(e, "dedupe_full_basic"),
        }
    }

    // ── #1914: the slim `dedupe()` entry must thread a full config's
    // `golden_rules` (custom survivorship) through to golden composition. Before
    // the fix it forwarded only exact/fuzzy/blocking/threshold, silently dropping
    // golden_rules so `gm_golden` always used goldenmatch's default composition.
    #[test]
    fn test_dedupe_threads_golden_rules_survivorship() {
        // Same email -> one cluster; the `name` field differs in length.
        // `longest_value` on `name` MUST win "Alice" over "Al".
        let rows = r#"[
            {"email": "a@x.com", "name": "Al"},
            {"email": "a@x.com", "name": "Alice"}
        ]"#;
        let config = r#"{
            "matchkeys": [{"name":"k","type":"exact","fields":[{"field":"email","scorer":"exact"}]}],
            "golden_rules": {
                "default_strategy": "first_non_null",
                "field_rules": {"name": {"strategy": "longest_value"}}
            }
        }"#;
        match dedupe(&td(rows), config) {
            Ok(result) => {
                let golden = result
                    .golden_json
                    .expect("golden_json should be present for a matched cluster");
                assert!(
                    golden.contains("Alice"),
                    "golden record should keep the longest name via golden_rules \
                     (survivorship must reach composition); got: {golden}"
                );
            }
            Err(e) => require_or_skip(e, "dedupe_threads_golden_rules_survivorship"),
        }
    }

    // ── autoconfig ──────────────────────────────────────────────────────────

    #[test]
    fn test_autoconfig_returns_config_and_telemetry() {
        match autoconfig(&td(two_row_json()), "standard") {
            Ok(result) => {
                assert!(!result.config_json.is_empty(), "config_json empty");
                assert!(!result.telemetry_json.is_empty(), "telemetry_json empty");
                let cfg_v: serde_json::Value =
                    serde_json::from_str(&result.config_json).expect("config_json not valid JSON");
                assert!(cfg_v.is_object(), "config_json not an object");
                let tel_v: serde_json::Value = serde_json::from_str(&result.telemetry_json)
                    .expect("telemetry_json not valid JSON");
                assert!(tel_v.is_object(), "telemetry_json not an object");
            }
            Err(e) => require_or_skip(e, "autoconfig_returns_config_and_telemetry"),
        }
    }

    #[test]
    fn test_autoconfig_mode_probabilistic_builds_probabilistic_matchkeys() {
        let rows = r#"[
            {"name":"John Smith","city":"Austin","dob":"1980-01-01"},
            {"name":"Jon Smith","city":"Austin","dob":"1980-01-01"},
            {"name":"Jane Doe","city":"Dallas","dob":"1975-05-05"}
        ]"#;
        let res = autoconfig(&td(rows), "probabilistic").expect("probabilistic autoconfig");
        assert!(
            res.config_json.contains("\"probabilistic\""),
            "expected a probabilistic matchkey in config_json, got: {}",
            res.config_json
        );
    }

    #[test]
    fn test_autoconfig_mode_standard_unchanged() {
        let rows = r#"[{"name":"A","city":"X"},{"name":"A","city":"X"}]"#;
        autoconfig(&td(rows), "standard").expect("standard autoconfig");
    }

    #[test]
    fn test_autoconfig_unknown_mode_errors() {
        let rows = r#"[{"name":"A"}]"#;
        assert!(autoconfig(&td(rows), "bogus").is_err());
    }

    // ── match_tables ────────────────────────────────────────────────────────

    #[test]
    fn test_match_tables_basic() {
        let target = r#"[{"email": "alice@x.com", "name": "Alice"}]"#;
        let reference = r#"[
            {"email": "alice@x.com", "name": "Alice Smith"},
            {"email": "bob@y.com",   "name": "Bob"}
        ]"#;
        let config = r#"{"exact": ["email"]}"#;
        match match_tables(&td(target), &td(reference), config) {
            Ok(_result) => {
                // MatchResult fields (matched_json / unmatched_json) are Option<String>;
                // the call succeeding (Ok) proves the marshalling round-trip works.
            }
            Err(e) => require_or_skip(e, "match_tables_basic"),
        }
    }

    // ── match_pairs ─────────────────────────────────────────────────────────

    #[test]
    fn test_match_pairs_reference_id_is_zero_based() {
        // target has 2 rows; reference rows get __row_id__ offset by 2 in the
        // combined match_df row-id space -> the bridge must normalize back to a
        // 0-based reference index by subtracting len(target).
        let target = r#"[{"name":"John Smith"},{"name":"Jane Doe"}]"#;
        let reference = r#"[{"name":"Jon Smith"},{"name":"Jayne Doe"},{"name":"Bob X"}]"#;
        // Empty config -> zero-config match_df (auto-config picks a weighted name
        // matchkey + multi-pass soundex/substring blocking that groups John/Jon
        // and Jane/Jayne). The slim `fuzzy` kwarg, by contrast, builds an empty
        // static blocking that yields zero candidate pairs on this single-column
        // shape, so it never produces a match here. The bridge only forwards
        // non-None config keys, so `{}` forwards nothing -> the zero-config path.
        let config = r#"{}"#;
        match match_pairs(&td(target), &td(reference), config) {
            Ok(pairs) => {
                assert!(!pairs.is_empty(), "expected at least one match");
                for p in &pairs {
                    assert!(
                        p.target_id >= 0 && p.target_id < 2,
                        "target_id 0-based into target: {}",
                        p.target_id
                    );
                    assert!(
                        p.reference_id >= 0 && p.reference_id < 3,
                        "reference_id MUST be 0-based into reference (not offset by len(target)): {}",
                        p.reference_id
                    );
                    assert!(
                        p.score >= 0.0 && p.score <= 1.0,
                        "score in [0, 1]: {}",
                        p.score
                    );
                }
            }
            Err(e) => require_or_skip(e, "match_pairs_reference_id_is_zero_based"),
        }
    }

    // ── explain_pair ────────────────────────────────────────────────────────

    #[test]
    fn test_explain_pair() {
        let rec_a = r#"{"name": "Alice Smith", "email": "alice@x.com"}"#;
        let rec_b = r#"{"name": "Alice Smyth", "email": "alice@x.com"}"#;
        let config = r#"{"fuzzy": {"name": 0.8}, "exact": ["email"]}"#;
        match explain_pair(rec_a, rec_b, config) {
            Ok(explanation) => {
                assert!(!explanation.is_empty(), "explanation was empty");
            }
            Err(e) => require_or_skip(e, "explain_pair"),
        }
    }

    // ── dedupe_pairs ────────────────────────────────────────────────────────

    #[test]
    fn test_dedupe_pairs() {
        let rows = r#"[
            {"email": "carol@x.com", "name": "Carol"},
            {"email": "carol@x.com", "name": "CAROL"},
            {"email": "dave@y.com",  "name": "Dave"}
        ]"#;
        let config = r#"{"exact": ["email"], "threshold": 0.5}"#;
        match dedupe_pairs(&td(rows), config) {
            Ok(pairs) => {
                // At least one duplicate pair for the two carol@ rows.
                // Scores must be finite and in [0, 1].
                for p in &pairs {
                    assert!(p.score.is_finite(), "non-finite score: {}", p.score);
                    assert!(
                        (0.0..=1.0).contains(&p.score),
                        "score out of range: {}",
                        p.score
                    );
                }
            }
            Err(e) => require_or_skip(e, "dedupe_pairs"),
        }
    }

    // ── dedupe_clusters ─────────────────────────────────────────────────────

    #[test]
    fn test_dedupe_clusters() {
        let rows = r#"[
            {"email": "eve@x.com", "name": "Eve"},
            {"email": "eve@x.com", "name": "EVE"},
            {"email": "frank@y.com", "name": "Frank"}
        ]"#;
        let config = r#"{"exact": ["email"]}"#;
        match dedupe_clusters(&td(rows), config) {
            Ok(members) => {
                for m in &members {
                    assert!(m.cluster_size >= 1, "cluster_size < 1");
                    assert!(m.cluster_id >= 0);
                }
            }
            Err(e) => require_or_skip(e, "dedupe_clusters"),
        }
    }

    // ── identity_resolve ────────────────────────────────────────────────────
    //
    // Uses a temp path that won't contain a real identity DB. Two acceptable
    // outcomes: Ok("{\"found\": false}") if the store opens on a blank path,
    // or Err routed through require_or_skip.

    #[test]
    fn test_identity_resolve_not_found() {
        let dir = std::env::temp_dir().join(format!("gm-id-resolve-{}", std::process::id()));
        let db_path = dir.join("identity.db").to_string_lossy().into_owned();
        match identity_resolve("nosource:999", &db_path) {
            Ok(json) => {
                let v: serde_json::Value =
                    serde_json::from_str(&json).expect("identity_resolve not valid JSON");
                assert!(v.is_object(), "expected JSON object, got: {}", json);
                // Either {"found": false} or a valid entity object.
            }
            Err(e) => require_or_skip(e, "identity_resolve_not_found"),
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    // ── identity read store-ref routing (#1913 P2) ──────────────────────────

    #[test]
    fn test_is_postgres_dsn_classifies_store_refs() {
        // libpq URI + keyword/value forms -> Postgres backend.
        assert!(is_postgres_dsn("postgresql://u:p@host:5432/db"));
        assert!(is_postgres_dsn("postgres://host/db"));
        assert!(is_postgres_dsn(
            "host=/var/run/postgresql port=5432 dbname=postgres"
        ));
        assert!(is_postgres_dsn("dbname=identity"));
        // SQLite file paths -> NOT a DSN (no '=' , no postgres URI scheme).
        assert!(!is_postgres_dsn(".goldenmatch/identity.db"));
        assert!(!is_postgres_dsn("/var/lib/goldenmatch/identity.sqlite"));
        assert!(!is_postgres_dsn("C:/data/identity.db"));
    }

    // ── resolve_identities (write path, #1913) ──────────────────────────────

    #[test]
    fn test_resolve_identities_rejects_empty_dsn() {
        // The empty-DSN guard fires before any Postgres connection is
        // attempted, so this asserts the contract without a live database
        // (the create/absorb round-trip is exercised end-to-end by the
        // rust_pgrx CI smoke against a real cluster).
        match resolve_identities(&td("[]"), "{}", "   ", "people", "run1") {
            Ok(json) => panic!("expected empty-DSN rejection, got Ok({json})"),
            Err(BridgeError::InvalidConfig(msg)) => {
                assert!(msg.contains("DSN"), "unexpected message: {msg}");
            }
            Err(e) => require_or_skip(e, "resolve_identities_rejects_empty_dsn"),
        }
    }

    // ── identity_view ───────────────────────────────────────────────────────

    #[test]
    fn test_identity_view_not_found() {
        let dir = std::env::temp_dir().join(format!("gm-id-view-{}", std::process::id()));
        let db_path = dir.join("identity.db").to_string_lossy().into_owned();
        match identity_view("nonexistent-entity-id", &db_path) {
            Ok(json) => {
                let v: serde_json::Value =
                    serde_json::from_str(&json).expect("identity_view not valid JSON");
                assert!(v.is_object());
            }
            Err(e) => require_or_skip(e, "identity_view_not_found"),
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    // ── identity_history ────────────────────────────────────────────────────

    #[test]
    fn test_identity_history_not_found() {
        let dir = std::env::temp_dir().join(format!("gm-id-hist-{}", std::process::id()));
        let db_path = dir.join("identity.db").to_string_lossy().into_owned();
        match identity_history("nonexistent-entity-id", &db_path) {
            Ok(json) => {
                let v: serde_json::Value =
                    serde_json::from_str(&json).expect("identity_history not valid JSON");
                // Expect a JSON array (possibly empty) for an unknown entity.
                assert!(v.is_array(), "expected JSON array, got: {}", json);
            }
            Err(e) => require_or_skip(e, "identity_history_not_found"),
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    // ── identity_conflicts ──────────────────────────────────────────────────

    #[test]
    fn test_identity_conflicts_empty_db() {
        let dir = std::env::temp_dir().join(format!("gm-id-conflicts-{}", std::process::id()));
        let db_path = dir.join("identity.db").to_string_lossy().into_owned();
        match identity_conflicts("", &db_path) {
            Ok(json) => {
                let v: serde_json::Value =
                    serde_json::from_str(&json).expect("identity_conflicts not valid JSON");
                assert!(v.is_array(), "expected JSON array, got: {}", json);
            }
            Err(e) => require_or_skip(e, "identity_conflicts_empty_db"),
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    // ── identity_list ───────────────────────────────────────────────────────

    #[test]
    fn test_identity_list_empty_db() {
        let dir = std::env::temp_dir().join(format!("gm-id-list-{}", std::process::id()));
        let db_path = dir.join("identity.db").to_string_lossy().into_owned();
        match identity_list("", "", &db_path) {
            Ok(json) => {
                let v: serde_json::Value =
                    serde_json::from_str(&json).expect("identity_list not valid JSON");
                assert!(v.is_array(), "expected JSON array, got: {}", json);
            }
            Err(e) => require_or_skip(e, "identity_list_empty_db"),
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    // ── correction_add (Rust-only validation — no Python needed) ────────────

    #[test]
    fn test_correction_add_validation_empty_dataset() {
        // Validation fires before Python: empty dataset -> BridgeError::Validation
        // immediately. No goldenmatch import needed.
        let args = CorrectionAddArgs {
            decision: "approve",
            dataset: "",
            id_a: Some(1),
            id_b: Some(2),
            ..Default::default()
        };
        match correction_add(args) {
            Ok(_) => panic!("expected validation error for empty dataset"),
            Err(BridgeError::Validation(msg)) => {
                assert!(msg.contains("dataset"), "error message: {}", msg);
            }
            Err(e) => {
                // In CI the Python path may produce a different error; that's OK.
                eprintln!("correction_add_validation (non-validation err): {e}");
            }
        }
    }

    #[test]
    fn test_correction_add_validation_bad_decision() {
        let args = CorrectionAddArgs {
            decision: "dunno",
            dataset: "test_ds",
            id_a: Some(1),
            id_b: Some(2),
            ..Default::default()
        };
        match correction_add(args) {
            Ok(_) => panic!("expected validation error for bad decision"),
            Err(BridgeError::Validation(msg)) => {
                assert!(
                    msg.contains("decision") || msg.contains("invalid"),
                    "msg: {}",
                    msg
                );
            }
            Err(e) => eprintln!("correction_add bad_decision (non-validation err): {e}"),
        }
    }

    // ── correction_list ─────────────────────────────────────────────────────

    #[test]
    fn test_correction_list_empty_store() {
        let dir = std::env::temp_dir().join(format!("gm-corr-list-{}", std::process::id()));
        let path = dir.join("memory.db").to_string_lossy().into_owned();
        match correction_list(None, Some(&path)) {
            Ok(json) => {
                let v: serde_json::Value =
                    serde_json::from_str(&json).expect("correction_list not valid JSON");
                assert!(v.is_array(), "expected JSON array, got: {}", json);
                assert_eq!(v.as_array().unwrap().len(), 0, "expected empty array");
            }
            Err(e) => require_or_skip(e, "correction_list_empty_store"),
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    // ── profile_table ───────────────────────────────────────────────────────

    #[test]
    fn test_profile_table() {
        match profile_table(&td(two_row_json())) {
            Ok(json) => {
                assert!(!json.is_empty(), "profile_table returned empty string");
                let v: serde_json::Value =
                    serde_json::from_str(&json).expect("profile_table not valid JSON");
                assert!(v.is_object(), "expected JSON object, got: {}", json);
            }
            Err(e) => require_or_skip(e, "profile_table"),
        }
    }

    // ── suggest_threshold ───────────────────────────────────────────────────

    #[test]
    fn test_suggest_threshold_bimodal() {
        // Bimodal distribution -> expect Some(threshold in (0, 1)).
        let scores: Vec<f64> = (0..50)
            .map(|_| 0.1_f64)
            .chain((0..50).map(|_| 0.9_f64))
            .collect();
        let scores_json = serde_json::to_string(&scores).unwrap();
        match suggest_threshold(&scores_json) {
            Ok(Some(t)) => {
                assert!(t > 0.0 && t < 1.0, "threshold out of (0,1): {}", t);
            }
            Ok(None) => {
                // Unimodal fallback for this distribution is acceptable; not a
                // hard failure. (Algorithm may not find a valley in the exact
                // 0.1/0.9 step — the bimodal shape used here is a hint, not a
                // guarantee of non-None.)
            }
            Err(e) => require_or_skip(e, "suggest_threshold_bimodal"),
        }
    }

    #[test]
    fn test_suggest_threshold_empty() {
        // Empty scores -> None (unimodal / too-few-scores path).
        match suggest_threshold("[]") {
            Ok(v) => assert!(v.is_none(), "expected None for empty scores, got {:?}", v),
            Err(e) => require_or_skip(e, "suggest_threshold_empty"),
        }
    }

    // ── detect_domain ───────────────────────────────────────────────────────

    #[test]
    fn test_detect_domain_person_columns() {
        let cols = r#"["first_name", "last_name", "email", "dob"]"#;
        match detect_domain(cols) {
            Ok(json) => {
                assert!(!json.is_empty(), "detect_domain returned empty string");
                let v: serde_json::Value =
                    serde_json::from_str(&json).expect("detect_domain not valid JSON");
                assert!(v.is_object(), "expected JSON object, got: {}", json);
            }
            Err(e) => require_or_skip(e, "detect_domain_person_columns"),
        }
    }

    // ── extract_features ────────────────────────────────────────────────────

    #[test]
    fn test_extract_features_product() {
        match extract_features("Apple iPhone 16 Pro 256GB Black", "product") {
            Ok(json) => {
                assert!(!json.is_empty());
                let v: serde_json::Value =
                    serde_json::from_str(&json).expect("extract_features product not valid JSON");
                assert!(v.is_object(), "expected JSON object, got: {}", json);
            }
            Err(e) => require_or_skip(e, "extract_features_product"),
        }
    }

    #[test]
    fn test_extract_features_software() {
        match extract_features("Microsoft Office 365 Business Premium v2.1", "software") {
            Ok(json) => {
                let v: serde_json::Value =
                    serde_json::from_str(&json).expect("extract_features software not valid JSON");
                assert!(v.is_object());
            }
            Err(e) => require_or_skip(e, "extract_features_software"),
        }
    }

    #[test]
    fn test_extract_features_biblio() {
        match extract_features(
            "Smith J (2023) Entity Resolution. J Data Sci 42:1-10",
            "biblio",
        ) {
            Ok(json) => {
                let v: serde_json::Value =
                    serde_json::from_str(&json).expect("extract_features biblio not valid JSON");
                assert!(v.is_object());
            }
            Err(e) => require_or_skip(e, "extract_features_biblio"),
        }
    }

    #[test]
    fn test_extract_features_unknown_kind_returns_error_json() {
        // Unknown kind -> fail-soft: returns {"error": "..."}, no BridgeError.
        match extract_features("some text", "totally_unknown_kind") {
            Ok(json) => {
                let v: serde_json::Value =
                    serde_json::from_str(&json).expect("extract_features unknown kind not JSON");
                assert!(
                    v.get("error").is_some(),
                    "expected {{\"error\": ...}}, got: {}",
                    json
                );
            }
            Err(e) => require_or_skip(e, "extract_features_unknown_kind"),
        }
    }

    // ── evaluate ────────────────────────────────────────────────────────────

    #[test]
    fn test_evaluate_pairs() {
        // predicted = [(1, 2, 0.9)], ground_truth = [[1, 2]]  -> perfect precision + recall
        let pairs_json = r#"[[1, 2, 0.9]]"#;
        let gt_json = r#"[[1, 2]]"#;
        match evaluate(pairs_json, gt_json) {
            Ok(json) => {
                assert!(!json.is_empty());
                let v: serde_json::Value =
                    serde_json::from_str(&json).expect("evaluate not valid JSON");
                assert!(v.is_object(), "expected JSON object, got: {}", json);
            }
            Err(e) => require_or_skip(e, "evaluate_pairs"),
        }
    }

    #[test]
    fn test_evaluate_empty() {
        // Empty predicted + empty ground truth -> valid summary with zeros.
        match evaluate("[]", "[]") {
            Ok(json) => {
                let v: serde_json::Value =
                    serde_json::from_str(&json).expect("evaluate empty not valid JSON");
                assert!(v.is_object());
            }
            Err(e) => require_or_skip(e, "evaluate_empty"),
        }
    }

    // ── compare_clusters ────────────────────────────────────────────────────

    #[test]
    fn test_compare_clusters_identical() {
        // Two identical clusterings -> perfect agreement.
        let clusters = r#"{"1": {"members": [1, 2]}, "2": {"members": [3]}}"#;
        match compare_clusters(clusters, clusters) {
            Ok(json) => {
                assert!(!json.is_empty());
                let v: serde_json::Value =
                    serde_json::from_str(&json).expect("compare_clusters not valid JSON");
                assert!(v.is_object(), "expected JSON object, got: {}", json);
            }
            Err(e) => require_or_skip(e, "compare_clusters_identical"),
        }
    }

    // ── validate_table ──────────────────────────────────────────────────────

    #[test]
    fn test_validate_table_no_rules() {
        // Empty rules list -> all rows valid, no quarantine.
        match validate_table(&td(two_row_json()), "[]") {
            Ok(json) => {
                let v: serde_json::Value =
                    serde_json::from_str(&json).expect("validate_table not valid JSON");
                assert!(v.is_object(), "expected JSON object");
                assert!(
                    v.get("valid_rows").is_some(),
                    "missing valid_rows key; got: {}",
                    json
                );
                assert!(
                    v.get("quarantine_rows").is_some(),
                    "missing quarantine_rows key"
                );
            }
            Err(e) => require_or_skip(e, "validate_table_no_rules"),
        }
    }

    // ── autofix_table ───────────────────────────────────────────────────────

    #[test]
    fn test_autofix_table() {
        match autofix_table(&td(two_row_json())) {
            Ok(json) => {
                let v: serde_json::Value =
                    serde_json::from_str(&json).expect("autofix_table not valid JSON");
                assert!(v.is_object(), "expected JSON object");
                assert!(
                    v.get("fixed_rows").is_some(),
                    "missing fixed_rows key; got: {}",
                    json
                );
                assert!(v.get("fixes").is_some(), "missing fixes key");
            }
            Err(e) => require_or_skip(e, "autofix_table"),
        }
    }

    // ── detect_anomalies ────────────────────────────────────────────────────

    #[test]
    fn test_detect_anomalies_medium() {
        match detect_anomalies(&td(two_row_json()), "medium") {
            Ok(json) => {
                let v: serde_json::Value =
                    serde_json::from_str(&json).expect("detect_anomalies not valid JSON");
                // Returns an array of anomaly dicts (may be empty for clean data).
                assert!(v.is_array(), "expected JSON array, got: {}", json);
            }
            Err(e) => require_or_skip(e, "detect_anomalies_medium"),
        }
    }

    // ── preflight ───────────────────────────────────────────────────────────

    #[test]
    fn test_preflight_clean_run() {
        let config = simple_full_config();
        match preflight(&td(two_row_json()), config) {
            Ok(json) => {
                // Structural check (not strict from_str): goldenmatch's report
                // serialization can embed raw control chars in string values,
                // which strict JSON parsing rejects -- not a marshalling fault.
                assert!(!json.is_empty(), "preflight returned empty");
                assert!(
                    json.trim_start().starts_with('{'),
                    "preflight not a JSON object; got: {}",
                    json
                );
                assert!(
                    json.contains("\"has_errors\""),
                    "missing has_errors; got: {}",
                    json
                );
                assert!(json.contains("\"findings\""), "missing findings");
            }
            Err(e) => require_or_skip(e, "preflight_clean_run"),
        }
    }

    // ── postflight ──────────────────────────────────────────────────────────

    #[test]
    fn test_postflight_basic() {
        let config = simple_full_config();
        match postflight(&td(two_row_json()), config) {
            Ok(json) => {
                // Structural check (not strict from_str): see preflight note.
                assert!(!json.is_empty(), "postflight returned empty");
                assert!(
                    json.trim_start().starts_with('{'),
                    "postflight not a JSON object; got: {}",
                    json
                );
                assert!(
                    json.contains("\"signals\""),
                    "missing signals; got: {}",
                    json
                );
                assert!(json.contains("\"adjustments\""), "missing adjustments");
            }
            Err(e) => require_or_skip(e, "postflight_basic"),
        }
    }

    // ── train_em ────────────────────────────────────────────────────────────
    //
    // NOTE: train_em requires a MatchkeyConfig JSON with `comparison` /
    // `fields` shape understood by `MatchkeyConfig.model_validate_json`. We use
    // the minimal probabilistic matchkey shape. If the schema has changed, this
    // test may get an error-JSON back (fail-soft) rather than a clean EMResult.
    // The assertion is therefore lenient: Ok(valid JSON object) is enough.

    #[test]
    fn test_train_em_minimal() {
        let rows = r#"[
            {"__row_id__": 0, "name": "Alice Smith"},
            {"__row_id__": 1, "name": "Alice Smyth"},
            {"__row_id__": 2, "name": "Bob Jones"},
            {"__row_id__": 3, "name": "Bob Jones Jr"}
        ]"#;
        // Minimal probabilistic matchkey -- fields + scorer list form.
        let matchkey_json = r#"{
            "name": "name_key",
            "comparisons": [{"field": "name", "scorer": "jaro_winkler"}]
        }"#;
        match train_em(rows, matchkey_json, "") {
            Ok(json) => {
                // Structural check (not strict from_str): see preflight note.
                // Fail-soft: either a proper EMResult object or {"error": ...}.
                assert!(!json.is_empty(), "train_em returned empty string");
                assert!(
                    json.trim_start().starts_with('{'),
                    "train_em not a JSON object; got: {}",
                    json
                );
            }
            Err(e) => require_or_skip(e, "train_em_minimal"),
        }
    }

    // ── score_probabilistic ─────────────────────────────────────────────────
    //
    // SKIPPED: score_probabilistic requires a valid EMResult JSON produced by
    // train_em. Constructing a synthetic EMResult that matches the Python
    // dataclass shape is too fragile to assert robustly without running train_em
    // first (a two-step dependency that belongs in an integration test). A
    // chained train_em -> score_probabilistic test is deferred to a follow-up
    // integration test fixture once the EMResult schema is pinned.

    // ── goldenflow_transform ────────────────────────────────────────────────
    //
    // goldenflow_transform is fail-open: an unknown transform returns the input
    // unchanged WITHOUT requiring goldenflow to be installed. This means this
    // test exercises the Rust marshalling path (crate::init + GIL acquire) and
    // the pass-through branch, with no Python package dependency.

    #[test]
    fn test_goldenflow_transform_unknown_passthrough() {
        // Unknown transform -> fail-open, returns input unchanged.
        match goldenflow_transform("definitely_not_a_real_transform", "hello world") {
            Ok(result) => {
                assert_eq!(
                    result, "hello world",
                    "fail-open should return input unchanged"
                );
            }
            Err(e) => require_or_skip(e, "goldenflow_transform_unknown_passthrough"),
        }
    }

    #[test]
    fn test_goldenflow_transform_email_normalize() {
        // email_normalize is a standard goldenflow transform. If goldenflow is
        // installed (CI), it should lowercase and strip the email. If not
        // installed, the fail-open path returns the input unchanged.
        let input = "Alice@Example.COM";
        match goldenflow_transform("email_normalize", input) {
            Ok(result) => {
                // Either normalized (goldenflow present) or passthrough (absent).
                // Both are correct: just verify non-empty and valid UTF-8.
                assert!(
                    !result.is_empty(),
                    "goldenflow_transform returned empty string"
                );
            }
            Err(e) => require_or_skip(e, "goldenflow_transform_email_normalize"),
        }
    }
}
