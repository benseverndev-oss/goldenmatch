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
use gm_echarts_spike::{
    graph::{identity_graph_chart, resolved_graph_chart},
    model::{IdentityView, ResolvedGraph},
};

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

    // Auto-detect the payload shape: a `{"entities": [...]}` wrapper is the WHOLE
    // resolved graph (multi-entity network); a bare identity view is one entity.
    let looks_multi = serde_json::from_str::<serde_json::Value>(&raw)
        .ok()
        .and_then(|v| v.get("entities").map(|e| e.is_array()))
        .unwrap_or(false);

    let (chart, summary) = if looks_multi {
        let g: ResolvedGraph = serde_json::from_str(&raw)?;
        let recs: usize = g.entities.iter().map(|e| e.records.len()).sum();
        let edges: usize = g.entities.iter().map(|e| e.edges.len()).sum();
        let s = format!(
            "resolved graph: {} entities, {} records, {} evidence edges",
            g.entities.len(),
            recs,
            edges
        );
        (resolved_graph_chart(&g), s)
    } else {
        let view: IdentityView = serde_json::from_str(&raw)?;
        let s = format!(
            "entity {} — {} records, {} evidence edges",
            view.entity_id,
            view.records.len(),
            view.edges.len()
        );
        (identity_graph_chart(&view), s)
    };

    let mut renderer = HtmlRenderer::new(
        "GoldenMatch — identity knowledge graph (ECharts spike)",
        1100,
        720,
    );
    renderer.save(&chart, OUT)?;
    eprintln!("rendered {summary} -> {OUT}");
    Ok(())
}
