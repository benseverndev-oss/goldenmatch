from __future__ import annotations

import polars as pl

from goldenflow.transforms import register_transform
from goldenflow.transforms._native import (
    address_expand_native,
    address_standardize_native,
    country_standardize_native,
    split_address_native,
    state_abbreviate_native,
    state_expand_native,
    unit_normalize_native,
    zip_normalize_native,
)

# In-crate data mirror: these tables are replicated char-for-char in
# goldenflow-core/src/address.rs and the TS port. They are the shared DATA the
# owned kernels look up; the pure-Python fallbacks below reproduce the Rust
# kernels byte-for-byte (kernel = spec under reference-mode), asserted over
# tests/parity/identifiers_corpus.jsonl (scalar transforms) and
# tests/transforms/test_address_kernels.py (split_address).
_STREET_ABBREV = {
    "Street": "St", "Avenue": "Ave", "Boulevard": "Blvd", "Drive": "Dr",
    "Lane": "Ln", "Road": "Rd", "Court": "Ct", "Place": "Pl",
    "Circle": "Cir", "Trail": "Trl", "Way": "Way", "Parkway": "Pkwy",
    "Highway": "Hwy", "Terrace": "Ter", "Square": "Sq",
}

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


# --- shared low-level helpers (mirror goldenflow-core/src/address.rs) ---------


def _ascii_lower(c: str) -> str:
    """ASCII-only lowercase (Rust ``eq_ignore_ascii_case`` semantics): fold
    ``A-Z`` only, leave everything else (incl. non-ASCII) untouched."""
    return chr(ord(c) + 32) if "A" <= c <= "Z" else c


def _is_word_char(c: str) -> bool:
    r"""``\w`` = ASCII ``[A-Za-z0-9_]`` (matches the Rust kernel, NOT Python's
    Unicode-aware ``\w``)."""
    return ("a" <= c <= "z") or ("A" <= c <= "Z") or ("0" <= c <= "9") or c == "_"


def _is_ascii_alpha(c: str) -> bool:
    return ("a" <= c <= "z") or ("A" <= c <= "Z")


def _is_ascii_digit(c: str) -> bool:
    return "0" <= c <= "9"


def _replace_word_bounded(s: str, needle: str, rep: str) -> str:
    """Case-insensitive, word-boundary-delimited replace-all -- byte-identical to
    ``address.rs::replace_word_bounded``. ``needle`` is a non-empty ASCII word."""
    nlen = len(needle)
    hlen = len(s)
    out: list[str] = []
    i = 0
    while i < hlen:
        replaced = False
        if i + nlen <= hlen and all(
            _ascii_lower(s[i + k]) == _ascii_lower(needle[k]) for k in range(nlen)
        ):
            left_ok = i == 0 or not _is_word_char(s[i - 1])
            right_idx = i + nlen
            right_ok = right_idx >= hlen or not _is_word_char(s[right_idx])
            if left_ok and right_ok:
                out.append(rep)
                i += nlen
                replaced = True
        if not replaced:
            out.append(s[i])
            i += 1
    return "".join(out)


# --- address_standardize / address_expand ------------------------------------


def _address_standardize_py(val: str | None) -> str | None:
    if val is None:
        return None
    out = val
    for full, abbr in _STREET_ABBREV.items():
        out = _replace_word_bounded(out, full, abbr)
    return out


def _address_standardize_series(series: pl.Series) -> pl.Series:
    native = address_standardize_native()
    if native is not None:
        return native(series)
    return series.map_elements(_address_standardize_py, return_dtype=pl.Utf8)


@register_transform(
    name="address_standardize", input_types=["address"], auto_apply=False, priority=50, mode="expr"
)
def address_standardize(column: str) -> pl.Expr:
    """Replace full street suffixes (Street, Avenue...) with abbreviations.

    Native-first (goldenflow-core's ``address::address_standardize`` kernel),
    dispatched via ``map_batches`` so the transform keeps its ``expr``-mode
    signature; the pure-Python fallback is the byte-exact reference.
    """
    return pl.col(column).map_batches(_address_standardize_series, return_dtype=pl.Utf8)


def _address_expand_py(val: str | None) -> str | None:
    if val is None:
        return None
    out = val
    for full, abbr in _STREET_ABBREV.items():
        out = _replace_word_bounded(out, abbr, full)
    return out


def _address_expand_series(series: pl.Series) -> pl.Series:
    native = address_expand_native()
    if native is not None:
        return native(series)
    return series.map_elements(_address_expand_py, return_dtype=pl.Utf8)


@register_transform(
    name="address_expand", input_types=["address"], auto_apply=False, priority=50, mode="expr"
)
def address_expand(column: str) -> pl.Expr:
    """Replace street abbreviations (St, Ave...) with full forms.

    Native-first (goldenflow-core's ``address::address_expand`` kernel); the
    pure-Python fallback is the byte-exact reference.
    """
    return pl.col(column).map_batches(_address_expand_series, return_dtype=pl.Utf8)


# --- state_abbreviate / state_expand -----------------------------------------


def _state_abbreviate_py(val: str | None) -> str | None:
    if val is None:
        return None
    cleaned = val.strip()
    upper = cleaned.upper()
    if len(cleaned) == 2 and upper in _STATES_REVERSE:
        return upper
    abbr = _STATES_LOWER.get(cleaned.lower())
    if abbr is not None:
        return abbr
    return val


def _state_abbreviate_series(series: pl.Series) -> pl.Series:
    native = state_abbreviate_native()
    if native is not None:
        return native(series)
    return series.map_elements(_state_abbreviate_py, return_dtype=pl.Utf8)


@register_transform(
    name="state_abbreviate", input_types=["state", "string"], auto_apply=False, priority=50,
    mode="expr",
)
def state_abbreviate(column: str) -> pl.Expr:
    """Normalize state name to a 2-letter abbreviation (unmatched -> original).

    Native-first (goldenflow-core's ``address::state_abbreviate`` kernel); the
    pure-Python fallback is the byte-exact reference.
    """
    return pl.col(column).map_batches(_state_abbreviate_series, return_dtype=pl.Utf8)


def _state_expand_py(val: str | None) -> str | None:
    if val is None:
        return None
    full = _STATES_REVERSE.get(val.strip().upper())
    return full if full is not None else val


def _state_expand_series(series: pl.Series) -> pl.Series:
    native = state_expand_native()
    if native is not None:
        return native(series)
    return series.map_elements(_state_expand_py, return_dtype=pl.Utf8)


@register_transform(
    name="state_expand", input_types=["state", "string"], auto_apply=False, priority=50,
    mode="expr",
)
def state_expand(column: str) -> pl.Expr:
    """Expand a 2-letter state abbreviation to its full name (unmatched -> original).

    Native-first (goldenflow-core's ``address::state_expand`` kernel); the
    pure-Python fallback is the byte-exact reference.
    """
    return pl.col(column).map_batches(_state_expand_series, return_dtype=pl.Utf8)


# --- zip_normalize -----------------------------------------------------------


def _zip_normalize_py(val: str | None) -> str | None:
    if val is None:
        return None
    base = val.strip().split("-", 1)[0]
    if base and all(_is_ascii_digit(c) for c in base):
        return base.zfill(5)
    return base


def _zip_normalize_series(series: pl.Series) -> pl.Series:
    native = zip_normalize_native()
    if native is not None:
        return native(series)
    return series.map_elements(_zip_normalize_py, return_dtype=pl.Utf8)


@register_transform(
    name="zip_normalize", input_types=["zip"], auto_apply=True, priority=55, mode="expr"
)
def zip_normalize(column: str) -> pl.Expr:
    """Normalize a US ZIP to 5-digit form (strip +4, zero-pad all-digit, preserve
    invalid inputs).

    Native-first (goldenflow-core's ``address::zip_normalize`` kernel); the
    pure-Python fallback is the byte-exact reference.
    """
    return pl.col(column).map_batches(_zip_normalize_series, return_dtype=pl.Utf8)


# --- country_standardize -----------------------------------------------------


def _country_standardize_py(val: str | None) -> str | None:
    if val is None:
        return None
    return _COUNTRIES.get(val.strip().lower(), val)


@register_transform(
    name="country_standardize",
    input_types=["country", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def country_standardize(series: pl.Series) -> pl.Series:
    """Normalize country names to ISO 3166-1 alpha-2 codes.

    Native-first (goldenflow-core's ``address::country_standardize`` kernel,
    whose lookup table is an in-crate copy of ``_COUNTRIES``); the pure-Python
    fallback is the byte-exact reference.
    """
    native = country_standardize_native()
    if native is not None:
        return native(series)
    return series.map_elements(_country_standardize_py, return_dtype=pl.Utf8)


# --- unit_normalize ----------------------------------------------------------


def _ci_startswith(s: str, prefix: str) -> bool:
    if len(s) < len(prefix):
        return False
    return all(_ascii_lower(s[i]) == _ascii_lower(prefix[i]) for i in range(len(prefix)))


def _sub_leading_token(s: str, tokens: tuple[str, ...], rep: str) -> str:
    for tok in tokens:
        if _ci_startswith(s, tok):
            rest = s[len(tok):]
            after_dot = rest[1:] if rest.startswith(".") else rest
            after_ws = after_dot.lstrip()
            if len(after_ws) < len(after_dot):
                return rep + after_ws
    return s


def _sub_leading_hash(s: str) -> str:
    if s.startswith("#"):
        return "Unit " + s[1:].lstrip()
    return s


def _unit_normalize_py(val: str | None) -> str | None:
    if val is None:
        return None
    result = val.strip()
    result = _sub_leading_token(result, ("Apt", "Apartment"), "Unit ")
    result = _sub_leading_token(result, ("Ste", "Suite"), "Ste ")
    result = _sub_leading_hash(result)
    return result


@register_transform(
    name="unit_normalize",
    input_types=["address", "string"],
    auto_apply=False,
    priority=45,
    mode="series",
)
def unit_normalize(series: pl.Series) -> pl.Series:
    """Normalize unit/apartment/suite designations.

    Native-first (goldenflow-core's ``address::unit_normalize`` kernel); the
    pure-Python fallback is the byte-exact reference.
    """
    native = unit_normalize_native()
    if native is not None:
        return native(series)
    return series.map_elements(_unit_normalize_py, return_dtype=pl.Utf8)


# --- split_address (multi-output dataframe) ----------------------------------


def _is_zip(s: str) -> bool:
    if len(s) == 5:
        return all(_is_ascii_digit(c) for c in s)
    if len(s) == 10:
        return (
            all(_is_ascii_digit(c) for c in s[:5])
            and s[5] == "-"
            and all(_is_ascii_digit(c) for c in s[6:10])
        )
    return False


def _parse_state_zip_tail(rem: str) -> tuple[str, str] | None:
    after_ws = rem.lstrip()
    if len(after_ws) < 2 or not (_is_ascii_alpha(after_ws[0]) and _is_ascii_alpha(after_ws[1])):
        return None
    state = after_ws[:2]
    rest = after_ws[2:]
    zipc = rest.lstrip()
    if len(zipc) == len(rest):
        return None  # no whitespace between state and ZIP -> `\s+` failed
    if _is_zip(zipc):
        return state, zipc
    return None


def _try_parse_address(t: str) -> tuple[str, str, str, str] | None:
    c1 = t.find(",")
    if c1 == -1:
        return None
    group1 = t[:c1]
    if group1 == "":
        return None
    after1_ws = t[c1 + 1:].lstrip()
    search = 0
    while True:
        c2 = after1_ws.find(",", search)
        if c2 == -1:
            break
        group2 = after1_ws[:c2]
        if group2 != "":
            tail = _parse_state_zip_tail(after1_ws[c2 + 1:])
            if tail is not None:
                state, zipc = tail
                return group1, group2, state, zipc
        search = c2 + 1
    return None


def _split_address_py(
    val: str | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    if val is None:
        return None, None, None, None
    parsed = _try_parse_address(val.strip())
    if parsed is not None:
        return parsed
    return val, None, None, None  # street = ORIGINAL (unstripped), rest None


@register_transform(
    name="split_address", input_types=["address"], auto_apply=False, priority=45, mode="dataframe"
)
def split_address(df: pl.DataFrame, column: str) -> pl.DataFrame:
    """Parse 'street, city, state zip' into separate columns.

    Native-first (goldenflow-core's ``address::split_address`` kernel, returning
    four Arrow arrays); the pure-Python fallback is the byte-exact reference.
    """
    native = split_address_native()
    if native is not None:
        street, city, state, zip_col = native(df[column])
        return df.with_columns(
            street.rename("street"),
            city.rename("city"),
            state.rename("state"),
            zip_col.rename("zip"),
        )
    streets: list[str | None] = []
    cities: list[str | None] = []
    states: list[str | None] = []
    zips: list[str | None] = []
    for val in df[column].to_list():
        s, c, st, z = _split_address_py(val)
        streets.append(s)
        cities.append(c)
        states.append(st)
        zips.append(z)
    return df.with_columns(
        pl.Series("street", streets),
        pl.Series("city", cities),
        pl.Series("state", states),
        pl.Series("zip", zips),
    )
