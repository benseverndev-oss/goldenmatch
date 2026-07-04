//! Owned numeric kernels (pyo3-free): string->number parsers (currency,
//! percentage, integer-truncation, EU comma-decimal, scientific notation) and
//! numeric-array ops (round, clamp, abs, fill-null-with-zero). These are the
//! reference implementations; the Python/TS fallbacks must reproduce their
//! VALUES exactly (byte/value-parity harness -- numeric comparison, not
//! string repr, since this family outputs floats/ints).
//!
//! Deliberately NOT using a `regex` crate dependency (mirrors the other
//! goldenflow-core kernels' no-regex policy) -- `currency_strip` hand-filters
//! chars instead of `[^\d.\-]` regex-replace.

/// Strip everything except ASCII digits, `.`, and `-`, then parse as `f64`.
/// `None` on parse failure (mirrors Polars `cast(Float64, strict=False)`
/// null-on-failure semantics).
pub fn currency_strip(s: &str) -> Option<f64> {
    let filtered: String = s
        .chars()
        .filter(|c| c.is_ascii_digit() || *c == '.' || *c == '-')
        .collect();
    filtered.parse::<f64>().ok()
}

/// Trim whitespace, strip trailing `%` character(s), trim again (in case
/// removing `%` exposes more whitespace), parse as `f64`, and divide by 100.
/// `None` on parse failure.
pub fn percentage_normalize(s: &str) -> Option<f64> {
    let trimmed = s.trim();
    let stripped = trimmed.trim_end_matches('%');
    let stripped = stripped.trim();
    stripped.parse::<f64>().ok().map(|v| v / 100.0)
}

/// Parse a string to `f64` then truncate toward zero to `i64` (mirrors the
/// old `int(float(val))` semantics / Polars `cast(Float64).cast(Int64)`,
/// both strict=False). `None` on parse failure.
pub fn to_integer(s: &str) -> Option<i64> {
    s.trim().parse::<f64>().ok().map(|v| v as i64)
}

/// Convert a European decimal format (`1.234,56`) to a plain `f64`
/// (`1234.56`). If the (trimmed) input has no comma, parse as-is (US format
/// or a plain number). `None` on parse failure.
pub fn comma_decimal(s: &str) -> Option<f64> {
    let trimmed = s.trim();
    if !trimmed.contains(',') {
        return trimmed.parse::<f64>().ok();
    }
    let converted = trimmed.replace('.', "").replace(',', ".");
    converted.parse::<f64>().ok()
}

/// Convert scientific notation (`1.5e3`) to a plain `f64` (`1500.0`). Trims
/// whitespace first; `None` on parse failure. Rust's `f64::from_str` already
/// accepts scientific notation, so this is just a trimmed parse.
pub fn scientific_to_decimal(s: &str) -> Option<f64> {
    s.trim().parse::<f64>().ok()
}

/// Round-half-away-from-zero at the `n`-th decimal place, computed via
/// multiply/round/divide (the KERNEL is the source of truth for this
/// rounding rule -- Python's fallback and TS's fallback must replicate this
/// exact formula, not their language's built-in `round()`, since e.g.
/// Python's `round()` uses round-half-to-even). `n` may be negative (rounds
/// to the left of the decimal point).
pub fn round_f64(x: f64, n: i32) -> f64 {
    let factor = 10f64.powi(n);
    let scaled = x * factor;
    let rounded = if scaled >= 0.0 {
        (scaled + 0.5).floor()
    } else {
        (scaled - 0.5).ceil()
    };
    rounded / factor
}

/// Clamp `x` into `[min_val, max_val]`.
pub fn clamp_f64(x: f64, min_val: f64, max_val: f64) -> f64 {
    if x < min_val {
        min_val
    } else if x > max_val {
        max_val
    } else {
        x
    }
}

/// Absolute value.
pub fn abs_f64(x: f64) -> f64 {
    x.abs()
}

/// Replace a null value with `0.0`; a present value passes through
/// unchanged. Operates on `Option<f64>` since this is fundamentally a
/// null-handling op, not a value transform.
pub fn fill_zero(x: Option<f64>) -> f64 {
    x.unwrap_or(0.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn currency_strip_basic() {
        assert_eq!(currency_strip("$1,234.56"), Some(1234.56));
        assert_eq!(currency_strip("-$42.00"), Some(-42.0));
        assert_eq!(currency_strip("USD 100"), Some(100.0));
        assert_eq!(currency_strip(""), None);
        assert_eq!(currency_strip("abc"), None);
    }

    #[test]
    fn percentage_normalize_basic() {
        assert_eq!(percentage_normalize("50%"), Some(0.5));
        assert_eq!(percentage_normalize(" 12.5 % "), Some(0.125));
        assert_eq!(percentage_normalize("100"), Some(1.0));
        assert_eq!(percentage_normalize("abc%"), None);
        assert_eq!(percentage_normalize(""), None);
    }

    #[test]
    fn to_integer_basic() {
        assert_eq!(to_integer("42"), Some(42));
        assert_eq!(to_integer("42.9"), Some(42));
        assert_eq!(to_integer("-3.5"), Some(-3));
        assert_eq!(to_integer("abc"), None);
        assert_eq!(to_integer(""), None);
    }

    #[test]
    fn comma_decimal_basic() {
        assert_eq!(comma_decimal("1.234,56"), Some(1234.56));
        assert_eq!(comma_decimal("1234.56"), Some(1234.56));
        assert_eq!(comma_decimal("42"), Some(42.0));
        assert_eq!(comma_decimal("abc,de"), None);
        assert_eq!(comma_decimal(""), None);
    }

    #[test]
    fn scientific_to_decimal_basic() {
        assert_eq!(scientific_to_decimal("1.5e3"), Some(1500.0));
        assert_eq!(scientific_to_decimal(" 2E-2 "), Some(0.02));
        assert_eq!(scientific_to_decimal("abc"), None);
        assert_eq!(scientific_to_decimal(""), None);
    }

    #[test]
    fn round_f64_basic() {
        assert_eq!(round_f64(2.345, 2), 2.35);
        // 2.005 is not exactly representable; its nearest f64 is slightly
        // ABOVE 2.005, so *100 rounds up (a real float-representation
        // quirk, not a bug -- Python's `float("2.005")` has the same value).
        assert_eq!(round_f64(2.005, 2), 2.01);
        assert_eq!(round_f64(-2.345, 2), -2.35);
        assert_eq!(round_f64(1234.0, -2), 1200.0);
    }

    #[test]
    fn clamp_f64_basic() {
        assert_eq!(clamp_f64(0.5, 0.0, 1.0), 0.5);
        assert_eq!(clamp_f64(-0.5, 0.0, 1.0), 0.0);
        assert_eq!(clamp_f64(1.5, 0.0, 1.0), 1.0);
    }

    #[test]
    fn abs_f64_basic() {
        assert_eq!(abs_f64(-3.5), 3.5);
        assert_eq!(abs_f64(3.5), 3.5);
    }

    #[test]
    fn fill_zero_basic() {
        assert_eq!(fill_zero(None), 0.0);
        assert_eq!(fill_zero(Some(5.0)), 5.0);
    }
}
