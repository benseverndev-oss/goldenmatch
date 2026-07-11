//! `goldencheck-core` -- pyo3-free deep-profiling kernels for GoldenCheck.
//!
//! Each function is a behaviour-exact replacement for a CPU-bound Python path
//! in the `goldencheck` package's `baseline/`, `drift/`, and `relations/`
//! modules. The Python side selects the native path only when
//! `GOLDENCHECK_NATIVE` opts in (see `goldencheck/core/_native_loader.py`); the
//! pure-Python implementation stays the default and the fallback.
//!
//! Most kernels take plain slices (`&[f64]`, `&[u64]`) so they carry no Python
//! or Arrow types. Benford is the exception (and the model for future
//! conversions): it takes Arrow arrays (`&dyn Array`) directly via the shared
//! `arrow_support` module, so the Arrow boundary lives in this pyo3-free core
//! -- the `goldencheck-native` crate now only marshals pyarrow<->Arrow for it,
//! it doesn't own the zero-copy read. This mirrors `score-core` / `graph-core`
//! on the goldenmatch side.
//!
//! Most kernels compare interned ids by equality only. The denial-constraint
//! kernel (`dc`) is the exception: its columns arrive order-preservingly
//! rank-encoded, so it does ordered `<`/`<=`/`>`/`>=` comparisons over those ids.

mod arrow_support;
mod benford;
mod date;
mod dc;
mod fuzzy;
mod keys;
mod regex;

pub use arrow_support::intern_column;
pub use benford::{benford_leading_digits, benford_leading_digits_slice};
pub use date::str_to_date;
pub use dc::{dc_pair_evidence, dc_row_evidence, Pred};
pub use fuzzy::near_duplicate_clusters;
pub use keys::{
    composite_key_search, discover_approximate_fds, discover_functional_dependencies,
    fd_violation_rows, functional_dependency_holds, tuple_distinct_count,
};
pub use regex::{str_contains_count, str_filter_mask, str_replace_all};
