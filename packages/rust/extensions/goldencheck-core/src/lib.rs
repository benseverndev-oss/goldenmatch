//! `goldencheck-core` -- pyo3-free deep-profiling kernels for GoldenCheck.
//!
//! Each function is a behaviour-exact replacement for a CPU-bound Python path
//! in the `goldencheck` package's `baseline/`, `drift/`, and `relations/`
//! modules. The Python side selects the native path only when
//! `GOLDENCHECK_NATIVE` opts in (see `goldencheck/core/_native_loader.py`); the
//! pure-Python implementation stays the default and the fallback.
//!
//! The kernels here take plain slices (`&[f64]`, `&[u64]`) so they carry no
//! Python or Arrow types -- the `goldencheck-native` crate owns the
//! `#[pyfunction]` shims and the zero-copy Arrow reads, and delegates the
//! actual work here. This mirrors `score-core` / `graph-core` on the
//! goldenmatch side.
//!
//! Most kernels compare interned ids by equality only. The denial-constraint
//! kernel (`dc`) is the exception: its columns arrive order-preservingly
//! rank-encoded, so it does ordered `<`/`<=`/`>`/`>=` comparisons over those ids.

mod benford;
mod dc;
mod fuzzy;
mod keys;
mod regex;

pub use benford::benford_leading_digits;
pub use dc::{dc_pair_evidence, dc_row_evidence, Pred};
pub use fuzzy::near_duplicate_clusters;
pub use keys::{
    composite_key_search, discover_approximate_fds, discover_functional_dependencies,
    fd_violation_rows, functional_dependency_holds, tuple_distinct_count,
};
pub use regex::{str_contains_count, str_filter_mask, str_replace_all};
