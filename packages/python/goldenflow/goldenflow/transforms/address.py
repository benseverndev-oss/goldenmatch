from __future__ import annotations

import re

import polars as pl

from goldenflow.transforms import register_transform

_STREET_ABBREV = {
    "Street": "St", "Avenue": "Ave", "Boulevard": "Blvd", "Drive": "Dr",
    "Lane": "Ln", "Road": "Rd", "Court": "Ct", "Place": "Pl",
    "Circle": "Cir", "Trail": "Trl", "Way": "Way", "Parkway": "Pkwy",
    "Highway": "Hwy", "Terrace": "Ter", "Square": "Sq",
}
_STREET_EXPAND = {v: k for k, v in _STREET_ABBREV.items()}

_STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY", "District Of Columbia": "DC",
}
_STATES_REVERSE = {v: k for k, v in _STATES.items()}
_STATES_LOWER = {k.lower(): v for k, v in _STATES.items()}


@register_transform(
    name="address_standardize", input_types=["address"], auto_apply=False, priority=50, mode="expr"
)
def address_standardize(column: str) -> pl.Expr:
    """Replace full street suffixes (Street, Avenue...) with abbreviations.

    Native Polars: chain of case-insensitive word-boundary regex replaces.
    One `str.replace_all` per (full, abbr) pair. Spec
    docs/superpowers/specs/2026-05-15-map-elements-attack-design.md Tier 1.
    """
    expr = pl.col(column)
    for full, abbr in _STREET_ABBREV.items():
        expr = expr.str.replace_all(rf"(?i)\b{full}\b", abbr)
    return expr


@register_transform(
    name="address_expand", input_types=["address"], auto_apply=False, priority=50, mode="expr"
)
def address_expand(column: str) -> pl.Expr:
    """Replace street abbreviations (St, Ave...) with full forms.

    Native Polars: chain of case-insensitive word-boundary regex replaces.
    Spec docs/superpowers/specs/2026-05-15-map-elements-attack-design.md
    Tier 1.
    """
    expr = pl.col(column)
    for abbr, full in _STREET_EXPAND.items():
        expr = expr.str.replace_all(rf"(?i)\b{abbr}\b", full)
    return expr


@register_transform(
    name="state_abbreviate", input_types=["state", "string"], auto_apply=False, priority=50, mode="expr"
)
def state_abbreviate(column: str) -> pl.Expr:
    """Normalize state name to 2-letter abbreviation.

    Three-way fallback:
      1. 2-letter input that's a valid abbreviation -> uppercase it.
      2. Full name (case-insensitive) -> look up in _STATES_LOWER.
      3. Neither matched -> return original column value unchanged.

    Native Polars: `replace_strict` with `default=...` provides the
    not-matched fallback; combined with `when/then` to handle the
    2-letter-already-valid case. Spec
    docs/superpowers/specs/2026-05-15-map-elements-attack-design.md Tier 1.
    """
    cleaned = pl.col(column).str.strip_chars()
    upper = cleaned.str.to_uppercase()
    is_valid_2letter = (cleaned.str.len_chars() == 2) & upper.is_in(
        list(_STATES_REVERSE.keys())
    )
    # Lower-cased lookup; default to a sentinel that means "not in dict".
    matched_full = cleaned.str.to_lowercase().replace_strict(
        _STATES_LOWER, default=None, return_dtype=pl.Utf8,
    )
    return (
        pl.when(is_valid_2letter).then(upper)
        .when(matched_full.is_not_null()).then(matched_full)
        .otherwise(pl.col(column))
    )


@register_transform(
    name="state_expand", input_types=["state", "string"], auto_apply=False, priority=50, mode="expr"
)
def state_expand(column: str) -> pl.Expr:
    """Expand 2-letter state abbreviation to full name.

    Native Polars: `replace_strict` with the dict, default=original value
    for unmatched inputs. Spec
    docs/superpowers/specs/2026-05-15-map-elements-attack-design.md Tier 1.
    """
    return (
        pl.col(column)
        .str.strip_chars()
        .str.to_uppercase()
        .replace_strict(
            _STATES_REVERSE, default=pl.col(column), return_dtype=pl.Utf8,
        )
    )


@register_transform(
    name="zip_normalize", input_types=["zip"], auto_apply=True, priority=55, mode="expr"
)
def zip_normalize(column: str) -> pl.Expr:
    """Normalize US ZIP to 5-digit form. Strip +4, zero-pad if all-digits,
    preserve invalid inputs unchanged.

    Native Polars: strip + take first segment before '-' + check all-digits
    via regex contains, then conditionally zfill. Spec Tier 2 (auto_apply=True
    transform; was firing per-row Python on every dedupe iteration).
    """
    base = pl.col(column).str.strip_chars().str.split("-").list.first()
    return (
        pl.when(base.str.contains(r"^\d+$"))
        .then(base.str.zfill(5))
        .otherwise(base)
    )


@register_transform(
    name="split_address", input_types=["address"], auto_apply=False, priority=45, mode="dataframe"
)
def split_address(df: pl.DataFrame, column: str) -> pl.DataFrame:
    """Parse 'street, city, state zip' into separate columns."""
    streets, cities, states, zips = [], [], [], []
    pattern = re.compile(r"^(.+?),\s*(.+?),\s*([A-Za-z]{2})\s+(\d{5}(?:-\d{4})?)$")
    for val in df[column].to_list():
        if val is None:
            streets.append(None)
            cities.append(None)
            states.append(None)
            zips.append(None)
            continue
        m = pattern.match(val.strip())
        if m:
            streets.append(m.group(1))
            cities.append(m.group(2))
            states.append(m.group(3))
            zips.append(m.group(4))
        else:
            streets.append(val)
            cities.append(None)
            states.append(None)
            zips.append(None)
    return df.with_columns(
        pl.Series("street", streets),
        pl.Series("city", cities),
        pl.Series("state", states),
        pl.Series("zip", zips),
    )


_COUNTRIES: dict[str, str] = {
    "united states": "US", "united states of america": "US", "usa": "US", "us": "US",
    "u.s.a.": "US", "u.s.": "US", "america": "US",
    "united kingdom": "GB", "uk": "GB", "great britain": "GB", "england": "GB",
    "scotland": "GB", "wales": "GB", "northern ireland": "GB",
    "canada": "CA", "ca": "CA",
    "australia": "AU", "au": "AU",
    "germany": "DE", "deutschland": "DE", "de": "DE",
    "france": "FR", "fr": "FR",
    "italy": "IT", "italia": "IT", "it": "IT",
    "spain": "ES", "espana": "ES", "es": "ES",
    "mexico": "MX", "mx": "MX",
    "brazil": "BR", "brasil": "BR", "br": "BR",
    "japan": "JP", "jp": "JP",
    "china": "CN", "cn": "CN",
    "india": "IN", "in": "IN",
    "south korea": "KR", "korea": "KR", "kr": "KR",
    "netherlands": "NL", "holland": "NL", "nl": "NL",
    "sweden": "SE", "se": "SE",
    "norway": "NO", "no": "NO",
    "denmark": "DK", "dk": "DK",
    "switzerland": "CH", "ch": "CH",
    "ireland": "IE", "ie": "IE",
    "new zealand": "NZ", "nz": "NZ",
    "singapore": "SG", "sg": "SG",
    "portugal": "PT", "pt": "PT",
    "argentina": "AR", "ar": "AR",
    "colombia": "CO", "co": "CO",
    "philippines": "PH", "ph": "PH",
    "poland": "PL", "pl": "PL",
    "belgium": "BE", "be": "BE",
    "austria": "AT", "at": "AT",
}


@register_transform(
    name="country_standardize",
    input_types=["country", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def country_standardize(series: pl.Series) -> pl.Series:
    """Normalize country names to ISO 3166-1 alpha-2 codes."""

    def _std(val: str | None) -> str | None:
        if val is None:
            return None
        lookup = val.strip().lower()
        return _COUNTRIES.get(lookup, val)

    return series.map_elements(_std, return_dtype=pl.Utf8)


_UNIT_PATTERNS = [
    (re.compile(r"^(?:Apt|Apartment)\.?\s+", re.IGNORECASE), "Unit "),
    (re.compile(r"^(?:Ste|Suite)\.?\s+", re.IGNORECASE), "Ste "),
    (re.compile(r"^#\s*", re.IGNORECASE), "Unit "),
]


@register_transform(
    name="unit_normalize",
    input_types=["address", "string"],
    auto_apply=False,
    priority=45,
    mode="series",
)
def unit_normalize(series: pl.Series) -> pl.Series:
    """Normalize unit/apartment/suite designations."""

    def _norm(val: str | None) -> str | None:
        if val is None:
            return None
        result = val.strip()
        for pattern, replacement in _UNIT_PATTERNS:
            result = pattern.sub(replacement, result)
        return result

    return series.map_elements(_norm, return_dtype=pl.Utf8)
