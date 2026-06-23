//! `goldenprofile-wasm` -- wasm-bindgen / C-ABI-shared wrapper over
//! `goldenprofile-core`.
//!
//! The pyo3-free [`resolve_json_impl`] is the marshaling shim the sibling
//! `goldenprofile-cabi` crate and the host parity tests call; the wasm-bindgen
//! `resolve_json` export (wasm32 only) is the thinnest possible adapter on top
//! of it. One core, so WASM/C/Python clusters are identical by construction.

/// Resolve a JSON `ResolveRequest` into a JSON `Resolution`. Pyo3-free and
/// target-agnostic; `Err` is the core's error string. This is the function the
/// C ABI wraps, so the C and WASM surfaces share exactly one code path.
pub fn resolve_json_impl(request: &str) -> Result<String, String> {
    goldenprofile_core::resolve_json(request)
}

#[cfg(target_arch = "wasm32")]
mod wasm {
    use wasm_bindgen::prelude::*;

    /// WASM export: resolve a JSON request string into a JSON result string.
    /// Throws a JS string on error.
    #[wasm_bindgen]
    pub fn resolve_json(request: &str) -> Result<String, JsValue> {
        super::resolve_json_impl(request).map_err(|e| JsValue::from_str(&e))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn impl_resolves_and_matches_core() {
        let req = r#"{"profiles":[
            {"kind":"node","name":"Acme Inc","category":"Company","anchor":"UNKNOWN","attribute":"Anvils"},
            {"kind":"node","name":"Acme","category":"Company","anchor":"UNKNOWN","attribute":"Founded 1900"}
        ]}"#;
        // The wasm shim and the core boundary must produce identical bytes.
        assert_eq!(resolve_json_impl(req), goldenprofile_core::resolve_json(req));
        let v: serde_json::Value =
            serde_json::from_str(&resolve_json_impl(req).unwrap()).unwrap();
        assert_eq!(v["clusters"].as_array().unwrap().len(), 1);
    }
}
