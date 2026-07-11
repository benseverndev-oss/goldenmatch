//! Fused single-pass sequence-gap analysis for the `sequence_detection`
//! profiler. Today that profiler routes through the Frame seam and calls Polars
//! `col.diff().drop_nulls()` + `count_eq(1)` + `count_gt(0)` + `is_sorted()` +
//! `min()/max()` + `set(unique().to_list())` and the
//! `range(min, max+1) not in present` gap scan. This kernel reproduces exactly
//! those signals in one order-preserving pass over the Arrow buffer (Rust =
//! source of truth), shadow-wired: parity vs `PolarsColumn`, authoritative
//! output stays Polars until the Flip.
//!
//! # Order preservation (non-negotiable)
//!
//! The gap detector is order-sensitive: `is_sorted()` inspects the values in
//! array order, and `diff()` differences consecutive rows. This kernel iterates
//! the array **in order and NEVER sorts** -- `present` (the distinct-value set)
//! is a separate concern from `is_sorted`.
//!
//! # Overflow parity
//!
//! Polars `Series.diff()` keeps `Int64` and **wraps** on overflow, so the
//! kernel uses `wrapping_sub` (plain `i64 - i64` PANICS in debug/`cargo test`
//! on overflow -- the `[i64::MIN, i64::MAX]` adversarial fixture hits it). The
//! `unit_diff_count`/`positive_diff_count` are derived from the WRAPPED diffs so
//! both sides agree. The gap-range span is computed in `i128` to avoid the
//! `max - min + 1` overflow, and `gap_count` is derived arithmetically
//! (`span - present_size`, since every distinct value lies in `[min, max]`) so
//! we never materialise a full `i64`-range scan just to count.
use arrow::array::{
    Array, Int16Array, Int32Array, Int64Array, Int8Array, UInt16Array, UInt32Array, UInt64Array,
    UInt8Array,
};
use arrow::datatypes::DataType;
use std::collections::HashSet;

/// Order-preserving sequence signals matching what the `sequence_detection`
/// profiler derives from Polars. All fields are integer/bool exact (int/uint
/// only, so NaN-free). Gap fields are conditional on `(max - min + 1) > total`
/// (mirroring the profiler's `expected_count <= total: return` guard);
/// `gap_sample` is the first 10 missing values in ascending order.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SeqStats {
    pub n_diffs: usize,
    pub unit_diff_count: usize,
    pub positive_diff_count: usize,
    pub is_sorted: bool,
    pub min: i64,
    pub max: i64,
    pub present_size: usize,
    pub gap_count: usize,
    pub gap_sample: Vec<i64>,
}

/// Collect the non-null values of an Int*/UInt* Arrow array as `i64`, in array
/// order. Returns `None` for non-integer dtypes (the profiler's
/// `dtype in ("int", "uint")` gate) or when a `UInt64` value exceeds
/// `i64::MAX` (out of the profiler's realistic `int()` range -- decline rather
/// than saturate/wrap).
fn collect_i64(array: &dyn Array) -> Option<Vec<i64>> {
    macro_rules! collect {
        ($arrty:ty) => {{
            let arr = array.as_any().downcast_ref::<$arrty>().unwrap();
            let mut vals: Vec<i64> = Vec::with_capacity(arr.len());
            for i in 0..arr.len() {
                if !arr.is_null(i) {
                    vals.push(arr.value(i) as i64);
                }
            }
            vals
        }};
    }
    let vals = match array.data_type() {
        DataType::Int8 => collect!(Int8Array),
        DataType::Int16 => collect!(Int16Array),
        DataType::Int32 => collect!(Int32Array),
        DataType::Int64 => collect!(Int64Array),
        DataType::UInt8 => collect!(UInt8Array),
        DataType::UInt16 => collect!(UInt16Array),
        DataType::UInt32 => collect!(UInt32Array),
        DataType::UInt64 => {
            let arr = array.as_any().downcast_ref::<UInt64Array>().unwrap();
            let mut vals: Vec<i64> = Vec::with_capacity(arr.len());
            for i in 0..arr.len() {
                if !arr.is_null(i) {
                    let v = arr.value(i);
                    if v > i64::MAX as u64 {
                        return None; // out of the profiler's int() range
                    }
                    vals.push(v as i64);
                }
            }
            vals
        }
        _ => return None,
    };
    Some(vals)
}

/// One fused, order-preserving pass over an integer Arrow column reproducing the
/// `sequence_detection` profiler's Polars computation. Returns `None` when the
/// dtype is not Int*/UInt* or when `count_nonnull < 2` (mirroring the profiler's
/// `dtype`-gate + `total < 2: return`).
pub fn sequence_analysis(array: &dyn Array) -> Option<SeqStats> {
    let vals = collect_i64(array)?;
    let count_nonnull = vals.len();
    if count_nonnull < 2 {
        return None;
    }

    let mut unit_diff_count = 0usize;
    let mut positive_diff_count = 0usize;
    let mut is_sorted = true;
    let mut min = vals[0];
    let mut max = vals[0];
    let mut present: HashSet<i64> = HashSet::with_capacity(count_nonnull);
    present.insert(vals[0]);

    for w in vals.windows(2) {
        let prev = w[0];
        let cur = w[1];
        // Polars `diff()` keeps Int64 and wraps -- match with wrapping_sub.
        let d = cur.wrapping_sub(prev);
        if d == 1 {
            unit_diff_count += 1;
        }
        if d > 0 {
            positive_diff_count += 1;
        }
        if cur < prev {
            is_sorted = false; // Polars is_sorted() = non-strict ascending
        }
        if cur < min {
            min = cur;
        }
        if cur > max {
            max = cur;
        }
        present.insert(cur);
    }

    let n_diffs = count_nonnull - 1; // diff().drop_nulls() drops the leading null
    let present_size = present.len();

    // Gap scan, gated exactly like the profiler's `expected_count <= total`.
    // Span computed in i128 so `[i64::MIN, i64::MAX]` doesn't overflow.
    let span = (max as i128) - (min as i128) + 1;
    let (gap_count, gap_sample) = if span > count_nonnull as i128 {
        // Every distinct value lies in [min, max], so the count of integers in
        // the range NOT present is exactly `span - present_size` -- identical to
        // the profiler's `len([v for v in range(min, max+1) if v not in present])`
        // without materialising the (possibly 2^64-wide) range.
        let gc = (span - present_size as i128) as usize;
        // First-10 missing values, ascending. Break early so a huge span with a
        // small `present` (e.g. the i64 min/max fixture) doesn't scan 2^64 ints.
        let mut sample: Vec<i64> = Vec::with_capacity(10);
        let mut v = min;
        loop {
            if !present.contains(&v) {
                sample.push(v);
                if sample.len() == 10 {
                    break;
                }
            }
            if v == max {
                break;
            }
            v += 1; // safe: v < max here, so no i64 overflow
        }
        (gc, sample)
    } else {
        (0, Vec::new())
    };

    Some(SeqStats {
        n_diffs,
        unit_diff_count,
        positive_diff_count,
        is_sorted,
        min,
        max,
        present_size,
        gap_count,
        gap_sample,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn monotonic_no_gap() {
        let s = sequence_analysis(&Int64Array::from(vec![1, 2, 3, 4, 5])).unwrap();
        assert_eq!(s.n_diffs, 4);
        assert_eq!(s.unit_diff_count, 4);
        assert_eq!(s.positive_diff_count, 4);
        assert!(s.is_sorted);
        assert_eq!((s.min, s.max), (1, 5));
        assert_eq!(s.present_size, 5);
        assert_eq!(s.gap_count, 0);
        assert!(s.gap_sample.is_empty());
    }

    #[test]
    fn gapped_ascending() {
        // 1,2,4,7,8 -> missing 3,5,6 in [1,8]
        let s = sequence_analysis(&Int64Array::from(vec![1, 2, 4, 7, 8])).unwrap();
        assert_eq!(s.n_diffs, 4);
        assert_eq!(s.unit_diff_count, 2); // 1->2, 7->8
        assert_eq!(s.positive_diff_count, 4);
        assert!(s.is_sorted);
        assert_eq!((s.min, s.max), (1, 8));
        assert_eq!(s.present_size, 5);
        assert_eq!(s.gap_count, 3);
        assert_eq!(s.gap_sample, vec![3, 5, 6]);
    }

    #[test]
    fn unsorted_not_sorted() {
        let s = sequence_analysis(&Int64Array::from(vec![3, 1, 2, 5])).unwrap();
        assert!(!s.is_sorted);
        assert_eq!(s.min, 1);
        assert_eq!(s.max, 5);
        // diffs: 1-3=-2, 2-1=1, 5-2=3
        assert_eq!(s.unit_diff_count, 1);
        assert_eq!(s.positive_diff_count, 2);
        // range [1,5] has 5 ints, present {1,2,3,5} -> gap {4}
        assert_eq!(s.gap_count, 1);
        assert_eq!(s.gap_sample, vec![4]);
    }

    #[test]
    fn duplicates_present_vs_total() {
        // [1,1,3]: total=3, present={1,3}, span=3 -> 3 <= 3 so NO gaps flagged,
        // exactly the profiler's `expected_count <= total` quirk (2 is missing
        // but the guard suppresses it).
        let s = sequence_analysis(&Int64Array::from(vec![1, 1, 3])).unwrap();
        assert_eq!(s.present_size, 2);
        assert_eq!(s.n_diffs, 2);
        assert_eq!(s.unit_diff_count, 0); // diffs: 0, 2
        assert_eq!(s.positive_diff_count, 1);
        assert_eq!(s.gap_count, 0);
        assert!(s.gap_sample.is_empty());
    }

    #[test]
    fn int64_min_max_overflow_no_panic() {
        // wrapping_sub must NOT panic; MAX - MIN wraps to -1.
        let s = sequence_analysis(&Int64Array::from(vec![i64::MIN, i64::MAX])).unwrap();
        assert_eq!(s.n_diffs, 1);
        assert_eq!(s.unit_diff_count, 0); // wrapped diff is -1, not 1
        assert_eq!(s.positive_diff_count, 0); // -1 is not > 0
        assert!(s.is_sorted); // MIN <= MAX
        assert_eq!(s.min, i64::MIN);
        assert_eq!(s.max, i64::MAX);
        assert_eq!(s.present_size, 2);
        // span = 2^64 > 2, so gaps are flagged; count = 2^64 - 2.
        assert_eq!(s.gap_count, (u64::MAX - 1) as usize);
        // first 10 gaps ascend from MIN+1 (early-break, no full-range scan).
        assert_eq!(
            s.gap_sample,
            (1..=10).map(|k| i64::MIN + k).collect::<Vec<_>>()
        );
    }

    #[test]
    fn single_value_returns_none() {
        assert!(sequence_analysis(&Int64Array::from(vec![42])).is_none());
    }

    #[test]
    fn nulls_dropped_before_analysis() {
        let s = sequence_analysis(&Int64Array::from(vec![
            Some(1),
            None,
            Some(2),
            None,
            Some(3),
        ]))
        .unwrap();
        assert_eq!(s.n_diffs, 2);
        assert_eq!(s.unit_diff_count, 2);
        assert_eq!(s.present_size, 3);
        assert_eq!(s.gap_count, 0);
    }

    #[test]
    fn uint32_widened() {
        let s = sequence_analysis(&UInt32Array::from(vec![10u32, 11, 13])).unwrap();
        assert_eq!((s.min, s.max), (10, 13));
        assert_eq!(s.gap_count, 1);
        assert_eq!(s.gap_sample, vec![12]);
    }

    #[test]
    fn uint64_overflow_declines() {
        // A value above i64::MAX -> out of int() range -> None.
        let s = sequence_analysis(&UInt64Array::from(vec![1u64, u64::MAX]));
        assert!(s.is_none());
    }

    #[test]
    fn non_integer_dtype_returns_none() {
        use arrow::array::Float64Array;
        assert!(sequence_analysis(&Float64Array::from(vec![1.0, 2.0, 3.0])).is_none());
    }
}
