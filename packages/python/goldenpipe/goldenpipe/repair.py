"""Pure repair-plan kernel — the SP2 mirror of goldenpipe-core/src/repair.rs.

Deterministic, no I/O, no polars. Hand-rolled ASCII matchers (no regex) so the
three surfaces are byte-identical by construction. See the design spec for the
canonical behavior tables.
"""
from __future__ import annotations

# coarse tags the host may supply
_COARSE = {"date", "email", "name", "phone", "zip"}

# ASCII char-class primitives (no regex; \d would diverge across engines)
def _is_digit(c: str) -> bool:
    return "0" <= c <= "9"

def _is_upper(c: str) -> bool:
    return "A" <= c <= "Z"

def _is_alnum_upper(c: str) -> bool:
    return _is_digit(c) or _is_upper(c)

def _all(s: str, pred) -> bool:
    return len(s) > 0 and all(pred(c) for c in s)

def _ascii_lower(s: str) -> str:
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in s)

def _is_ascii_ws(c: str) -> bool:
    return c in " \t\n\r\f\v"

# value predicates (detection shape, not full validation)
def _v_cusip(s: str) -> bool:
    return len(s) == 9 and _all(s, _is_alnum_upper)

def _v_npi(s: str) -> bool:
    return len(s) == 10 and _all(s, _is_digit)

def _v_imei(s: str) -> bool:
    return len(s) == 15 and _all(s, _is_digit)

def _v_ean(s: str) -> bool:
    return len(s) in (8, 13) and _all(s, _is_digit)

def _v_isbn(s: str) -> bool:
    if len(s) == 13 and _all(s, _is_digit):
        return True
    return len(s) == 10 and _all(s[:9], _is_digit) and s[9] in "0123456789Xx"

def _v_aba(s: str) -> bool:
    return len(s) == 9 and _all(s, _is_digit)

def _v_iban(s: str) -> bool:
    if not (15 <= len(s) <= 34):
        return False
    return _is_upper(s[0]) and _is_upper(s[1]) and _is_digit(s[2]) and _is_digit(s[3]) and _all(s[4:], _is_alnum_upper)

def _v_isin(s: str) -> bool:
    return len(s) == 12 and _is_upper(s[0]) and _is_upper(s[1]) and _all(s[2:11], _is_alnum_upper) and _is_digit(s[11])

def _v_swift(s: str) -> bool:
    if len(s) not in (8, 11):
        return False
    return _all(s[:6], _is_upper) and _all(s[6:8], _is_alnum_upper) and (len(s) == 8 or _all(s[8:11], _is_alnum_upper))

def _luhn_ok(s: str) -> bool:
    d = [int(c) for c in s]
    total, alt = 0, False
    for x in reversed(d):
        if alt:
            x *= 2
            if x > 9:
                x -= 9
        total += x
        alt = not alt
    return total % 10 == 0

def _v_credit_card(s: str) -> bool:
    t = s.replace(" ", "").replace("-", "")
    return 13 <= len(t) <= 19 and _all(t, _is_digit) and _luhn_ok(t)

# detectors: (tag, name_hints_or_None, value_predicate) in fixed order
# name-gated group first (low false-positive), value-distinctive fallback second.
_DETECTORS = [
    ("cusip", ("cusip",), _v_cusip),
    ("npi", ("npi",), _v_npi),
    ("imei", ("imei", "imsi"), _v_imei),
    ("ean", ("ean", "gtin", "barcode"), _v_ean),
    ("isbn", ("isbn",), _v_isbn),
    ("aba_routing", ("routing", "aba"), _v_aba),
    ("iban", None, _v_iban),
    ("isin", None, _v_isin),
    ("swift", None, _v_swift),
    ("credit_card", None, _v_credit_card),
]


def fine_type(name: str, samples: list[str]) -> str | None:
    lname = _ascii_lower(name)
    nonempty = [s for s in samples if any(not _is_ascii_ws(c) for c in s)]
    if not nonempty:
        return None
    for tag, hints, pred in _DETECTORS:
        if hints is not None and not any(h in lname for h in hints):
            continue
        matches = sum(1 for s in nonempty if pred(s))
        if matches * 2 > len(nonempty):
            return tag
    return None


def resolve_tag(name: str, coarse_type: str, samples: list[str]) -> str | None:
    ft = fine_type(name, samples)
    if ft is not None:
        return ft
    return coarse_type if coarse_type in _COARSE else None


# mapping table: (check, tag) -> transforms; "*" tag = wildcard
_VALIDATOR = {
    "iban": "iban_validate", "isin": "isin_validate", "swift": "swift_validate",
    "cusip": "cusip_validate", "npi": "npi_validate", "imei": "imei_validate",
    "ean": "ean_validate", "isbn": "isbn_validate", "credit_card": "luhn_validate",
    "aba_routing": "aba_validate",
}
_TABLE: dict[tuple[str, str], list[str]] = {
    ("encoding_detection", "*"): ["fix_mojibake", "normalize_unicode"],
    ("future_dated", "date"): ["date_validate"],
    ("temporal_order", "date"): ["date_validate"],
    ("stale_data", "date"): ["date_validate"],
    ("format_detection", "date"): ["date_parse"],
    ("format_detection", "email"): ["email_normalize"],
    ("pattern_consistency", "email"): ["email_canonical"],
    ("pattern_consistency", "name"): ["name_proper"],
    ("format_detection", "phone"): ["phone_validate"],
    ("pattern_consistency", "phone"): ["phone_national"],
    ("format_detection", "zip"): ["zip_normalize"],
}
for _t, _v in _VALIDATOR.items():
    _TABLE[("format_detection", _t)] = [_v]
    _TABLE[("pattern_consistency", _t)] = [_v]


def _lookup(check: str, tag: str | None) -> list[str] | None:
    if tag is not None and (check, tag) in _TABLE:
        return _TABLE[(check, tag)]
    if (check, "*") in _TABLE:
        return _TABLE[(check, "*")]
    return None


def build_repair_plan(findings: list[dict], columns: list[dict]) -> dict:
    tags: dict[str, str | None] = {}
    for c in columns:
        tags[c["name"]] = resolve_tag(c["name"], c.get("coarse_type", ""), c.get("samples", []))

    repairs = []
    for f in findings:
        col = f.get("column")
        check = f.get("check", "")
        # encoding wildcard can apply even to an omitted-tag column present in `columns`
        if col not in tags:
            continue
        transforms = _lookup(check, tags[col])
        if not transforms:
            continue
        repairs.append({
            "column": col,
            "check": check,
            "type_tag": tags[col] if tags[col] is not None else "*",
            "suggested_transforms": list(transforms),
            "reason": str(f.get("message", ""))[:80],
        })
    return {"repairs": repairs}
