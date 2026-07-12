//! Fused single-pass numeric column stats for the `range_distribution`
//! profiler. Today that profiler routes through the Frame seam and calls Polars
//! `col.min()/max()/mean()/std()` (four scans) plus `filter_outside(lower,
//! upper)` for the +/-3 sigma outlier branch. This kernel reproduces exactly
//! those numbers in one pass over the Arrow buffer (Rust = source of truth),
//! shadow-wired: parity vs `PolarsColumn`, authoritative output stays Polars
//! until the Flip.
//!
//! # Polars parity (verified empirically against Polars 1.40.1)
//!
//! `min` / `max` **ignore NaN** but include +/-inf:
//!   - `[1, 2, NaN, 3].min()==1, .max()==3`
//!   - `[1, 2, inf, 3].max()==inf`; `[1, 2, -inf, 3].min()==-inf`
//!   - all-NaN -> `NaN`; empty -> `None` (both surface as `NaN` from the
//!     kernel, since Polars `None` also canonicalises to `NaN` in the parity
//!     compare).
//!
//! `mean` / `std` use **plain IEEE arithmetic over every non-null value**, so
//! NaN and inf propagate naturally:
//!   - `[1, 2, NaN, 3].mean()==NaN`, `.std()==NaN`
//!   - `[1, 2, inf, 3].mean()==inf`, `.std()==NaN` (because `inf-inf==NaN`
//!     appears in the deviation sum).
//!
//! `std` is the **sample** standard deviation (ddof=1):
//! `sqrt(sum((x-mean)^2)/(n-1))`. Polars returns `None` for `n<2`; we return
//! `NaN`. This is the suite's first float-stat kernel, so the parity harness
//! registers an epsilon divergence class for `mean`/`std` (float reduction
//! order) and canonicalises NaN before comparing (`NaN != NaN` otherwise
//! false-positives).
use arrow::array::{
    Array, Float32Array, Float64Array, Int16Array, Int32Array, Int64Array, Int8Array, UInt16Array,
    UInt32Array, UInt64Array, UInt8Array,
};
use arrow::datatypes::DataType;
use rustc_hash::FxHashSet;

/// Canonical NaN hashset key so every NaN bit-pattern collapses to ONE distinct
/// value, matching pyarrow `count_distinct(mode="all")` (which treats all NaN as
/// equal). Signed zeros are deliberately NOT canonicalised (`+0.0` and `-0.0`
/// have distinct bit patterns and pyarrow keeps them distinct).
const CANONICAL_NAN_U64: u64 = 0x7ff8_0000_0000_0000; // f64::NAN.to_bits()

/// Single-pass numeric summary matching Polars `.min()/.max()/.mean()/.std()`,
/// plus `n_unique` matching pyarrow `count_distinct(mode="all")`.
/// f64 fields carry `NaN` where Polars would return `None` (empty / all-NaN
/// min-max, or `n<2` std) or where IEEE arithmetic propagates `NaN`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct NumStats {
    pub count_nonnull: usize,
    pub min: f64,
    pub max: f64,
    pub mean: f64,
    pub std: f64,
    pub sum: f64,
    pub n_unique: usize,
}

/// Compute the summary from the already-extracted non-null values (as f64).
fn stats_from_values(vals: &[f64]) -> NumStats {
    let count = vals.len();
    if count == 0 {
        return NumStats {
            count_nonnull: 0,
            min: f64::NAN,
            max: f64::NAN,
            mean: f64::NAN,
            std: f64::NAN,
            sum: 0.0,
            n_unique: 0,
        };
    }

    // sum + min/max in one pass. min/max skip NaN (Polars semantics); the sum
    // includes every value so NaN/inf propagate into mean/std like Polars.
    let mut sum = 0.0f64;
    let mut min = f64::INFINITY;
    let mut max = f64::NEG_INFINITY;
    let mut seen_non_nan = false;
    for &v in vals {
        sum += v;
        if !v.is_nan() {
            if v < min {
                min = v;
            }
            if v > max {
                max = v;
            }
            seen_non_nan = true;
        }
    }
    let (min, max) = if seen_non_nan {
        (min, max)
    } else {
        // Every value was NaN -> Polars min/max return None.
        (f64::NAN, f64::NAN)
    };

    let mean = sum / count as f64;

    // Sample std (ddof=1). Two-pass (mean first) matches Polars' NaN/inf
    // propagation exactly: with a NaN present mean is NaN so every deviation is
    // NaN; with an inf present mean is inf and the `inf-inf` deviation is NaN.
    let std = if count < 2 {
        f64::NAN
    } else {
        let mut ss = 0.0f64;
        for &v in vals {
            let d = v - mean;
            ss += d * d;
        }
        (ss / (count as f64 - 1.0)).sqrt()
    };

    NumStats {
        count_nonnull: count,
        min,
        max,
        mean,
        std,
        sum,
        n_unique: 0, // filled in by `column_numeric_stats` (fused distinct pass)
    }
}

/// One fused pass over a numeric Arrow column (Int*/UInt*/Float*), null-aware.
/// Computes the min/max/mean/std/sum summary AND `n_unique` (distinct-count) in
/// the SAME streaming pass -- a distinct hashset is built alongside the stat
/// accumulation so numeric cardinality is a free byproduct of the stats scan.
/// `n_unique` matches pyarrow `count_distinct(mode="all")`: NaN collapses to one
/// distinct, signed zeros stay distinct, and any null slot adds one distinct.
/// Non-numeric dtypes yield an empty (count 0, NaN, n_unique 0) summary -- the
/// profiler only calls this on int/uint/float columns (after `mostly_numeric`).
pub fn column_numeric_stats(array: &dyn Array) -> NumStats {
    let mut set: FxHashSet<u64> = FxHashSet::default();
    let mut has_null = false;

    // Integer/uint arrays: raw integer value is the distinct key (`as u64` is a
    // bijection within a single fixed-width type, so no false collisions). No
    // NaN concern.
    macro_rules! collect_int {
        ($arrty:ty) => {{
            let arr = array.as_any().downcast_ref::<$arrty>().unwrap();
            let mut vals: Vec<f64> = Vec::with_capacity(arr.len());
            set.reserve(arr.len());
            for i in 0..arr.len() {
                if arr.is_null(i) {
                    has_null = true;
                    continue;
                }
                let v = arr.value(i);
                set.insert(v as u64);
                vals.push(v as f64);
            }
            vals
        }};
    }
    // Float arrays: key on the IEEE bits so `+0.0`/`-0.0` stay distinct, but map
    // every NaN bit-pattern to the canonical NaN key so they collapse to one.
    // Float32 promotes to f64 losslessly (distinctness + signed-zero + NaN all
    // preserved), matching pyarrow's per-dtype count_distinct.
    macro_rules! collect_float {
        ($arrty:ty) => {{
            let arr = array.as_any().downcast_ref::<$arrty>().unwrap();
            let mut vals: Vec<f64> = Vec::with_capacity(arr.len());
            set.reserve(arr.len());
            for i in 0..arr.len() {
                if arr.is_null(i) {
                    has_null = true;
                    continue;
                }
                let v = arr.value(i) as f64;
                let key = if v.is_nan() {
                    CANONICAL_NAN_U64
                } else {
                    v.to_bits()
                };
                set.insert(key);
                vals.push(v);
            }
            vals
        }};
    }
    let vals: Vec<f64> = match array.data_type() {
        DataType::Int8 => collect_int!(Int8Array),
        DataType::Int16 => collect_int!(Int16Array),
        DataType::Int32 => collect_int!(Int32Array),
        DataType::Int64 => collect_int!(Int64Array),
        DataType::UInt8 => collect_int!(UInt8Array),
        DataType::UInt16 => collect_int!(UInt16Array),
        DataType::UInt32 => collect_int!(UInt32Array),
        DataType::UInt64 => collect_int!(UInt64Array),
        DataType::Float32 => collect_float!(Float32Array),
        DataType::Float64 => collect_float!(Float64Array),
        _ => Vec::new(),
    };
    let mut stats = stats_from_values(&vals);
    // count_distinct(mode="all"): distinct non-null values + 1 for the null slot.
    stats.n_unique = set.len() + usize::from(has_null);
    stats
}

/// Format an f64 the way Python's `str(float)` / `repr(float)` does: shortest
/// round-trip digits, an always-present decimal point in fixed notation
/// (`1.0`, not `1`), and CPython's exact fixed-vs-scientific switch (scientific
/// when the decimal point sits at position `> 16` or `<= -4`) with a
/// sign-and-2-digit-padded exponent (`1e+16`, `1e-05`). This makes the outlier
/// sample byte-match `[str(v) for v in outliers.to_list()]` for float columns.
fn py_float_str(v: f64) -> String {
    if v.is_nan() {
        return "nan".to_string();
    }
    if v.is_infinite() {
        return if v.is_sign_negative() { "-inf" } else { "inf" }.to_string();
    }
    if v == 0.0 {
        return if v.is_sign_negative() { "-0.0" } else { "0.0" }.to_string();
    }
    let neg = v < 0.0;
    // Rust's LowerExp gives shortest round-trip mantissa: e.g. "1e0",
    // "1.2345e4", "3.0000000000000004e-1".
    let e = format!("{:e}", v.abs());
    let (mant, exp_str) = e.split_once('e').expect("LowerExp always contains 'e'");
    let exp: i32 = exp_str.parse().expect("LowerExp exponent is an integer");
    let digits: String = mant.chars().filter(|c| *c != '.').collect();
    // decpt = position of the decimal point relative to the first significant
    // digit (CPython's `decpt`): mantissa is `d.ddd` so the point sits one past
    // the leading digit's 10^exp place.
    let decpt = exp + 1;
    let ndigits = digits.len() as i32;

    let body = if decpt <= -4 || decpt > 16 {
        // Scientific: d.ddde[+-]XX
        let first = &digits[0..1];
        let rest = &digits[1..];
        let mant_out = if rest.is_empty() {
            first.to_string()
        } else {
            format!("{first}.{rest}")
        };
        let exp_p = decpt - 1;
        let sign = if exp_p < 0 { '-' } else { '+' };
        format!("{mant_out}e{sign}{:02}", exp_p.abs())
    } else if decpt <= 0 {
        // 0.00ddd
        let zeros = "0".repeat((-decpt) as usize);
        format!("0.{zeros}{digits}")
    } else if decpt >= ndigits {
        // Integer-valued: trailing zeros then ".0"
        let zeros = "0".repeat((decpt - ndigits) as usize);
        format!("{digits}{zeros}.0")
    } else {
        // Decimal point inside the digit run.
        let (a, b) = digits.split_at(decpt as usize);
        format!("{a}.{b}")
    };
    if neg {
        format!("-{body}")
    } else {
        body
    }
}

/// Count values strictly outside `[lower, upper]` (i.e. `v < lower || v > upper`,
/// matching Polars `filter((s < lower) | (s > upper))`), returning the count and
/// the first-5 such values **in array order**, string-formatted per the array's
/// native dtype (Int64 -> `"1"`; Float64 -> Python `str(float)` form). NaN never
/// counts (both comparisons are false), matching Polars.
pub fn count_outside(array: &dyn Array, lower: f64, upper: f64) -> (usize, Vec<String>) {
    let mut count = 0usize;
    let mut sample: Vec<String> = Vec::new();

    macro_rules! scan {
        ($arrty:ty, $fmt:expr) => {{
            let arr = array.as_any().downcast_ref::<$arrty>().unwrap();
            for i in 0..arr.len() {
                if arr.is_null(i) {
                    continue;
                }
                let v = arr.value(i);
                let vf = v as f64;
                if vf < lower || vf > upper {
                    count += 1;
                    if sample.len() < 5 {
                        sample.push($fmt(v));
                    }
                }
            }
        }};
    }

    match array.data_type() {
        DataType::Int8 => scan!(Int8Array, |v: i8| v.to_string()),
        DataType::Int16 => scan!(Int16Array, |v: i16| v.to_string()),
        DataType::Int32 => scan!(Int32Array, |v: i32| v.to_string()),
        DataType::Int64 => scan!(Int64Array, |v: i64| v.to_string()),
        DataType::UInt8 => scan!(UInt8Array, |v: u8| v.to_string()),
        DataType::UInt16 => scan!(UInt16Array, |v: u16| v.to_string()),
        DataType::UInt32 => scan!(UInt32Array, |v: u32| v.to_string()),
        DataType::UInt64 => scan!(UInt64Array, |v: u64| v.to_string()),
        DataType::Float32 => scan!(Float32Array, |v: f32| py_float_str(v as f64)),
        DataType::Float64 => scan!(Float64Array, py_float_str),
        _ => {}
    }
    (count, sample)
}

#[cfg(test)]
mod tests {
    use super::*;
    use arrow::array::{Float64Array, Int64Array};

    fn approx(a: f64, b: f64) -> bool {
        (a - b).abs() <= 1e-9 * (1.0 + a.abs().max(b.abs()))
    }

    #[test]
    fn empty_all_nan_sentinels() {
        let s = column_numeric_stats(&Int64Array::from(Vec::<Option<i64>>::new()));
        assert_eq!(s.count_nonnull, 0);
        assert!(s.min.is_nan() && s.max.is_nan() && s.mean.is_nan() && s.std.is_nan());
        assert_eq!(s.n_unique, 0); // truly empty -> 0 distinct
    }

    #[test]
    fn n_unique_int_null_adds_one() {
        // pyarrow count_distinct([1,2,2,None,3], mode="all") == 4.
        let s = column_numeric_stats(&Int64Array::from(vec![
            Some(1),
            Some(2),
            Some(2),
            None,
            Some(3),
        ]));
        assert_eq!(s.n_unique, 4);
    }

    #[test]
    fn n_unique_nan_collapses_to_one() {
        // [1.0, NaN, NaN, 2.0] -> {1.0, NaN, 2.0} == 3.
        let s = column_numeric_stats(&Float64Array::from(vec![1.0, f64::NAN, f64::NAN, 2.0]));
        assert_eq!(s.n_unique, 3);
    }

    #[test]
    fn n_unique_signed_zero_distinct() {
        // pyarrow keeps +0.0 and -0.0 distinct -> [0.0, -0.0, 1.0] == 3.
        let s = column_numeric_stats(&Float64Array::from(vec![0.0, -0.0, 1.0]));
        assert_eq!(s.n_unique, 3);
    }

    #[test]
    fn n_unique_nan_and_null_both_present() {
        // [NaN, None, NaN, 1.0] -> {NaN, 1.0} + null == 3.
        let s = column_numeric_stats(&Float64Array::from(vec![
            Some(f64::NAN),
            None,
            Some(f64::NAN),
            Some(1.0),
        ]));
        assert_eq!(s.n_unique, 3);
    }

    #[test]
    fn n_unique_all_null() {
        // All-null numeric -> only the null slot -> 1 distinct.
        let s = column_numeric_stats(&Int64Array::from(vec![None, None, None] as Vec<Option<i64>>));
        assert_eq!(s.n_unique, 1);
    }

    #[test]
    fn single_value_std_is_nan() {
        let s = column_numeric_stats(&Float64Array::from(vec![5.0]));
        assert_eq!(s.count_nonnull, 1);
        assert!(approx(s.min, 5.0) && approx(s.max, 5.0) && approx(s.mean, 5.0));
        assert!(s.std.is_nan()); // Polars .std() == None for n<2
    }

    #[test]
    fn all_same_std_zero() {
        let s = column_numeric_stats(&Float64Array::from(vec![3.0, 3.0, 3.0]));
        assert!(approx(s.std, 0.0));
        assert!(approx(s.mean, 3.0));
    }

    #[test]
    fn ints_ddof1() {
        // Polars [1,2,3,4].std() == 1.2909944487358056 (ddof=1).
        let s = column_numeric_stats(&Int64Array::from(vec![1, 2, 3, 4]));
        assert!(approx(s.mean, 2.5));
        assert!(approx(s.std, 1.290_994_448_735_805_6));
        assert!(approx(s.min, 1.0) && approx(s.max, 4.0) && approx(s.sum, 10.0));
    }

    #[test]
    fn nulls_ignored() {
        let s = column_numeric_stats(&Int64Array::from(vec![Some(1), None, Some(3), None]));
        assert_eq!(s.count_nonnull, 2);
        assert!(approx(s.mean, 2.0) && approx(s.min, 1.0) && approx(s.max, 3.0));
    }

    #[test]
    fn nan_ignored_for_minmax_propagates_to_mean_std() {
        let s = column_numeric_stats(&Float64Array::from(vec![1.0, 2.0, f64::NAN, 3.0]));
        assert!(approx(s.min, 1.0) && approx(s.max, 3.0)); // NaN ignored
        assert!(s.mean.is_nan() && s.std.is_nan()); // NaN propagates
    }

    #[test]
    fn inf_participates_in_minmax_nans_std() {
        let s = column_numeric_stats(&Float64Array::from(vec![1.0, 2.0, f64::INFINITY, 3.0]));
        assert!(approx(s.min, 1.0));
        assert!(s.max.is_infinite() && s.max > 0.0);
        assert!(s.mean.is_infinite() && s.mean > 0.0);
        assert!(s.std.is_nan()); // inf-inf deviation -> NaN, like Polars
    }

    #[test]
    fn neg_inf_min() {
        let s = column_numeric_stats(&Float64Array::from(vec![1.0, 2.0, f64::NEG_INFINITY, 3.0]));
        assert!(s.min.is_infinite() && s.min < 0.0);
        assert!(approx(s.max, 3.0));
    }

    #[test]
    fn all_nan_minmax_nan() {
        let s = column_numeric_stats(&Float64Array::from(vec![f64::NAN, f64::NAN]));
        assert!(s.min.is_nan() && s.max.is_nan());
    }

    #[test]
    fn negatives() {
        let s = column_numeric_stats(&Int64Array::from(vec![-5, -3, -10, -1]));
        assert!(approx(s.min, -10.0) && approx(s.max, -1.0) && approx(s.sum, -19.0));
    }

    #[test]
    fn count_outside_int_format() {
        let a = Int64Array::from(vec![1, 2, 100, 3, -100, 4]);
        let (c, sample) = count_outside(&a, -10.0, 10.0);
        assert_eq!(c, 2);
        assert_eq!(sample, vec!["100".to_string(), "-100".to_string()]); // array order
    }

    #[test]
    fn count_outside_float_format_python_str() {
        let a = Float64Array::from(vec![1.0, 1000.0, 0.5, 99999.5, -1234.25]);
        let (c, sample) = count_outside(&a, -100.0, 100.0);
        assert_eq!(c, 3);
        assert_eq!(
            sample,
            vec![
                "1000.0".to_string(),
                "99999.5".to_string(),
                "-1234.25".to_string(),
            ]
        );
    }

    #[test]
    fn count_outside_sample_capped_at_5() {
        let a = Int64Array::from(vec![100, 101, 102, 103, 104, 105, 106]);
        let (c, sample) = count_outside(&a, 0.0, 10.0);
        assert_eq!(c, 7);
        assert_eq!(sample.len(), 5);
        assert_eq!(sample[0], "100");
    }

    #[test]
    fn count_outside_nan_never_counts() {
        let a = Float64Array::from(vec![f64::NAN, 5.0, 200.0]);
        let (c, sample) = count_outside(&a, -100.0, 100.0);
        assert_eq!(c, 1);
        assert_eq!(sample, vec!["200.0".to_string()]);
    }

    #[test]
    fn py_float_str_matches_cpython() {
        // Values + expected strings captured from CPython str(float).
        let cases: &[(f64, &str)] = &[
            (1.0, "1.0"),
            (1.5, "1.5"),
            (-3.5, "-3.5"),
            (1000.0, "1000.0"),
            (99999.5, "99999.5"),
            (100000.0, "100000.0"),
            (-1234.25, "-1234.25"),
            (0.5, "0.5"),
            (0.1, "0.1"),
            (1e15, "1000000000000000.0"),
            (1e16, "1e+16"),
            (1e17, "1e+17"),
            (1e-4, "0.0001"),
            (1e-5, "1e-05"),
            (1234567.0, "1234567.0"),
            (0.30000000000000004, "0.30000000000000004"),
            (123456789012345.0, "123456789012345.0"),
            (f64::INFINITY, "inf"),
            (f64::NEG_INFINITY, "-inf"),
        ];
        for &(v, want) in cases {
            assert_eq!(py_float_str(v), want, "py_float_str({v:?})");
        }
    }
}
