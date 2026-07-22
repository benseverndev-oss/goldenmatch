//! gm-echarts-spike: proves the "Apache ECharts, built in Rust" render path for
//! a future Dioxus/WASM port of GoldenMatch's web UI.

pub mod chart;
pub mod model;

#[cfg(all(feature = "web", target_arch = "wasm32"))]
pub mod dioxus_app;
