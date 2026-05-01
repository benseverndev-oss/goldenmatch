from __future__ import annotations

from rapidfuzz import fuzz

# Common column name aliases
ALIASES: dict[str, list[str]] = {
    "first_name": ["fname", "first", "given_name", "first_nm"],
    "last_name": ["lname", "last", "surname", "family_name", "last_nm"],
    "email": ["email_address", "e_mail", "email_addr", "mail"],
    "phone": ["phone_number", "ph", "telephone", "tel", "mobile", "cell"],
    "address": ["addr", "street_address", "addr_line_1", "address_line_1"],
    "city": ["town", "municipality"],
    "state": ["st", "province", "region"],
    "zip": ["zipcode", "zip_code", "postal_code", "postal"],
    "name": ["full_name", "fullname", "customer_name"],
    "created_at": ["signup_date", "signup_dt", "create_date", "date_created"],
}

# Build reverse lookup: alias → canonical
_ALIAS_LOOKUP: dict[str, str] = {}
for canonical, aliases in ALIASES.items():
    for alias in aliases:
        _ALIAS_LOOKUP[alias.lower()] = canonical.lower()
    _ALIAS_LOOKUP[canonical.lower()] = canonical.lower()


def name_similarity(source: str, target: str) -> float:
    """Score how similar two column names are (0.0-1.0)."""
    s_lower = source.lower().strip()
    t_lower = target.lower().strip()

    # Exact match
    if s_lower == t_lower:
        return 1.0

    # Alias match
    s_canonical = _ALIAS_LOOKUP.get(s_lower)
    t_canonical = _ALIAS_LOOKUP.get(t_lower)
    if s_canonical and t_canonical and s_canonical == t_canonical:
        return 0.95

    # Fuzzy match using Jaro-Winkler
    score = fuzz.WRatio(s_lower, t_lower) / 100.0
    return score
