//! `goldenmatch-perceptual-wasm` -- wasm-bindgen wrapper over
//! `goldenmatch-perceptual-core`. The TS/WASM analogue of the Python perceptual
//! reference; every op is `(json, ...) -> json`, crossed once per call.
//!
//! The pure `*_impl` fns are `#[cfg]`-independent and host-`rlib`-testable; the
//! `#[cfg(wasm32)]` wrappers map `Err(String)` to a thrown JS error.

use goldenmatch_perceptual_core::{
    audio_ber, fingerprint_audio, hamming, phash_image, radial_variance,
};

type R = Result<String, String>;

/// `(grid_json: f64[][]) -> hex string` of the 64-bit DCT pHash.
pub fn phash_image_impl(grid_json: &str) -> R {
    let grid: Vec<Vec<f64>> = serde_json::from_str(grid_json).map_err(|e| format!("grid: {e}"))?;
    Ok(format!("{:#x}", phash_image(&grid)))
}

/// `(grid_json: f64[][]) -> f64[]` radial-variance feature (JSON).
pub fn radial_variance_impl(grid_json: &str) -> R {
    let grid: Vec<Vec<f64>> = serde_json::from_str(grid_json).map_err(|e| format!("grid: {e}"))?;
    serde_json::to_string(&radial_variance(&grid)).map_err(|e| e.to_string())
}

/// `(samples_json: f64[], sample_rate) -> u32[]` audio fingerprint (JSON).
pub fn fingerprint_audio_impl(samples_json: &str, sample_rate: u32) -> R {
    let samples: Vec<f64> =
        serde_json::from_str(samples_json).map_err(|e| format!("samples: {e}"))?;
    serde_json::to_string(&fingerprint_audio(&samples, sample_rate)).map_err(|e| e.to_string())
}

/// `(a_hex, b_hex) -> bit distance` between two 64-bit pHash hex strings.
pub fn hamming_hex_impl(a_hex: &str, b_hex: &str) -> Result<u32, String> {
    let parse =
        |s: &str| u64::from_str_radix(s.trim_start_matches("0x"), 16).map_err(|e| e.to_string());
    Ok(hamming(parse(a_hex)?, parse(b_hex)?))
}

/// `(a_json: u32[], b_json: u32[]) -> bit-error-rate` between two audio fps.
pub fn audio_ber_impl(a_json: &str, b_json: &str) -> Result<f64, String> {
    let a: Vec<u32> = serde_json::from_str(a_json).map_err(|e| format!("a: {e}"))?;
    let b: Vec<u32> = serde_json::from_str(b_json).map_err(|e| format!("b: {e}"))?;
    Ok(audio_ber(&a, &b))
}

#[cfg(target_arch = "wasm32")]
mod wasm {
    use super::*;
    use wasm_bindgen::prelude::*;

    fn js(r: R) -> Result<String, JsValue> {
        r.map_err(|e| JsValue::from_str(&e))
    }

    #[wasm_bindgen]
    pub fn phash_image_hex(grid_json: &str) -> Result<String, JsValue> {
        js(super::phash_image_impl(grid_json))
    }
    #[wasm_bindgen]
    pub fn radial_variance_json(grid_json: &str) -> Result<String, JsValue> {
        js(super::radial_variance_impl(grid_json))
    }
    #[wasm_bindgen]
    pub fn fingerprint_audio_json(samples_json: &str, sample_rate: u32) -> Result<String, JsValue> {
        js(super::fingerprint_audio_impl(samples_json, sample_rate))
    }
    #[wasm_bindgen]
    pub fn hamming_hex(a_hex: &str, b_hex: &str) -> Result<u32, JsValue> {
        super::hamming_hex_impl(a_hex, b_hex).map_err(|e| JsValue::from_str(&e))
    }
    #[wasm_bindgen]
    pub fn audio_ber_json(a_json: &str, b_json: &str) -> Result<f64, JsValue> {
        super::audio_ber_impl(a_json, b_json).map_err(|e| JsValue::from_str(&e))
    }
}
