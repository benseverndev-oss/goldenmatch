//! Assert the Rust kernel reproduces the shared golden-vector fixture generated
//! from the Python reference (`goldenmatch/core/perceptual.py`). This is the
//! cross-language parity anchor: Python and Rust both check against the same
//! `perceptual_golden.json`, so the implementations cannot drift.

use goldenmatch_perceptual_core::{fingerprint_audio, phash_image};

fn fixture_path() -> std::path::PathBuf {
    // CARGO_MANIFEST_DIR = packages/rust/extensions/perceptual-core
    std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../python/goldenmatch/tests/fixtures/perceptual_golden.json")
}

#[test]
fn rust_reproduces_image_fixture() {
    let raw = std::fs::read_to_string(fixture_path()).expect("read golden fixture");
    let fx: serde_json::Value = serde_json::from_str(&raw).expect("parse golden fixture");
    let images = fx["images"].as_array().expect("images array");
    assert!(images.len() >= 3, "fixture should have image coverage");

    for img in images {
        let name = img["name"].as_str().unwrap();
        let grid: Vec<Vec<f64>> = img["pixels"]
            .as_array()
            .unwrap()
            .iter()
            .map(|row| {
                row.as_array()
                    .unwrap()
                    .iter()
                    .map(|v| v.as_f64().unwrap())
                    .collect()
            })
            .collect();
        let got = phash_image(&grid);
        assert_eq!(
            format!("{got:#x}"),
            img["phash"].as_str().unwrap(),
            "phash drift for {name}"
        );
    }
}

#[test]
fn rust_reproduces_audio_fixture() {
    let raw = std::fs::read_to_string(fixture_path()).expect("read golden fixture");
    let fx: serde_json::Value = serde_json::from_str(&raw).expect("parse golden fixture");
    let scale = fx["pcm_scale"].as_f64().unwrap();
    let audio = fx["audio"].as_array().expect("audio array");
    assert!(audio.len() >= 2, "fixture should have audio coverage");

    for aud in audio {
        let name = aud["name"].as_str().unwrap();
        let sample_rate = aud["sample_rate"].as_u64().unwrap() as u32;
        let samples: Vec<f64> = aud["pcm16"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_f64().unwrap() / scale)
            .collect();
        let got: Vec<String> = fingerprint_audio(&samples, sample_rate)
            .iter()
            .map(|w| format!("{w:#x}"))
            .collect();
        let want: Vec<String> = aud["fingerprint"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap().to_string())
            .collect();
        assert_eq!(got, want, "fingerprint drift for {name}");
    }
}
