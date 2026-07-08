//! Repair-plan kernel — the authoritative source for GoldenPipe's finding->transform
//! suggestion mapping. The pure-Python (`goldenpipe/repair.py`) and pure-TS mirrors
//! must reproduce these exact bytes.
//!
//! CODE-POINT SEMANTICS: every value predicate collects `s.chars()` into a `Vec<char>`
//! and indexes/slices on code points, mirroring Python `len`/`s[i]`/`s[a:b]`. Byte
//! indexing would diverge on non-ASCII input. No regex — hand-rolled ASCII matchers.
use std::collections::HashMap;

// ---- inputs / outputs (field order is the wire contract; serde preserves it) --------

#[derive(serde::Deserialize)]
pub struct Finding {
    pub column: Option<String>,
    #[serde(default)]
    pub check: String,
    #[serde(default)]
    pub message: String,
    #[serde(default)]
    pub severity: String,
}

#[derive(serde::Deserialize)]
pub struct ColumnInput {
    pub name: String,
    #[serde(default)]
    pub coarse_type: String,
    #[serde(default)]
    pub samples: Vec<String>,
}

#[derive(serde::Serialize)]
pub struct RepairItem {
    pub column: String,
    pub check: String,
    pub type_tag: String,
    pub suggested_transforms: Vec<String>,
    pub reason: String,
}

#[derive(serde::Serialize, Default)]
pub struct RepairPlan {
    pub repairs: Vec<RepairItem>,
}

// ---- ASCII char-class primitives (no regex) -----------------------------------------

fn is_digit(c: char) -> bool {
    c.is_ascii_digit()
}

fn is_upper(c: char) -> bool {
    c.is_ascii_uppercase()
}

fn is_alnum_upper(c: char) -> bool {
    is_digit(c) || is_upper(c)
}

/// Python `_all(s, pred)` = len > 0 and every char satisfies pred.
fn all_pred(cs: &[char], pred: fn(char) -> bool) -> bool {
    !cs.is_empty() && cs.iter().all(|&c| pred(c))
}

/// ASCII-only lower: map 'A'..='Z' -> +32, leave everything else (NOT Unicode lower).
fn ascii_lower(s: &str) -> String {
    s.chars()
        .map(|c| {
            if c.is_ascii_uppercase() {
                ((c as u32) + 32) as u8 as char
            } else {
                c
            }
        })
        .collect()
}

fn is_ascii_ws(c: char) -> bool {
    matches!(c, ' ' | '\t' | '\n' | '\r' | '\u{000C}' | '\u{000B}')
}

// ---- value predicates (detection shape, not full validation) ------------------------

fn v_cusip(s: &str) -> bool {
    let cs: Vec<char> = s.chars().collect();
    cs.len() == 9 && all_pred(&cs, is_alnum_upper)
}

fn v_npi(s: &str) -> bool {
    let cs: Vec<char> = s.chars().collect();
    cs.len() == 10 && all_pred(&cs, is_digit)
}

fn v_imei(s: &str) -> bool {
    let cs: Vec<char> = s.chars().collect();
    cs.len() == 15 && all_pred(&cs, is_digit)
}

fn v_ean(s: &str) -> bool {
    let cs: Vec<char> = s.chars().collect();
    (cs.len() == 8 || cs.len() == 13) && all_pred(&cs, is_digit)
}

fn v_isbn(s: &str) -> bool {
    let cs: Vec<char> = s.chars().collect();
    if cs.len() == 13 && all_pred(&cs, is_digit) {
        return true;
    }
    cs.len() == 10 && all_pred(&cs[..9], is_digit) && matches!(cs[9], '0'..='9' | 'X' | 'x')
}

fn v_aba(s: &str) -> bool {
    let cs: Vec<char> = s.chars().collect();
    cs.len() == 9 && all_pred(&cs, is_digit)
}

fn v_iban(s: &str) -> bool {
    let cs: Vec<char> = s.chars().collect();
    if !(15..=34).contains(&cs.len()) {
        return false;
    }
    is_upper(cs[0])
        && is_upper(cs[1])
        && is_digit(cs[2])
        && is_digit(cs[3])
        && all_pred(&cs[4..], is_alnum_upper)
}

fn v_isin(s: &str) -> bool {
    let cs: Vec<char> = s.chars().collect();
    cs.len() == 12
        && is_upper(cs[0])
        && is_upper(cs[1])
        && all_pred(&cs[2..11], is_alnum_upper)
        && is_digit(cs[11])
}

fn v_swift(s: &str) -> bool {
    let cs: Vec<char> = s.chars().collect();
    if cs.len() != 8 && cs.len() != 11 {
        return false;
    }
    all_pred(&cs[..6], is_upper)
        && all_pred(&cs[6..8], is_alnum_upper)
        && (cs.len() == 8 || all_pred(&cs[8..11], is_alnum_upper))
}

/// Standard Luhn over digit chars; caller guarantees `s` is all digits (short-circuit).
fn luhn_ok(s: &str) -> bool {
    let mut total: i64 = 0;
    let mut alt = false;
    for c in s.chars().rev() {
        let mut x = (c as u8 - b'0') as i64;
        if alt {
            x *= 2;
            if x > 9 {
                x -= 9;
            }
        }
        total += x;
        alt = !alt;
    }
    total % 10 == 0
}

fn v_credit_card(s: &str) -> bool {
    let t: String = s.chars().filter(|&c| c != ' ' && c != '-').collect();
    let cs: Vec<char> = t.chars().collect();
    (13..=19).contains(&cs.len()) && all_pred(&cs, is_digit) && luhn_ok(&t)
}

// ---- detectors: (tag, name_hints_or_None, predicate) in FIXED order -----------------
// name-gated group first (low false-positive), value-distinctive fallback second.
type Pred = fn(&str) -> bool;
const DETECTORS: &[(&str, Option<&[&str]>, Pred)] = &[
    ("cusip", Some(&["cusip"]), v_cusip as Pred),
    ("npi", Some(&["npi"]), v_npi as Pred),
    ("imei", Some(&["imei", "imsi"]), v_imei as Pred),
    ("ean", Some(&["ean", "gtin", "barcode"]), v_ean as Pred),
    ("isbn", Some(&["isbn"]), v_isbn as Pred),
    ("aba_routing", Some(&["routing", "aba"]), v_aba as Pred),
    ("iban", None, v_iban as Pred),
    ("isin", None, v_isin as Pred),
    ("swift", None, v_swift as Pred),
    ("credit_card", None, v_credit_card as Pred),
];

fn fine_type(name: &str, samples: &[String]) -> Option<&'static str> {
    let lname = ascii_lower(name);
    let nonempty: Vec<&str> = samples
        .iter()
        .map(|s| s.as_str())
        .filter(|s| s.chars().any(|c| !is_ascii_ws(c)))
        .collect();
    if nonempty.is_empty() {
        return None;
    }
    for &(tag, hints, pred) in DETECTORS.iter() {
        if let Some(hs) = hints {
            if !hs.iter().any(|h| lname.contains(h)) {
                continue;
            }
        }
        let matches = nonempty.iter().filter(|&&s| pred(s)).count();
        if matches * 2 > nonempty.len() {
            return Some(tag);
        }
    }
    None
}

fn resolve_tag(name: &str, coarse_type: &str, samples: &[String]) -> Option<&'static str> {
    if let Some(ft) = fine_type(name, samples) {
        return Some(ft);
    }
    // _COARSE = {"date", "email", "name", "phone", "zip"}
    match coarse_type {
        "date" => Some("date"),
        "email" => Some("email"),
        "name" => Some("name"),
        "phone" => Some("phone"),
        "zip" => Some("zip"),
        _ => None,
    }
}

// ---- mapping table: (check, tag) -> transforms; "*" tag = wildcard -------------------

/// Validator name for a fine type, or None if `tag` isn't a fine type.
fn validator(tag: &str) -> Option<&'static str> {
    Some(match tag {
        "iban" => "iban_validate",
        "isin" => "isin_validate",
        "swift" => "swift_validate",
        "cusip" => "cusip_validate",
        "npi" => "npi_validate",
        "imei" => "imei_validate",
        "ean" => "ean_validate",
        "isbn" => "isbn_validate",
        "credit_card" => "luhn_validate",
        "aba_routing" => "aba_validate",
        _ => return None,
    })
}

/// Exact (check, tag) row, or None. Mirrors `_TABLE` minus the wildcard entry. The
/// fine-type validator rows exist for BOTH `format_detection` and `pattern_consistency`.
fn exact_lookup(check: &str, tag: &str) -> Option<Vec<&'static str>> {
    if check == "format_detection" || check == "pattern_consistency" {
        if let Some(v) = validator(tag) {
            return Some(vec![v]);
        }
    }
    let row: &[&str] = match (check, tag) {
        ("future_dated", "date") => &["date_validate"],
        ("temporal_order", "date") => &["date_validate"],
        ("stale_data", "date") => &["date_validate"],
        ("format_detection", "date") => &["date_parse"],
        ("format_detection", "email") => &["email_normalize"],
        ("pattern_consistency", "email") => &["email_canonical"],
        ("pattern_consistency", "name") => &["name_proper"],
        ("format_detection", "phone") => &["phone_validate"],
        ("pattern_consistency", "phone") => &["phone_national"],
        ("format_detection", "zip") => &["zip_normalize"],
        _ => return None,
    };
    Some(row.to_vec())
}

/// Python `_lookup`: exact (check, tag) first, then (check, "*"), else None.
fn lookup(check: &str, tag: Option<&str>) -> Option<Vec<String>> {
    if let Some(t) = tag {
        if let Some(row) = exact_lookup(check, t) {
            return Some(row.iter().map(|s| s.to_string()).collect());
        }
    }
    // only wildcard row: ("encoding_detection", "*")
    if check == "encoding_detection" {
        return Some(vec![
            "fix_mojibake".to_string(),
            "normalize_unicode".to_string(),
        ]);
    }
    None
}

pub fn build_repair_plan(findings: &[Finding], columns: &[ColumnInput]) -> RepairPlan {
    // last insert wins on duplicate names (mirror Python dict)
    let mut tags: HashMap<String, Option<&'static str>> = HashMap::new();
    for c in columns {
        tags.insert(
            c.name.clone(),
            resolve_tag(&c.name, &c.coarse_type, &c.samples),
        );
    }

    let mut repairs: Vec<RepairItem> = Vec::new();
    for f in findings {
        // `col not in tags` — a None column, or a column absent from `columns`, is skipped.
        // A column PRESENT with tag None is still "in tags" (wildcard can still apply).
        let col = match &f.column {
            Some(c) => c,
            None => continue,
        };
        let tag = match tags.get(col) {
            Some(t) => *t,
            None => continue,
        };
        let transforms = match lookup(&f.check, tag) {
            Some(t) if !t.is_empty() => t,
            _ => continue,
        };
        repairs.push(RepairItem {
            column: col.clone(),
            check: f.check.clone(),
            type_tag: tag.unwrap_or("*").to_string(),
            suggested_transforms: transforms,
            reason: f.message.chars().take(80).collect(),
        });
    }
    RepairPlan { repairs }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ss(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn iban_by_value_majority() {
        let s = ss(&["GB82WEST12345698765432", "DE89370400440532013000"]);
        assert_eq!(fine_type("iban", &s), Some("iban"));
    }

    #[test]
    fn bare_nine_digits_need_name_hint() {
        let s = ss(&["021000021", "011401533"]);
        // no routing/aba hint in the name -> no fine type
        assert_eq!(fine_type("digits", &s), None);
        // hinted name -> aba_routing
        assert_eq!(fine_type("routing_number", &s), Some("aba_routing"));
    }

    #[test]
    fn credit_card_luhn_pass_and_fail() {
        let pass = ss(&["4539578763621486", "4485275742308327"]);
        assert_eq!(fine_type("card", &pass), Some("credit_card"));
        // flip the last check digit on both -> luhn fails -> no fine type
        let fail = ss(&["4539578763621487", "4485275742308328"]);
        assert_eq!(fine_type("card", &fail), None);
    }

    #[test]
    fn barcode_thirteen_digits_is_ean() {
        let s = ss(&["4006381333931", "0012345678905"]);
        assert_eq!(fine_type("barcode", &s), Some("ean"));
    }

    #[test]
    fn minority_iban_is_not_detected() {
        // 1 of 3 valid -> matches*2 (2) not > 3 -> None
        let s = ss(&["GB82WEST12345698765432", "not_an_iban", "12345"]);
        assert_eq!(fine_type("iban", &s), None);
    }
}
