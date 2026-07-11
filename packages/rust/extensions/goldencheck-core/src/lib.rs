//! `goldencheck-core` -- pyo3-free deep-profiling kernels for GoldenCheck.
//!
//! Each function is a behaviour-exact replacement for a CPU-bound Python path
//! in the `goldencheck` package's `baseline/`, `drift/`, and `relations/`
//! modules. The Python side selects the native path only when
//! `GOLDENCHECK_NATIVE` opts in (see `goldencheck/core/_native_loader.py`); the
//! pure-Python implementation stays the default and the fallback.
//!
//! Benford, the key/FD kernels, and the fuzzy near-duplicate kernel take Arrow
//! arrays (`&dyn Array` / `&[ArrayRef]`) directly via the shared `arrow_support`
//! module, so the Arrow boundary lives in this pyo3-free core -- the
//! `goldencheck-native` crate only marshals pyarrow<->Arrow for them, it
//! doesn't own the zero-copy read. This mirrors `score-core` / `graph-core` on
//! the goldenmatch side. Each of these also keeps a `_slice`-suffixed twin
//! (over already-interned `&[u64]` / plain `&[String]`) as the entry point for
//! non-Arrow surfaces that call this crate directly: `goldencheck-wasm`
//! (JSON in/out over wasm-bindgen), the `goldenmatch_pg` Postgres extension,
//! and this crate's own `tests/golden.rs` cross-surface fixture.
//!
//! Most kernels compare interned ids by equality only. The denial-constraint
//! kernel (`dc`) is the exception: its columns arrive order-preservingly
//! rank-encoded, so it does ordered `<`/`<=`/`>`/`>=` comparisons over those ids.

mod aggregate;
mod arrow_support;
mod benford;
mod csv_infer;
mod date;
mod dc;
mod fuzzy;
mod keys;
mod regex;
mod sequence;
mod stats;

pub use aggregate::{column_aggregate, dtype_category, ColumnAgg, DtypeCat};
pub use arrow_support::intern_column;
pub use benford::{benford_leading_digits, benford_leading_digits_slice};
pub use csv_infer::{infer_and_type, read_csv_bytes, read_csv_owned_bytes, TypedColumn};
pub use date::str_to_date;
pub use dc::{dc_pair_evidence, dc_row_evidence, Pred};
pub use fuzzy::{near_duplicate_clusters, near_duplicate_clusters_slice};
pub use keys::{
    composite_key_search, composite_key_search_slice, discover_approximate_fds,
    discover_approximate_fds_slice, discover_functional_dependencies,
    discover_functional_dependencies_slice, fd_violation_rows, fd_violation_rows_slice,
    functional_dependency_holds, functional_dependency_holds_slice, tuple_distinct_count,
};
pub use regex::{str_contains_count, str_filter_mask, str_replace_all};
pub use sequence::{sequence_analysis, SeqStats};
pub use stats::{column_numeric_stats, count_outside, NumStats};
