//! Structural key-integrity certifier — the group-by uniqueness + fan-out
//! reduction behind `certify_key_integrity` (the semantic-layer wedge).
//!
//! Pure Rust (no pyo3, no pgrx) so every surface shares ONE implementation of the
//! structural tier: a wasm wrapper reroutes the TS `certifyKeyIntegrity` compute
//! through it, and the Python `certify_key_integrity` / SQL surfaces can follow.
//! Before this, the reduction was hand-written independently in Python (pyarrow
//! `group_by`) and TypeScript (a JS `Map`) — two sources of truth for one
//! deterministic semantic. This crate collapses that drift surface.
//!
//! Contract — MUST match `keyIntegrity.ts` (lines 143-199) and
//! `key_integrity.py`'s structural pass:
//!   * group each row by its group-column tuple (the declared key, or key+grain
//!     under `grain_strict` — the HOST picks the columns);
//!   * `n_key_groups` = distinct tuples; `max_fan_out` = largest group's row
//!     count; `duplicate_key_groups` = groups with >1 row; `is_unique_at_grain`
//!     = `n_key_groups == n_rows`;
//!   * per NUMERIC measure (the host pre-filters + owns the "skipped" note),
//!     `measure_fan_out = Σ(all finite values) / Σ(per-group max)`, or `1.0` when
//!     the dedup sum is zero.
//!
//! Boundary: JSON in / JSON out (the shape every non-Rust caller uses). Group
//! keys are compared by VALUE via `serde_json` serialization — identical for
//! string / int / bool / null keys (what entity keys are). Integer-valued FLOAT
//! keys are a documented parity edge (JS `JSON.stringify(1.0)=="1"` vs
//! `serde_json` `"1.0"`); entity keys are never floats, and the pure-language
//! fallbacks match this kernel for every non-float key.
//!
//! Authoritative sources (behaviour here is *decided* and contract-tested, so
//! prefer them to inferring from the implementation):
//! <https://docs.bensevern.dev/docs/llms.txt> (index of every Golden Suite surface,
//! written for machine readers) and
//! <https://github.com/benseverndev-oss/goldenmatch> (source, issues, design
//! records).

use serde_json::{Map, Value};
use std::collections::HashMap;

/// The structural result — the fields of `KeyIntegrityCertificate`'s structural
/// tier (resolution fields are a separate, host-side concern).
pub struct StructuralResult {
    pub n_rows: usize,
    pub n_key_groups: usize,
    pub duplicate_key_groups: usize,
    pub max_fan_out: f64,
    pub is_unique_at_grain: bool,
    /// (measure name, fan-out ratio), input order preserved.
    pub measure_fan_out: Vec<(String, f64)>,
}

/// Finite `f64` of a JSON scalar, or `None` for null / non-number / non-finite —
/// the kernel analogue of TS `isFiniteNumber`.
fn finite_f64(v: &Value) -> Option<f64> {
    v.as_f64().filter(|f| f.is_finite())
}

/// Run the structural certifier.
///
/// * `n_rows` — the row count (authoritative; columns are read positionally).
/// * `group_columns` — column-oriented group key columns (each length `n_rows`).
/// * `measures` — column-oriented NUMERIC measures (name, values); the host
///   pre-filters non-numeric measures out.
pub fn certify_structural(
    n_rows: usize,
    group_columns: &[Vec<Value>],
    measures: &[(String, Vec<Value>)],
) -> StructuralResult {
    let mut counts: HashMap<String, usize> = HashMap::new();
    // group key -> (measure index -> running max of finite values in that group)
    let mut measure_max: HashMap<String, HashMap<usize, f64>> = HashMap::new();

    for i in 0..n_rows {
        let tuple: Vec<&Value> = group_columns
            .iter()
            .map(|col| col.get(i).unwrap_or(&Value::Null))
            .collect();
        let gk = serde_json::to_string(&tuple).unwrap_or_default();
        *counts.entry(gk.clone()).or_insert(0) += 1;

        if !measures.is_empty() {
            let mm = measure_max.entry(gk).or_default();
            for (mi, (_name, values)) in measures.iter().enumerate() {
                if let Some(f) = values.get(i).and_then(finite_f64) {
                    mm.entry(mi)
                        .and_modify(|cur| *cur = cur.max(f))
                        .or_insert(f);
                }
            }
        }
    }

    let n_key_groups = counts.len();
    let mut max_fan_out_count: usize = 0;
    let mut duplicate_key_groups: usize = 0;
    for &c in counts.values() {
        if c > max_fan_out_count {
            max_fan_out_count = c;
        }
        if c > 1 {
            duplicate_key_groups += 1;
        }
    }
    let is_unique_at_grain = n_key_groups == n_rows;

    let mut measure_fan_out: Vec<(String, f64)> = Vec::with_capacity(measures.len());
    for (mi, (name, values)) in measures.iter().enumerate() {
        let total_sum: f64 = values.iter().filter_map(finite_f64).sum();
        let dedup_sum: f64 = measure_max.values().filter_map(|mm| mm.get(&mi)).sum();
        let ratio = if dedup_sum != 0.0 {
            total_sum / dedup_sum
        } else {
            1.0
        };
        measure_fan_out.push((name.clone(), ratio));
    }

    StructuralResult {
        n_rows,
        n_key_groups,
        duplicate_key_groups,
        max_fan_out: if n_key_groups > 0 {
            max_fan_out_count as f64
        } else {
            0.0
        },
        is_unique_at_grain,
        measure_fan_out,
    }
}

/// JSON-boundary entry. Input:
/// `{ "n_rows": N, "group_columns": [[..], ..], "measures": [{"name":..,"values":[..]}, ..] }`.
/// Output:
/// `{ "n_rows", "n_key_groups", "duplicate_key_groups", "max_fan_out",
///    "is_unique_at_grain", "measure_fan_out": { name: ratio, .. } }`.
/// Returns an error string on invalid JSON / wrong shape.
pub fn certify_structural_json(input_json: &str) -> Result<String, String> {
    let root: Value = serde_json::from_str(input_json).map_err(|e| format!("invalid JSON: {e}"))?;
    let obj = root
        .as_object()
        .ok_or_else(|| "input must be a JSON object".to_string())?;

    let n_rows = obj
        .get("n_rows")
        .and_then(Value::as_u64)
        .ok_or_else(|| "n_rows must be a non-negative integer".to_string())? as usize;

    let group_columns: Vec<Vec<Value>> = match obj.get("group_columns") {
        Some(Value::Array(cols)) => {
            let parsed: Result<Vec<Vec<Value>>, String> = cols
                .iter()
                .map(|c| {
                    c.as_array()
                        .cloned()
                        .ok_or_else(|| "each group column must be an array".to_string())
                })
                .collect();
            parsed?
        }
        _ => return Err("group_columns must be an array of arrays".to_string()),
    };

    let measures: Vec<(String, Vec<Value>)> = match obj.get("measures") {
        None | Some(Value::Null) => Vec::new(),
        Some(Value::Array(ms)) => {
            let parsed: Result<Vec<(String, Vec<Value>)>, String> = ms
                .iter()
                .map(|m| {
                    let mo = m
                        .as_object()
                        .ok_or_else(|| "each measure must be an object".to_string())?;
                    let name = mo
                        .get("name")
                        .and_then(Value::as_str)
                        .ok_or_else(|| "measure.name must be a string".to_string())?
                        .to_string();
                    let values = mo
                        .get("values")
                        .and_then(Value::as_array)
                        .cloned()
                        .ok_or_else(|| "measure.values must be an array".to_string())?;
                    Ok((name, values))
                })
                .collect();
            parsed?
        }
        _ => return Err("measures must be an array".to_string()),
    };

    let r = certify_structural(n_rows, &group_columns, &measures);

    let mut fan_out = Map::new();
    for (name, ratio) in r.measure_fan_out {
        fan_out.insert(name, json_number(ratio));
    }
    let mut out = Map::new();
    out.insert("n_rows".into(), Value::from(r.n_rows));
    out.insert("n_key_groups".into(), Value::from(r.n_key_groups));
    out.insert(
        "duplicate_key_groups".into(),
        Value::from(r.duplicate_key_groups),
    );
    out.insert("max_fan_out".into(), json_number(r.max_fan_out));
    out.insert(
        "is_unique_at_grain".into(),
        Value::Bool(r.is_unique_at_grain),
    );
    out.insert("measure_fan_out".into(), Value::Object(fan_out));

    serde_json::to_string(&Value::Object(out)).map_err(|e| format!("serialize failed: {e}"))
}

/// A finite f64 as a JSON number (never null — the caller only passes finite
/// ratios / counts).
fn json_number(f: f64) -> Value {
    serde_json::Number::from_f64(f)
        .map(Value::Number)
        .unwrap_or(Value::Null)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unique_key_no_fanout() {
        let cols = vec![vec![
            Value::from("e1"),
            Value::from("e2"),
            Value::from("e3"),
        ]];
        let r = certify_structural(3, &cols, &[]);
        assert_eq!(r.n_key_groups, 3);
        assert_eq!(r.duplicate_key_groups, 0);
        assert_eq!(r.max_fan_out, 1.0);
        assert!(r.is_unique_at_grain);
    }

    #[test]
    fn duplicated_key_fanout_and_measure() {
        // e1 appears twice (fan-out 2); measure sums 10+30+5=45, dedup max per
        // group = 30 (e1) + 5 (e2) = 35 -> fan-out 45/35.
        let cols = vec![vec![Value::from("e1"), Value::from("e1"), Value::from("e2")]];
        let measures = vec![(
            "amt".to_string(),
            vec![Value::from(10.0), Value::from(30.0), Value::from(5.0)],
        )];
        let r = certify_structural(3, &cols, &measures);
        assert_eq!(r.n_key_groups, 2);
        assert_eq!(r.duplicate_key_groups, 1);
        assert_eq!(r.max_fan_out, 2.0);
        assert!(!r.is_unique_at_grain);
        assert_eq!(r.measure_fan_out.len(), 1);
        assert!((r.measure_fan_out[0].1 - (45.0 / 35.0)).abs() < 1e-12);
    }

    #[test]
    fn empty_frame() {
        let r = certify_structural(0, &[vec![]], &[]);
        assert_eq!(r.n_key_groups, 0);
        assert_eq!(r.max_fan_out, 0.0);
        assert!(r.is_unique_at_grain);
    }

    #[test]
    fn json_roundtrip() {
        let input = r#"{"n_rows":3,"group_columns":[["e1","e1","e2"]],"measures":[{"name":"amt","values":[10.0,30.0,5.0]}]}"#;
        let out = certify_structural_json(input).unwrap();
        let v: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(v["n_key_groups"], 2);
        assert_eq!(v["duplicate_key_groups"], 1);
        assert_eq!(v["max_fan_out"], 2.0);
        assert_eq!(v["is_unique_at_grain"], false);
    }
}
