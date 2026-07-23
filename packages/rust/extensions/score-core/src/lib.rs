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

// --- date_diff: magnitude-aware date comparator (score_one id 15) -----------
// The reference for `goldenmatch.core.scorer._date_diff_similarity_py` (spec
// 2026-07-23-fs-domain-comparators). `date_similarity` above is magnitude-BLIND
// (a one-digit edit -> 0.90, so a full-year DOB gap over-scores); `date_diff`
// parses both to a proleptic-Gregorian day count and bands by |days_a - days_b|,
// with an MM/DD-transposition floor and the SAME `date_similarity` edit-distance
// fallback on unparseable input (so it never diverges from the Python missing
// semantics -- both return a real similarity for any non-null pair). Day-distance
// -> similarity, monotone non-increasing (1827 d ~= 5 y).
const DATE_DIFF_BANDS: [(i64, f64); 5] =
    [(0, 1.0), (1, 0.92), (31, 0.80), (366, 0.60), (1827, 0.30)];

/// Parse a date to raw `(year, month, day)` ints -- range NOT validated here
/// (validity is checked in `date_to_days`, mirroring Python's `_date_parts` +
/// `datetime.date()` two-step). Accepts ISO `YYYY-MM-DD` / `YYYY/MM/DD` (1-2
/// digit m/d ok), compact `YYYYMMDD`, and bare `YYYY` (-> Jan 1). Regex-free,
/// byte-for-byte with `_date_parts`.
fn date_parts(s: &str) -> Option<(i64, i64, i64)> {
    let t = s.trim();
    if t.is_empty() {
        return None;
    }
    let sep = if t.contains('-') {
        Some('-')
    } else if t.contains('/') {
        Some('/')
    } else {
        None
    };
    if let Some(c) = sep {
        let bits: Vec<&str> = t.split(c).collect();
        if bits.len() == 3
            && bits
                .iter()
                .all(|b| !b.is_empty() && b.bytes().all(|x| x.is_ascii_digit()))
            && bits[0].len() == 4
        {
            return Some((
                bits[0].parse::<i64>().ok()?,
                bits[1].parse::<i64>().ok()?,
                bits[2].parse::<i64>().ok()?,
            ));
        }
        return None;
    }
    if t.len() == 8 && t.bytes().all(|x| x.is_ascii_digit()) {
        return Some((
            t[0..4].parse::<i64>().ok()?,
            t[4..6].parse::<i64>().ok()?,
            t[6..8].parse::<i64>().ok()?,
        ));
    }
    if t.len() == 4 && t.bytes().all(|x| x.is_ascii_digit()) {
        return Some((t.parse::<i64>().ok()?, 1, 1));
    }
    None
}

fn is_leap(y: i64) -> bool {
    (y % 4 == 0 && y % 100 != 0) || y % 400 == 0
}

fn days_in_month(y: i64, m: i64) -> i64 {
    match m {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 => {
            if is_leap(y) {
                29
            } else {
                28
            }
        }
        _ => 0,
    }
}

/// Proleptic-Gregorian day count (Howard Hinnant's days_from_civil) for a valid
/// date, else None -- rejecting exactly what `datetime.date(y,m,d)` rejects
/// (year 1..=9999, month 1..=12, day 1..=days_in_month). Only DIFFERENCES between
/// two dates are used, so the epoch offset cancels and this matches Python
/// `date.toordinal()` deltas exactly.
fn date_to_days(y: i64, m: i64, d: i64) -> Option<i64> {
    if !(1..=9999).contains(&y) || !(1..=12).contains(&m) {
        return None;
    }
    if d < 1 || d > days_in_month(y, m) {
        return None;
    }
    let yy = if m <= 2 { y - 1 } else { y };
    let era = if yy >= 0 { yy } else { yy - 399 } / 400;
    let yoe = yy - era * 400; // [0, 399]
    let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) + 2) / 5 + d - 1; // [0, 365]
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy; // [0, 146096]
    Some(era * 146097 + doe - 719468)
}

fn parse_date_days(s: &str) -> Option<i64> {
    let (y, m, d) = date_parts(s)?;
    date_to_days(y, m, d)
}

fn date_diff_band(d: i64) -> f64 {
    for (lim, val) in DATE_DIFF_BANDS {
        if d <= lim {
            return val;
        }
    }
    0.0
}

/// Day-distance banded similarity; MM/DD transposition floored to the <=31d band;
/// `date_similarity` edit-distance fallback when either side won't parse.
pub fn date_diff_similarity(a: &str, b: &str) -> f64 {
    match (parse_date_days(a), parse_date_days(b)) {
        (Some(oa), Some(ob)) => {
            let mut d = (oa - ob).abs();
            if d != 0 {
                // A month<->day swap on either operand that collapses the distance
                // to 0 is a data-entry transposition (1990-01-02 vs 1990-02-01) --
                // a partial, not a disagree -> floor at the <=31d band.
                for (parts, other) in [(date_parts(a), ob), (date_parts(b), oa)] {
                    if let Some((y, mo, dd)) = parts {
                        if let Some(sw) = date_to_days(y, dd, mo) {
                            if sw == other {
                                d = d.min(31);
                                break;
                            }
                        }
                    }
                }
            }
            date_diff_band(d)
        }
        // Either side unparseable: reuse the edit-distance date scorer (never None).
        _ => date_similarity(a, b),
    }
}

// --- geo_haversine: great-circle distance comparator (score_one id 16) -------
// The reference for `goldenmatch.core.scorer._geo_haversine_similarity_py`:
// parse ONE combined "lat,long" field per side and band the great-circle
// (haversine) km distance, monotone non-increasing. Exact-string fallback when
// either side won't parse (never None for a non-null pair). Parity is on the
// BANDED similarity (a discrete value); the haversine itself runs the platform
// libm (same as Python's `math`), so band assignment is identical for any pair
// not within an ULP of a band edge -- which the parity fixtures avoid.
const GEO_HAVERSINE_BANDS: [(f64, f64); 4] = [(0.1, 1.0), (1.0, 0.85), (10.0, 0.5), (100.0, 0.2)];
const EARTH_RADIUS_KM: f64 = 6371.0088;

fn parse_latlong(s: &str) -> Option<(f64, f64)> {
    let t = s.trim();
    if t.is_empty() {
        return None;
    }
    let sep = if t.contains(',') {
        ','
    } else if t.contains(';') {
        ';'
    } else {
        return None;
    };
    let bits: Vec<&str> = t.split(sep).collect();
    if bits.len() != 2 {
        return None;
    }
    let lat = bits[0].trim().parse::<f64>().ok()?;
    let lon = bits[1].trim().parse::<f64>().ok()?;
    if lat.is_finite()
        && lon.is_finite()
        && (-90.0..=90.0).contains(&lat)
        && (-180.0..=180.0).contains(&lon)
    {
        Some((lat, lon))
    } else {
        None
    }
}

fn haversine_km(a: (f64, f64), b: (f64, f64)) -> f64 {
    let lat1 = a.0.to_radians();
    let lat2 = b.0.to_radians();
    let dlat = lat2 - lat1;
    let dlon = b.1.to_radians() - a.1.to_radians();
    // Mirror Python's `math.sin(x) ** 2` (base**int -> C pow, not x*x): use powf.
    let h = (dlat / 2.0).sin().powf(2.0) + lat1.cos() * lat2.cos() * (dlon / 2.0).sin().powf(2.0);
    2.0 * EARTH_RADIUS_KM * h.sqrt().min(1.0).asin()
}

fn geo_haversine_band(km: f64) -> f64 {
    for (lim, val) in GEO_HAVERSINE_BANDS {
        if km <= lim {
            return val;
        }
    }
    0.0
}

/// Haversine-distance banded similarity on a `lat,long` field; exact-string
/// fallback when either side won't parse (never None).
pub fn geo_haversine_similarity(a: &str, b: &str) -> f64 {
    match (parse_latlong(a), parse_latlong(b)) {
        (Some(pa), Some(pb)) => geo_haversine_band(haversine_km(pa, pb)),
        _ => {
            if a == b {
                1.0
            } else {
                0.0
            }
        }
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

/// GoldenMatch canonical American Soundex -- the in-house reference for every
/// `goldenmatch` soundex surface (the bucket `score_one` path, the Python
/// pure/blocking fallbacks, and the TS port). A Unicode-folding STANDARD Soundex:
///
/// - `NFKD`-normalize + Unicode-uppercase, then walk the string. ASCII letters
///   `[A-Z]` are seed / coded consonant / `H`-`W`-transparent / vowel-break as in
///   classic Soundex; EVERY other char -- digit, punctuation, whitespace, the
///   combining marks NFKD strips off accents, and exotic non-decomposable letters
///   (`þ`/`Æ`/`Đ`) -- is a SEPARATOR that breaks the coding run (resets adjacency)
///   but is skipped and never seeds. So multi-token values code each token's
///   boundary consonant instead of merging it away (`"joseph bradshaw" -> "J211"`,
///   NOT `"J216"`), and accents fold to their base letter (`José` -> `J200`,
///   `Muñoz` -> `M520`). On PURE-ASCII input this equals classic American Soundex
///   (word separators break the run) -- the historical behavior real person-name
///   blocking/scoring depends on; it also equals jellyfish on ASCII EXCEPT that
///   jellyfish nonsensically seeds a leading digit (`"1st" -> "1230"`) where this
///   seeds the first real letter.
/// - Standard Soundex over each letter run: seed = the first letter, code `B..R`
///   per `soundex_code`, adjacent duplicate codes collapse, `H`/`W` transparent,
///   vowels (incl. `Y`) break the run; right-pad to four with `0`.
/// - NO surviving letter (empty / all-digit / all-punctuation) -> `""`. A value
///   with no phonetic content has no code; on the blocking side an empty key is
///   filtered (no giant garbage block), and the `soundex_match` scorer treats an
///   empty code as a non-match against ANYTHING (see `soundex_match`).
///
/// Rust `nfkd()` (`unicode-normalization`) + `str::to_uppercase` implement the
/// same Unicode algorithms as Python `unicodedata.normalize("NFKD", …).upper()`
/// and JS `String.prototype.normalize("NFKD")` / `toUpperCase()`, so the result
/// is byte-identical across the Rust / Python-fallback / TS-fallback surfaces
/// (the ASCII/Latin-scoped Unicode-version parity edge the other kernels document
/// applies here too). Cross-surface parity in `tests/test_native_soundex_parity.py`.
pub fn soundex(s: &str) -> String {
    let normalized: String = s.nfkd().collect::<String>().to_uppercase();
    let mut result = String::with_capacity(4);
    let mut count = 0usize; // chars pushed so far (seed + digits)
    let mut last: Option<char> = None; // code of the previous letter (None after seed/vowel/separator)
    for c in normalized.chars() {
        if c.is_ascii_uppercase() {
            if count == 0 {
                result.push(c); // seed = first surviving letter
                count = 1;
                last = soundex_code(c); // seed's would-be code suppresses a same-code follower
                continue;
            }
            match soundex_code(c) {
                Some(code) => {
                    if Some(code) != last {
                        result.push(code);
                        count += 1;
                    }
                    last = Some(code);
                }
                None => {
                    // Vowels (A/E/I/O/U/Y) break the run; H/W stay transparent.
                    if c != 'H' && c != 'W' {
                        last = None;
                    }
                }
            }
            if count == 4 {
                break;
            }
        } else {
            // Separator (digit / punctuation / whitespace / combining mark): break the
            // coding run so a same code across the gap is re-emitted, not merged, and
            // never seed on a non-letter.
            last = None;
        }
    }
    if count == 0 {
        return String::new();
    }
    for _ in count..4 {
        result.push('0');
    }
    result
}

/// `soundex_match` scorer: `1.0` iff two values share a NON-EMPTY soundex code,
/// else `0.0`. The empty-code guard is load-bearing -- a value with no phonetic
/// content (empty / all-digit / all-punctuation -> `soundex` returns `""`) never
/// matches, INCLUDING another empty-code value, so placeholder columns
/// (`"000"`, `"-"`, `""`) can't mega-cluster into one phonetic bucket. Byte-for-byte
/// with the Python `_soundex_score_single` + TS `soundexMatch` fallbacks.
pub fn soundex_match(a: &str, b: &str) -> f64 {
    let ca = soundex(a);
    if ca.is_empty() {
        return 0.0;
    }
    if ca == soundex(b) {
        1.0
    } else {
        0.0
    }
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

// ---- bloom / hash scorers (dice / jaccard / phash) --------------------------
// Byte-exact ports of `core.scorer._dice_score_single` / `_jaccard_score_single`
// / `_phash_score_single`: hex-decode -> INTEGER popcount -> a single f64 divide.
// The Python single functions use `np.unpackbits(...).sum()` (an integer count)
// then one float64 division, so `count_ones()` here is bit-exact -- unlike the
// numpy MATRIX forms (`_dice_score_matrix` etc.), which compute in float32 and
// round to ~1e-7. Pure primitives (no table/regex) -> no feature gate.
//
// Padding is PAIRWISE, matching `_pad_to_equal_length`. dice/jaccard are
// padding-INVARIANT (their denominators are popcounts, unchanged by trailing
// zero bytes), so this is also matrix-parity for them; phash's denominator is
// bit-LENGTH, so it matches the PAIRWISE `_phash_score_single` (NOT the
// block-global `_phash_score_matrix` -- the Option-A choice in the
// `2026-07-21-block-aware-bucket-kernel-design.md` spec).

/// Decode an even-length hex string to bytes, or `None` if malformed. The Python
/// single functions raise `ValueError` on bad hex (`bytes.fromhex`); a `score_one`
/// kernel can't raise, so unparseable hex -> `None` -> score `0.0` (never crashes
/// the block loop). PPRL CLK / image pHash inputs are always valid fixed-width hex,
/// so this edge doesn't arise in practice; it's asserted in the parity test.
/// (Python `bytes.fromhex` also skips ASCII whitespace between pairs; CLK/pHash hex
/// has none, so this strict decoder is byte-parity on the real inputs.)
fn decode_hex(s: &str) -> Option<Vec<u8>> {
    let bytes = s.as_bytes();
    if !bytes.len().is_multiple_of(2) {
        return None;
    }
    let mut out = Vec::with_capacity(bytes.len() / 2);
    let mut i = 0;
    while i < bytes.len() {
        let hi = (bytes[i] as char).to_digit(16)?;
        let lo = (bytes[i + 1] as char).to_digit(16)?;
        out.push((hi * 16 + lo) as u8);
        i += 2;
    }
    Some(out)
}

fn popcount_bytes(b: &[u8]) -> u32 {
    b.iter().map(|x| x.count_ones()).sum()
}

/// `_norm_phash_hex`: strip an optional `0x`/`0X` prefix, left-pad an odd length
/// to even so the hex decodes. (Python `s[:2]` on a <2-char string is the string
/// itself and never matches `"0x"`/`"0X"`, so the prefix strip needs len >= 2.)
fn norm_phash_hex(s: &str) -> String {
    let s = if s.len() >= 2 && (s.starts_with("0x") || s.starts_with("0X")) {
        &s[2..]
    } else {
        s
    };
    if !s.len().is_multiple_of(2) {
        format!("0{s}")
    } else {
        s.to_string()
    }
}

/// Dice coefficient `2*|A&B| / (|A|+|B|)` on two hex bloom filters; byte-for-byte
/// with `_dice_score_single`. Padding-invariant (popcount denominators).
pub fn dice_similarity(a: &str, b: &str) -> f64 {
    let (pa, pb) = match (decode_hex(a), decode_hex(b)) {
        (Some(x), Some(y)) => (x, y),
        _ => return 0.0,
    };
    let m = pa.len().min(pb.len());
    let inter: u32 = (0..m).map(|i| (pa[i] & pb[i]).count_ones()).sum();
    let total = popcount_bytes(&pa) + popcount_bytes(&pb);
    if total == 0 {
        0.0
    } else {
        2.0 * inter as f64 / total as f64
    }
}

/// Jaccard `|A&B| / |A|B|` on two hex bloom filters; byte-for-byte with
/// `_jaccard_score_single`. `|A|B| = |A|+|B|-|A&B|` (inclusion-exclusion), which
/// is padding-invariant.
pub fn jaccard_similarity(a: &str, b: &str) -> f64 {
    let (pa, pb) = match (decode_hex(a), decode_hex(b)) {
        (Some(x), Some(y)) => (x, y),
        _ => return 0.0,
    };
    let m = pa.len().min(pb.len());
    let inter: u32 = (0..m).map(|i| (pa[i] & pb[i]).count_ones()).sum();
    let total = popcount_bytes(&pa) + popcount_bytes(&pb);
    let union = total - inter;
    if union == 0 {
        0.0
    } else {
        inter as f64 / union as f64
    }
}

/// Perceptual-hash Hamming similarity `1 - dist / nbits` on two hex pHashes;
/// byte-for-byte with `_phash_score_single`. PAIRWISE padding: `nbits` is the
/// longer of the two hashes in bits (a trailing byte XOR implicit zero is the
/// byte itself, so it counts in `dist`). Empty -> `nbits == 0` -> `0.0`.
pub fn phash_similarity(a: &str, b: &str) -> f64 {
    let (pa, pb) = match (decode_hex(&norm_phash_hex(a)), decode_hex(&norm_phash_hex(b))) {
        (Some(x), Some(y)) => (x, y),
        _ => return 0.0,
    };
    let nbits = pa.len().max(pb.len()) * 8;
    if nbits == 0 {
        return 0.0;
    }
    let m = pa.len().min(pb.len());
    let mut dist: u32 = (0..m).map(|i| (pa[i] ^ pb[i]).count_ones()).sum();
    dist += popcount_bytes(&pa[m..]); // tail of the longer ^ 0 = the byte itself
    dist += popcount_bytes(&pb[m..]); // (exactly one of these slices is non-empty)
    1.0 - dist as f64 / nbits as f64
}

/// `ensemble`: element-wise max of jaro_winkler, the UNSCALED token_sort, and a
/// `0.8` soundex bonus -- composes `score_one(0)`/`(2)`/`(6)`. Byte-for-byte with
/// the bucket per-pair mirror `_ensemble_score_single`
/// (`max(JaroWinkler.similarity, token_sort_ratio/100, 0.8 if soundex(a)==soundex(b) else 0)`)
/// to the same tolerance jaro_winkler / token_sort individually hold vs rapidfuzz:
/// `score_one(2)` is already the UNSCALED `fuzz::ratio` on [0, 1], so it maps
/// directly onto the Python `/100` form (no divide), and `score_one(6)` is the
/// binary 1.0/0.0 soundex-equality, so `0.8 * score_one(6)` is the exact bonus.
pub fn ensemble_similarity(a: &str, b: &str) -> f64 {
    let jw = score_one(0, a, b);
    let ts = score_one(2, a, b);
    let sx = score_one(6, a, b);
    jw.max(ts).max(0.8 * sx)
}

/// `radial_from_hex`: parse a 2-hex-char-per-bin signed-byte radial-variance
/// profile (`0x` prefix tolerated) back to a list of ints; byte-for-byte with the
/// Python `radial_from_hex`. Odd trailing char is dropped (`usable` truncation);
/// a non-hex char -> `None` (the Python `int(..., 16)` would raise there, and the
/// score_one arm cannot, so it declines to 0.0).
fn radial_from_hex(s: &str) -> Option<Vec<i64>> {
    let s = if s.len() >= 2 && (s.starts_with("0x") || s.starts_with("0X")) {
        &s[2..]
    } else {
        s
    };
    let bytes = s.as_bytes();
    let usable = bytes.len() - (bytes.len() % 2);
    let mut out = Vec::with_capacity(usable / 2);
    let mut i = 0;
    while i < usable {
        // `bytes[i] as char` maps a non-ASCII byte to a non-hex char -> None,
        // so this never panics on a multibyte input the way `&s[i..i+2]` would.
        let hi = (bytes[i] as char).to_digit(16)?;
        let lo = (bytes[i + 1] as char).to_digit(16)?;
        let b = (hi * 16 + lo) as i64; // 0..=255
        out.push(if b >= 128 { b - 256 } else { b });
        i += 2;
    }
    Some(out)
}

/// Pearson correlation of two equal-length int sequences; 0.0 if either is
/// constant. Byte-for-byte with the Python `_pearson`: the mean is
/// `sum(int)/len` (exact integer sum, one float divide) and every reduction
/// accumulates left-to-right in f64, so the summation order matches.
fn pearson(a: &[i64], b: &[i64]) -> f64 {
    let n = a.len() as f64;
    let sa: i64 = a.iter().sum();
    let sb: i64 = b.iter().sum();
    let ma = sa as f64 / n;
    let mb = sb as f64 / n;
    let mut da = 0.0f64;
    for &x in a {
        let d = x as f64 - ma;
        da += d * d;
    }
    let mut db = 0.0f64;
    for &y in b {
        let d = y as f64 - mb;
        db += d * d;
    }
    if da == 0.0 || db == 0.0 {
        return 0.0;
    }
    let mut num = 0.0f64;
    for (&x, &y) in a.iter().zip(b.iter()) {
        num += (x as f64 - ma) * (y as f64 - mb);
    }
    num / (da * db).sqrt()
}

/// Rotation-aligned radial-profile similarity in [0, 1]: the max Pearson over
/// every cyclic angular shift of `b`, clamped. Mismatched/empty profiles -> 0.0.
/// Byte-for-byte with the Python `radial_align_similarity`.
fn radial_align(a: &[i64], b: &[i64]) -> f64 {
    let la = a.len();
    if la == 0 || b.len() != la {
        return 0.0;
    }
    let mut best = -1.0f64;
    let mut rotated = vec![0i64; la];
    for shift in 0..la {
        // rotated = b[shift:] + b[:shift]
        for (k, slot) in rotated.iter_mut().enumerate() {
            *slot = b[(shift + k) % la];
        }
        let c = pearson(a, &rotated);
        if c > best {
            best = c;
        }
    }
    // == Python `max(0.0, min(1.0, best))`; `best` is a Pearson value or the -1.0
    // seed, never NaN, so clamp is safe and bit-identical.
    best.clamp(0.0, 1.0)
}

/// `radial` scorer (score_one id 13): rotation-aligned Pearson of two hex
/// radial-variance profiles; byte-for-byte with `_radial_score_single`
/// (`radial_align_similarity(radial_from_hex(a), radial_from_hex(b))`). A parse
/// failure -> 0.0 (the score_one contract cannot raise).
pub fn radial_similarity(a: &str, b: &str) -> f64 {
    match (radial_from_hex(a), radial_from_hex(b)) {
        (Some(x), Some(y)) => radial_align(&x, &y),
        _ => 0.0,
    }
}

/// `audio_fp_from_hex`: parse a concatenated 8-hex-char-per-word fingerprint
/// (`0x` prefix tolerated) back to a list of u32 sub-fingerprints; byte-for-byte
/// with the Python `audio_fp_from_hex`. Trailing chars past the last full word
/// are dropped; a non-hex char -> `None` (score_one declines to 0.0).
fn audio_fp_from_hex(s: &str) -> Option<Vec<u32>> {
    let s = if s.len() >= 2 && (s.starts_with("0x") || s.starts_with("0X")) {
        &s[2..]
    } else {
        s
    };
    let bytes = s.as_bytes();
    let usable = bytes.len() - (bytes.len() % 8);
    let mut out = Vec::with_capacity(usable / 8);
    let mut i = 0;
    while i < usable {
        let mut w: u32 = 0;
        for j in 0..8 {
            let d = (bytes[i + j] as char).to_digit(16)?;
            w = w * 16 + d; // 8 hex digits fit u32 with no overflow
        }
        out.push(w);
        i += 8;
    }
    Some(out)
}

/// Best (minimum) bit-error-rate over all frame offsets of two audio
/// fingerprints; byte-for-byte with the Python `audio_ber_aligned` (min_overlap
/// 8, `AUDIO_BANDS - 1 == 32` bits per sub-fingerprint). Empty input -> 1.0.
fn audio_ber_aligned(a: &[u32], b: &[u32]) -> f64 {
    let la = a.len() as i64;
    let lb = b.len() as i64;
    if la == 0 || lb == 0 {
        return 1.0;
    }
    let need = 8.min(la).min(lb);
    let nb = 32.0f64; // AUDIO_BANDS - 1
    let mut best = 1.0f64;
    let mut off = -(lb - 1);
    while off < la {
        let lo = off.max(0);
        let hi = la.min(off + lb);
        let overlap = hi - lo;
        if overlap < need {
            off += 1;
            continue;
        }
        let mut bits: u64 = 0;
        let mut i = lo;
        while i < hi {
            bits += (a[i as usize] ^ b[(i - off) as usize]).count_ones() as u64;
            i += 1;
        }
        let ber = bits as f64 / (overlap as f64 * nb);
        if ber < best {
            best = ber;
        }
        off += 1;
    }
    best
}

/// `audio_fp` scorer (score_one id 14): offset-aligned similarity `1 - best BER`
/// of two hex audio fingerprints; byte-for-byte with `_audio_fp_score_single`
/// (`1.0 - audio_ber_aligned(audio_fp_from_hex(a), audio_fp_from_hex(b))`). A
/// parse failure -> 0.0.
pub fn audio_fp_similarity(a: &str, b: &str) -> f64 {
    match (audio_fp_from_hex(a), audio_fp_from_hex(b)) {
        (Some(x), Some(y)) => 1.0 - audio_ber_aligned(&x, &y),
        _ => 0.0,
    }
}

/// Scorer dispatch matching `score_buckets._resolve_score_pair_callable`'s
/// fast-path scale, all on [0, 1]. ids: 0=jaro_winkler, 1=levenshtein,
/// 2=token_sort, 3=exact, 4=date, 5=qgram, 6=soundex_match, 7=initialism_match,
/// 8=alias_match, 9=dice, 10=jaccard, 11=phash, 12=ensemble, 13=radial,
/// 14=audio_fp, 17=date_diff, 18=geo_haversine. (ids 15/16 are the
/// weighted-bucket name scorers, dispatched by the native crate's
/// `bucket_field_similarity` before it delegates other ids here -- not by
/// `score_one` -- so they are intentionally absent from this match.)
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
        // id=6 = soundex_match: 1.0 iff a NON-EMPTY soundex code is shared, else
        // 0.0 (the empty-code guard lives in `soundex_match` -- garbage/empty never
        // matches, so placeholder columns can't mega-cluster). Matches the bucket
        // per-pair mirror `_soundex_score_single`.
        6 => soundex_match(a, b),
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
        // ids 9/10/11 = dice / jaccard / phash (bloom-hex + hex-hamming, integer
        // popcount). Byte-exact with the `_*_score_single` per-pair references the
        // bucket path already uses; the Python guard gates each on its capability
        // symbol so a stale wheel declines to those mirrors.
        9 => dice_similarity(a, b),
        10 => jaccard_similarity(a, b),
        11 => phash_similarity(a, b),
        // id=12 = ensemble: max(jaro_winkler, unscaled token_sort, 0.8*soundex).
        // Composes ids 0/2/6 (each already parity-validated vs its per-pair
        // mirror), so it matches `_ensemble_score_single` by construction. The
        // Python guard gates it on the `ensemble_similarity` capability symbol.
        12 => ensemble_similarity(a, b),
        // ids 13/14 = radial / audio_fp (perceptual profile scorers: hex-parse +
        // alignment search, f64 reductions). Byte-exact with the per-pair
        // `_radial_score_single` / `_audio_fp_score_single` mirrors. The Python
        // guard gates each on its capability symbol so a stale wheel declines to
        // those mirrors instead of silently zeroing via this catch-all.
        13 => radial_similarity(a, b),
        14 => audio_fp_similarity(a, b),
        // ids 17/18 = date_diff / geo_haversine (FS domain comparators, spec
        // 2026-07-23). Magnitude-aware day-distance / great-circle km bands.
        // (ids 15/16 are the weighted-bucket name scorers, handled by the native
        // crate's `bucket_field_similarity`, never reaching here.) Byte-parity
        // with the `_date_diff_similarity_py` / `_geo_haversine_similarity_py`
        // mirrors the bucket per-pair path falls back to; the Python guard gates
        // each on its `date_diff_similarity` / `geo_haversine_similarity`
        // capability symbol so a stale wheel declines to those mirrors instead of
        // silently zeroing via this catch-all.
        17 => date_diff_similarity(a, b),
        18 => geo_haversine_similarity(a, b),
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
    fn soundex_canonical_in_house() {
        // Alphabetic single-token names: standard American Soundex, byte-identical
        // to jellyfish (the in-house spec agrees on all pure-ASCII letter runs).
        assert_eq!(soundex("Robert"), "R163");
        assert_eq!(soundex("Rupert"), "R163"); // Robert/Rupert collide
        assert_eq!(soundex("Ashcraft"), "A261"); // H/W skip rule
        assert_eq!(soundex("Tymczak"), "T522");
        assert_eq!(soundex("Pfister"), "P236"); // adjacent same-code (P,F -> 1) coalesces
        assert_eq!(soundex("Honeyman"), "H555");
        // Multi-token names: a separator (space) BREAKS the coding run, so the code
        // on each side of the gap is kept -- it is NOT merged the way stripping
        // separators would. This is the historical behavior person-name blocking
        // depends on (matches jellyfish; the strip variant regressed it).
        assert_eq!(soundex("joseph bradshaw"), "J211"); // P then B kept (not merged to "J216")
        assert_eq!(soundex("warren nale"), "W655"); // N | N kept (not merged to "W654")
        // Accented Latin folds via NFKD to the base letter -- byte-identical here.
        assert_eq!(soundex("Ürüm"), "U650");
        assert_eq!(soundex("José"), "J200");
        assert_eq!(soundex("Muñoz"), "M520"); // ñ folds to n (jellyfish drops it -> "M200")
        assert_eq!(soundex("ß"), "S000"); // upper() -> "SS" -> dup-collapse
        // No surviving letter -> "" (the DIVERGENCE from jellyfish, which would
        // seed the literal digit/symbol: "123" -> "1000").
        assert_eq!(soundex(""), "");
        assert_eq!(soundex("123"), "");
        assert_eq!(soundex("!!"), "");
        // Non-letters are SEPARATORS (break the run) and never seed. A leading
        // separator is skipped; a MID separator breaks adjacency so a same code
        // across the gap is re-emitted (unlike jellyfish, which seeds a leading
        // digit: "3M"->"3500", "S1S"->"S200" matches us on the break but "1st"
        // differs by seed).
        assert_eq!(soundex("3M"), "M000"); // leading sep -> seed M
        assert_eq!(soundex("4abc"), "A120"); // leading sep -> "abc"
        assert_eq!(soundex("12ab"), "A100"); // leading seps -> "ab"
        assert_eq!(soundex("S1S"), "S200"); // S | S: the "1" breaks adjacency -> 2 re-emitted
        // Exotic non-decomposable letters are separators too (jellyfish keeps the
        // raw char as the literal seed: "Þór"->"Þ600", "Æthel"->"Æ340").
        assert_eq!(soundex("Þór"), "O600"); // Þ sep -> "or"
        assert_eq!(soundex("Æthel"), "T400"); // Æ sep -> "thel"
        assert_eq!(soundex("Đặng"), "A520"); // Đ + combining seps -> "ang"
    }

    #[test]
    fn soundex_match_empty_code_never_matches() {
        // Non-empty shared code -> 1.0; different -> 0.0.
        assert_eq!(soundex_match("Robert", "Rupert"), 1.0);
        assert_eq!(soundex_match("Robert", "Smith"), 0.0);
        // Empty code (garbage) never matches -- not even another empty code, so
        // placeholder columns don't collapse into one phonetic bucket.
        assert_eq!(soundex_match("123", "456"), 0.0); // both "" -> still 0.0
        assert_eq!(soundex_match("123", "123"), 0.0); // identical garbage -> 0.0
        assert_eq!(soundex_match("", ""), 0.0);
        assert_eq!(soundex_match("123", "Robert"), 0.0); // "" vs a real code
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
        // Empty-code guard: garbage never matches (id 6 == soundex_match).
        assert_eq!(score_one(6, "123", "456"), 0.0);
        assert_eq!(score_one(6, "123", "123"), 0.0); // both "" -> guarded to 0.0
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
    fn score_one_ids_9_10_11_are_dice_jaccard_phash() {
        // Ground truth from core.scorer._dice_score_single / _jaccard_score_single
        // / _phash_score_single. Exact f64 (integer popcount + one f64 divide, same
        // op order as the Python single functions) -- no float32 rounding.
        // dice = 2*|A&B| / (|A|+|B|)
        assert_eq!(score_one(9, "ab12", "cd34"), 2.0 * 4.0 / 15.0);
        assert_eq!(score_one(9, "ffff", "0f0f"), 2.0 * 8.0 / 24.0);
        assert_eq!(score_one(9, "ab", "abcd"), 2.0 * 5.0 / 15.0); // pairwise pad invariant
        assert_eq!(score_one(9, "00ff", "ff00"), 0.0); // disjoint
        assert_eq!(score_one(9, "abcd", "abcd"), 1.0);
        assert_eq!(score_one(9, "0000", "ffff"), 0.0); // empty A -> total>0, inter 0
        // jaccard = |A&B| / |A|B|
        assert_eq!(score_one(10, "ab12", "cd34"), 4.0 / 11.0);
        assert_eq!(score_one(10, "ffff", "0f0f"), 0.5);
        assert_eq!(score_one(10, "ab", "abcd"), 0.5);
        assert_eq!(score_one(10, "abcd", "abcd"), 1.0);
        assert_eq!(score_one(10, "00ff", "ff00"), 0.0);
        // phash = 1 - dist/nbits (PAIRWISE nbits)
        assert_eq!(score_one(11, "ab12", "cd34"), 0.5625);
        assert_eq!(score_one(11, "ffff", "0f0f"), 0.5);
        assert_eq!(score_one(11, "ab", "abcd"), 0.6875); // pairwise pad: nbits=16
        assert_eq!(score_one(11, "abcd", "abcd"), 1.0);
        assert_eq!(score_one(11, "0x00ff", "ff00"), 0.0); // 0x prefix stripped
        assert_eq!(score_one(11, "f", "f"), 1.0); // odd length left-padded to "0f"
        // Unparseable hex -> 0.0 (kernel can't raise like the single fns do).
        assert_eq!(score_one(9, "zz", "abcd"), 0.0);
        assert_eq!(score_one(11, "", ""), 0.0); // nbits == 0
    }

    #[test]
    fn score_one_id12_is_ensemble_max_of_components() {
        // ensemble = max(jaro_winkler, unscaled token_sort, 0.8*soundex_match).
        // Arm 12 must equal the standalone ensemble_similarity, which equals the
        // component max.
        for (a, b) in [
            ("John Smith", "Smith John"),
            ("Robert", "Rupert"),
            ("cafe", "café"),
            ("", ""),
            ("abc", "xyz"),
        ] {
            let jw = score_one(0, a, b);
            let ts = score_one(2, a, b);
            let sx = score_one(6, a, b);
            let want = jw.max(ts).max(0.8 * sx);
            assert_eq!(score_one(12, a, b), want, "score_one(12) for {a:?}/{b:?}");
            assert_eq!(ensemble_similarity(a, b), want, "ensemble_similarity {a:?}/{b:?}");
        }
        // Identical strings -> 1.0 (jw dominates). Soundex-only agreement floors at 0.8.
        assert_eq!(score_one(12, "robert", "robert"), 1.0);
        assert!(score_one(12, "Robert", "Rupert") >= 0.8 - 1e-12); // both R163
    }

    #[test]
    fn score_one_ids_13_14_are_radial_audio_fp() {
        // Ground truth from core.scorer._radial_score_single / _audio_fp_score_single.
        // --- radial (id 13): rotation-aligned Pearson, clamped to [0, 1] ---
        // identity of a NON-constant profile -> 1.0 (pearson(a,a)==1 at shift 0)
        assert_eq!(score_one(13, "01ff02", "01ff02"), 1.0); // [1,-1,2]
        // a cyclic rotation of the same profile recovers 1.0 (the whole point)
        assert_eq!(score_one(13, "01ff02", "ff0201"), 1.0); // [-1,2,1] is a shift
        // constant profile -> variance 0 -> pearson 0 -> 0.0
        assert_eq!(score_one(13, "010101", "020202"), 0.0);
        // mismatched length / empty / unparseable -> 0.0
        assert_eq!(score_one(13, "0102", "010203"), 0.0);
        assert_eq!(score_one(13, "", ""), 0.0);
        assert_eq!(score_one(13, "zz", "01ff02"), 0.0);
        // --- audio_fp (id 14): 1 - best offset BER over 32-bit sub-fingerprints ---
        assert_eq!(score_one(14, "00000001", "00000001"), 1.0); // aligned identical
        assert_eq!(score_one(14, "ffffffff", "00000000"), 0.0); // all 32 bits differ
        assert_eq!(score_one(14, "0x00000001", "00000001"), 1.0); // 0x prefix stripped
        assert_eq!(score_one(14, "", ""), 0.0); // empty -> BER 1.0 -> 1-1
        assert_eq!(score_one(14, "zz", "00000001"), 0.0); // unparseable
        // offset search: [0,1] vs [1] aligns the shared word -> BER 0 -> 1.0
        assert_eq!(score_one(14, "0000000000000001", "00000001"), 1.0);
        // arm N == the standalone fn across a mixed set
        for (a, b) in [
            ("01ff02", "020304"),
            ("0abbe11c", "1cffaa0b"),
            ("", "01"),
            ("bad", "01ff"),
        ] {
            assert_eq!(score_one(13, a, b), radial_similarity(a, b));
        }
        for (a, b) in [
            ("00000001", "0000000200000003"),
            ("deadbeef", "deadbeef"),
            ("", "00000001"),
            ("zzzzzzzz", "00000001"),
        ] {
            assert_eq!(score_one(14, a, b), audio_fp_similarity(a, b));
        }
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
