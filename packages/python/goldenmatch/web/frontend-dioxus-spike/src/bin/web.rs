//! Dioxus/WASM entry point for the ported Sensitivity route.
//!
//!   cargo build --target wasm32-unknown-unknown --features web --bin web
//!   dx serve --features web            # full dev server (proxy /api -> FastAPI)

fn main() {
    dioxus::launch(gm_echarts_spike::dioxus_app::App);
}
