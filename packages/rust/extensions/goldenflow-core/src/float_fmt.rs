//! Polars-matching f64 → string formatting — the byte-parity foundation for the
//! numeric columnar path (Phase 3 wave 3 of the Polars eviction).
//!
//! Polars' `cast(Utf8)` and `write_csv` use the **same** float formatter (`ryu`
//! shortest round-trip digits) with a specific LAYOUT: fixed-point notation for a
//! value whose leading decimal exponent is in `[-5, 15]`, scientific notation
//! outside it (exponent carrying an explicit `+`/`-` sign and NO leading zero,
//! e.g. `1e-6`, `2.5e+18`). Integers keep a trailing `.0` in fixed form; the
//! scientific mantissa does not force one (`1e-6`, not `1.0e-6`).
//!
//! We reproduce it exactly by taking the digits from the SAME crate Polars uses
//! (`ryu`, so the shortest-decimal tie-breaks match by construction) and applying
//! that layout. Verified byte-identical to Polars 1.40 `cast(Utf8)` across a large
//! random f64 corpus spanning every magnitude band (`tests/float_fmt.rs`).

/// Format `x` exactly as Polars' `cast(Utf8)` / `write_csv` does.
pub fn float_to_polars_string(x: f64) -> String {
    if x.is_nan() {
        return "NaN".to_string();
    }
    if x.is_infinite() {
        return if x < 0.0 { "-inf" } else { "inf" }.to_string();
    }
    let mut buf = ryu::Buffer::new();
    let s = buf.format_finite(x);
    let neg = s.starts_with('-');
    let body = s.trim_start_matches('-');
    // ryu emits either "d.ddd", "d.ddde[-]E", or "0.0". Split mantissa / exponent.
    let (mant, exp0) = match body.split_once('e') {
        Some((m, e)) => (m, e.parse::<i32>().unwrap()),
        None => (body, 0),
    };
    // Strip the '.' to get raw significant digits, and drop trailing zeros so the
    // decimal-point placement below is exact (ryu's "0.0" → digits "0").
    let dot = mant.find('.').unwrap_or(mant.len());
    let raw: String = mant.chars().filter(|c| *c != '.').collect();
    let digits = raw.trim_end_matches('0');
    let digits = if digits.is_empty() { "0" } else { digits };
    // Decimal exponent of the LEADING digit: (#int digits - 1) + ryu's exponent.
    let lead_exp = (dot as i32 - 1) + exp0;
    let sign = if neg { "-" } else { "" };

    if (-5..=15).contains(&lead_exp) {
        // Fixed-point.
        let mut out = String::new();
        if lead_exp >= 0 {
            let intlen = (lead_exp + 1) as usize;
            if digits.len() <= intlen {
                out.push_str(digits);
                out.push_str(&"0".repeat(intlen - digits.len()));
                out.push_str(".0");
            } else {
                out.push_str(&digits[..intlen]);
                out.push('.');
                out.push_str(&digits[intlen..]);
            }
        } else {
            out.push_str("0.");
            out.push_str(&"0".repeat((-lead_exp - 1) as usize));
            out.push_str(digits);
        }
        format!("{sign}{out}")
    } else {
        // Scientific: leading digit, then the rest after a '.', then e±exp.
        let mant = if digits.len() == 1 {
            digits.to_string()
        } else {
            format!("{}.{}", &digits[..1], &digits[1..])
        };
        let esign = if lead_exp >= 0 { "+" } else { "-" };
        format!("{sign}{mant}e{esign}{}", lead_exp.abs())
    }
}
