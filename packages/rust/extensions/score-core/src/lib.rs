//! Canonical string scorers backed by the `rapidfuzz` Rust crate — the single
//! source of truth shared (by construction) between the `goldenmatch._native`
//! PyO3 extension and the `datafusion-udf` FFI ScalarUDFs. Both link this crate,
//! so the per-pair scoring is identical across surfaces; parity is structural,
//! not asserted after the fact.
//!
//! This crate is intentionally pyo3-free. The `native` crate keeps thin
//! `#[pyfunction]` shims that delegate here; the FFI UDFs call these `pub fn`s
//! directly. All functions operate on Unicode chars (codepoints), matching
//! rapidfuzz.
use rapidfuzz::distance::{damerau_levenshtein, jaro_winkler, levenshtein};
use rapidfuzz::fuzz;
use unicode_normalization::UnicodeNormalization;
// `alias_match` (score_one id 8) + its `regex`-backed strip_legal_form live behind
// the default `alias` feature; `pub use` re-exports the public fns at the crate
// root so callers keep the flat `goldenmatch_score_core::{alias_match, ...}` path.
#[cfg(feature = "alias")]
pub use alias::{alias_match, set_business_aliases, set_given_name_canonicals};

// Fellegi–Sunter EM training core (pyo3-free numeric heart). PR-C / C1 of the
// FS Rust+Arrow-only epic; the `native` crate will add the Arrow/#[pyfunction]
// shim in C2. No wiring yet — this module is self-contained + unit-tested.
pub mod em_core;

/// `rapidfuzz.fuzz.token_sort_ratio` preprocessing: split on whitespace, sort
/// the tokens, rejoin with a single space. (Then `fuzz::ratio` on the result.)
/// Private: its only callers (`token_sort_ratio` + `score_one`) live in this
/// crate.
fn token_sort_string(s: &str) -> String {
    let mut toks: Vec<&str> = s.split_whitespace().collect();
    toks.sort_unstable();
    toks.join(" ")
}

// ---- Scorer surface (scale matches score_buckets._resolve_score_pair_callable:
//      jaro_winkler/levenshtein on 0-1, token_sort_ratio on 0-100) ----

pub fn jaro_winkler_similarity(a: &str, b: &str) -> f64 {
    // rapidfuzz JaroWinkler default prefix_weight = 0.1.
    jaro_winkler::normalized_similarity(a.chars(), b.chars())
}

pub fn levenshtein_similarity(a: &str, b: &str) -> f64 {
    // rapidfuzz Levenshtein default uniform weights (1, 1, 1).
    levenshtein::normalized_similarity(a.chars(), b.chars())
}

/// token_sort_ratio on the 0-100 scale (score_field divides by 100).
pub fn token_sort_ratio(a: &str, b: &str) -> f64 {
    let sa = token_sort_string(a);
    let sb = token_sort_string(b);
    // rapidfuzz-rs fuzz::ratio returns [0, 1]; Python fuzz.ratio is [0, 100].
    fuzz::ratio(sa.chars(), sb.chars()) * 100.0
}

/// TS/Python `token_sort_ratio` preprocessing for the **WASM TS-parity path**:
/// lowercase, replace every non-`[a-z0-9 + whitespace]` char with a space, then
/// split / sort / join (matching goldenmatch TS `tokenSortRatio`'s
/// `.toLowerCase().replace(/[^a-z0-9\s]/g," ")` normalize), then `fuzz::ratio`
/// (== rapidfuzz `Indel.normalized_similarity`) on `[0, 1]`.
///
/// DISTINCT from `score_one(2)` / `token_sort_string`, which do NOT normalize
/// (the pinned native asymmetry the FFI/native path depends on) — do not merge.
/// Used only by `score-wasm` to give the TS opt-in backend token_sort coverage.
pub fn token_sort_normalized_ratio(a: &str, b: &str) -> f64 {
    fn normalize(s: &str) -> String {
        let cleaned: String = s
            .to_lowercase()
            .chars()
            .map(|c| {
                if c.is_ascii_alphanumeric() || c.is_whitespace() {
                    c
                } else {
                    ' '
                }
            })
            .collect();
        let mut toks: Vec<&str> = cleaned.split_whitespace().collect();
        toks.sort_unstable();
        toks.join(" ")
    }
    fuzz::ratio(normalize(a).chars(), normalize(b).chars())
}

/// Canonicalize an ISO-8601 `YYYY-MM-DD` date to its 8 packed digits
/// (`YYYYMMDD`), or `None` if the string isn't that exact shape. Deliberately
/// strict (no locale parsing, no `YYYY/MM/DD`): the point is to recognize a real
/// ISO date so a typo can be told apart from a different date; anything else
/// falls back to plain edit distance. Month/day RANGES are not validated -- a
/// malformed-but-ISO-shaped value still scores structurally, which is fine for a
/// similarity (and avoids dragging a calendar into the kernel).
fn iso_date_digits(s: &str) -> Option<[u8; 8]> {
    let b = s.as_bytes();
    if b.len() != 10 || b[4] != b'-' || b[7] != b'-' {
        return None;
    }
    let mut out = [0u8; 8];
    let mut oi = 0;
    for (i, &c) in b.iter().enumerate() {
        if i == 4 || i == 7 {
            continue;
        }
        if !c.is_ascii_digit() {
            return None;
        }
        out[oi] = c;
        oi += 1;
    }
    Some(out)
}

/// Date-aware similarity on [0, 1]. `jaro_winkler` on an ISO date scores
/// unrelated birthdays 0.80+ (the fixed `YYYY-MM-DD` shape, shared digit
/// alphabet, and common `19..`/`20..` prefix dominate) -- it cannot separate a
/// typo from a different person (#1858). This parses both sides as ISO dates and
/// uses the Damerau-Levenshtein edit distance over the 8 canonical digits
/// (transposition-aware -- swapped digits are ONE edit), mapped so a single-digit
/// typo stays above a typical 0.85 cutoff while an unrelated date cliffs to 0:
///
///   d == 0 -> 1.00 (same date)     d == 2 -> 0.75 (two edits -- weak)
///   d == 1 -> 0.90 (one typo)      d >= 3 -> 0.00 (unrelated)
///
/// Mirrors Splink's `DamerauLevenshtein <= 2` date comparison. When EITHER side
/// isn't an ISO date, degrades to `levenshtein` on the raw strings -- the
/// like-for-like the issue recommends over `jaro_winkler`, never worse.
pub fn date_similarity(a: &str, b: &str) -> f64 {
    match (iso_date_digits(a), iso_date_digits(b)) {
        (Some(da), Some(db)) => {
            let d = damerau_levenshtein::distance(da.iter().copied(), db.iter().copied());
            match d {
                0 => 1.0,
                1 => 0.90,
                2 => 0.75,
                _ => 0.0,
            }
        }
        // Not both ISO dates: fall back to plain normalized edit distance.
        _ => levenshtein::normalized_similarity(a.chars(), b.chars()),
    }
}

/// Character-trigram (q-gram) Jaccard set for one raw string, mirroring Python
/// `goldenmatch.core.scorer._qgram_set` (n=3): lowercase, pad each side with
/// `n-1` `#` sentinels, and take the set of length-`n` codepoint substrings.
/// Padding means even the empty string yields the all-`#` gram, so the set is
/// never empty for n>=2 (the Python `if not union` branch is unreachable, but
/// `qgram_similarity` guards it anyway).
fn qgram_set(s: &str) -> std::collections::HashSet<[char; 3]> {
    const N: usize = 3;
    // Build the padded codepoint sequence directly into one Vec -- (N-1) `#`
    // sentinels, the lowercased chars, then (N-1) `#` -- with no intermediate
    // padding/`format!` `String` allocations (only `to_lowercase`, which Unicode
    // case mapping requires).
    let lower = s.to_lowercase();
    let mut chars: Vec<char> = Vec::with_capacity(lower.chars().count() + 2 * (N - 1));
    chars.extend(std::iter::repeat_n('#', N - 1));
    chars.extend(lower.chars());
    chars.extend(std::iter::repeat_n('#', N - 1));
    if chars.len() < N {
        return std::collections::HashSet::new();
    }
    // The gram count is known (chars.len() - N + 1), so pre-size the set to avoid
    // rehashing while inserting. Grams are stored as a fixed `[char; N]` (N=3)
    // rather than an allocated `String`, so scoring many pairs doesn't
    // heap-allocate per trigram; set membership semantics are identical
    // (codepoint-wise equality).
    let mut set = std::collections::HashSet::with_capacity(chars.len() - N + 1);
    for i in 0..=(chars.len() - N) {
        set.insert([chars[i], chars[i + 1], chars[i + 2]]);
    }
    set
}

/// Character-trigram (q-gram) Jaccard similarity on two raw strings, the
/// reference for `goldenmatch.core.scorer._qgram_score_single` (n=3):
/// `|A ∩ B| / |A ∪ B|` over the padded q-gram sets. Identical strings (incl.
/// both empty) score 1.0; an empty union scores 0.0.
///
/// Unicode note: lowercasing uses Rust `str::to_lowercase` (Unicode default
/// case mapping), which matches Python `str.lower()` across ASCII and common
/// Latin. A handful of exotic codepoints can differ -- the same ASCII/Latin
/// scoped parity edge documented for the infermap scorers. q-gram is a
/// short-code scorer (SKUs / codes / names), so inputs are ASCII-dominant in
/// practice.
pub fn qgram_similarity(a: &str, b: &str) -> f64 {
    if a == b {
        return 1.0;
    }
    let sa = qgram_set(a);
    let sb = qgram_set(b);
    // One hash-lookup pass for the intersection, then |A ∪ B| = |A| + |B| - |A ∩ B|
    // arithmetically (avoids a second `union()` pass over the sets).
    let inter = sa.intersection(&sb).count();
    let union = sa.len() + sb.len() - inter;
    if union == 0 {
        return 0.0;
    }
    inter as f64 / union as f64
}

/// American Soundex matching `jellyfish.soundex` byte-for-byte, INCLUDING its
/// Unicode handling. Mirrors the jellyfish reference exactly
/// (`jellyfish/_jellyfish.py::soundex`):
///
/// - empty input -> `""`.
/// - `unicodedata.normalize("NFKD", s).upper()`: NFKD decomposition (an accented letter becomes base-letter + combining mark) then Unicode uppercase (`ß` -> `"SS"`).
/// - seed = the LITERAL first char (kept as-is, not coded); `last` = its would-be code, or None if the seed isn't a coded consonant.
/// - each subsequent char: a coded consonant appends its code when it differs from `last` (adjacent-dup collapse); ANY other char resets `last` to None UNLESS it is `H`/`W` (which leave `last` untouched).
/// - stop at 4 output chars; right-pad with `0`.
///
/// This is the single reference for every `goldenmatch` soundex surface: the
/// `native` field-matrix kernel and the bucket `score_one` path both call it.
/// Rust `nfkd()` (the `unicode-normalization` crate) plus `str::to_uppercase`
/// implement the same Unicode algorithms as Python's `unicodedata.normalize`
/// and `str.upper`, so the result is byte-identical to jellyfish (batteried in
/// `tests/test_native_soundex_parity.py`).
pub fn soundex(s: &str) -> String {
    if s.is_empty() {
        return String::new();
    }
    let normalized: String = s.nfkd().collect::<String>().to_uppercase();
    let chars: Vec<char> = normalized.chars().collect();
    if chars.is_empty() {
        return String::new();
    }
    let mut result = String::with_capacity(4);
    result.push(chars[0]); // literal seed (jellyfish `result = [s[0]]`)
    let mut count = 1usize;
    let mut last = soundex_code(chars[0]); // would-be code of the seed, or None
    for &c in &chars[1..] {
        match soundex_code(c) {
            Some(code) => {
                if Some(code) != last {
                    result.push(code);
                    count += 1;
                }
                last = Some(code);
            }
            None => {
                // Non-coded char (vowel / mark / digit / symbol): reset the
                // dedup state, EXCEPT H/W which jellyfish leaves alone.
                if c != 'H' && c != 'W' {
                    last = None;
                }
            }
        }
        if count == 4 {
            break;
        }
    }
    for _ in count..4 {
        result.push('0');
    }
    result
}

/// Soundex digit for a coded consonant (uppercase), else `None`. Vowels
/// (A/E/I/O/U/Y), H, W, and every non-letter map to `None` (jellyfish's
/// "not in any replacement set" branch).
fn soundex_code(c: char) -> Option<char> {
    match c {
        'B' | 'F' | 'P' | 'V' => Some('1'),
        'C' | 'G' | 'J' | 'K' | 'Q' | 'S' | 'X' | 'Z' => Some('2'),
        'D' | 'T' => Some('3'),
        'L' => Some('4'),
        'M' | 'N' => Some('5'),
        'R' => Some('6'),
        _ => None,
    }
}

// ---- initialism_match (abbreviation matcher) --------------------------------
// Mirrors `goldenmatch.core.acronym.derive_initialism` +
// `core.scorer._initialism_match_single`. `legal_forms` is the caller-shipped
// entity-form variant set (`refdata.business.entity_form_variants()`, ~77 entries,
// already lowercase-normalized); passing it in keeps this fn pyo3-/refdata-free.

/// Remove every `(...)` group, mirroring the Python `\([^)]*\)` substitution:
/// a `(` with the nearest following `)` (and everything between) is dropped; a
/// `(` with no later `)` is kept literally (the regex requires the close).
fn strip_parentheticals(s: &str) -> String {
    let chars: Vec<char> = s.chars().collect();
    let mut out = String::with_capacity(s.len());
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == '(' {
            if let Some(rel) = chars[i + 1..].iter().position(|&c| c == ')') {
                i += rel + 2; // skip "(...)" inclusive
                continue;
            }
        }
        out.push(chars[i]);
        i += 1;
    }
    out
}

/// Python `token.strip().rstrip(".,").lower()` (the per-token legal-form key).
fn normalize_token_for_legal(token: &str) -> String {
    token
        .trim()
        .trim_end_matches(['.', ','])
        .to_lowercase()
}

/// Python `str.isupper()`: at least one cased char and no lowercase char.
fn py_isupper(s: &str) -> bool {
    s.chars().any(char::is_uppercase) && !s.chars().any(char::is_lowercase)
}

/// Python `str.isalpha()`: non-empty and every char alphabetic.
fn py_isalpha(s: &str) -> bool {
    !s.is_empty() && s.chars().all(char::is_alphabetic)
}

/// Abbreviation block key for a name, byte-for-byte with
/// `acronym.derive_initialism`: strip parentheticals; tokenize on whitespace,
/// dropping punctuation-only tokens (no ASCII letter) and legal-form tokens
/// (normalized form in `legal_forms`); then a lone all-caps alphabetic 2-6 char
/// token returns itself uppercased, >=2 tokens return the first-ASCII-letter of
/// each uppercased, else `""`.
pub fn derive_initialism(text: &str, legal_forms: &std::collections::HashSet<String>) -> String {
    let stripped = strip_parentheticals(text);
    let mut cleaned: Vec<&str> = Vec::new();
    for token in stripped.split_whitespace() {
        if !token.chars().any(|c| c.is_ascii_alphabetic()) {
            continue; // punctuation-only / digits-only (`_ANY_ALPHA` = [A-Za-z])
        }
        if legal_forms.contains(&normalize_token_for_legal(token)) {
            continue;
        }
        cleaned.push(token);
    }
    if cleaned.len() == 1 {
        let tok = cleaned[0];
        let n = tok.chars().count();
        if py_isupper(tok) && py_isalpha(tok) && (2..=6).contains(&n) {
            return tok.to_uppercase();
        }
        return String::new();
    }
    if cleaned.is_empty() {
        return String::new();
    }
    // First ASCII letter of each token (`_FIRST_ALPHA` = [A-Za-z]), uppercased.
    let mut initials = String::with_capacity(cleaned.len());
    for token in &cleaned {
        if let Some(c) = token.chars().find(|c| c.is_ascii_alphabetic()) {
            initials.push(c.to_ascii_uppercase());
        }
    }
    if initials.chars().count() < 2 {
        return String::new();
    }
    initials
}

/// `1.0` if either string is the other's initialism, or the two initialisms are
/// equal (non-empty); else `0.0`. Byte-for-byte with `_initialism_match_single`.
pub fn initialism_match(a: &str, b: &str, legal_forms: &std::collections::HashSet<String>) -> f64 {
    let ia = derive_initialism(a, legal_forms);
    let ib = derive_initialism(b, legal_forms);
    let matched = (!ia.is_empty() && ia == b)
        || (!ib.is_empty() && a == ib)
        || (!ia.is_empty() && !ib.is_empty() && ia == ib);
    if matched {
        1.0
    } else {
        0.0
    }
}

// The legal-form variant set for `initialism_match` is process-global state,
// installed once by the host (`refdata.business.entity_form_variants()`, ~77
// entries), so that `score_one(7, ...)` stays a UNIFORM `(id, a, b)` call — the
// dispatch signature can't carry a per-call table without breaking the
// `_NATIVE_SCORER_IDS == score_one` id-map invariant every other scorer holds.
static LEGAL_FORMS: std::sync::OnceLock<std::collections::HashSet<String>> =
    std::sync::OnceLock::new();

/// Install the caller-shipped legal-form variant set for `initialism_match`.
/// `OnceLock` semantics: only the FIRST call wins (returns `true`); later calls
/// are ignored (`false`). The host ships the deterministic 77-entry
/// `entity_form_variants()` once before routing initialism through the kernel;
/// until then `score_one(7, ...)` scores against an EMPTY set (no legal-form
/// dropping), which is why the Python fast-path guard also gates on this being
/// set. Content is deterministic, so the first-wins race is benign.
pub fn set_legal_forms(forms: std::collections::HashSet<String>) -> bool {
    LEGAL_FORMS.set(forms).is_ok()
}

/// The installed legal-form set, or an empty set if the host never called
/// `set_legal_forms` (kernel stays defined but drops no legal forms).
fn legal_forms() -> &'static std::collections::HashSet<String> {
    LEGAL_FORMS.get_or_init(std::collections::HashSet::new)
}

// ---- alias_match (business + given-name canonical equality) ------------------
// Ports `refdata.business_aliases.canonical_company_form` +
// `refdata.given_names.canonical_form` + `core.scorer._alias_match_single`. Two
// host-installed tables keep `score_one(id, a, b)` uniform (no per-call table arg):
//  * business: the `strip_legal_form` variant list, rebuilt here into the SAME
//    trailing-suffix regex Python compiles, plus the surface->canonical alias map.
//  * given-name: a PRE-RESOLVED `normalized -> min(canonical set)` map -- the
//    Python `min(canon_set)` lex-first resolution is done host-side, so the kernel
//    needs no alias graph, only a normalize + lookup.
#[cfg(feature = "alias")]
mod alias {
    use regex::Regex;

struct BusinessAliasTable {
    strip_re: Regex,
    surface_to_canonical: std::collections::HashMap<String, String>,
}

static BUSINESS_ALIAS: std::sync::OnceLock<BusinessAliasTable> = std::sync::OnceLock::new();
static GIVEN_NAME_CANON: std::sync::OnceLock<std::collections::HashMap<String, String>> =
    std::sync::OnceLock::new();

/// Collapse whitespace runs to a single space and trim, mirroring Python
/// `re.sub(r"\s+", " ", s).strip()`. NOTE: `char::is_whitespace()` uses the
/// Unicode White_Space property, which (like the `regex` crate's `\s`) differs
/// from Python `re`'s `\s` on the C1 separators `\x1c`-`\x1f`/`\x85`; an
/// ASCII-scoped parity edge shared with the other refdata ports (business names
/// are ASCII/Latin in practice).
fn collapse_ws(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut prev_ws = false;
    for ch in s.chars() {
        if ch.is_whitespace() {
            if !prev_ws {
                out.push(' ');
            }
            prev_ws = true;
        } else {
            out.push(ch);
            prev_ws = false;
        }
    }
    out.trim().to_string()
}

/// Rust port of `refdata.business.strip_legal_form`: whitespace-collapse, then
/// iteratively (bounded to 4, like Python) remove a trailing legal-form suffix
/// via the installed regex. The `$`-anchored pattern strips exactly the LAST
/// token per pass, so compound suffixes ("Acme Holdings Inc.") peel one at a time.
fn strip_legal_form(value: &str, table: &BusinessAliasTable) -> String {
    let mut cleaned = collapse_ws(value);
    if cleaned.is_empty() {
        return cleaned;
    }
    for _ in 0..4 {
        // `Regex::replace` == Python `pattern.sub("", ...)`: the `$` anchor admits
        // one match, then `.trim()` mirrors the Python `.strip()`.
        let new = table.strip_re.replace(&cleaned, "").trim().to_string();
        if new == cleaned || new.is_empty() {
            // Python: `cleaned = new if new else cleaned` -- keep `cleaned` when a
            // strip would empty the string; otherwise `new == cleaned` (no-op).
            if !new.is_empty() {
                cleaned = new;
            }
            break;
        }
        cleaned = new;
    }
    cleaned
}

/// Business `_normalize`: `strip_legal_form` -> whitespace-collapse -> lowercase,
/// byte-for-byte with `refdata.business_aliases._normalize`.
fn business_normalize(name: &str, table: &BusinessAliasTable) -> String {
    let stripped = strip_legal_form(name, table);
    collapse_ws(&stripped).to_lowercase()
}

/// `refdata.business_aliases.canonical_company_form` (table always installed here):
/// normalize, then map surface->canonical with a passthrough default.
fn canonical_company_form(name: &str, table: &BusinessAliasTable) -> String {
    let norm = business_normalize(name, table);
    if norm.is_empty() {
        return String::new();
    }
    table
        .surface_to_canonical
        .get(&norm)
        .cloned()
        .unwrap_or(norm)
}

/// Given-name `_normalize`: keep only alphabetic chars, lowercase -- byte-for-byte
/// with `refdata.given_names._normalize` (`"".join(c for c in s if c.isalpha()).lower()`).
fn given_normalize(name: &str) -> String {
    name.chars()
        .filter(|c| c.is_alphabetic())
        .collect::<String>()
        .to_lowercase()
}

/// `refdata.given_names.canonical_form` via the pre-resolved lex-first map:
/// normalize, then look up `min(canonical set)` with a passthrough default.
fn canonical_given_form(name: &str, gmap: &std::collections::HashMap<String, String>) -> String {
    let norm = given_normalize(name);
    if norm.is_empty() {
        return String::new();
    }
    gmap.get(&norm).cloned().unwrap_or(norm)
}

/// Install the business alias table. `variants` are the normalized legal-form
/// variants (`refdata.business._state.variants_normalized`), rebuilt here into the
/// SAME regex Python compiles -- `[\s,\-.]+(?:v...)[\s.,]*$` (IGNORECASE), variants
/// sorted DESCENDING by char length so multi-word forms are preferred (Python's
/// `sorted(key=lambda s: (-len(s), s))`). `surface_to_canonical` is the raw alias
/// map. `OnceLock` first-wins; returns `false` if already set or the regex fails.
pub fn set_business_aliases(
    variants: Vec<String>,
    surface_to_canonical: Vec<(String, String)>,
) -> bool {
    let mut sorted = variants;
    sorted.sort_by(|a, b| {
        b.chars()
            .count()
            .cmp(&a.chars().count())
            .then_with(|| a.cmp(b))
    });
    let alt = sorted
        .iter()
        .map(|v| regex::escape(v))
        .collect::<Vec<_>>()
        .join("|");
    let pattern = format!(r"(?i)[\s,\-.]+(?:{alt})[\s.,]*$");
    let strip_re = match Regex::new(&pattern) {
        Ok(re) => re,
        Err(_) => return false,
    };
    BUSINESS_ALIAS
        .set(BusinessAliasTable {
            strip_re,
            surface_to_canonical: surface_to_canonical.into_iter().collect(),
        })
        .is_ok()
}

/// Install the given-name canonical map (`normalized -> min(canonical set)`,
/// lex-first resolution done host-side). `OnceLock` first-wins.
pub fn set_given_name_canonicals(pairs: Vec<(String, String)>) -> bool {
    GIVEN_NAME_CANON.set(pairs.into_iter().collect()).is_ok()
}

/// `1.0` if both values canonicalize to the same NON-EMPTY business alias OR the
/// same given-name canonical; else `0.0`. Byte-for-byte with
/// `_alias_match_single`. Returns `0.0` for a half whose table isn't installed --
/// the Python fast-path guard requires BOTH installed before routing here.
pub fn alias_match(a: &str, b: &str) -> f64 {
    if let Some(table) = BUSINESS_ALIAS.get() {
        let cb_a = canonical_company_form(a, table);
        if !cb_a.is_empty() && cb_a == canonical_company_form(b, table) {
            return 1.0;
        }
    }
    if let Some(gmap) = GIVEN_NAME_CANON.get() {
        let cg_a = canonical_given_form(a, gmap);
        if !cg_a.is_empty() && cg_a == canonical_given_form(b, gmap) {
            return 1.0;
        }
    }
    0.0
}
} // mod alias

/// Scorer dispatch matching `score_buckets._resolve_score_pair_callable`'s
/// fast-path scale, all on [0, 1]. ids: 0=jaro_winkler, 1=levenshtein,
/// 2=token_sort, 3=exact, 4=date, 5=qgram, 6=soundex_match, 7=initialism_match,
/// 8=alias_match.
///
/// NOTE: id=2 returns the UNSCALED `fuzz::ratio` ([0,1], NOT *100). This is
/// deliberate and must not be reconciled with `token_sort_ratio`'s *100 form:
/// `score_field_matrix` (native) depends on the unscaled value (it divides
/// token-sort by 100 only in the PyO3-exposed path, never here). Changing this
/// is a silent-drift trap.
pub fn score_one(scorer_id: u8, a: &str, b: &str) -> f64 {
    match scorer_id {
        0 => jaro_winkler::normalized_similarity(a.chars(), b.chars()),
        1 => levenshtein::normalized_similarity(a.chars(), b.chars()),
        2 => {
            let sa = token_sort_string(a);
            let sb = token_sort_string(b);
            fuzz::ratio(sa.chars(), sb.chars())
        }
        // id=3 = exact match. Guard arm collapses the if/else into the match
        // (clippy::collapsible-match under CI's stable toolchain); scorer_id==3
        // with a!=b falls through to the catch-all 0.0, same as every other id.
        3 if a == b => 1.0,
        4 => date_similarity(a, b),
        5 => qgram_similarity(a, b),
        // id=6 = soundex_match: binary 1.0/0.0 on soundex-code equality. Guard
        // arm collapses the if/else into the match (clippy::collapsible-match),
        // like id 3; a soundex mismatch falls through to the 0.0 catch-all.
        // Matches the bucket per-pair mirror `1.0 if jf.soundex(a)==jf.soundex(b) else 0.0`.
        6 if soundex(a) == soundex(b) => 1.0,
        // id=7 = initialism_match against the host-installed legal-form set (empty
        // until `set_legal_forms` is called). Byte-for-byte with
        // `_initialism_match_single`; the Python fast-path guard requires the
        // table to be installed before routing here.
        7 => initialism_match(a, b, legal_forms()),
        // id=8 = alias_match: 1.0 iff both values share a non-empty business OR
        // given-name canonical, against the host-installed tables (empty until
        // `set_business_aliases`/`set_given_name_canonicals`). Byte-for-byte with
        // `_alias_match_single`; the Python fast-path guard requires both installed.
        // Behind the `alias` feature (off for the wasm surface); id 8 falls to the
        // 0.0 catch-all there, which wasm never routes.
        #[cfg(feature = "alias")]
        8 => alias_match(a, b),
        _ => 0.0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn jaro_winkler_identity_and_disjoint() {
        assert_eq!(jaro_winkler_similarity("abc", "abc"), 1.0);
        assert_eq!(jaro_winkler_similarity("abc", "xyz"), 0.0);
    }

    #[test]
    fn levenshtein_identity_and_disjoint() {
        assert_eq!(levenshtein_similarity("abc", "abc"), 1.0);
        let s = levenshtein_similarity("abc", "abx");
        assert!((s - (2.0 / 3.0)).abs() < 1e-9, "got {s}");
    }

    #[test]
    fn token_sort_is_order_invariant_on_0_100_scale() {
        assert_eq!(token_sort_ratio("a b", "b a"), 100.0);
    }

    #[test]
    fn date_similarity_separates_typo_from_unrelated() {
        // The #1858 cases: jaro_winkler scored the unrelated pair 0.80+; date must not.
        assert_eq!(date_similarity("1980-01-01", "1980-01-01"), 1.0); // same
        assert_eq!(date_similarity("1980-01-01", "1980-01-02"), 0.90); // 1-digit typo
        assert_eq!(date_similarity("1980-01-01", "1975-11-30"), 0.0); // unrelated (>=3 edits)
        // A single-digit typo must clear a typical 0.85 cutoff; unrelated must not.
        assert!(date_similarity("1980-01-01", "1980-01-02") >= 0.85);
        assert!(date_similarity("1980-01-01", "1975-01-01") < 0.85); // 2-edit year change
    }

    #[test]
    fn date_similarity_transposition_is_one_edit() {
        // Swapped adjacent digits = ONE Damerau edit (not two) -> stays a typo.
        assert_eq!(date_similarity("1980-12-01", "1980-21-01"), 0.90);
    }

    #[test]
    fn date_similarity_non_iso_falls_back_to_levenshtein() {
        // Not both ISO -> plain normalized edit distance, never jaro_winkler.
        assert_eq!(date_similarity("abc", "abc"), 1.0);
        let s = date_similarity("1980-01-01", "Jan 1 1980");
        assert!((s - levenshtein_similarity("1980-01-01", "Jan 1 1980")).abs() < 1e-12);
    }

    #[test]
    fn qgram_identity_and_jaccard() {
        // identical (incl. empty) -> 1.0
        assert_eq!(qgram_similarity("abc", "abc"), 1.0);
        assert_eq!(qgram_similarity("", ""), 1.0);
        // case-insensitive: same q-gram sets after lowercasing
        assert_eq!(qgram_similarity("ABC", "abc"), 1.0);
        // disjoint short strings share only the all-`#` padding gram is false here:
        // "ab" -> {##a,#ab,ab#} lower; "xy" -> {##x,#xy,xy#}; no overlap -> 0.0
        assert_eq!(qgram_similarity("ab", "xy"), 0.0);
        // one empty, one not: union non-empty, intersection empty -> 0.0
        assert_eq!(qgram_similarity("", "x"), 0.0);
        // partial overlap is strictly between 0 and 1
        let s = qgram_similarity("abcd", "abce");
        assert!(s > 0.0 && s < 1.0, "got {s}");
    }

    #[test]
    fn qgram_matches_hand_computed_jaccard() {
        // "abc" -> {##a,#ab,abc,bc#,c##}; "abd" -> {##a,#ab,abd,bd#,d##}
        // intersection {##a,#ab} = 2, union = 8 -> 0.25
        let s = qgram_similarity("abc", "abd");
        assert!((s - 0.25).abs() < 1e-12, "got {s}");
    }

    #[test]
    fn score_one_id5_is_qgram() {
        assert_eq!(score_one(5, "abc", "abc"), 1.0);
        assert_eq!(score_one(5, "abc", "abd"), qgram_similarity("abc", "abd"));
    }

    #[test]
    fn soundex_matches_jellyfish_reference() {
        // Canonical alphabetic jellyfish values.
        assert_eq!(soundex("Robert"), "R163");
        assert_eq!(soundex("Rupert"), "R163"); // Robert/Rupert collide
        assert_eq!(soundex("Ashcraft"), "A261"); // H/W skip rule
        assert_eq!(soundex("Tymczak"), "T522");
        assert_eq!(soundex("Pfister"), "P236"); // adjacent same-code (P,F -> 1) coalesces
        assert_eq!(soundex("Honeyman"), "H555");
        // Empty -> empty (jellyfish `if not s`).
        assert_eq!(soundex(""), "");
        // Leading non-alpha: jellyfish seeds on the LITERAL first char (NOT the
        // first letter) and codes the rest -- full-parity cases probed from
        // jellyfish itself.
        assert_eq!(soundex("123"), "1000");
        assert_eq!(soundex("3M"), "3500");
        assert_eq!(soundex("4abc"), "4120");
        // Mid-string non-letter resets the dedup state (S..1..S -> both S's coded).
        assert_eq!(soundex("S1S"), "S200");
        // Unicode: NFKD fold + uppercase. Ürüm -> U + r(6) + m(5); ß.upper()="SS".
        assert_eq!(soundex("Ürüm"), "U650");
        assert_eq!(soundex("José"), "J200");
        assert_eq!(soundex("ß"), "S000");
    }

    #[test]
    fn soundex_pads_short_codes_to_four() {
        assert_eq!(soundex("Lee").len(), 4);
        assert_eq!(soundex("A"), "A000");
    }

    #[test]
    fn soundex_code_table() {
        assert_eq!(soundex_code('B'), Some('1')); // B F P V
        assert_eq!(soundex_code('C'), Some('2')); // C G J K Q S X Z
        assert_eq!(soundex_code('D'), Some('3')); // D T
        assert_eq!(soundex_code('L'), Some('4')); // L
        assert_eq!(soundex_code('M'), Some('5')); // M N
        assert_eq!(soundex_code('R'), Some('6')); // R
        assert_eq!(soundex_code('A'), None); // vowels
        assert_eq!(soundex_code('1'), None); // non-letter
    }

    #[test]
    fn score_one_id6_is_soundex_match() {
        assert_eq!(score_one(6, "Robert", "Rupert"), 1.0); // same code
        assert_eq!(score_one(6, "Robert", "Smith"), 0.0); // different code
        // full jellyfish parity: soundex("123")="1000" != soundex("456")="4000"
        assert_eq!(score_one(6, "123", "456"), 0.0);
        assert_eq!(score_one(6, "123", "123"), 1.0);
    }

    fn _legal_forms() -> std::collections::HashSet<String> {
        // subset of refdata.business.entity_form_variants() relevant to the tests
        ["inc", "corp", "corporation", "llc", "ltd", "gmbh", "ab", "company", "co"]
            .iter()
            .map(|s| s.to_string())
            .collect()
    }

    #[test]
    fn derive_initialism_matches_python_reference() {
        let lf = _legal_forms();
        // ground truth from goldenmatch.core.acronym.derive_initialism
        assert_eq!(derive_initialism("IBM", &lf), "IBM"); // lone acronym -> itself
        assert_eq!(derive_initialism("Apple", &lf), ""); // lone non-acronym
        assert_eq!(derive_initialism("International Business Machines", &lf), "IBM");
        assert_eq!(
            derive_initialism("International Business Machines Corporation (Armonk, NY)", &lf),
            "IBM" // parenthetical stripped, "Corporation" dropped
        );
        assert_eq!(derive_initialism("Acme Industries LLC", &lf), "AI"); // keep descriptive
        assert_eq!(derive_initialism("GmbH", &lf), ""); // lone legal form -> dropped
        assert_eq!(derive_initialism("AB", &lf), ""); // "ab" is a legal form (Aktiebolag)
        assert_eq!(derive_initialism("ABCDEFG", &lf), ""); // 7 chars > acronym cap
        assert_eq!(derive_initialism("3M Company", &lf), ""); // "3M" not alpha; Company dropped
        assert_eq!(derive_initialism("a b c", &lf), "ABC"); // first-alpha of each, upper
    }

    #[test]
    fn initialism_match_matches_python_reference() {
        let lf = _legal_forms();
        assert_eq!(initialism_match("International Business Machines", "IBM", &lf), 1.0);
        assert_eq!(initialism_match("IBM", "International Business Machines", &lf), 1.0);
        assert_eq!(initialism_match("Apple", "Apricot", &lf), 0.0);
        // known false positive: same initials -> 1.0 by design
        assert_eq!(initialism_match("International Business Machines", "Indian Banana Market", &lf), 1.0);
        assert_eq!(initialism_match("Acme Industries LLC", "AI", &lf), 1.0);
    }

    #[test]
    fn score_one_id7_is_initialism_match_against_global_table() {
        // This is the ONLY test that installs the process-global legal-form set
        // (OnceLock first-wins), so it never races a different-content setter;
        // every other initialism test passes a LOCAL table to the `*_single` fns.
        assert!(set_legal_forms(_legal_forms()));
        // Table-INDEPENDENT (no legal-form tokens involved): dispatch works.
        assert_eq!(score_one(7, "International Business Machines", "IBM"), 1.0);
        assert_eq!(score_one(7, "Apple", "Apricot"), 0.0);
        // Table-DEPENDENT: only 1.0 because "LLC" is dropped as a legal form
        // ("Acme Industries LLC" -> initials "AI"). With an empty table it would
        // be "AIL" != "AI" -> 0.0, so this asserts the global table is wired.
        assert_eq!(score_one(7, "Acme Industries LLC", "AI"), 1.0);
    }

    #[cfg(feature = "alias")]
    #[test]
    fn score_one_id8_is_alias_match_against_global_tables() {
        // The ONLY test that installs the alias OnceLocks (business + given-name),
        // so no different-content setter races it. Representative subsets of
        // refdata.business._state.variants_normalized / business_aliases +
        // given_names (norm -> min(canonical)).
        let variants: Vec<String> = [
            "inc", "incorporated", "corp", "corporation", "llc", "ltd", "gmbh",
            "co", "company", "holdings", "limited liability company",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        let biz: Vec<(String, String)> = [
            ("acme", "acme"),
            ("google", "alphabet"),
            ("alphabet", "alphabet"),
        ]
        .iter()
        .map(|(a, b)| (a.to_string(), b.to_string()))
        .collect();
        let given: Vec<(String, String)> = [
            ("bob", "robert"),
            ("robert", "robert"),
            ("bill", "william"),
            ("william", "william"),
            ("kate", "catherine"),
            ("catherine", "catherine"),
        ]
        .iter()
        .map(|(a, b)| (a.to_string(), b.to_string()))
        .collect();
        assert!(set_business_aliases(variants, biz));
        assert!(set_given_name_canonicals(given));

        // Business: legal-form strip + alias map (ground truth from the Python ref).
        assert_eq!(score_one(8, "Acme Inc", "Acme Incorporated"), 1.0);
        assert_eq!(score_one(8, "Acme, LLC", "Acme"), 1.0);
        assert_eq!(score_one(8, "Acme Holdings Inc.", "acme"), 1.0); // iterative strip
        assert_eq!(score_one(8, "Acme Limited Liability Company", "Acme"), 1.0); // multi-word
        assert_eq!(score_one(8, "Google", "Alphabet"), 1.0); // alias map
        assert_eq!(score_one(8, "Acme", "Globex"), 0.0); // distinct passthrough
        // Given-name: nickname canonical equality.
        assert_eq!(score_one(8, "Bob", "Robert"), 1.0);
        assert_eq!(score_one(8, "Bill", "William"), 1.0);
        assert_eq!(score_one(8, "Kate", "Catherine"), 1.0);
        assert_eq!(score_one(8, "Bob", "Bill"), 0.0); // different canonicals
        // Empty both -> no canonical -> 0.0.
        assert_eq!(score_one(8, "", ""), 0.0);
    }

    #[test]
    fn score_one_id4_is_date() {
        assert_eq!(score_one(4, "1980-01-01", "1980-01-01"), 1.0);
        assert_eq!(score_one(4, "1980-01-01", "1975-11-30"), 0.0);
    }

    #[test]
    fn score_one_dispatches_by_id() {
        // id=3 is exact match; score_one returns [0,1] (NOT the *100 token_sort_ratio scale)
        assert_eq!(score_one(3, "abc", "abc"), 1.0);
        assert_eq!(score_one(3, "abc", "abd"), 0.0);
    }

    #[test]
    fn score_one_id2_is_unscaled_not_100_scale() {
        // score_one(id=2) returns fuzz::ratio on [0,1], NOT token_sort_ratio's
        // *100 form. This asymmetry is load-bearing (the PyO3 score_field_matrix
        // path divides by 100, never here). Pinned so a silent unification breaks.
        assert_eq!(score_one(2, "a b", "b a"), 1.0);
        assert_eq!(token_sort_ratio("a b", "b a"), 100.0);
    }
}
