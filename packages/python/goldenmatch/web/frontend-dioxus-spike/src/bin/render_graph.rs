//! Native spike (KG surface): load a real `/api/v1/identities/{id}` response —
//! one resolved identity from GoldenMatch's Identity Graph — and render it to an
//! interactive ECharts force-directed knowledge graph HTML page.
//!
//!   cargo run --bin render_graph                  # baked sample identity
//!   cargo run --bin render_graph -- identity.json # any saved API response
//!   curl -s localhost:5050/api/v1/identities/<id> \
//!       | cargo run --bin render_graph -- -        # or pipe a live response
//!
//! Same file/stdin posture as `render` so the spike runs offline in the sandbox
//! without the FastAPI server up. The `identity_graph_chart` it renders is the
//! exact `charming::Chart` the Dioxus `WasmRenderer` would mount into a DOM node.

use std::io::Read;

use charming::HtmlRenderer;
use gm_echarts_spike::{graph::identity_graph_chart, model::IdentityView};

const SAMPLE: &str = include_str!("../../sample_identity_graph.json");
const OUT: &str = "identity_graph.html";

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

    let view: IdentityView = serde_json::from_str(&raw)?;
    let chart = identity_graph_chart(&view);

    let mut renderer = HtmlRenderer::new(
        "GoldenMatch — identity knowledge graph (ECharts spike)",
        1000,
        640,
    );
    renderer.save(&chart, OUT)?;

    eprintln!(
        "rendered entity {} — {} records, {} evidence edges -> {}",
        view.entity_id,
        view.records.len(),
        view.edges.len(),
        OUT
    );
    Ok(())
}
