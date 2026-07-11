//! Fused single-pass column aggregate: `{len, null_count, n_unique_nonnull,
//! dtype}` computed together over one Arrow column. Feeds the
//! nullability/uniqueness/cardinality checks in `goldencheck.baseline` --
//! today those each re-scan the column separately (Polars `.len()`,
//! `.null_count()`, `.n_unique()`, `.dtype`); this kernel gives the same four
//! numbers in one pass over the Arrow buffer.
//!
//! `dtype_category` maps an Arrow `DataType` to the neutral vocabulary the
//! Python side already uses (`goldencheck.core.frame._neutral_dtype`):
//! `str | int | uint | float | date | datetime | bool | other`.
//!
//! Parity subtlety (Polars `Series.n_unique()`), verified empirically against
//! Polars 1.40.1:
//!   - `[1.0, 2.0, NaN, NaN].n_unique() == 3` -- Polars treats ALL NaN payloads
//!     as one distinct value, unlike raw bit-pattern equality (which would
//!     split on differing NaN payloads/signs).
//!   - `[0.0, -0.0].n_unique() == 1` -- Polars treats positive and negative
//!     zero as the same value, unlike `f64::to_bits` (which differ).
//!
//! We therefore canonicalise before hashing: any NaN collapses to a single
//! canonical bit pattern, and `-0.0` normalises to `0.0`. This makes the
//! native uniqueness count agree with Polars on both cases; see
//! `tests/core/test_column_aggregate_parity.py` for the cross-check.
use arrow::array::{
    Array, BooleanArray, Date32Array, Date64Array, Float32Array, Float64Array, Int16Array,
    Int32Array, Int64Array, Int8Array, LargeStringArray, StringArray, TimestampMicrosecondArray,
    TimestampMillisecondArray, TimestampNanosecondArray, TimestampSecondArray, UInt16Array,
    UInt32Array, UInt64Array, UInt8Array,
};
use arrow::datatypes::DataType;
use rustc_hash::FxHashSet;

/// Neutral dtype vocabulary, matching `goldencheck.core.frame._neutral_dtype`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DtypeCat {
    Str,
    Int,
    Uint,
    Float,
    Date,
    Datetime,
    Bool,
    Other,
}

impl DtypeCat {
    /// The Python-side string the neutral vocabulary uses.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Str => "str",
            Self::Int => "int",
            Self::Uint => "uint",
            Self::Float => "float",
            Self::Date => "date",
            Self::Datetime => "datetime",
            Self::Bool => "bool",
            Self::Other => "other",
        }
    }
}

/// Classify an Arrow column into the neutral dtype vocabulary.
pub fn dtype_category(array: &dyn Array) -> DtypeCat {
    use DataType::*;
    match array.data_type() {
        Utf8 | LargeUtf8 | Utf8View => DtypeCat::Str,
        Int8 | Int16 | Int32 | Int64 => DtypeCat::Int,
        UInt8 | UInt16 | UInt32 | UInt64 => DtypeCat::Uint,
        Float16 | Float32 | Float64 => DtypeCat::Float,
        Date32 | Date64 => DtypeCat::Date,
        Timestamp(_, _) => DtypeCat::Datetime,
        Boolean => DtypeCat::Bool,
        _ => DtypeCat::Other,
    }
}

/// Result of a fused single-pass column scan.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ColumnAgg {
    pub len: usize,
    pub null_count: usize,
    pub n_unique_nonnull: usize,
    pub dtype: DtypeCat,
}

/// Canonical bit pattern all NaN payloads collapse to before hashing, so
/// Polars' "all NaN are one distinct value" semantics are preserved.
const CANON_NAN_BITS: u64 = f64::NAN.to_bits();

/// Bit-pattern key for an f64 that matches Polars uniqueness semantics:
/// NaN (any payload/sign) canonicalises to one bit pattern, and `-0.0`
/// normalises to `0.0`.
#[inline]
fn f64_unique_key(v: f64) -> u64 {
    if v.is_nan() {
        CANON_NAN_BITS
    } else if v == 0.0 {
        0.0f64.to_bits() // normalises -0.0 -> +0.0's bit pattern
    } else {
        v.to_bits()
    }
}

#[inline]
fn f32_unique_key(v: f32) -> u64 {
    f64_unique_key(v as f64)
}

/// One fused pass over an Arrow column: length, null count, distinct
/// non-null value count, and the neutral dtype category.
pub fn column_aggregate(array: &dyn Array) -> ColumnAgg {
    let len = array.len();
    let null_count = array.null_count();
    let dtype = dtype_category(array);

    let n_unique_nonnull: usize = match array.data_type() {
        DataType::Utf8 => {
            let arr = array.as_any().downcast_ref::<StringArray>().unwrap();
            let mut seen: FxHashSet<&str> = FxHashSet::default();
            for i in 0..len {
                if arr.is_null(i) {
                    continue;
                }
                seen.insert(arr.value(i));
            }
            seen.len()
        }
        DataType::LargeUtf8 => {
            let arr = array.as_any().downcast_ref::<LargeStringArray>().unwrap();
            let mut seen: FxHashSet<&str> = FxHashSet::default();
            for i in 0..len {
                if arr.is_null(i) {
                    continue;
                }
                seen.insert(arr.value(i));
            }
            seen.len()
        }
        DataType::Boolean => {
            let arr = array.as_any().downcast_ref::<BooleanArray>().unwrap();
            let mut seen: FxHashSet<bool> = FxHashSet::default();
            for i in 0..len {
                if arr.is_null(i) {
                    continue;
                }
                seen.insert(arr.value(i));
            }
            seen.len()
        }
        DataType::Int8 => {
            let arr = array.as_any().downcast_ref::<Int8Array>().unwrap();
            let mut seen: FxHashSet<i64> = FxHashSet::default();
            for i in 0..len {
                if arr.is_null(i) {
                    continue;
                }
                seen.insert(arr.value(i) as i64);
            }
            seen.len()
        }
        DataType::Int16 => {
            let arr = array.as_any().downcast_ref::<Int16Array>().unwrap();
            let mut seen: FxHashSet<i64> = FxHashSet::default();
            for i in 0..len {
                if arr.is_null(i) {
                    continue;
                }
                seen.insert(arr.value(i) as i64);
            }
            seen.len()
        }
        DataType::Int32 => {
            let arr = array.as_any().downcast_ref::<Int32Array>().unwrap();
            let mut seen: FxHashSet<i64> = FxHashSet::default();
            for i in 0..len {
                if arr.is_null(i) {
                    continue;
                }
                seen.insert(arr.value(i) as i64);
            }
            seen.len()
        }
        DataType::Int64 => {
            let arr = array.as_any().downcast_ref::<Int64Array>().unwrap();
            let mut seen: FxHashSet<i64> = FxHashSet::default();
            for i in 0..len {
                if arr.is_null(i) {
                    continue;
                }
                seen.insert(arr.value(i));
            }
            seen.len()
        }
        DataType::UInt8 => {
            let arr = array.as_any().downcast_ref::<UInt8Array>().unwrap();
            let mut seen: FxHashSet<u64> = FxHashSet::default();
            for i in 0..len {
                if arr.is_null(i) {
                    continue;
                }
                seen.insert(arr.value(i) as u64);
            }
            seen.len()
        }
        DataType::UInt16 => {
            let arr = array.as_any().downcast_ref::<UInt16Array>().unwrap();
            let mut seen: FxHashSet<u64> = FxHashSet::default();
            for i in 0..len {
                if arr.is_null(i) {
                    continue;
                }
                seen.insert(arr.value(i) as u64);
            }
            seen.len()
        }
        DataType::UInt32 => {
            let arr = array.as_any().downcast_ref::<UInt32Array>().unwrap();
            let mut seen: FxHashSet<u64> = FxHashSet::default();
            for i in 0..len {
                if arr.is_null(i) {
                    continue;
                }
                seen.insert(arr.value(i) as u64);
            }
            seen.len()
        }
        DataType::UInt64 => {
            let arr = array.as_any().downcast_ref::<UInt64Array>().unwrap();
            let mut seen: FxHashSet<u64> = FxHashSet::default();
            for i in 0..len {
                if arr.is_null(i) {
                    continue;
                }
                seen.insert(arr.value(i));
            }
            seen.len()
        }
        DataType::Float32 => {
            let arr = array.as_any().downcast_ref::<Float32Array>().unwrap();
            let mut seen: FxHashSet<u64> = FxHashSet::default();
            for i in 0..len {
                if arr.is_null(i) {
                    continue;
                }
                seen.insert(f32_unique_key(arr.value(i)));
            }
            seen.len()
        }
        DataType::Float64 => {
            let arr = array.as_any().downcast_ref::<Float64Array>().unwrap();
            let mut seen: FxHashSet<u64> = FxHashSet::default();
            for i in 0..len {
                if arr.is_null(i) {
                    continue;
                }
                seen.insert(f64_unique_key(arr.value(i)));
            }
            seen.len()
        }
        DataType::Date32 => {
            let arr = array.as_any().downcast_ref::<Date32Array>().unwrap();
            let mut seen: FxHashSet<i64> = FxHashSet::default();
            for i in 0..len {
                if arr.is_null(i) {
                    continue;
                }
                seen.insert(arr.value(i) as i64);
            }
            seen.len()
        }
        DataType::Date64 => {
            let arr = array.as_any().downcast_ref::<Date64Array>().unwrap();
            let mut seen: FxHashSet<i64> = FxHashSet::default();
            for i in 0..len {
                if arr.is_null(i) {
                    continue;
                }
                seen.insert(arr.value(i));
            }
            seen.len()
        }
        DataType::Timestamp(unit, _) => {
            use arrow::datatypes::TimeUnit::*;
            let mut seen: FxHashSet<i64> = FxHashSet::default();
            match unit {
                Second => {
                    let arr = array
                        .as_any()
                        .downcast_ref::<TimestampSecondArray>()
                        .unwrap();
                    for i in 0..len {
                        if !arr.is_null(i) {
                            seen.insert(arr.value(i));
                        }
                    }
                }
                Millisecond => {
                    let arr = array
                        .as_any()
                        .downcast_ref::<TimestampMillisecondArray>()
                        .unwrap();
                    for i in 0..len {
                        if !arr.is_null(i) {
                            seen.insert(arr.value(i));
                        }
                    }
                }
                Microsecond => {
                    let arr = array
                        .as_any()
                        .downcast_ref::<TimestampMicrosecondArray>()
                        .unwrap();
                    for i in 0..len {
                        if !arr.is_null(i) {
                            seen.insert(arr.value(i));
                        }
                    }
                }
                Nanosecond => {
                    let arr = array
                        .as_any()
                        .downcast_ref::<TimestampNanosecondArray>()
                        .unwrap();
                    for i in 0..len {
                        if !arr.is_null(i) {
                            seen.insert(arr.value(i));
                        }
                    }
                }
            }
            seen.len()
        }
        // Unsupported dtype for uniqueness counting (the profilers only run
        // this kernel on str/numeric/bool/date columns). Best-effort: report
        // 0 rather than panicking; callers should not rely on this number for
        // `Other` columns.
        _ => 0,
    };

    ColumnAgg {
        len,
        null_count,
        n_unique_nonnull,
        dtype,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use arrow::array::{
        BooleanArray, Date32Array, Float64Array, Int32Array, StringArray,
        TimestampMicrosecondArray, UInt32Array,
    };

    #[test]
    fn str_basic() {
        let a = StringArray::from(vec![Some("x"), Some("y"), Some("x"), None]);
        let r = column_aggregate(&a);
        assert_eq!(r.len, 4);
        assert_eq!(r.null_count, 1);
        assert_eq!(r.n_unique_nonnull, 2);
        assert_eq!(r.dtype, DtypeCat::Str);
    }

    #[test]
    fn int_basic() {
        let a = Int32Array::from(vec![Some(1), Some(2), Some(1), None]);
        let r = column_aggregate(&a);
        assert_eq!(r.null_count, 1);
        assert_eq!(r.n_unique_nonnull, 2);
        assert_eq!(r.dtype, DtypeCat::Int);
    }

    #[test]
    fn uint_basic() {
        let a = UInt32Array::from(vec![Some(1u32), Some(2), Some(2)]);
        let r = column_aggregate(&a);
        assert_eq!(r.n_unique_nonnull, 2);
        assert_eq!(r.dtype, DtypeCat::Uint);
    }

    #[test]
    fn float_with_nan_collapses_to_one() {
        // Matches Polars: [1.0, 2.0, NaN, NaN].n_unique() == 3 (NaN is one value).
        let a = Float64Array::from(vec![1.0, 2.0, f64::NAN, f64::NAN]);
        let r = column_aggregate(&a);
        assert_eq!(r.n_unique_nonnull, 3);
        assert_eq!(r.dtype, DtypeCat::Float);
    }

    #[test]
    fn float_signed_zero_collapses_to_one() {
        // Matches Polars: [0.0, -0.0].n_unique() == 1.
        let a = Float64Array::from(vec![0.0, -0.0]);
        let r = column_aggregate(&a);
        assert_eq!(r.n_unique_nonnull, 1);
    }

    #[test]
    fn float_mixed_nan_and_zero() {
        let a = Float64Array::from(vec![1.0, 2.0, f64::NAN, f64::NAN, 0.0, -0.0]);
        let r = column_aggregate(&a);
        assert_eq!(r.n_unique_nonnull, 4); // {1.0, 2.0, NaN, 0.0}
    }

    #[test]
    fn bool_basic() {
        let a = BooleanArray::from(vec![Some(true), Some(false), Some(true), None]);
        let r = column_aggregate(&a);
        assert_eq!(r.null_count, 1);
        assert_eq!(r.n_unique_nonnull, 2);
        assert_eq!(r.dtype, DtypeCat::Bool);
    }

    #[test]
    fn date_basic() {
        let a = Date32Array::from(vec![Some(1), Some(2), Some(1), None]);
        let r = column_aggregate(&a);
        assert_eq!(r.null_count, 1);
        assert_eq!(r.n_unique_nonnull, 2);
        assert_eq!(r.dtype, DtypeCat::Date);
    }

    #[test]
    fn datetime_basic() {
        let a: TimestampMicrosecondArray = vec![Some(1i64), Some(2), Some(1), None].into();
        let r = column_aggregate(&a);
        assert_eq!(r.null_count, 1);
        assert_eq!(r.n_unique_nonnull, 2);
        assert_eq!(r.dtype, DtypeCat::Datetime);
    }

    #[test]
    fn all_null() {
        let a = Int32Array::from(vec![None, None, None]);
        let r = column_aggregate(&a);
        assert_eq!(r.len, 3);
        assert_eq!(r.null_count, 3);
        assert_eq!(r.n_unique_nonnull, 0);
    }

    #[test]
    fn empty() {
        let a = Int32Array::from(Vec::<Option<i32>>::new());
        let r = column_aggregate(&a);
        assert_eq!(r.len, 0);
        assert_eq!(r.null_count, 0);
        assert_eq!(r.n_unique_nonnull, 0);
    }

    #[test]
    fn single_value() {
        let a = StringArray::from(vec![Some("only")]);
        let r = column_aggregate(&a);
        assert_eq!(r.len, 1);
        assert_eq!(r.null_count, 0);
        assert_eq!(r.n_unique_nonnull, 1);
    }

    #[test]
    fn other_dtype_reports_zero_unique_not_panic() {
        use arrow::array::BinaryArray;
        let a = BinaryArray::from(vec![Some(b"a".as_ref()), Some(b"b".as_ref())]);
        let r = column_aggregate(&a);
        assert_eq!(r.dtype, DtypeCat::Other);
        assert_eq!(r.n_unique_nonnull, 0);
        assert_eq!(r.len, 2);
    }
}
