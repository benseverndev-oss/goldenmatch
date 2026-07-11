//! Arrow-reading shim for the date/datetime freshness kernel (freshness).
use arrow::array::{make_array, ArrayData};
use arrow::pyarrow::PyArrowType;
use pyo3::prelude::*;

/// Date/datetime freshness scan matching the `freshness` profiler's Polars
/// `count_gt(now)` + `max()`. Returns `(future_count, max_epoch)`, or `None`
/// when the array is empty, entirely null, or not a temporal dtype (Date32 /
/// Date64 / Timestamp).
///
/// The kernel reads the array's RAW integer values in its OWN native unit and
/// compares them against `now_epoch`, doing NO unit conversion. The caller MUST
/// pass `now_epoch` in the array's native unit -- Date32 = days since epoch,
/// Date64 = milliseconds, Timestamp = the declared unit (s/ms/us/ns) -- computed
/// offset-free (NEVER `datetime.timestamp()`, which applies the local UTC
/// offset). Determine the unit from the pyarrow type on the caller side (the
/// same mapping `goldencheck.core.frame` uses). Delegates to
/// `goldencheck_core::date_freshness`, which owns the downcast + null handling.
#[pyfunction]
pub fn date_freshness(array: PyArrowType<ArrayData>, now_epoch: i64) -> Option<(usize, i64)> {
    let arr = make_array(array.0);
    goldencheck_core::date_freshness(arr.as_ref(), now_epoch).map(|s| (s.future_count, s.max_epoch))
}
