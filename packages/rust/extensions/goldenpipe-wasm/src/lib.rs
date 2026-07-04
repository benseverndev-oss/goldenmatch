//! wasm-bindgen wrapper over `goldenpipe-core::json`. The TS analogue of the
//! goldenpipe-native pyo3 crate: thin shims delegating to `goldenpipe-core`
//! so the planner (resolve/route/decisions/auto_config/skip_if) is
//! byte-identical across Python, native, and TS WASM. All logic lives in
//! `goldenpipe-core` (the reference); this crate only marshals strings across
//! the JS<->WASM boundary.
//!
//! `wasm-bindgen` is a wasm32-only dependency (see Cargo.toml), so the actual
//! `#[wasm_bindgen]` exports live in a `cfg(target_arch = "wasm32")`-gated
//! module — this keeps a plain host `cargo build`/`cargo test` (no wasm
//! target) compiling clean, matching goldenflow-wasm / score-wasm.

#[cfg(target_arch = "wasm32")]
mod wasm {
    use goldenpipe_core::json;
    use wasm_bindgen::prelude::*;

    #[wasm_bindgen]
    pub fn resolve_json(input: &str) -> String {
        json::resolve_json(input)
    }

    #[wasm_bindgen]
    pub fn apply_decision_json(input: &str) -> String {
        json::apply_decision_json(input)
    }

    #[wasm_bindgen]
    pub fn evaluate_builtin_json(input: &str) -> String {
        json::evaluate_builtin_json(input)
    }

    #[wasm_bindgen]
    pub fn auto_config_json(input: &str) -> String {
        json::auto_config_json(input)
    }

    #[wasm_bindgen]
    pub fn skip_if_falsy_json(input: &str) -> String {
        json::skip_if_falsy_json(input)
    }
}
