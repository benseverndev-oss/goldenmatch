//! Full port of `web/frontend/src/routes/Sensitivity.tsx` to Dioxus (WASM).
//!
//! This is the "port one whole route end-to-end, grid included" deliverable —
//! not a component sketch. It covers everything the React route does:
//!   * the parameter form (field select + start/stop/step/sample_n + Sweep)
//!   * loading / error / empty states
//!   * the ECharts chart (via the shared `crate::chart::sensitivity_chart`)
//!   * the stability stat panel
//!   * the per-point detail grid
//!
//! The grid is made **sortable by column** on purpose. The React route's table
//! is static, but TanStack Table (which the harder GM grids use) gives sort/
//! filter/paginate for free — so a static port would understate the real cost.
//! `SortState` + the header click handlers + the comparator below are exactly
//! the hand-rolled bookkeeping you trade for when there's no TanStack Table in
//! Rust/WASM. That is the honest price signal for this exercise.
//!
//! Build: `cargo build --target wasm32-unknown-unknown --features web --bin web`

use charming::WasmRenderer;
use dioxus::prelude::*;

use crate::{chart::sensitivity_chart, model::SensitivityPoint, model::SensitivityResponse};

const CHART_ID: &str = "sensitivity-chart";

/// Sweep field presets — mirrors PRESET_FIELDS in the React route.
const PRESET_FIELDS: &[(&str, &str)] = &[
    ("threshold", "threshold (all fuzzy matchkeys)"),
    ("blocking.max_block_size", "blocking · max_block_size"),
];

#[component]
pub fn App() -> Element {
    // Form state (React: useState per field).
    let mut field = use_signal(|| "threshold".to_string());
    let mut start = use_signal(|| 0.70_f64);
    let mut stop = use_signal(|| 0.95_f64);
    let mut step = use_signal(|| 0.05_f64);
    let mut sample_n = use_signal(|| 500_i64);

    // Request lifecycle (React: useMutation — pending / data / error).
    let mut pending = use_signal(|| false);
    let mut result = use_signal(|| None::<SensitivityResponse>);
    let mut error = use_signal(|| None::<String>);

    let run_sweep = move |_| {
        let body = SweepRequest {
            field: field(),
            start: start(),
            stop: stop(),
            step: step(),
            sample_n: sample_n(),
        };
        pending.set(true);
        error.set(None);
        spawn(async move {
            match fetch_sensitivity(body).await {
                Ok(r) => result.set(Some(r)),
                Err(e) => error.set(Some(e)),
            }
            pending.set(false);
        });
    };

    let n_points = estimate_points(start(), stop(), step());
    let sweep_disabled = pending() || stop() <= start() || step() <= 0.0;

    rsx! {
        div { class: "px-8 py-10 max-w-6xl mx-auto",
            header { class: "mb-8",
                p { class: "eyebrow mb-2", "sensitivity" }
                h1 { class: "display text-3xl text-ink-900", "Parameter sweep" }
                p { class: "mt-2 text-sm text-ink-500 max-w-2xl",
                    "Re-run the pipeline at each value, CCMS-compare the result against a baseline, and chart how the clustering shifts."
                }
            }

            section { class: "card px-5 py-4 mb-8",
                div { class: "grid grid-cols-1 md:grid-cols-[2fr_repeat(4,1fr)_auto] gap-3 items-end",
                    label { class: "block",
                        span { class: "eyebrow block mb-1", "parameter" }
                        select {
                            class: "w-full bg-paper-50 border border-ink-200 rounded px-2 py-1 font-mono text-sm",
                            value: "{field}",
                            onchange: move |e| field.set(e.value()),
                            for (val, lbl) in PRESET_FIELDS {
                                option { key: "{val}", value: "{val}", "{lbl}" }
                            }
                        }
                    }
                    NumField { label: "start", value: start(), step: 0.01, oninput: move |v| start.set(v) }
                    NumField { label: "stop", value: stop(), step: 0.01, oninput: move |v| stop.set(v) }
                    NumField { label: "step", value: step(), step: 0.01, oninput: move |v| step.set(v) }
                    NumField {
                        label: "sample n",
                        value: sample_n() as f64,
                        step: 100.0,
                        oninput: move |v: f64| sample_n.set((v.round() as i64).max(10)),
                    }
                    button {
                        class: "btn btn-primary",
                        disabled: sweep_disabled,
                        onclick: run_sweep,
                        if pending() { "Sweeping…" } else { "Sweep" }
                    }
                }
                if let Some(e) = error() {
                    p { class: "mt-3 text-xs text-red-700 font-mono break-all", "↳ {e}" }
                }
                p { class: "mt-3 text-[11px] text-ink-500",
                    "Each sweep runs the pipeline {n_points + 1} times on a {sample_n} row sample."
                }
            }

            if let Some(res) = result() {
                Results { result: res }
            }
        }
    }
}

#[component]
fn Results(result: SensitivityResponse) -> Element {
    if result.points.is_empty() {
        return rsx! {
            div { class: "card px-6 py-10 text-center",
                p { class: "display text-2xl text-ink-700", "No points returned." }
                p { class: "mt-2 text-sm text-ink-500", "Every sweep value failed — widen the range or check the field name." }
            }
        };
    }

    // Mount ECharts once the result lands and the target div is in the DOM.
    // use_effect re-runs when `result` changes; the shared chart spec is the
    // same `charming::Chart` the native HtmlRenderer binary builds.
    let chart_data = result.clone();
    use_effect(move || {
        let chart = sensitivity_chart(&chart_data);
        let renderer = WasmRenderer::new(1000, 560);
        let _ = renderer.render(CHART_ID, &chart);
    });

    let baseline = result
        .baseline_value
        .map(|v| format!("{v:.4}"))
        .unwrap_or_else(|| "—".to_string());
    let field = result.field.clone();

    rsx! {
        section { class: "grid grid-cols-1 md:grid-cols-[2fr_1fr] gap-6 mb-8",
            div { class: "card px-5 py-4",
                p { class: "eyebrow mb-3", "cluster count vs {field}" }
                // ECharts mounts here (replaces the hand-rolled <svg> sparkline).
                div { id: CHART_ID, style: "width:1000px;height:560px" }
            }
            div { class: "card px-5 py-4",
                p { class: "eyebrow mb-3", "stability" }
                Stat { label: "baseline", value: baseline }
                Stat { label: "most stable value", value: format!("{:.4}", result.stability.best_value) }
                Stat { label: "points", value: result.points.len().to_string() }
                Stat { label: "sample n", value: result.sample_n.to_string() }
            }
        }
        DetailGrid { points: result.points.clone() }
    }
}

/// Sortable per-point detail grid — the "price the grid work" centrepiece.
#[component]
fn DetailGrid(points: Vec<SensitivityPoint>) -> Element {
    // Hand-rolled sort state: the bookkeeping TanStack Table gives you free.
    let mut sort_col = use_signal(|| Col::Value);
    let mut sort_desc = use_signal(|| false);

    let mut rows = points.clone();
    let col = sort_col();
    let desc = sort_desc();
    rows.sort_by(|a, b| {
        let o = col.get(a).partial_cmp(&col.get(b)).unwrap_or(std::cmp::Ordering::Equal);
        if desc { o.reverse() } else { o }
    });

    let mut header = move |c: Col| {
        if sort_col() == c {
            sort_desc.set(!sort_desc());
        } else {
            sort_col.set(c);
            sort_desc.set(false);
        }
    };
    let arrow = move |c: Col| if sort_col() == c { if sort_desc() { " ↓" } else { " ↑" } } else { "" };

    rsx! {
        section { class: "card px-5 py-4",
            p { class: "eyebrow mb-3", "per-point detail" }
            div { class: "overflow-x-auto",
                table { class: "w-full text-sm",
                    thead {
                        tr { class: "text-left eyebrow text-ink-500 border-b border-ink-200",
                            for c in Col::ALL {
                                th {
                                    key: "{c.label()}",
                                    class: "py-2 pr-3 cursor-pointer select-none",
                                    onclick: move |_| header(c),
                                    "{c.label()}{arrow(c)}"
                                }
                            }
                        }
                    }
                    tbody {
                        for p in rows {
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
}

/// Sortable columns. Each maps a row to an f64 sort key — the manual equivalent
/// of TanStack Table's `accessorKey` + `sortingFn`.
#[derive(Clone, Copy, PartialEq)]
enum Col {
    Value,
    ClustersB,
    Twi,
    Unchanged,
    Merged,
    Partitioned,
    Overlapping,
}

impl Col {
    const ALL: [Col; 7] = [
        Col::Value,
        Col::ClustersB,
        Col::Twi,
        Col::Unchanged,
        Col::Merged,
        Col::Partitioned,
        Col::Overlapping,
    ];

    fn label(self) -> &'static str {
        match self {
            Col::Value => "value",
            Col::ClustersB => "clusters (B)",
            Col::Twi => "TWI",
            Col::Unchanged => "unchanged",
            Col::Merged => "merged",
            Col::Partitioned => "partitioned",
            Col::Overlapping => "overlapping",
        }
    }

    fn get(self, p: &SensitivityPoint) -> f64 {
        match self {
            Col::Value => p.value,
            Col::ClustersB => p.cluster_count_b as f64,
            Col::Twi => p.twi,
            Col::Unchanged => p.unchanged as f64,
            Col::Merged => p.merged as f64,
            Col::Partitioned => p.partitioned as f64,
            Col::Overlapping => p.overlapping as f64,
        }
    }
}

#[component]
fn Stat(label: String, value: String) -> Element {
    rsx! {
        div { class: "flex justify-between py-1.5 border-b border-ink-100 last:border-0",
            span { class: "text-sm text-ink-500", "{label}" }
            span { class: "font-mono tabular-nums text-ink-800", "{value}" }
        }
    }
}

#[component]
fn NumField(label: String, value: f64, step: f64, oninput: EventHandler<f64>) -> Element {
    rsx! {
        label { class: "block",
            span { class: "eyebrow block mb-1", "{label}" }
            input {
                r#type: "number",
                class: "w-full bg-paper-50 border border-ink-200 rounded px-2 py-1 font-mono text-sm tabular-nums",
                value: "{value}",
                step: "{step}",
                oninput: move |e| {
                    if let Ok(v) = e.value().parse::<f64>() {
                        oninput.call(v);
                    }
                },
            }
        }
    }
}

fn estimate_points(start: f64, stop: f64, step: f64) -> i64 {
    if step <= 0.0 || stop <= start {
        return 0;
    }
    ((stop - start) / step).floor() as i64
}

/// The `lib/api.ts` port — POST the sweep request, decode the same JSON shape.
struct SweepRequest {
    field: String,
    start: f64,
    stop: f64,
    step: f64,
    sample_n: i64,
}

async fn fetch_sensitivity(req: SweepRequest) -> Result<SensitivityResponse, String> {
    let body = serde_json::json!({
        "field": req.field,
        "start": req.start,
        "stop": req.stop,
        "step": req.step,
        "sample_n": req.sample_n,
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
