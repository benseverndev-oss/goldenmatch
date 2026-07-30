//! Owned, deterministic date/datetime parsing kernel (pyo3-free, wasm-friendly,
//! zero-dep — no `chrono`, no `dateutil`).
//!
//! This is the single source of truth for GoldenFlow's date family. It replaces
//! BOTH `python-dateutil` (the per-row reference) AND the Polars-vectorized
//! `str.to_date` fast path: `parse_date` / `parse_datetime` run over a column
//! with no Polars and no fuzzy-parser non-determinism. The pure-Python /
//! pure-TS fallbacks must reproduce these bytes exactly.
//!
//! ## Supported formats (byte-identical to
//! `dateutil.parser.parse(v, default=datetime(2000,1,1)).date()` on this set)
//!
//! Numeric (ambiguous numeric is MONTH-FIRST, dateutil's default):
//! - `%Y-%m-%d`, `%Y/%m/%d`  (ISO: a 4-digit FIRST group anchors year-first)
//! - `%m/%d/%Y`, `%m-%d-%Y`  (US month-first; if the month field is > 12 and the
//!   day field is <= 12 it flips to day-first, matching dateutil's
//!   disambiguation — e.g. `15-03-2024` -> 2024-03-15)
//!
//! English month names (abbrev + full, case-insensitive, like dateutil):
//! - `%b %d, %Y`, `%B %d, %Y`, `%b %d %Y`, `%B %d %Y`  (Month Day[,] Year)
//! - `%d %b %Y`, `%d %B %Y`                            (Day Month Year)
//! - `%b %Y`, `%B %Y`  (Month Year -> day 1, the deterministic partial fill)
//!
//! Year-only `YYYY` (4 digits) -> `(YYYY, 1, 1)`.
//!
//! Datetime (`parse_datetime`): any supported date, optionally followed by a
//! 24-hour time `HH:MM` or `HH:MM:SS`, separated by a space or a `T`. Missing
//! time -> `00:00:00`.
//!
//! ## Deterministic rules (documented, some INTENTIONALLY diverge from dateutil)
//! - **Missing month/day -> 1; missing time -> 00:00:00.** (Matches GoldenFlow's
//!   `_DEFAULT_DATE = datetime(2000, 1, 1)` policy — the owned semantics.)
//! - **2-digit years use a deterministic POSIX pivot: 00-68 -> 2000-2068,
//!   69-99 -> 1969-1999.** dateutil's mapping is `now()`-relative (non-
//!   deterministic); this is a deliberate, documented divergence.
//! - **Anything not in the supported set -> `None`.** dateutil fuzz-parses the
//!   exotic tail (bare month names, AM/PM times, weekday words, ordinals, ...);
//!   we don't. Deliberate, documented divergence — the caller passes the value
//!   through unchanged.
//! - **Real calendar validation:** month 1-12, day within the month (leap-aware);
//!   `2020-02-30`, month 13, etc. -> `None`.

/// Parse a supported date string to `(year, month, day)`, or `None` for anything
/// outside the supported set (see the module docs). Deterministic.
pub fn parse_date(s: &str) -> Option<(i32, u32, u32)> {
    let s = s.trim();
    if s.is_empty() {
        return None;
    }
    // Accept a datetime-bearing string and TRUNCATE to the date (e.g.
    // "2024-01-20 14:05:00" -> (2024,1,20)), matching the prior dateutil
    // behavior -- datetime values in a date column are a common real shape.
    // A trailing token that is NOT a valid time still fails (whole-string parse).
    let (date_part, time_part) = peel_time(s);
    let ymd = parse_date_only(date_part)?;
    if let Some(t) = time_part {
        parse_time(t)?; // trailing token must be a valid time, else not a date
    }
    Some(ymd)
}

/// Strict date-only parse (no trailing time): year-only, numeric, or English
/// month-name. `parse_date` (time-tolerant) and `parse_datetime` build on this.
fn parse_date_only(s: &str) -> Option<(i32, u32, u32)> {
    // 1. Year-only: exactly 4 ASCII digits -> (YYYY, 1, 1).
    if s.len() == 4 && s.bytes().all(|b| b.is_ascii_digit()) {
        let y: i32 = s.parse().ok()?;
        return Some((y, 1, 1));
    }

    // 2. Numeric with a single '-' or '/' separator (three all-digit groups).
    if let Some(ymd) = parse_numeric(s) {
        return Some(ymd);
    }

    // 3. English month-name forms.
    parse_month_name(s)
}

/// Parse a supported datetime string to `(year, month, day, hour, min, sec)`,
/// or `None`. A date with no time component fills the time with `00:00:00`.
pub fn parse_datetime(s: &str) -> Option<(i32, u32, u32, u32, u32, u32)> {
    let s = s.trim();
    if s.is_empty() {
        return None;
    }
    let (date_part, time_part) = peel_time(s);
    let (y, m, d) = parse_date_only(date_part)?;
    let (hh, mm, ss) = match time_part {
        Some(t) => parse_time(t)?,
        None => (0, 0, 0),
    };
    Some((y, m, d, hh, mm, ss))
}

// --------------------------------------------------------------------------- //
// String-output transform kernels (the fused-chain surface).
//
// These are the TOTAL `fn(&str) -> String` shapes the fused chain (`chain::Kernel`)
// dispatches — byte-identical to GoldenFlow's `_date_*_py` scalars: parse, format,
// and on a parse miss PASS THE INPUT THROUGH UNCHANGED (never null; the null cell
// is handled by the caller scattering nulls back). Years are always 4-digit from
// the parser's covered inputs (explicit `YYYY` / POSIX-pivoted 2-digit -> 1969-2068),
// so `{:04}` matches Python `date.isoformat()` / `strftime("%Y")` on this set.
// --------------------------------------------------------------------------- //

/// `date_iso8601` / `date_parse`: parse -> `YYYY-MM-DD`; passthrough on a miss.
pub fn date_iso8601(s: &str) -> String {
    match parse_date(s) {
        Some((y, m, d)) => format!("{y:04}-{m:02}-{d:02}"),
        None => s.to_string(),
    }
}

/// `date_us`: parse -> `MM/DD/YYYY`; passthrough on a miss.
pub fn date_us(s: &str) -> String {
    match parse_date(s) {
        Some((y, m, d)) => format!("{m:02}/{d:02}/{y:04}"),
        None => s.to_string(),
    }
}

/// `date_eu`: parse -> `DD/MM/YYYY`; passthrough on a miss.
pub fn date_eu(s: &str) -> String {
    match parse_date(s) {
        Some((y, m, d)) => format!("{d:02}/{m:02}/{y:04}"),
        None => s.to_string(),
    }
}

/// `datetime_iso8601`: parse -> `YYYY-MM-DDTHH:MM:SS` (missing time -> `00:00:00`);
/// passthrough on a miss.
pub fn datetime_iso8601(s: &str) -> String {
    match parse_datetime(s) {
        Some((y, m, d, hh, mm, ss)) => format!("{y:04}-{m:02}-{d:02}T{hh:02}:{mm:02}:{ss:02}"),
        None => s.to_string(),
    }
}

// --------------------------------------------------------------------------- //
// Numeric dates
// --------------------------------------------------------------------------- //

fn parse_numeric(s: &str) -> Option<(i32, u32, u32)> {
    for sep in ['-', '/'] {
        let parts: Vec<&str> = s.split(sep).collect();
        if parts.len() == 3
            && parts
                .iter()
                .all(|p| !p.is_empty() && p.len() <= 4 && p.bytes().all(|b| b.is_ascii_digit()))
        {
            if let Some(ymd) = interpret_numeric(parts[0], parts[1], parts[2]) {
                return Some(ymd);
            }
        }
    }
    None
}

fn interpret_numeric(g1: &str, g2: &str, g3: &str) -> Option<(i32, u32, u32)> {
    let (y, m, d) = if g1.len() == 4 {
        // ISO: a 4-digit first group anchors year-first (Y, M, D).
        let y: i32 = g1.parse().ok()?;
        let m: u32 = g2.parse().ok()?;
        let d: u32 = g3.parse().ok()?;
        (y, m, d)
    } else {
        // US month-first: year is the last group. If the "month" field is > 12
        // and the "day" field is <= 12, flip to day-first (dateutil's rule).
        let y = parse_year_token(g3)?;
        let a: u32 = g1.parse().ok()?;
        let b: u32 = g2.parse().ok()?;
        if a > 12 && b <= 12 {
            (y, b, a)
        } else {
            (y, a, b)
        }
    };
    if valid_ymd(y, m, d) {
        Some((y, m, d))
    } else {
        None
    }
}

// --------------------------------------------------------------------------- //
// Month-name dates
// --------------------------------------------------------------------------- //

fn parse_month_name(s: &str) -> Option<(i32, u32, u32)> {
    // Tokenize on whitespace, dropping a trailing comma on each token
    // ("Jan 5, 2023" -> ["Jan", "5", "2023"]).
    let toks: Vec<&str> = s
        .split_whitespace()
        .map(|t| t.trim_end_matches(','))
        .filter(|t| !t.is_empty())
        .collect();

    match toks.len() {
        3 => {
            // "Mon Day Year"
            if let Some(m) = month_from_name(toks[0]) {
                let d = parse_day_token(toks[1])?;
                let y = parse_year_token(toks[2])?;
                return finish(y, m, d);
            }
            // "Day Mon Year"
            if let Some(m) = month_from_name(toks[1]) {
                let d = parse_day_token(toks[0])?;
                let y = parse_year_token(toks[2])?;
                return finish(y, m, d);
            }
            None
        }
        2 => {
            // "Mon Year" -> day 1 (deterministic partial fill).
            if let Some(m) = month_from_name(toks[0]) {
                let y = parse_year_token(toks[1])?;
                return finish(y, m, 1);
            }
            None
        }
        _ => None,
    }
}

fn finish(y: i32, m: u32, d: u32) -> Option<(i32, u32, u32)> {
    if valid_ymd(y, m, d) {
        Some((y, m, d))
    } else {
        None
    }
}

fn month_from_name(tok: &str) -> Option<u32> {
    let t = tok.trim_end_matches('.').to_ascii_lowercase();
    let m = match t.as_str() {
        "jan" | "january" => 1,
        "feb" | "february" => 2,
        "mar" | "march" => 3,
        "apr" | "april" => 4,
        "may" => 5,
        "jun" | "june" => 6,
        "jul" | "july" => 7,
        "aug" | "august" => 8,
        "sep" | "sept" | "september" => 9,
        "oct" | "october" => 10,
        "nov" | "november" => 11,
        "dec" | "december" => 12,
        _ => return None,
    };
    Some(m)
}

// --------------------------------------------------------------------------- //
// Tokens, time, validity
// --------------------------------------------------------------------------- //

/// Parse a day/month numeric token (1-2 digits, all ASCII digits).
fn parse_day_token(tok: &str) -> Option<u32> {
    if tok.is_empty() || tok.len() > 2 || !tok.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    tok.parse().ok()
}

/// Parse a year token. Exactly 2 digits -> deterministic POSIX pivot; 4 digits
/// (or 1/3, accepted as-is) -> the integer value. More than 4 digits -> `None`.
fn parse_year_token(tok: &str) -> Option<i32> {
    if tok.is_empty() || tok.len() > 4 || !tok.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let v: i32 = tok.parse().ok()?;
    if tok.len() == 2 {
        Some(pivot_2digit(v))
    } else {
        Some(v)
    }
}

/// POSIX 2-digit-year pivot: 00-68 -> 2000-2068, 69-99 -> 1969-1999.
fn pivot_2digit(yy: i32) -> i32 {
    if yy <= 68 {
        2000 + yy
    } else {
        1900 + yy
    }
}

/// Parse a 24-hour time `HH:MM` or `HH:MM:SS` (all ASCII digits). Seconds
/// default to 0. Validates H<24, M<60, S<60. Anything else -> `None`.
fn parse_time(t: &str) -> Option<(u32, u32, u32)> {
    let parts: Vec<&str> = t.split(':').collect();
    if !(parts.len() == 2 || parts.len() == 3) {
        return None;
    }
    if parts
        .iter()
        .any(|p| p.is_empty() || p.len() > 2 || !p.bytes().all(|b| b.is_ascii_digit()))
    {
        return None;
    }
    let hh: u32 = parts[0].parse().ok()?;
    let mm: u32 = parts[1].parse().ok()?;
    let ss: u32 = if parts.len() == 3 {
        parts[2].parse().ok()?
    } else {
        0
    };
    if hh < 24 && mm < 60 && ss < 60 {
        Some((hh, mm, ss))
    } else {
        None
    }
}

/// Split `s` into a date part and an optional trailing time part.
/// - ISO `T` separator: `<date>T<time>` when the `T` sits between digits.
/// - Otherwise a trailing whitespace token that looks like a time is peeled off.
fn peel_time(s: &str) -> (&str, Option<&str>) {
    // ISO 'T': a 'T' flanked by digits separates date and time.
    let bytes = s.as_bytes();
    for (i, &b) in bytes.iter().enumerate() {
        if b == b'T' && i > 0 && i + 1 < bytes.len() {
            let before = bytes[i - 1];
            let after = bytes[i + 1];
            if before.is_ascii_digit() && after.is_ascii_digit() {
                return (&s[..i], Some(&s[i + 1..]));
            }
        }
    }
    // Trailing whitespace-delimited time token.
    if let Some(pos) = s.rfind(char::is_whitespace) {
        let (head, tail) = s.split_at(pos);
        let tail = tail.trim_start();
        if looks_like_time(tail) {
            return (head.trim_end(), Some(tail));
        }
    }
    (s, None)
}

/// A token is time-shaped if it is `HH:MM` or `HH:MM:SS` with 1-2 digit fields.
fn looks_like_time(tok: &str) -> bool {
    let parts: Vec<&str> = tok.split(':').collect();
    (parts.len() == 2 || parts.len() == 3)
        && parts
            .iter()
            .all(|p| !p.is_empty() && p.len() <= 2 && p.bytes().all(|b| b.is_ascii_digit()))
}

fn is_leap(y: i32) -> bool {
    (y % 4 == 0 && y % 100 != 0) || y % 400 == 0
}

fn days_in_month(y: i32, m: u32) -> u32 {
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

fn valid_ymd(y: i32, m: u32, d: u32) -> bool {
    (1..=12).contains(&m) && d >= 1 && d <= days_in_month(y, m)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn iso_numeric() {
        assert_eq!(parse_date("2024-03-15"), Some((2024, 3, 15)));
        assert_eq!(parse_date("2024/03/15"), Some((2024, 3, 15)));
        assert_eq!(parse_date("2024-3-5"), Some((2024, 3, 5)));
    }

    #[test]
    fn parse_date_truncates_datetime() {
        // A datetime-bearing string on a date-only parse truncates to the date
        // (matches dateutil, which parses the whole string then we take .date()).
        assert_eq!(parse_date("2024-01-20 14:05:00"), Some((2024, 1, 20)));
        assert_eq!(parse_date("2024-01-20T14:05:00"), Some((2024, 1, 20)));
        assert_eq!(parse_date("03/15/2024 09:30"), Some((2024, 3, 15)));
        // Month-name dates with internal spaces are NOT mistaken for a time.
        assert_eq!(parse_date("Jan 2, 2020"), Some((2020, 1, 2)));
        assert_eq!(parse_date("2 January 2020"), Some((2020, 1, 2)));
        // A trailing non-time token is still rejected (not silently truncated).
        assert_eq!(parse_date("2024-01-20 garbage"), None);
    }

    #[test]
    fn string_kernels_format_and_passthrough() {
        // Format shapes match the `_date_*_py` scalars.
        assert_eq!(date_iso8601("03/15/2024"), "2024-03-15");
        assert_eq!(date_iso8601("Jan 2, 2020"), "2020-01-02");
        assert_eq!(date_iso8601("2020"), "2020-01-01");
        assert_eq!(date_us("2024-03-15"), "03/15/2024");
        assert_eq!(date_eu("2024-03-15"), "15/03/2024");
        assert_eq!(
            datetime_iso8601("2024-01-20 14:05:00"),
            "2024-01-20T14:05:00"
        );
        assert_eq!(datetime_iso8601("2024-03-15"), "2024-03-15T00:00:00");
        // Datetime-bearing string truncates on the date-only kernels.
        assert_eq!(date_iso8601("2024-01-20 14:05:00"), "2024-01-20");
        // A parse miss passes the input through UNCHANGED (total, never null).
        assert_eq!(date_iso8601("garbage"), "garbage");
        assert_eq!(date_us("not a date"), "not a date");
        assert_eq!(datetime_iso8601("2024-01-20 garbage"), "2024-01-20 garbage");
    }

    #[test]
    fn us_month_first() {
        assert_eq!(parse_date("03/15/2024"), Some((2024, 3, 15)));
        assert_eq!(parse_date("03-15-2024"), Some((2024, 3, 15)));
        // Ambiguous -> month-first.
        assert_eq!(parse_date("05/06/2024"), Some((2024, 5, 6)));
    }

    #[test]
    fn day_first_when_month_gt_12() {
        // dateutil disambiguation: 15 can't be a month, so day-first.
        assert_eq!(parse_date("15-03-2024"), Some((2024, 3, 15)));
        assert_eq!(parse_date("31/12/2024"), Some((2024, 12, 31)));
    }

    #[test]
    fn two_digit_year_posix_pivot() {
        assert_eq!(parse_date("05/06/93"), Some((1993, 5, 6)));
        assert_eq!(parse_date("01/01/68"), Some((2068, 1, 1)));
        assert_eq!(parse_date("01/01/69"), Some((1969, 1, 1)));
        assert_eq!(parse_date("12/31/00"), Some((2000, 12, 31)));
    }

    #[test]
    fn month_names() {
        assert_eq!(parse_date("Jan 5, 2023"), Some((2023, 1, 5)));
        assert_eq!(parse_date("January 5, 2023"), Some((2023, 1, 5)));
        assert_eq!(parse_date("Jan 5 2023"), Some((2023, 1, 5)));
        assert_eq!(parse_date("March 15, 2024"), Some((2024, 3, 15)));
        assert_eq!(parse_date("5 January 2023"), Some((2023, 1, 5)));
        assert_eq!(parse_date("15 Mar 2024"), Some((2024, 3, 15)));
        // Case-insensitive.
        assert_eq!(parse_date("MARCH 15, 2024"), Some((2024, 3, 15)));
        assert_eq!(parse_date("sep 9 1999"), Some((1999, 9, 9)));
    }

    #[test]
    fn month_year_partial_fills_day_one() {
        assert_eq!(parse_date("March 1995"), Some((1995, 3, 1)));
        assert_eq!(parse_date("Sep 2000"), Some((2000, 9, 1)));
    }

    #[test]
    fn year_only() {
        assert_eq!(parse_date("2024"), Some((2024, 1, 1)));
        assert_eq!(parse_date("1995"), Some((1995, 1, 1)));
    }

    #[test]
    fn rejects_invalid_calendar_dates() {
        assert_eq!(parse_date("2020-02-30"), None);
        assert_eq!(parse_date("Feb 30, 2021"), None);
        assert_eq!(parse_date("13/13/2024"), None);
        assert_eq!(parse_date("2024-13-01"), None);
        assert_eq!(parse_date("2024-00-10"), None);
        assert_eq!(parse_date("2024-05-00"), None);
        // Leap-year boundary.
        assert_eq!(parse_date("2020-02-29"), Some((2020, 2, 29)));
        assert_eq!(parse_date("2021-02-29"), None);
    }

    #[test]
    fn rejects_exotic_and_junk() {
        assert_eq!(parse_date("not a date"), None);
        assert_eq!(parse_date(""), None);
        assert_eq!(parse_date("   "), None);
        assert_eq!(parse_date("tbd"), None);
        // Bare month name is exotic (dateutil would fill year=2000) -> None.
        assert_eq!(parse_date("March"), None);
        // Ordinal / weekday words -> None.
        assert_eq!(parse_date("March 3rd, 2024"), None);
    }

    #[test]
    fn whitespace_trimmed() {
        assert_eq!(parse_date("  2024-03-15  "), Some((2024, 3, 15)));
        assert_eq!(parse_date(" 1995 "), Some((1995, 1, 1)));
    }

    #[test]
    fn datetime_space_and_t() {
        assert_eq!(
            parse_datetime("2024-01-20 14:05:00"),
            Some((2024, 1, 20, 14, 5, 0))
        );
        assert_eq!(
            parse_datetime("2024-01-20T14:05:00"),
            Some((2024, 1, 20, 14, 5, 0))
        );
        // HH:MM (seconds default to 0).
        assert_eq!(
            parse_datetime("2024-03-15 09:30"),
            Some((2024, 3, 15, 9, 30, 0))
        );
    }

    #[test]
    fn datetime_date_only_midnight() {
        assert_eq!(parse_datetime("2024-03-15"), Some((2024, 3, 15, 0, 0, 0)));
        assert_eq!(parse_datetime("March 5, 2021"), Some((2021, 3, 5, 0, 0, 0)));
    }

    #[test]
    fn datetime_ampm_is_exotic() {
        // 12-hour AM/PM is the exotic tail -> None (documented divergence).
        assert_eq!(parse_datetime("March 15, 2024 3:30 PM"), None);
    }

    #[test]
    fn datetime_rejects_invalid_time() {
        assert_eq!(parse_datetime("2024-01-20 25:00:00"), None);
        assert_eq!(parse_datetime("2024-01-20 14:60:00"), None);
    }
}
