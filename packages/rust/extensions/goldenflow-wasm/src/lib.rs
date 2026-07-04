//! wasm-bindgen wrapper over `goldenflow-core::identifiers`. The TS analogue
//! of a future native-flow pyo3 crate: thin shims delegating to
//! `goldenflow-core` so identifier validation/formatting is byte-identical
//! across Python, native, and TS WASM. All logic lives in `goldenflow-core`
//! (the reference implementation); this crate only marshals strings across
//! the JS<->WASM boundary.
//!
//! `wasm-bindgen` is a wasm32-only dependency (see Cargo.toml), so the actual
//! `#[wasm_bindgen]` exports live in a `cfg(target_arch = "wasm32")`-gated
//! module — this keeps a plain host `cargo build`/`cargo test` (no wasm
//! target) compiling clean, matching `score-wasm`'s shape.

#[cfg(target_arch = "wasm32")]
mod wasm {
    use goldenflow_core::identifiers::{aba, ean, iban, imei, isbn, luhn, swift, vat};
    use goldenflow_core::names;
    use wasm_bindgen::prelude::*;

    #[wasm_bindgen]
    pub fn cc_validate(s: &str) -> bool {
        luhn::cc_validate(s)
    }

    #[wasm_bindgen]
    pub fn cc_format(s: &str) -> Option<String> {
        luhn::cc_format(s)
    }

    #[wasm_bindgen]
    pub fn cc_mask(s: &str) -> Option<String> {
        luhn::cc_mask(s)
    }

    #[wasm_bindgen]
    pub fn iban_validate(s: &str) -> bool {
        iban::iban_validate(s)
    }

    #[wasm_bindgen]
    pub fn iban_format(s: &str) -> Option<String> {
        iban::iban_format(s)
    }

    #[wasm_bindgen]
    pub fn isbn_validate(s: &str) -> bool {
        isbn::isbn_validate(s)
    }

    #[wasm_bindgen]
    pub fn isbn_normalize(s: &str) -> Option<String> {
        isbn::isbn_normalize(s)
    }

    #[wasm_bindgen]
    pub fn ean_validate(s: &str) -> bool {
        ean::ean_validate(s)
    }

    #[wasm_bindgen]
    pub fn swift_validate(s: &str) -> bool {
        swift::swift_validate(s)
    }

    #[wasm_bindgen]
    pub fn swift_format(s: &str) -> Option<String> {
        swift::swift_format(s)
    }

    #[wasm_bindgen]
    pub fn aba_validate(s: &str) -> bool {
        aba::aba_validate(s)
    }

    #[wasm_bindgen]
    pub fn imei_validate(s: &str) -> bool {
        imei::imei_validate(s)
    }

    #[wasm_bindgen]
    pub fn vat_validate(s: &str) -> bool {
        vat::vat_validate(s)
    }

    #[wasm_bindgen]
    pub fn vat_format(s: &str) -> Option<String> {
        vat::vat_format(s)
    }

    #[wasm_bindgen]
    pub fn name_transliterate(s: &str) -> String {
        names::name_transliterate(s)
    }

    #[wasm_bindgen]
    pub fn name_script(s: &str) -> String {
        names::name_script(s)
    }
}
