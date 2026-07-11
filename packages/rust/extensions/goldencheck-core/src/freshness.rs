//! Fused single-pass date/datetime freshness scan for the `freshness` profiler.
//! Today that profiler routes through the Frame seam and calls Polars
//! `col.count_gt(now)` + `col.max()`. This kernel reproduces exactly those two
//! signals in one pass over the Arrow temporal buffer (Rust = source of truth),
//! shadow-wired: parity vs `PolarsColumn`, authoritative output stays Polars
//! until the Flip.
//!
//! # Raw-value / unit contract (non-negotiable, spec review B2)
//!
//! The kernel reads the **raw integer values** of the temporal array in the
//! array's OWN native unit and compares them against `now_epoch`, which the
//! caller supplies in that SAME unit. It performs NO unit conversion:
//!   - `Date32` = i32 days since 1970-01-01 (widened to i64).
//!   - `Date64` = i64 milliseconds since 1970-01-01.
//!   - `Timestamp(unit, _)` = i64 in the declared `unit` (s / ms / us / ns).
//!
//! So the caller must build `now_epoch` offset-free in the matching unit
//! (Date32: `(ref_date - date(1970,1,1)).days`; Timestamp(us): microseconds
//! since the naive epoch, NEVER `datetime.timestamp()` which applies the local
//! UTC offset). Polars stores tz-naive `Datetime` as wall-clock in the unit with
//! no tz shift and exports it as `Timestamp(unit, None)`; the tz-aware
//! `count_gt` path bails in Python (`except: return []`) before ever reaching
//! this kernel, so the kernel only ever sees data the Polars path would accept.
use arrow::array::{
    Array, Date32Array, Date64Array, TimestampMicrosecondArray, TimestampMillisecondArray,
    TimestampNanosecondArray, TimestampSecondArray,
};
use arrow::datatypes::{DataType, TimeUnit};

/// Freshness signals matching what the `freshness` profiler derives from Polars.
/// `future_count` = count of non-null RAW values strictly greater than
/// `now_epoch`; `max_epoch` = the maximum non-null RAW value. Both are exact
/// integers (no float reduction).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FreshStats {
    pub future_count: usize,
    pub max_epoch: i64,
}

/// Fold a temporal array's raw i64 values into `(future_count, max)`, skipping
/// nulls. Returns `None` if there are no non-null values (all-null), mirroring
/// the profiler's `len(non_null) == 0: return []`. `is_null`/`value` close over
/// the already-downcast array.
fn scan(
    len: usize,
    is_null: impl Fn(usize) -> bool,
    value: impl Fn(usize) -> i64,
    now_epoch: i64,
) -> Option<FreshStats> {
    let mut future_count = 0usize;
    let mut max_epoch: Option<i64> = None;
    for i in 0..len {
        if is_null(i) {
            continue;
        }
        let v = value(i);
        if v > now_epoch {
            future_count += 1;
        }
        max_epoch = Some(match max_epoch {
            Some(m) if m >= v => m,
            _ => v,
        });
    }
    max_epoch.map(|m| FreshStats {
        future_count,
        max_epoch: m,
    })
}

/// One fused pass over a Date32/Date64/Timestamp Arrow column reproducing the
/// `freshness` profiler's Polars `count_gt(now)` + `max()`. `now_epoch` is in
/// the array's native unit (see module docs). Returns `None` when the array is
/// empty, entirely null, or not a temporal dtype (the profiler's dtype gate +
/// `len(non_null) == 0` early return).
pub fn date_freshness(array: &dyn Array, now_epoch: i64) -> Option<FreshStats> {
    let len = array.len();
    match array.data_type() {
        DataType::Date32 => {
            let a = array.as_any().downcast_ref::<Date32Array>().unwrap();
            scan(len, |i| a.is_null(i), |i| a.value(i) as i64, now_epoch)
        }
        DataType::Date64 => {
            let a = array.as_any().downcast_ref::<Date64Array>().unwrap();
            scan(len, |i| a.is_null(i), |i| a.value(i), now_epoch)
        }
        DataType::Timestamp(unit, _) => match unit {
            TimeUnit::Second => {
                let a = array
                    .as_any()
                    .downcast_ref::<TimestampSecondArray>()
                    .unwrap();
                scan(len, |i| a.is_null(i), |i| a.value(i), now_epoch)
            }
            TimeUnit::Millisecond => {
                let a = array
                    .as_any()
                    .downcast_ref::<TimestampMillisecondArray>()
                    .unwrap();
                scan(len, |i| a.is_null(i), |i| a.value(i), now_epoch)
            }
            TimeUnit::Microsecond => {
                let a = array
                    .as_any()
                    .downcast_ref::<TimestampMicrosecondArray>()
                    .unwrap();
                scan(len, |i| a.is_null(i), |i| a.value(i), now_epoch)
            }
            TimeUnit::Nanosecond => {
                let a = array
                    .as_any()
                    .downcast_ref::<TimestampNanosecondArray>()
                    .unwrap();
                scan(len, |i| a.is_null(i), |i| a.value(i), now_epoch)
            }
        },
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn date32_all_past() {
        // days since epoch; now = 20000. All values < now -> no future.
        let a = Date32Array::from(vec![Some(100), Some(19999), Some(5000)]);
        let s = date_freshness(&a, 20000).unwrap();
        assert_eq!(s.future_count, 0);
        assert_eq!(s.max_epoch, 19999);
    }

    #[test]
    fn date32_some_future() {
        let a = Date32Array::from(vec![Some(100), Some(25000), Some(30000), Some(19999)]);
        let s = date_freshness(&a, 20000).unwrap();
        assert_eq!(s.future_count, 2); // 25000, 30000 > 20000
        assert_eq!(s.max_epoch, 30000);
    }

    #[test]
    fn date32_boundary_is_not_future() {
        // strictly greater: a value equal to now is NOT future (matches count_gt).
        let a = Date32Array::from(vec![Some(20000), Some(20001)]);
        let s = date_freshness(&a, 20000).unwrap();
        assert_eq!(s.future_count, 1);
        assert_eq!(s.max_epoch, 20001);
    }

    #[test]
    fn date64_ms() {
        // Date64 = milliseconds since epoch.
        let a = Date64Array::from(vec![Some(1_000i64), Some(2_000), Some(5_000)]);
        let s = date_freshness(&a, 3_000).unwrap();
        assert_eq!(s.future_count, 1); // 5000 > 3000
        assert_eq!(s.max_epoch, 5_000);
    }

    #[test]
    fn timestamp_micros() {
        let a: TimestampMicrosecondArray =
            vec![Some(10i64), Some(20), Some(999), Some(1_000_000)].into();
        let s = date_freshness(&a, 500).unwrap();
        assert_eq!(s.future_count, 2); // 999, 1_000_000 > 500
        assert_eq!(s.max_epoch, 1_000_000);
    }

    #[test]
    fn timestamp_seconds() {
        let a: TimestampSecondArray = vec![Some(100i64), Some(200)].into();
        let s = date_freshness(&a, 150).unwrap();
        assert_eq!(s.future_count, 1);
        assert_eq!(s.max_epoch, 200);
    }

    #[test]
    fn timestamp_nanos() {
        let a: TimestampNanosecondArray = vec![Some(1i64), Some(2), Some(3)].into();
        let s = date_freshness(&a, 5).unwrap();
        assert_eq!(s.future_count, 0);
        assert_eq!(s.max_epoch, 3);
    }

    #[test]
    fn nulls_skipped() {
        let a = Date32Array::from(vec![Some(100), None, Some(30000), None]);
        let s = date_freshness(&a, 20000).unwrap();
        assert_eq!(s.future_count, 1);
        assert_eq!(s.max_epoch, 30000);
    }

    #[test]
    fn all_null_returns_none() {
        let a = Date32Array::from(vec![None, None, None]);
        assert!(date_freshness(&a, 0).is_none());
    }

    #[test]
    fn empty_returns_none() {
        let a = Date32Array::from(Vec::<Option<i32>>::new());
        assert!(date_freshness(&a, 0).is_none());
    }

    #[test]
    fn non_temporal_returns_none() {
        use arrow::array::Int64Array;
        assert!(date_freshness(&Int64Array::from(vec![1, 2, 3]), 0).is_none());
    }

    #[test]
    fn negative_epoch_pre_1970() {
        // Dates before 1970 have negative day counts; comparison still works.
        let a = Date32Array::from(vec![Some(-100), Some(-1), Some(50)]);
        let s = date_freshness(&a, 0).unwrap();
        assert_eq!(s.future_count, 1); // only 50 > 0
        assert_eq!(s.max_epoch, 50);
    }
}
