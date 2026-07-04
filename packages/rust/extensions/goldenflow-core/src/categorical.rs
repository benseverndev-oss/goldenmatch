//! Owned categorical kernels (pyo3-free): boolean normalization, gender
//! standardization, null-sentinel standardization, and the shared
//! key-normalization step used by the two mapping-based transforms
//! (`category_standardize` / `category_from_file`).
//!
//! **Split between owned LOGIC and runtime DATA (load-bearing design
//! decision, see the goldenflow D5 wave):** `category_standardize` and
//! `category_from_file` apply a caller-supplied variant->canonical mapping
//! (a dict built from a function param, or loaded from a CSV/YAML file at
//! runtime). That mapping is DATA, not logic, and has no sensible Rust
//! representation shared across Python/TS/WASM call sites -- so this crate
//! does NOT own the dict lookup. What IS shared, deterministic logic is the
//! NORMALIZATION applied to a raw value before it's used as a lookup key
//! (`category_normalize_key`, below) -- identical to what `null_standardize`
//! and `gender_standardize` do internally before their own (fixed, in-crate)
//! lookups. Owning that one function keeps the key-derivation byte-identical
//! across surfaces while the mapping-application loop stays in
//! Python/TS, where it belongs.
//!
//! These are the reference implementations; the Python/TS fallbacks must
//! reproduce their bytes exactly (byte-parity harness,
//! `tests/parity/identifiers_corpus.jsonl`).

const TRUE_VALUES: &[&str] = &["yes", "y", "1", "true", "t"];
const FALSE_VALUES: &[&str] = &["no", "n", "0", "false", "f"];
const NULL_VALUES: &[&str] = &["n/a", "null", "none", "na", "nil", "nan", "-", ""];

/// Trim + lowercase -- the shared key-normalization step used before any of
/// this module's lookups (fixed in-crate maps for `gender_standardize`/
/// `null_standardize`, and the caller-supplied mapping for
/// `category_standardize`/`category_from_file`).
pub fn category_normalize_key(s: &str) -> String {
    s.trim().to_lowercase()
}

/// Parse a loose boolean-ish string. `Some(true)`/`Some(false)` on a
/// recognized token (case/whitespace-insensitive); `None` for anything else
/// (including empty string).
pub fn boolean_normalize(s: &str) -> Option<bool> {
    let key = category_normalize_key(s);
    if TRUE_VALUES.contains(&key.as_str()) {
        Some(true)
    } else if FALSE_VALUES.contains(&key.as_str()) {
        Some(false)
    } else {
        None
    }
}

/// Standardize a gender string to `"M"`/`"F"` via a fixed lookup
/// (`male`/`m` -> `M`, `female`/`f` -> `F`); any other value passes through
/// UNCHANGED (the original string, not the normalized key) -- mirrors the
/// Python `dict.get(key, val)` fallback semantics.
pub fn gender_standardize(s: &str) -> String {
    let key = category_normalize_key(s);
    match key.as_str() {
        "male" | "m" => "M".to_string(),
        "female" | "f" => "F".to_string(),
        _ => s.to_string(),
    }
}

/// Map a null-sentinel string (`n/a`, `null`, `none`, `na`, `nil`, `nan`,
/// `-`, or empty, case/whitespace-insensitive) to `None`; any other value
/// passes through as `Some(original)` -- mirrors the Python
/// `None if key in NULL_VALUES else val` semantics.
pub fn null_standardize(s: &str) -> Option<String> {
    let key = category_normalize_key(s);
    if NULL_VALUES.contains(&key.as_str()) {
        None
    } else {
        Some(s.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn category_normalize_key_basic() {
        assert_eq!(category_normalize_key("  Yes  "), "yes");
        assert_eq!(category_normalize_key("USA"), "usa");
        assert_eq!(category_normalize_key(""), "");
        assert_eq!(category_normalize_key("MiXeD Case"), "mixed case");
    }

    #[test]
    fn boolean_normalize_basic() {
        assert_eq!(boolean_normalize("Yes"), Some(true));
        assert_eq!(boolean_normalize("Y"), Some(true));
        assert_eq!(boolean_normalize("1"), Some(true));
        assert_eq!(boolean_normalize("True"), Some(true));
        assert_eq!(boolean_normalize("true"), Some(true));
        assert_eq!(boolean_normalize(" t "), Some(true));
        assert_eq!(boolean_normalize("No"), Some(false));
        assert_eq!(boolean_normalize("N"), Some(false));
        assert_eq!(boolean_normalize("0"), Some(false));
        assert_eq!(boolean_normalize("false"), Some(false));
        assert_eq!(boolean_normalize("f"), Some(false));
        assert_eq!(boolean_normalize("maybe"), None);
        assert_eq!(boolean_normalize(""), None);
    }

    #[test]
    fn gender_standardize_basic() {
        assert_eq!(gender_standardize("Male"), "M");
        assert_eq!(gender_standardize("male"), "M");
        assert_eq!(gender_standardize("M"), "M");
        assert_eq!(gender_standardize("m"), "M");
        assert_eq!(gender_standardize("Female"), "F");
        assert_eq!(gender_standardize("female"), "F");
        assert_eq!(gender_standardize("F"), "F");
        assert_eq!(gender_standardize("f"), "F");
        // Unrecognized: passes through UNCHANGED (original, not lowercased).
        assert_eq!(gender_standardize("Nonbinary"), "Nonbinary");
        assert_eq!(gender_standardize(""), "");
    }

    #[test]
    fn null_standardize_basic() {
        assert_eq!(null_standardize("N/A"), None);
        assert_eq!(null_standardize("NULL"), None);
        assert_eq!(null_standardize("none"), None);
        assert_eq!(null_standardize(""), None);
        assert_eq!(null_standardize("  "), None); // trims to empty
        assert_eq!(null_standardize("null"), None);
        assert_eq!(null_standardize("NA"), None);
        assert_eq!(null_standardize("nil"), None);
        assert_eq!(null_standardize("nan"), None);
        assert_eq!(null_standardize("-"), None);
        // Passes through UNCHANGED (original, not lowercased).
        assert_eq!(
            null_standardize("actual value"),
            Some("actual value".to_string())
        );
    }
}
