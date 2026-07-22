//! The migration target: the Dioxus (WASM) rewrite of `routes/Sensitivity.tsx`.
//!
//! This module is compiled ONLY under `--features web` on a `wasm32` target
//! (see lib.rs `cfg`), which this sandbox lacks — so it is not exercised by the
//! native `render` binary. It is real, faithful source showing the port, not a
//! sketch: note that it calls the exact same `crate::chart::sensitivity_chart`
//! the native binary does. One chart spec, two render surfaces.
//!
//! Toolchain to actually build/run this (documented in README):
//!   rustup target add wasm32-unknown-unknown
//!   cargo install dioxus-cli
//!   dx serve --features web
//!
//! Deps this feature adds (in Cargo.toml under `web`): dioxus, charming/wasm,
//! gloo-net (fetch), wasm-bindgen-futures (spawn).

use charming::WasmRenderer;
use dioxus::prelude::*;

use crate::{chart::sensitivity_chart, model::SensitivityResponse};

const CHART_ID: &str = "sensitivity-chart";

/// Equivalent of the React route's `useMutation(api.sensitivity)` +
/// `<Sparkline>` + detail `<table>`, in ~one screen of Dioxus.
#[component]
pub fn SensitivityView() -> Element {
    // Reactive resource = TanStack `useQuery`/`useMutation`. Re-runs when its
    // reactive inputs change; here we fetch once. The FastAPI backend is
    // unchanged — same `/api/v1/sensitivity` contract the React app hits.
    let resp = use_resource(|| async move { fetch_sensitivity().await });

    // After the response lands AND the target <div> is in the DOM, mount the
    // ECharts instance. WasmRenderer drives the real echarts.js in the browser,
    // so this is genuine interactive ECharts — tooltip/legend/dataZoom included.
    use_effect(move || {
        if let Some(Ok(data)) = resp.read().as_ref() {
            let chart = sensitivity_chart(data);
            // 1000x560 to match the HtmlRenderer spike; charming mounts into
            // the element with id = CHART_ID (must already be rendered below).
            let renderer = WasmRenderer::new(1000, 560);
            let _ = renderer.render(CHART_ID, &chart);
        }
    });

    match resp.read().as_ref() {
        None => rsx! { p { class: "text-ink-500", "Sweeping…" } },
        Some(Err(e)) => rsx! { p { class: "text-red-700 font-mono", "↳ {e}" } },
        Some(Ok(data)) => {
            let data = data.clone();
            rsx! {
                section { class: "card px-5 py-4 mb-8",
                    p { class: "eyebrow mb-3", "cluster count vs {data.field}" }
                    // ECharts mounts here (replaces the hand-rolled <svg> sparkline).
                    div { id: CHART_ID, style: "width:1000px;height:560px" }
                }
                DetailTable { points: data.points.clone() }
            }
        }
    }
}

/// Per-point detail table — the same columns as the React route. This is the
/// "grid ergonomics" reference: for the row counts this endpoint returns
/// (a handful of sweep points, and server-capped tables elsewhere) a plain
/// element loop is fine. A large client-side grid would need windowing — the
/// one place Rust's ecosystem is thinner than TanStack Table (see README).
#[component]
fn DetailTable(points: Vec<crate::model::SensitivityPoint>) -> Element {
    rsx! {
        section { class: "card px-5 py-4",
            p { class: "eyebrow mb-3", "per-point detail" }
            table { class: "w-full text-sm",
                thead {
                    tr { class: "text-left eyebrow text-ink-500 border-b border-ink-200",
                        th { class: "py-2 pr-3", "value" }
                        th { class: "py-2 pr-3", "clusters (B)" }
                        th { class: "py-2 pr-3", "TWI" }
                        th { class: "py-2 pr-3", "unchanged" }
                        th { class: "py-2 pr-3", "merged" }
                        th { class: "py-2 pr-3", "partitioned" }
                        th { class: "py-2 pr-3", "overlapping" }
                    }
                }
                tbody {
                    for p in points {
                        tr { key: "{p.value}", class: "border-b border-ink-100 font-mono tabular-nums",
                            td { class: "py-2 pr-3", "{p.value:.4}" }
                            td { class: "py-2 pr-3", "{p.cluster_count_b}" }
                            td { class: "py-2 pr-3", "{p.twi:.3}" }
                            td { class: "py-2 pr-3", "{p.unchanged}" }
                            td { class: "py-2 pr-3", "{p.merged}" }
                            td { class: "py-2 pr-3", "{p.partitioned}" }
                            td { class: "py-2 pr-3", "{p.overlapping}" }
                        }
                    }
                }
            }
        }
    }
}

/// The `lib/api.ts` port: POST the sweep request, decode the same JSON shape.
/// gloo-net is the browser fetch wrapper; on native you'd use reqwest instead.
async fn fetch_sensitivity() -> Result<SensitivityResponse, String> {
    let body = serde_json::json!({
        "field": "threshold",
        "start": 0.70,
        "stop": 0.95,
        "step": 0.05,
        "sample_n": 500
    });
    gloo_net::http::Request::post("/api/v1/sensitivity")
        .json(&body)
        .map_err(|e| e.to_string())?
        .send()
        .await
        .map_err(|e| e.to_string())?
        .json::<SensitivityResponse>()
        .await
        .map_err(|e| e.to_string())
}
