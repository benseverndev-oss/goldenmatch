//! GoldenPipe owned planner kernel (pyo3-free).
//!
//! This crate is the single source of truth for GoldenPipe's PLANNER — resolve
//! (ordering + wiring validation), router (skip/abort/insert), the built-in decision
//! predicates, auto_config, and the skip_if predicate. The native PyO3 ext and the
//! WASM surface are thin marshaling shims over these functions; the pure-Python and
//! pure-TS planners are non-authoritative fallbacks that must reproduce these bytes.
//! Execution/IO (the Runner, registry discovery, CSV, Reporter) stays a per-language
//! host and is deliberately NOT here.
pub mod config;
pub mod decisions;
pub mod ir;
pub mod json;
pub mod model;
pub mod planner;
pub mod provenance;
pub mod repair;
pub mod resolve;
pub mod router;
