//! The framework-agnostic chart spec. This is the whole point of the spike:
//! ONE `sensitivity_chart()` builds an ECharts option in Rust, and BOTH
//! surfaces render it — the native `render` binary (HtmlRenderer -> .html) and
//! the Dioxus web component (WasmRenderer -> live DOM). No chart logic is
//! duplicated per surface, and it is real interactive ECharts, not a static
//! SVG sparkline.
//!
//! Compared to today's hand-rolled `<svg>` sparkline (cluster_count only), this
//! adds what SVG can't cheaply do: an axis-triggered tooltip, a toggleable
//! legend, a second y-axis for TWI, and a dataZoom brush — all free from
//! ECharts once the option is built.

use charming::{
    component::{Axis, DataZoom, Grid, Legend, Title},
    element::{AxisType, NameLocation, Tooltip, Trigger},
    series::Line,
    Chart,
};

use crate::model::SensitivityResponse;

pub fn sensitivity_chart(resp: &SensitivityResponse) -> Chart {
    // x categories = the swept parameter values, formatted like the React table.
    let x: Vec<String> = resp.points.iter().map(|p| format!("{:.3}", p.value)).collect();

    let cluster_counts: Vec<f64> = resp.points.iter().map(|p| p.cluster_count_b as f64).collect();
    let twi: Vec<f64> = resp.points.iter().map(|p| p.twi).collect();

    let subtitle = format!(
        "field: {}   ·   sample n = {}   ·   most stable @ {:.3} ({:.1}% unchanged)",
        resp.field,
        resp.sample_n,
        resp.stability.best_value,
        resp.stability.best_unchanged_pct * 100.0,
    );

    Chart::new()
        .title(
            Title::new()
                .text("Parameter sweep — cluster count vs TWI")
                .subtext(subtitle),
        )
        .tooltip(Tooltip::new().trigger(Trigger::Axis))
        .legend(Legend::new().data(vec!["cluster count (B)", "TWI"]).top("bottom"))
        .grid(Grid::new().left("6%").right("6%").bottom("18%").contain_label(true))
        .x_axis(
            Axis::new()
                .type_(AxisType::Category)
                .name(resp.field.clone())
                .name_location(NameLocation::Middle)
                .name_gap(32)
                .data(x),
        )
        // Left axis: cluster count (integer-ish).
        .y_axis(
            Axis::new()
                .type_(AxisType::Value)
                .name("clusters")
                .scale(true),
        )
        // Right axis: TWI, fixed 0..1 so the stability curve is readable.
        .y_axis(
            Axis::new()
                .type_(AxisType::Value)
                .name("TWI")
                .min(0.0)
                .max(1.0),
        )
        .data_zoom(DataZoom::new().start(0).end(100))
        .series(
            Line::new()
                .name("cluster count (B)")
                .y_axis_index(0)
                .smooth(true)
                .data(cluster_counts),
        )
        .series(
            Line::new()
                .name("TWI")
                .y_axis_index(1)
                .smooth(true)
                .data(twi),
        )
}
