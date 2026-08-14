//! wasm-bindgen wrapper over `infermap-core`. The TS analogue of the
//! `infermap-native` pyo3 crate: a thin JSON-boundary shim delegating to
//! `detect_domain` so the TS surface is byte-identical to Python + the Rust FFI.
//!
//! Boundary: `detect_domain_json(input_json) -> output_json`, crossed ONCE per
//! call (the perf-audit lesson: boundary cost dwarfs the kernel). serde DTOs live
//! here; `infermap-core` stays serde-free.

use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
struct DetectInput {
    columns: Vec<String>,
    domains: Vec<(String, Vec<String>)>,
    min_score: f64,
}

#[derive(Serialize)]
struct DetectOutput {
    domain: Option<String>,
    score: f64,
    runner_up: Option<String>,
    runner_up_score: f64,
    reason: String,
}

/// Host-testable core of the boundary. Parses the resolved detect input, calls
/// the pyo3-free kernel, serializes the Detection. Panics on malformed JSON
/// (the TS caller always sends well-formed input built from typed values).
pub fn detect_domain_json_impl(input_json: &str) -> String {
    let inp: DetectInput =
        serde_json::from_str(input_json).expect("valid detect input json");
    let d = infermap_core::detect_domain(&inp.columns, &inp.domains, inp.min_score);
    let out = DetectOutput {
        domain: d.domain,
        score: d.score,
        runner_up: d.runner_up,
        runner_up_score: d.runner_up_score,
        reason: d.reason,
    };
    serde_json::to_string(&out).expect("serialize detect output")
}

#[derive(Deserialize)]
struct LayerRoleInput {
    name: String,
    kind: String,
    name_hints: Vec<String>,
    typical_type_hints: Vec<String>,
}

#[derive(Deserialize)]
struct LayersInput {
    columns: Vec<String>,
    roles: Vec<LayerRoleInput>,
    type_hints: Vec<String>,
    min_score: f64,
}

#[derive(Serialize)]
struct LayerOutput {
    role: String,
    kind: String,
    columns: Vec<String>,
    score: f64,
    reason: String,
    qualifier: String,
    positions: Vec<String>,
    role_matched: bool,
    type_corroboration: f64,
}

#[derive(Serialize)]
struct LayersOutput {
    layers: Vec<LayerOutput>,
    unassigned: Vec<String>,
}

/// Host-testable core of the identity-layers boundary. The TS host resolves the
/// domain pack into plain lists; this crosses JSON once and delegates to the
/// pyo3-free kernel, so the TS surface is byte-identical to Python + the FFI.
pub fn detect_identity_layers_json_impl(input_json: &str) -> String {
    let inp: LayersInput =
        serde_json::from_str(input_json).expect("valid layers input json");
    let roles: Vec<infermap_core::RoleInput> = inp
        .roles
        .into_iter()
        .map(|r| infermap_core::RoleInput {
            name: r.name,
            kind: r.kind,
            name_hints: r.name_hints,
            typical_type_hints: r.typical_type_hints,
        })
        .collect();
    let d = infermap_core::detect_identity_layers(
        &inp.columns,
        &roles,
        &inp.type_hints,
        inp.min_score,
    );
    let out = LayersOutput {
        layers: d
            .layers
            .into_iter()
            .map(|l| LayerOutput {
                role: l.role,
                kind: l.kind,
                columns: l.columns,
                score: l.score,
                reason: l.reason,
                qualifier: l.qualifier,
                positions: l.positions,
                role_matched: l.role_matched,
                type_corroboration: l.type_corroboration,
            })
            .collect(),
        unassigned: d.unassigned,
    };
    serde_json::to_string(&out).expect("serialize layers output")
}

// wasm-only surface: the free `detect_domain_json` export the TS glue calls.
// Mirrors analysis-wasm's `#[cfg(target_arch="wasm32")] mod` re-export.
#[cfg(target_arch = "wasm32")]
mod wasm {
    use wasm_bindgen::prelude::*;

    #[wasm_bindgen]
    pub fn detect_domain_json(input_json: &str) -> String {
        super::detect_domain_json_impl(input_json)
    }

    #[wasm_bindgen]
    pub fn detect_identity_layers_json(input_json: &str) -> String {
        super::detect_identity_layers_json_impl(input_json)
    }

    #[wasm_bindgen]
    pub fn exact_score(a: &str, b: &str) -> f64 {
        infermap_core::exact_score(a, b)
    }

    #[wasm_bindgen]
    pub fn fuzzy_name_score(a: &str, b: &str) -> f64 {
        infermap_core::fuzzy_name_score(a, b)
    }

    // Option<f64> marshals to `number | undefined` in the glue (abstain).
    #[wasm_bindgen]
    pub fn initialism_score(a: &str, b: &str) -> Option<f64> {
        infermap_core::initialism_score(a, b)
    }

    #[allow(clippy::too_many_arguments)]
    #[wasm_bindgen]
    pub fn profile_score(
        src_dtype: &str,
        tgt_dtype: &str,
        src_null: f64,
        tgt_null: f64,
        src_uniq: f64,
        tgt_uniq: f64,
        src_val_count: f64,
        tgt_val_count: f64,
        src_avg_len: f64,
        tgt_avg_len: f64,
    ) -> f64 {
        infermap_core::profile_score(
            src_dtype, tgt_dtype, src_null, tgt_null, src_uniq, tgt_uniq,
            src_val_count, tgt_val_count, src_avg_len, tgt_avg_len,
        )
    }

    #[wasm_bindgen]
    pub fn pattern_match_types(samples: Vec<String>) -> Vec<u32> {
        infermap_core::pattern_match_types(&samples)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn confident_detect_round_trips() {
        let input = r#"{"columns":["provider_npi","first_name"],
            "domains":[["health",["provider npi"]],["fin",["iban"]]],"min_score":0.3}"#;
        let out = detect_domain_json_impl(input);
        // health scores 1/2=0.5 (provider_npi matches), fin 0 -> confident health.
        assert!(out.contains(r#""domain":"health""#));
        assert!(out.contains(r#""reason":"confident""#));
    }

    #[test]
    fn empty_columns_is_no_data() {
        let input = r#"{"columns":[],"domains":[["h",["x"]]],"min_score":0.3}"#;
        let out = detect_domain_json_impl(input);
        assert!(out.contains(r#""reason":"no_data""#));
        assert!(out.contains(r#""domain":null"#));
    }
}
