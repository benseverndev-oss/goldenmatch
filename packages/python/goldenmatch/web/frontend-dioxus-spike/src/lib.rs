//! gm-echarts-spike: proves the "Apache ECharts, built in Rust" render path for
//! a future Dioxus/WASM port of GoldenMatch's web UI.

pub mod chart;
pub mod graph;
pub mod graph_neighborhood;
pub mod model;

#[cfg(feature = "web")]
pub mod dioxus_app;
