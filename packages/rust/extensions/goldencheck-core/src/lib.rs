//! `goldencheck-core` -- pyo3-free deep-profiling kernels for GoldenCheck.
//!
//! Each function is a behaviour-exact replacement for a CPU-bound Python path
//! in the `goldencheck` package's `baseline/`, `drift/`, and `relations/`
//! modules. The Python side selects the native path only when
//! `GOLDENCHECK_NATIVE` opts in (see `goldencheck/core/_native_loader.py`); the
//! pure-Python implementation stays the default and the fallback.
//!
//! The crate exposes an Arrow-native public API (`&dyn Array` / `&[ArrayRef]`);
//! the internal slice algorithms stay pyo3-free and are wrapped by thin
//! Arrow-decoding entry points. The `goldencheck-native` crate now only
//! marshals pyarrow<->Arrow. This mirrors `score-core` / `graph-core` on the
//! goldenmatch side.

mod arrow_support;
mod benford;
mod fuzzy;
mod keys;

pub use arrow_support::intern_column;
pub use benford::benford_leading_digits;
pub use fuzzy::near_duplicate_clusters;
pub use keys::{
    composite_key_search, discover_approximate_fds, discover_functional_dependencies,
    fd_violation_rows, functional_dependency_holds, tuple_distinct_count,
};
