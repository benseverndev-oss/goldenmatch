//! GoldenFlow owned reference kernels (pyo3-free).
//!
//! This crate is the single source of truth for GoldenFlow's transform
//! primitives. The native PyO3 ext (`native-flow`) and, from Wave 0c, the WASM
//! surface (`goldenflow-wasm`) are thin marshaling shims over these functions.
//! The pure-Python / pure-TS transform paths are non-authoritative fallbacks
//! that must reproduce these bytes (asserted by the byte-parity harness).
pub mod address;
pub mod autocorrect;
pub mod categorical;
pub mod email;
pub mod identifiers;
pub mod names;
pub mod numeric;
pub mod phone;
pub mod text;
pub mod url;
