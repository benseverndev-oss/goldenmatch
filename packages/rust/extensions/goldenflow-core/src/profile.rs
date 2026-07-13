//! Owned auto-detect profiling kernel — the type-inference DECISION behind
//! GoldenFlow's zero-config `transform_df(config=None)`. Byte-parity reference
//! for `_infer_type` / `_infer_type_list`. The column-NAME override
//! (`_override_type_by_column_name`) stays in the Python/TS caller — this kernel
//! is a pure function of column VALUES.

use std::collections::HashSet;

/// The caller's dtype/value knowledge, mapped into the kernel. `Numeric` /
/// `Boolean` / `Date` short-circuit the decision; `Utf8` runs the regex block.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum TypeHint {
    Utf8,
    Numeric,
    Boolean,
    Date,
}

/// Map a caller-supplied hint string to `TypeHint` (shared by native-flow +
/// goldenflow-wasm so all surfaces agree). Unknown → `Utf8` (run the regexes).
pub fn hint_from_str(h: &str) -> TypeHint {
    match h {
        "numeric" => TypeHint::Numeric,
        "boolean" => TypeHint::Boolean,
        "date" => TypeHint::Date,
        _ => TypeHint::Utf8,
    }
}

/// Full single-pass column profile for the columnar (Arrow) path.
pub struct ColumnProfileOut {
    pub null_count: u64,
    pub unique_count: u64,
    pub samples: Vec<String>,
    pub inferred_type: String,
}

/// A single matcher entry: (type label, predicate over one stripped value,
/// hit-ratio threshold to win).
type Check = (&'static str, fn(&str) -> bool, f64);

// (email 0.7, zip 0.7, date 0.5, phone 0.6, name 0.5) — order = most-specific first.
const CHECKS: &[Check] = &[
    ("email", is_email, 0.7),
    ("zip", is_zip, 0.7),
    ("date", is_date, 0.5),
    ("phone", is_phone, 0.6),
    ("name", is_name, 0.5),
];

/// Infer a column's semantic type from its VALUES + the caller's `hint`.
///
/// `Numeric`/`Boolean`/`Date` hints short-circuit. `Utf8` samples the first
/// ≤100 non-null values, trims + drops stripped-empties, and runs the five
/// matchers most-specific-first; the first whose hit-ratio meets its threshold
/// wins, else `"string"`.
pub fn infer_type(values: &[Option<&str>], hint: TypeHint) -> String {
    match hint {
        TypeHint::Numeric => return "numeric".into(),
        TypeHint::Boolean => return "boolean".into(),
        TypeHint::Date => return "date".into(),
        TypeHint::Utf8 => {}
    }
    // sample = first 100 non-null; then strip + drop empties (mirror the Python order)
    let sample: Vec<&str> = values.iter().flatten().copied().take(100).collect();
    let stripped: Vec<&str> = sample
        .iter()
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .collect();
    if stripped.is_empty() {
        return "string".into();
    }
    let n = stripped.len() as f64;
    for (name, matcher, threshold) in CHECKS {
        let hits = stripped.iter().filter(|v| matcher(v)).count() as f64;
        if hits / n >= *threshold {
            return (*name).into();
        }
    }
    "string".into()
}

/// Full column profile in one pass: null/unique/first-5-samples over the RAW
/// values plus `infer_type`. Used by the columnar (Arrow) path; the list path
/// computes null/unique/samples in Python over raw values to dodge the
/// `[1,"1"]` stringify-collision.
pub fn profile_column(values: &[Option<&str>], hint: TypeHint) -> ColumnProfileOut {
    let mut null_count = 0u64;
    let mut seen: HashSet<&str> = HashSet::new();
    let mut samples: Vec<String> = Vec::with_capacity(5);
    for v in values {
        match v {
            None => null_count += 1,
            Some(s) => {
                seen.insert(s);
                if samples.len() < 5 {
                    samples.push((*s).to_string());
                }
            }
        }
    }
    ColumnProfileOut {
        null_count,
        unique_count: seen.len() as u64,
        samples,
        inferred_type: infer_type(values, hint),
    }
}

// --- Matchers (hand-rolled to the exact Python regex semantics; NO regex crate).
// ASCII semantics: `\d` = [0-9] = is_ascii_digit; `\s` = char::is_whitespace.

/// `_EMAIL_RE = ^[^@\s]+@[^@\s]+\.[^@\s]+$` — reuse the byte-identical
/// hand-rolled `email_validate` (Task-1 vector asserts agreement).
fn is_email(s: &str) -> bool {
    crate::email::email_validate(s) == Some(true)
}

/// `_ZIP_RE = ^\d{5}(-\d{4})?$` — exactly 5 digits, optional `-` + 4 digits.
fn is_zip(s: &str) -> bool {
    let b = s.as_bytes();
    match b.len() {
        5 => b.iter().all(u8::is_ascii_digit),
        10 => {
            b[..5].iter().all(u8::is_ascii_digit)
                && b[5] == b'-'
                && b[6..].iter().all(u8::is_ascii_digit)
        }
        _ => false,
    }
}

/// `_PHONE_RE = ^[\+\(]?[\d][\d\(\)\-\.\s]{6,18}\d$` — optional leading `+`/`(`,
/// a digit, 6..=18 chars from `[\d()\-.\s]`, a trailing digit.
fn is_phone(s: &str) -> bool {
    let c: Vec<char> = s.chars().collect();
    let n = c.len();
    if n == 0 {
        return false;
    }
    let mut i = 0;
    // optional leading + or (
    if c[i] == '+' || c[i] == '(' {
        i += 1;
    }
    // leading digit
    if i >= n || !c[i].is_ascii_digit() {
        return false;
    }
    i += 1;
    // trailing digit (distinct from the leading digit)
    if i > n - 1 || !c[n - 1].is_ascii_digit() {
        return false;
    }
    // middle = c[i .. n-1], must be 6..=18 chars from the class
    let middle = &c[i..n - 1];
    if middle.len() < 6 || middle.len() > 18 {
        return false;
    }
    middle.iter().all(|&ch| {
        ch.is_ascii_digit() || ch == '(' || ch == ')' || ch == '-' || ch == '.' || ch.is_whitespace()
    })
}

/// `_DATE_RE = ^(\d{4}[-/]\d{1,2}[-/]\d{1,2} | \d{1,2}[-/]\d{1,2}[-/]\d{2,4} |
/// [A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})$` — three alternatives (whitespace added
/// here only for readability).
fn is_date(s: &str) -> bool {
    let c: Vec<char> = s.chars().collect();
    date_alt1(&c) || date_alt2(&c) || date_alt3(&c)
}

// \d{4}[-/]\d{1,2}[-/]\d{1,2}
fn date_alt1(c: &[char]) -> bool {
    let mut i = 0;
    take_digits(c, &mut i, 4, 4)
        && take_sep(c, &mut i)
        && take_digits(c, &mut i, 1, 2)
        && take_sep(c, &mut i)
        && take_digits(c, &mut i, 1, 2)
        && i == c.len()
}

// \d{1,2}[-/]\d{1,2}[-/]\d{2,4}
fn date_alt2(c: &[char]) -> bool {
    let mut i = 0;
    take_digits(c, &mut i, 1, 2)
        && take_sep(c, &mut i)
        && take_digits(c, &mut i, 1, 2)
        && take_sep(c, &mut i)
        && take_digits(c, &mut i, 2, 4)
        && i == c.len()
}

// [A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}
fn date_alt3(c: &[char]) -> bool {
    let mut i = 0;
    if !take_letters(c, &mut i, 3, 9) || !take_ws(c, &mut i) || !take_digits(c, &mut i, 1, 2) {
        return false;
    }
    if i < c.len() && c[i] == ',' {
        i += 1; // ,?
    }
    take_ws(c, &mut i) && take_digits(c, &mut i, 4, 4) && i == c.len()
}

/// `_NAME_RE = ^[A-Z][a-z]+(\s+[A-Z][a-z]+)+$` — a Titlecased word followed by
/// one or more whitespace-separated Titlecased words.
fn is_name(s: &str) -> bool {
    let c: Vec<char> = s.chars().collect();
    let mut i = 0;
    if !take_name_word(&c, &mut i) {
        return false;
    }
    let mut groups = 0u32;
    loop {
        let save = i;
        if !take_ws(&c, &mut i) || !take_name_word(&c, &mut i) {
            i = save;
            break;
        }
        groups += 1;
    }
    groups >= 1 && i == c.len()
}

// --- Cursor helpers (greedy; the classes they consume are disjoint from what
// follows, so greedy consumption is equivalent to the regex without backtracking).

/// Consume greedily up to `max` ASCII digits; succeed iff at least `min` taken.
fn take_digits(c: &[char], i: &mut usize, min: usize, max: usize) -> bool {
    let start = *i;
    while *i < c.len() && (*i - start) < max && c[*i].is_ascii_digit() {
        *i += 1;
    }
    (*i - start) >= min
}

/// Consume greedily up to `max` ASCII letters; succeed iff at least `min` taken.
fn take_letters(c: &[char], i: &mut usize, min: usize, max: usize) -> bool {
    let start = *i;
    while *i < c.len() && (*i - start) < max && c[*i].is_ascii_alphabetic() {
        *i += 1;
    }
    (*i - start) >= min
}

/// `[-/]` — a single dash or slash separator.
fn take_sep(c: &[char], i: &mut usize) -> bool {
    if *i < c.len() && (c[*i] == '-' || c[*i] == '/') {
        *i += 1;
        true
    } else {
        false
    }
}

/// `\s+` — one or more whitespace chars.
fn take_ws(c: &[char], i: &mut usize) -> bool {
    let start = *i;
    while *i < c.len() && c[*i].is_whitespace() {
        *i += 1;
    }
    *i > start
}

/// `[A-Z][a-z]+` — one uppercase letter then one or more lowercase letters.
fn take_name_word(c: &[char], i: &mut usize) -> bool {
    if *i >= c.len() || !c[*i].is_ascii_uppercase() {
        return false;
    }
    *i += 1;
    let start = *i;
    while *i < c.len() && c[*i].is_ascii_lowercase() {
        *i += 1;
    }
    *i > start
}

#[cfg(test)]
mod tests {
    use super::*;

    fn t(vals: &[&str], hint: TypeHint) -> String {
        let v: Vec<Option<&str>> = vals.iter().map(|s| Some(*s)).collect();
        infer_type(&v, hint)
    }

    #[test]
    fn hint_short_circuits_skip_regex() {
        assert_eq!(t(&["2020-01-01"], TypeHint::Numeric), "numeric");
        assert_eq!(t(&["x"], TypeHint::Boolean), "boolean");
        assert_eq!(t(&["whatever"], TypeHint::Date), "date");
    }
    #[test]
    fn empty_and_all_blank_is_string() {
        assert_eq!(infer_type(&[None, None], TypeHint::Utf8), "string");
        assert_eq!(t(&["   ", ""], TypeHint::Utf8), "string"); // stripped-empty skipped
    }
    #[test]
    fn email_matcher() {
        // threshold 0.7
        assert_eq!(t(&["a@b.co", "x@y.io", "p@q.net"], TypeHint::Utf8), "email");
        assert_eq!(t(&["a@b"], TypeHint::Utf8), "string"); // no dot
        assert_eq!(t(&["a b@c.co"], TypeHint::Utf8), "string"); // whitespace
    }
    #[test]
    fn zip_matcher() {
        // threshold 0.7, checked BEFORE date/phone
        assert_eq!(t(&["12345", "90210-1234"], TypeHint::Utf8), "zip");
        assert_eq!(t(&["1234"], TypeHint::Utf8), "string"); // 4 digits
        // "12345-12" is NOT a valid ZIP (bad +4), so ZIP (checked first) does
        // not fire. But it IS 8 digit/punct chars, so `_PHONE_RE` matches it in
        // the Python reference — the byte-parity contract classifies it "phone".
        // (Verified against profiler_bridge.py's `_PHONE_RE.match`.)
        assert_eq!(t(&["12345-12"], TypeHint::Utf8), "phone");
    }
    #[test]
    fn date_matcher() {
        // threshold 0.5
        assert_eq!(t(&["2020-01-02", "1999/12/31"], TypeHint::Utf8), "date"); // yyyy-m-d
        assert_eq!(t(&["1/2/99", "12-31-2020"], TypeHint::Utf8), "date"); // m/d/yy(yy)
        assert_eq!(
            t(&["January 2, 2020", "Mar 3 1999"], TypeHint::Utf8),
            "date"
        ); // month name
        assert_eq!(t(&["2020"], TypeHint::Utf8), "string");
    }
    #[test]
    fn phone_matcher() {
        // threshold 0.6 ; 8..=20 chars, digit-bordered
        assert_eq!(
            t(&["(212) 555-1234", "+1 415 555 9999"], TypeHint::Utf8),
            "phone"
        );
        assert_eq!(t(&["12"], TypeHint::Utf8), "string"); // too short
        assert_eq!(t(&["abc-defg"], TypeHint::Utf8), "string");
    }
    #[test]
    fn name_matcher() {
        // threshold 0.5 ; Titlecased multi-word
        assert_eq!(t(&["John Smith", "Jane Marie Doe"], TypeHint::Utf8), "name");
        assert_eq!(t(&["john smith"], TypeHint::Utf8), "string"); // lowercase
        assert_eq!(t(&["John"], TypeHint::Utf8), "string"); // single word
    }
    #[test]
    fn most_specific_first_and_threshold() {
        // 1 email of 3 = 0.33 < 0.7 -> not email; falls through to string
        assert_eq!(t(&["a@b.co", "foo", "bar"], TypeHint::Utf8), "string");
        // zip beats date: "12345" matches ZIP (checked first)
        assert_eq!(t(&["12345", "12345", "12345"], TypeHint::Utf8), "zip");
    }
    #[test]
    fn only_first_100_sampled() {
        let mut v: Vec<Option<&str>> = vec![Some("a@b.co"); 100];
        v.extend(vec![Some("not-an-email"); 100]); // ignored (beyond 100)
        assert_eq!(infer_type(&v, TypeHint::Utf8), "email");
    }
    #[test]
    fn email_matcher_agrees_with_email_validate() {
        for s in ["a@b.co", "x@y", "no at", "a@b.c.d"] {
            let via_profile = is_email(s);
            let via_email = crate::email::email_validate(s) == Some(true);
            assert_eq!(via_profile, via_email, "mismatch on {s:?}");
        }
    }
}
