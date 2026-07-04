//! Owned US-address kernels (pyo3-free). These are the reference
//! implementations; the Python (`goldenflow/transforms/address.py`) and TS
//! (`transforms/address.ts`) fallbacks must reproduce their bytes exactly
//! (byte-parity harness). Ported one-for-one from the existing pure-Polars /
//! per-row-Python transforms (kernel = spec under reference-mode).
//!
//! Deliberately NO regex dependency: the `\b`-word-boundary replaces, the
//! anchored unit-prefix substitutions, and the `split_address` grammar are all
//! hand-rolled so the byte output is identical across Rust/Python/JS (whose
//! regex engines differ on `\b`, greedy/non-greedy, and `replace_all`
//! semantics -- the same reason `email.rs` hand-rolls its validator). The
//! street/state/country tables are in-crate DATA replicated char-for-char to
//! the other surfaces.

/// `\w` in the Python regexes = `[A-Za-z0-9_]` (ASCII).
fn is_word_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

// ---------------------------------------------------------------------------
// Street-suffix standardize / expand
// ---------------------------------------------------------------------------

/// Full street suffix -> abbreviation, in the Python `_STREET_ABBREV` insertion
/// order (replicated exactly -- the replaces are applied sequentially, so order
/// is observable). `"Way" -> "Way"` is an intentional identity entry.
const STREET_ABBREV: [(&str, &str); 15] = [
    ("Street", "St"),
    ("Avenue", "Ave"),
    ("Boulevard", "Blvd"),
    ("Drive", "Dr"),
    ("Lane", "Ln"),
    ("Road", "Rd"),
    ("Court", "Ct"),
    ("Place", "Pl"),
    ("Circle", "Cir"),
    ("Trail", "Trl"),
    ("Way", "Way"),
    ("Parkway", "Pkwy"),
    ("Highway", "Hwy"),
    ("Terrace", "Ter"),
    ("Square", "Sq"),
];

/// Replace every word-boundary-delimited, case-insensitive occurrence of
/// `needle` in `s` with `rep`. Mirrors polars `str.replace_all(r"(?i)\b{needle}\b",
/// rep)`: a match must be bounded by a non-word char (or string edge) on both
/// sides, so `"Streets"` is NOT matched by `"Street"`. Single left-to-right,
/// non-overlapping pass; the replacement text is not re-scanned within this call
/// (matching `replace_all`). `needle` is a non-empty ASCII word.
fn replace_word_bounded(s: &str, needle: &str, rep: &str) -> String {
    let hay: Vec<char> = s.chars().collect();
    let ndl: Vec<char> = needle.chars().collect();
    let nlen = ndl.len();
    let hlen = hay.len();
    let mut out = String::with_capacity(s.len());
    let mut i = 0;
    while i < hlen {
        let mut replaced = false;
        if i + nlen <= hlen {
            let matches = (0..nlen).all(|k| hay[i + k].eq_ignore_ascii_case(&ndl[k]));
            if matches {
                let left_ok = i == 0 || !is_word_char(hay[i - 1]);
                let right_idx = i + nlen;
                let right_ok = right_idx >= hlen || !is_word_char(hay[right_idx]);
                if left_ok && right_ok {
                    out.push_str(rep);
                    i += nlen;
                    replaced = true;
                }
            }
        }
        if !replaced {
            out.push(hay[i]);
            i += 1;
        }
    }
    out
}

/// Replace full street suffixes with abbreviations (Street->St, ...).
/// Byte-identical to `address.py::address_standardize`.
pub fn address_standardize(s: &str) -> String {
    let mut out = s.to_string();
    for (full, abbr) in STREET_ABBREV {
        out = replace_word_bounded(&out, full, abbr);
    }
    out
}

/// Replace street abbreviations with full forms (St->Street, ...). Iterates the
/// REVERSED `_STREET_ABBREV` in the same insertion order (Python
/// `{v: k for k, v in _STREET_ABBREV.items()}`). Byte-identical to
/// `address.py::address_expand`.
pub fn address_expand(s: &str) -> String {
    let mut out = s.to_string();
    for (full, abbr) in STREET_ABBREV {
        // reverse direction: abbr -> full, preserving the original insertion order.
        out = replace_word_bounded(&out, abbr, full);
    }
    out
}

// ---------------------------------------------------------------------------
// US states
// ---------------------------------------------------------------------------

/// `(full name, 2-letter abbreviation)` in the Python `_STATES` insertion order.
const STATES: [(&str, &str); 51] = [
    ("Alabama", "AL"),
    ("Alaska", "AK"),
    ("Arizona", "AZ"),
    ("Arkansas", "AR"),
    ("California", "CA"),
    ("Colorado", "CO"),
    ("Connecticut", "CT"),
    ("Delaware", "DE"),
    ("Florida", "FL"),
    ("Georgia", "GA"),
    ("Hawaii", "HI"),
    ("Idaho", "ID"),
    ("Illinois", "IL"),
    ("Indiana", "IN"),
    ("Iowa", "IA"),
    ("Kansas", "KS"),
    ("Kentucky", "KY"),
    ("Louisiana", "LA"),
    ("Maine", "ME"),
    ("Maryland", "MD"),
    ("Massachusetts", "MA"),
    ("Michigan", "MI"),
    ("Minnesota", "MN"),
    ("Mississippi", "MS"),
    ("Missouri", "MO"),
    ("Montana", "MT"),
    ("Nebraska", "NE"),
    ("Nevada", "NV"),
    ("New Hampshire", "NH"),
    ("New Jersey", "NJ"),
    ("New Mexico", "NM"),
    ("New York", "NY"),
    ("North Carolina", "NC"),
    ("North Dakota", "ND"),
    ("Ohio", "OH"),
    ("Oklahoma", "OK"),
    ("Oregon", "OR"),
    ("Pennsylvania", "PA"),
    ("Rhode Island", "RI"),
    ("South Carolina", "SC"),
    ("South Dakota", "SD"),
    ("Tennessee", "TN"),
    ("Texas", "TX"),
    ("Utah", "UT"),
    ("Vermont", "VT"),
    ("Virginia", "VA"),
    ("Washington", "WA"),
    ("West Virginia", "WV"),
    ("Wisconsin", "WI"),
    ("Wyoming", "WY"),
    ("District Of Columbia", "DC"),
];

/// True if `up` (already uppercased) is one of the 51 valid state abbreviations.
fn is_valid_abbr(up: &str) -> bool {
    STATES.iter().any(|&(_, abbr)| abbr == up)
}

/// `_STATES_LOWER` lookup: full-name (lowercased) -> abbreviation.
fn state_from_full_lower(key: &str) -> Option<&'static str> {
    STATES
        .iter()
        .find(|&&(full, _)| full.eq_ignore_ascii_case(key))
        .map(|&(_, abbr)| abbr)
}

/// `_STATES_REVERSE` lookup: abbreviation -> full name.
fn state_from_abbr(up: &str) -> Option<&'static str> {
    STATES
        .iter()
        .find(|&&(_, abbr)| abbr == up)
        .map(|&(full, _)| full)
}

/// Normalize a state to its 2-letter abbreviation. Three-way fallback,
/// byte-identical to `address.py::state_abbreviate`:
///   1. a 2-char input whose uppercase is a valid abbreviation -> that uppercase,
///   2. a full name (case-insensitive) -> its abbreviation,
///   3. neither -> the ORIGINAL (unstripped) input value, unchanged.
pub fn state_abbreviate(s: &str) -> String {
    let cleaned = s.trim();
    let upper = cleaned.to_uppercase();
    if cleaned.chars().count() == 2 && is_valid_abbr(&upper) {
        return upper;
    }
    if let Some(abbr) = state_from_full_lower(&cleaned.to_lowercase()) {
        return abbr.to_string();
    }
    s.to_string()
}

/// Expand a 2-letter state abbreviation to its full name; unmatched inputs
/// return the ORIGINAL (unstripped) value. Byte-identical to
/// `address.py::state_expand`.
pub fn state_expand(s: &str) -> String {
    let key = s.trim().to_uppercase();
    match state_from_abbr(&key) {
        Some(full) => full.to_string(),
        None => s.to_string(),
    }
}

// ---------------------------------------------------------------------------
// ZIP
// ---------------------------------------------------------------------------

/// Left-pad `s` with `'0'` to width `width` (like `str.zfill` on an all-digit
/// string -- no sign handling needed since the caller guarantees digits only).
fn zfill(s: &str, width: usize) -> String {
    let len = s.chars().count();
    if len >= width {
        return s.to_string();
    }
    let mut out = String::with_capacity(width);
    for _ in 0..(width - len) {
        out.push('0');
    }
    out.push_str(s);
    out
}

/// Normalize a US ZIP to 5-digit form: strip, take the segment before the first
/// `-`, and if it is all digits zero-pad to width 5; otherwise return that
/// segment unchanged. Byte-identical to `address.py::zip_normalize`. (An empty
/// base fails the all-digit `^\d+$` test -- the `+` requires >=1 digit -- and
/// passes through unchanged.)
pub fn zip_normalize(s: &str) -> String {
    let stripped = s.trim();
    let base = stripped.split('-').next().unwrap_or("");
    if !base.is_empty() && base.chars().all(|c| c.is_ascii_digit()) {
        zfill(base, 5)
    } else {
        base.to_string()
    }
}

// ---------------------------------------------------------------------------
// Country
// ---------------------------------------------------------------------------

/// `_COUNTRIES` lookup: name/alias (trimmed-lowercased) -> ISO 3166-1 alpha-2.
fn country_lookup(key: &str) -> Option<&'static str> {
    Some(match key {
        "united states"
        | "united states of america"
        | "usa"
        | "us"
        | "u.s.a."
        | "u.s."
        | "america" => "US",
        "united kingdom" | "uk" | "great britain" | "england" | "scotland" | "wales"
        | "northern ireland" => "GB",
        "canada" | "ca" => "CA",
        "australia" | "au" => "AU",
        "germany" | "deutschland" | "de" => "DE",
        "france" | "fr" => "FR",
        "italy" | "italia" | "it" => "IT",
        "spain" | "espana" | "es" => "ES",
        "mexico" | "mx" => "MX",
        "brazil" | "brasil" | "br" => "BR",
        "japan" | "jp" => "JP",
        "china" | "cn" => "CN",
        "india" | "in" => "IN",
        "south korea" | "korea" | "kr" => "KR",
        "netherlands" | "holland" | "nl" => "NL",
        "sweden" | "se" => "SE",
        "norway" | "no" => "NO",
        "denmark" | "dk" => "DK",
        "switzerland" | "ch" => "CH",
        "ireland" | "ie" => "IE",
        "new zealand" | "nz" => "NZ",
        "singapore" | "sg" => "SG",
        "portugal" | "pt" => "PT",
        "argentina" | "ar" => "AR",
        "colombia" | "co" => "CO",
        "philippines" | "ph" => "PH",
        "poland" | "pl" => "PL",
        "belgium" | "be" => "BE",
        "austria" | "at" => "AT",
        _ => return None,
    })
}

/// Normalize a country name to its ISO 3166-1 alpha-2 code; unknown values pass
/// through UNCHANGED (the original, not the trimmed lookup key). Byte-identical
/// to `address.py::country_standardize` (`_COUNTRIES.get(val.strip().lower(), val)`).
pub fn country_standardize(s: &str) -> String {
    match country_lookup(&s.trim().to_lowercase()) {
        Some(code) => code.to_string(),
        None => s.to_string(),
    }
}

// ---------------------------------------------------------------------------
// Unit / apartment
// ---------------------------------------------------------------------------

/// Apply a leading `^(?:<tok>)\.?\s+` substitution (case-insensitive): if `s`
/// starts with one of `tokens` optionally followed by `.` then one-or-more
/// whitespace, replace that whole prefix with `rep`; else return `s`. `tokens`
/// are tried in order (mirrors the regex alternation).
fn sub_leading_token(s: &str, tokens: &[&str], rep: &str) -> String {
    let lower = s.to_ascii_lowercase(); // ASCII-only fold preserves byte offsets
    for tok in tokens {
        let tl = tok.to_ascii_lowercase();
        if lower.starts_with(&tl) {
            let rest = &s[tok.len()..];
            let after_dot = rest.strip_prefix('.').unwrap_or(rest);
            let after_ws = after_dot.trim_start();
            if after_ws.len() < after_dot.len() {
                // at least one whitespace consumed -> the `\s+` matched.
                return format!("{rep}{after_ws}");
            }
        }
    }
    s.to_string()
}

/// Apply the leading `^#\s*` -> `"Unit "` substitution (`\s*` = zero-or-more).
fn sub_leading_hash(s: &str) -> String {
    match s.strip_prefix('#') {
        Some(rest) => format!("Unit {}", rest.trim_start()),
        None => s.to_string(),
    }
}

/// Normalize unit / apartment / suite designations. Strips, then applies the
/// three anchored prefix substitutions IN ORDER (Apt/Apartment -> "Unit ",
/// Ste/Suite -> "Ste ", # -> "Unit "). Byte-identical to
/// `address.py::unit_normalize`.
pub fn unit_normalize(s: &str) -> String {
    let mut result = s.trim().to_string();
    result = sub_leading_token(&result, &["Apt", "Apartment"], "Unit ");
    result = sub_leading_token(&result, &["Ste", "Suite"], "Ste ");
    result = sub_leading_hash(&result);
    result
}

// ---------------------------------------------------------------------------
// split_address
// ---------------------------------------------------------------------------

/// Match a `^\s*([A-Za-z]{2})\s+(\d{5}(?:-\d{4})?)$` tail: after optional
/// leading whitespace, exactly 2 ASCII letters (the state), one-or-more
/// whitespace, then a 5- or 5+4-digit ZIP consuming the rest. Returns
/// `(state, zip)` or `None`.
fn parse_state_zip_tail(rem: &str) -> Option<(String, String)> {
    let after_ws: Vec<char> = rem.trim_start().chars().collect();
    if after_ws.len() < 2
        || !(after_ws[0].is_ascii_alphabetic() && after_ws[1].is_ascii_alphabetic())
    {
        return None;
    }
    let state: String = after_ws[0..2].iter().collect();
    let rest: String = after_ws[2..].iter().collect();
    let zip = rest.trim_start();
    if zip.len() == rest.len() {
        return None; // no whitespace between state and ZIP -> `\s+` failed
    }
    if is_zip(zip) {
        Some((state, zip.to_string()))
    } else {
        None
    }
}

/// Full-match `^\d{5}(-\d{4})?$`.
fn is_zip(s: &str) -> bool {
    let c: Vec<char> = s.chars().collect();
    match c.len() {
        5 => c.iter().all(|ch| ch.is_ascii_digit()),
        10 => {
            c[0..5].iter().all(|ch| ch.is_ascii_digit())
                && c[5] == '-'
                && c[6..10].iter().all(|ch| ch.is_ascii_digit())
        }
        _ => false,
    }
}

/// Parse a stripped `"street, city, ST zip"` string. `street` = up to the first
/// comma; `city` = the shortest run up to a later comma whose remainder is a
/// valid `\s*ST\s+zip$` tail (non-greedy + backtracking, mirroring the Python
/// regex). Returns `(street, city, state, zip)` or `None`.
fn try_parse_address(t: &str) -> Option<(String, String, String, String)> {
    let c1 = t.find(',')?;
    let group1 = &t[..c1];
    if group1.is_empty() {
        return None; // `(.+?)` requires >=1 char before the first comma
    }
    let after1_ws = t[c1 + 1..].trim_start(); // `\s*` before the city group
    let mut search = 0;
    while let Some(rel) = after1_ws[search..].find(',') {
        let c2 = search + rel;
        let group2 = &after1_ws[..c2];
        if !group2.is_empty() {
            if let Some((state, zip)) = parse_state_zip_tail(&after1_ws[c2 + 1..]) {
                return Some((group1.to_string(), group2.to_string(), state, zip));
            }
        }
        search = c2 + 1;
    }
    None
}

/// Parse `"street, city, state zip"` into its four parts. On a match, all four
/// are `Some`. On no match, `street` is the ORIGINAL (unstripped) input and the
/// other three are `None`. Byte-identical to `address.py::split_address` (the
/// `None`-input row -> all-`None` is handled by the marshaling layer, not here).
pub fn split_address(s: &str) -> (String, Option<String>, Option<String>, Option<String>) {
    match try_parse_address(s.trim()) {
        Some((street, city, state, zip)) => (street, Some(city), Some(state), Some(zip)),
        None => (s.to_string(), None, None, None),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn address_standardize_cases() {
        assert_eq!(address_standardize("123 Main Street"), "123 Main St");
        assert_eq!(address_standardize("1 Park Avenue"), "1 Park Ave");
        assert_eq!(address_standardize("5 Sunset Boulevard"), "5 Sunset Blvd");
        // case-insensitive match, canonical-cased replacement
        assert_eq!(address_standardize("10 elm STREET"), "10 elm St");
        // word-boundary: "Streets" must NOT be abbreviated
        assert_eq!(address_standardize("Streetsboro Road"), "Streetsboro Rd");
        // no suffix -> unchanged
        assert_eq!(address_standardize("42 Nowhere"), "42 Nowhere");
    }

    #[test]
    fn address_expand_cases() {
        assert_eq!(address_expand("123 Main St"), "123 Main Street");
        assert_eq!(address_expand("1 Park Ave"), "1 Park Avenue");
        // "St" inside "Ste" is not a word-boundary match
        assert_eq!(address_expand("1 Park Ste"), "1 Park Ste");
        // case-insensitive
        assert_eq!(address_expand("5 sunset blvd"), "5 sunset Boulevard");
    }

    #[test]
    fn state_abbreviate_cases() {
        assert_eq!(state_abbreviate("California"), "CA");
        assert_eq!(state_abbreviate("new york"), "NY");
        assert_eq!(state_abbreviate("North Carolina"), "NC");
        // already a valid 2-letter -> uppercased
        assert_eq!(state_abbreviate("ca"), "CA");
        assert_eq!(state_abbreviate("Ny"), "NY");
        assert_eq!(state_abbreviate("DC"), "DC");
        // unmatched -> ORIGINAL (unstripped) value
        assert_eq!(state_abbreviate("  Freedonia  "), "  Freedonia  ");
        // a 2-char non-abbreviation is not valid -> falls through to original
        assert_eq!(state_abbreviate("XZ"), "XZ");
    }

    #[test]
    fn state_expand_cases() {
        assert_eq!(state_expand("CA"), "California");
        assert_eq!(state_expand("ny"), "New York");
        assert_eq!(state_expand("  il  "), "Illinois");
        assert_eq!(state_expand("DC"), "District Of Columbia");
        // unmatched -> ORIGINAL (unstripped)
        assert_eq!(state_expand("  ZZ  "), "  ZZ  ");
    }

    #[test]
    fn zip_normalize_cases() {
        assert_eq!(zip_normalize("12345"), "12345");
        assert_eq!(zip_normalize("12345-6789"), "12345");
        assert_eq!(zip_normalize("  90210  "), "90210");
        // zero-pad short all-digit
        assert_eq!(zip_normalize("210"), "00210");
        // >5 all-digit returned as-is (zfill only left-pads)
        assert_eq!(zip_normalize("123456"), "123456");
        // non-numeric passthrough (the base segment before '-')
        assert_eq!(zip_normalize("SW1A"), "SW1A");
        assert_eq!(zip_normalize("SW1A-1AA"), "SW1A");
        // empty -> unchanged
        assert_eq!(zip_normalize(""), "");
    }

    #[test]
    fn country_standardize_cases() {
        assert_eq!(country_standardize("United States"), "US");
        assert_eq!(country_standardize("usa"), "US");
        assert_eq!(country_standardize("  England  "), "GB");
        assert_eq!(country_standardize("Deutschland"), "DE");
        assert_eq!(country_standardize("CA"), "CA");
        // unknown -> original, unchanged (NOT the trimmed key)
        assert_eq!(country_standardize("  Atlantis  "), "  Atlantis  ");
    }

    #[test]
    fn unit_normalize_cases() {
        assert_eq!(unit_normalize("Apt 4"), "Unit 4");
        assert_eq!(unit_normalize("Apt. 4"), "Unit 4");
        assert_eq!(unit_normalize("Apartment 12B"), "Unit 12B");
        assert_eq!(unit_normalize("Suite 200"), "Ste 200");
        assert_eq!(unit_normalize("Ste. 200"), "Ste 200");
        assert_eq!(unit_normalize("#5"), "Unit 5");
        assert_eq!(unit_normalize("# 5"), "Unit 5");
        // case-insensitive
        assert_eq!(unit_normalize("APT 9"), "Unit 9");
        // no leading whitespace after the token -> no match (the `\s+`)
        assert_eq!(unit_normalize("Apt.5"), "Apt.5");
        // "Aptos" starts with "Apt" but no dot/ws boundary -> unchanged
        assert_eq!(unit_normalize("Aptos"), "Aptos");
        // no designator -> just trimmed
        assert_eq!(unit_normalize("  Building C  "), "Building C");
    }

    #[test]
    fn split_address_match() {
        assert_eq!(
            split_address("123 Main St, Springfield, IL 62704"),
            (
                "123 Main St".to_string(),
                Some("Springfield".to_string()),
                Some("IL".to_string()),
                Some("62704".to_string()),
            )
        );
        // +4 ZIP
        assert_eq!(
            split_address("1 Park Ave, New York, NY 10001-2345"),
            (
                "1 Park Ave".to_string(),
                Some("New York".to_string()),
                Some("NY".to_string()),
                Some("10001-2345".to_string()),
            )
        );
        // leading/trailing whitespace stripped before parsing
        assert_eq!(
            split_address("  9 Elm Rd, Denver, CO 80014  "),
            (
                "9 Elm Rd".to_string(),
                Some("Denver".to_string()),
                Some("CO".to_string()),
                Some("80014".to_string()),
            )
        );
    }

    #[test]
    fn split_address_multi_comma_backtracks() {
        // city group backtracks past an interior comma to reach a valid tail
        assert_eq!(
            split_address("123 Main St, Apt 4, Springfield, IL 62704"),
            (
                "123 Main St".to_string(),
                Some("Apt 4, Springfield".to_string()),
                Some("IL".to_string()),
                Some("62704".to_string()),
            )
        );
    }

    #[test]
    fn split_address_no_match() {
        // no match -> street = ORIGINAL (unstripped), rest None
        assert_eq!(
            split_address("  just a street  "),
            ("  just a street  ".to_string(), None, None, None)
        );
        // one comma only (regex needs two) -> no match
        assert_eq!(
            split_address("123 Main St, IL 62704"),
            ("123 Main St, IL 62704".to_string(), None, None, None)
        );
        // 3-letter "state" -> tail fails
        assert_eq!(
            split_address("123 Main St, Springfield, ILL 62704"),
            (
                "123 Main St, Springfield, ILL 62704".to_string(),
                None,
                None,
                None
            )
        );
    }
}
