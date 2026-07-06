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

/// The canonical Roman-numeral suffix would be complex to validate loosely, so
/// this parses to an integer then round-trips: a value only validates if it
/// re-encodes to exactly the (uppercased, trimmed) input. Range 1..=3999.
/// `None` for empty / non-Roman chars / malformed forms (e.g. `IIII`).
pub fn roman_to_int(s: &str) -> Option<i64> {
    let t: String = s.trim().to_uppercase();
    if t.is_empty() {
        return None;
    }
    let val = |c: char| -> Option<i64> {
        Some(match c {
            'I' => 1,
            'V' => 5,
            'X' => 10,
            'L' => 50,
            'C' => 100,
            'D' => 500,
            'M' => 1000,
            _ => return None,
        })
    };
    let chars: Vec<char> = t.chars().collect();
    let mut total = 0i64;
    for i in 0..chars.len() {
        let cur = val(chars[i])?;
        let nxt = if i + 1 < chars.len() {
            val(chars[i + 1])?
        } else {
            0
        };
        if cur < nxt {
            total -= cur;
        } else {
            total += cur;
        }
    }
    if !(1..=3999).contains(&total) {
        return None;
    }
    // Round-trip validation rejects non-canonical forms (IIII, VX, ...).
    if int_to_roman(total) == t {
        Some(total)
    } else {
        None
    }
}

/// Encode 1..=3999 as a canonical Roman numeral (helper for `roman_to_int`'s
/// round-trip check).
fn int_to_roman(mut n: i64) -> String {
    const TABLE: &[(i64, &str)] = &[
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    ];
    let mut out = String::new();
    for &(v, sym) in TABLE {
        while n >= v {
            out.push_str(sym);
            n -= v;
        }
    }
    out
}

/// Parse a fraction or mixed number to `f64`: `1/2` -> 0.5, `3 3/4` -> 3.75,
/// `-1/2` -> -0.5, a plain number (`5`, `2.5`) -> itself. `None` on parse
/// failure or division by zero.
pub fn fraction_to_decimal(s: &str) -> Option<f64> {
    let t = s.trim();
    if t.is_empty() {
        return None;
    }
    // Mixed number: "<whole> <num>/<den>".
    if let Some((whole_str, frac_str)) = t.split_once(char::is_whitespace) {
        let frac_str = frac_str.trim();
        if frac_str.contains('/') {
            let whole: i64 = whole_str.trim().parse().ok()?;
            let frac = parse_fraction(frac_str)?;
            return Some(if whole < 0 {
                whole as f64 - frac
            } else {
                whole as f64 + frac
            });
        }
        return None; // whitespace but not a mixed fraction -> not parseable
    }
    if t.contains('/') {
        return parse_fraction(t);
    }
    t.parse::<f64>().ok()
}

/// Parse a bare `num/den` fraction to `f64`. `None` on parse failure or a zero
/// denominator.
fn parse_fraction(s: &str) -> Option<f64> {
    let (num, den) = s.split_once('/')?;
    let num: f64 = num.trim().parse().ok()?;
    let den: f64 = den.trim().parse().ok()?;
    if den == 0.0 {
        return None;
    }
    Some(num / den)
}

/// Parse an English ordinal (`1st`, `22nd`, `3RD`, `11th`) to its integer.
/// The suffix must be the CORRECT one for the number (so `1th` -> `None`). A
/// bare number with no suffix is NOT an ordinal (`5` -> `None`). `None` for
/// empty / malformed input.
pub fn ordinal_to_int(s: &str) -> Option<i64> {
    let t = s.trim().to_lowercase();
    let digits: String = t.chars().take_while(char::is_ascii_digit).collect();
    if digits.is_empty() {
        return None;
    }
    let n: i64 = digits.parse().ok()?;
    let suffix = &t[digits.len()..];
    if suffix == ordinal_suffix(n) {
        Some(n)
    } else {
        None
    }
}

/// The English ordinal suffix for `n` (`st`/`nd`/`rd`/`th`).
fn ordinal_suffix(n: i64) -> &'static str {
    let m = n % 100;
    if (11..=13).contains(&m) {
        return "th";
    }
    match n % 10 {
        1 => "st",
        2 => "nd",
        3 => "rd",
        _ => "th",
    }
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
