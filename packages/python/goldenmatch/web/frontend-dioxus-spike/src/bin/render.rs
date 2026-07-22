//! Native spike: load a real `/api/v1/sensitivity` response (the same JSON the
//! React app consumes) and render it to an interactive ECharts HTML page.
//!
//!   cargo run --bin render                     # uses the baked sample payload
//!   cargo run --bin render -- response.json    # any saved API response
//!   curl -s -XPOST localhost:5050/api/v1/sensitivity -d @req.json \
//!       | cargo run --bin render -- -           # or pipe a live response
//!
//! Swapping the file read for a live GET is one line (see README) — kept as a
//! file/stdin read so the spike runs offline in CI/sandbox without the FastAPI
//! server up.

use std::io::Read;

use charming::HtmlRenderer;
use gm_echarts_spike::{chart::sensitivity_chart, model::SensitivityResponse};

const SAMPLE: &str = include_str!("../../sample_response.json");
const OUT: &str = "sensitivity.html";

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let raw = match std::env::args().nth(1).as_deref() {
        None => SAMPLE.to_string(),
        Some("-") => {
            let mut s = String::new();
            std::io::stdin().read_to_string(&mut s)?;
            s
        }
        Some(path) => std::fs::read_to_string(path)?,
    };

    let resp: SensitivityResponse = serde_json::from_str(&raw)?;
    let chart = sensitivity_chart(&resp);

    // HtmlRenderer emits a self-contained page that loads echarts and renders
    // the option we built in Rust. This is the exact `charming::Chart` the
    // Dioxus WasmRenderer would mount into a DOM node — same spec, two surfaces.
    let mut renderer = HtmlRenderer::new("GoldenMatch — sensitivity (ECharts spike)", 1000, 560);
    renderer.save(&chart, OUT)?;

    eprintln!(
        "rendered {} points ({} field) -> {}",
        resp.points.len(),
        resp.field,
        OUT
    );
    Ok(())
}
