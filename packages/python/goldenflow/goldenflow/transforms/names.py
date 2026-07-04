from __future__ import annotations

import re

import polars as pl

from goldenflow.transforms import register_transform
from goldenflow.transforms._native import name_transliterate_native

_TITLES = re.compile(
    r"^(Mr\.?|Mrs\.?|Ms\.?|Miss\.?|Dr\.?|Prof\.?|Rev\.?|Sr\.?|Sra\.?)\s+", re.IGNORECASE
)
_SUFFIXES = re.compile(
    r"\s+(Jr\.?|Sr\.?|II|III|IV|MD|PhD|PharmD|DDS|DVM|Esq\.?|CPA|RN|DO)$", re.IGNORECASE
)
_INITIAL_PATTERN = re.compile(r"\b[A-Z]\.\s")

_MC_PATTERN = re.compile(r"\bMc(\w)")
_O_PATTERN = re.compile(r"\bO'(\w)")


@register_transform(
    name="split_name", input_types=["name"], auto_apply=False, priority=50, mode="dataframe"
)
def split_name(df: pl.DataFrame, column: str) -> pl.DataFrame:
    """Split 'First Last' into first_name and last_name columns."""
    first_names = []
    last_names = []
    for val in df[column].to_list():
        if val is None:
            first_names.append(None)
            last_names.append(None)
            continue
        parts = val.strip().rsplit(" ", 1)
        if len(parts) == 2:
            first_names.append(parts[0])
            last_names.append(parts[1])
        else:
            first_names.append(parts[0])
            last_names.append("")
    return df.with_columns(
        pl.Series("first_name", first_names),
        pl.Series("last_name", last_names),
    )


@register_transform(
    name="split_name_reverse", input_types=["name"], auto_apply=False, priority=50, mode="dataframe"
)
def split_name_reverse(df: pl.DataFrame, column: str) -> pl.DataFrame:
    """Split 'Last, First' into first_name and last_name columns."""
    first_names = []
    last_names = []
    for val in df[column].to_list():
        if val is None:
            first_names.append(None)
            last_names.append(None)
            continue
        parts = val.split(",", 1)
        if len(parts) == 2:
            last_names.append(parts[0].strip())
            first_names.append(parts[1].strip())
        else:
            first_names.append(val.strip())
            last_names.append("")
    return df.with_columns(
        pl.Series("first_name", first_names),
        pl.Series("last_name", last_names),
    )


@register_transform(
    name="strip_titles", input_types=["name"], auto_apply=True, priority=70, mode="expr"
)
def strip_titles(column: str) -> pl.Expr:
    """Strip leading personal titles (Mr/Mrs/Ms/Dr/Prof/etc.) from names.

    Native Polars: case-insensitive regex replace then strip. Spec
    docs/superpowers/specs/2026-05-15-map-elements-attack-design.md Tier 1.
    """
    return (
        pl.col(column)
        .str.replace(
            r"(?i)^(Mr\.?|Mrs\.?|Ms\.?|Miss\.?|Dr\.?|Prof\.?|Rev\.?|Sr\.?|Sra\.?)\s+",
            "",
        )
        .str.strip_chars()
    )


@register_transform(
    name="strip_suffixes", input_types=["name"], auto_apply=False, priority=60, mode="expr"
)
def strip_suffixes(column: str) -> pl.Expr:
    """Strip trailing professional suffixes (Jr/Sr/II/MD/PhD/etc.) from names.

    Native Polars: case-insensitive regex replace then strip. Spec
    docs/superpowers/specs/2026-05-15-map-elements-attack-design.md Tier 1.
    """
    return (
        pl.col(column)
        .str.replace(
            r"(?i)\s+(Jr\.?|Sr\.?|II|III|IV|MD|PhD|PharmD|DDS|DVM|Esq\.?|CPA|RN|DO)$",
            "",
        )
        .str.strip_chars()
    )


@register_transform(
    name="name_proper", input_types=["name"], auto_apply=False, priority=45, mode="series"
)
def name_proper(series: pl.Series) -> pl.Series:
    def _proper(val: str | None) -> str | None:
        if val is None:
            return None
        result = val.title()
        result = _MC_PATTERN.sub(lambda m: f"Mc{m.group(1).upper()}", result)
        result = _O_PATTERN.sub(lambda m: f"O'{m.group(1).upper()}", result)
        return result

    return series.map_elements(_proper, return_dtype=pl.Utf8)


@register_transform(
    name="initial_expand", input_types=["name"], auto_apply=False, priority=40, mode="series"
)
def initial_expand(series: pl.Series) -> tuple[pl.Series, list[int]]:
    """Returns (series, flagged_rows). Values with initials are unchanged but flagged."""
    flagged: list[int] = []
    for i, val in enumerate(series.to_list()):
        if val and _INITIAL_PATTERN.search(val):
            flagged.append(i)
    return series, flagged


_NICKNAMES: dict[str, str] = {
    "bob": "Robert", "rob": "Robert", "robby": "Robert", "robbie": "Robert",
    "bobby": "Robert",
    "bill": "William", "billy": "William", "will": "William", "willy": "William",
    "jim": "James", "jimmy": "James", "jamie": "James",
    "mike": "Michael", "mikey": "Michael", "mick": "Michael",
    "dick": "Richard", "rick": "Richard", "rich": "Richard", "ricky": "Richard",
    "tom": "Thomas", "tommy": "Thomas",
    "joe": "Joseph", "joey": "Joseph",
    "jack": "John", "johnny": "John", "jon": "Jonathan",
    "dave": "David", "davy": "David",
    "steve": "Steven", "stevie": "Steven",
    "dan": "Daniel", "danny": "Daniel",
    "pat": "Patrick", "patty": "Patricia", "patsy": "Patricia",
    "chris": "Christopher", "kit": "Christopher",
    "tony": "Anthony",
    "ed": "Edward", "eddie": "Edward", "ted": "Edward", "teddy": "Edward",
    "al": "Albert", "bert": "Albert",
    "charlie": "Charles", "chuck": "Charles",
    "sam": "Samuel", "sammy": "Samuel",
    "ben": "Benjamin", "benny": "Benjamin",
    "matt": "Matthew",
    "andy": "Andrew", "drew": "Andrew",
    "nick": "Nicholas",
    "alex": "Alexander",
    "liz": "Elizabeth", "beth": "Elizabeth", "betty": "Elizabeth",
    "kate": "Katherine", "kathy": "Katherine", "katie": "Katherine",
    "sue": "Susan", "susie": "Susan",
    "meg": "Margaret", "maggie": "Margaret", "peggy": "Margaret",
    "jenny": "Jennifer", "jen": "Jennifer",
    "debbie": "Deborah", "deb": "Deborah",
    "barb": "Barbara",
    "cindy": "Cynthia",
    "sandy": "Sandra",
}


@register_transform(
    name="nickname_standardize",
    input_types=["name"],
    auto_apply=False,
    priority=42,
    mode="series",
)
def nickname_standardize(series: pl.Series) -> pl.Series:
    """Map common nicknames to formal first names."""

    def _standardize(val: str | None) -> str | None:
        if val is None:
            return None
        return _NICKNAMES.get(val.strip().lower(), val)

    return series.map_elements(_standardize, return_dtype=pl.Utf8)


@register_transform(
    name="merge_name",
    input_types=["name"],
    auto_apply=False,
    priority=45,
    mode="dataframe",
)
def merge_name(
    df: pl.DataFrame, column: str, last_name_col: str = "last_name"
) -> pl.DataFrame:
    """Merge first_name and last_name columns into a full_name column."""
    if last_name_col not in df.columns:
        return df
    full_names = []
    first_list = df[column].to_list()
    last_list = df[last_name_col].to_list()
    for first, last in zip(first_list, last_list):
        parts = [p for p in (first, last) if p is not None and p.strip()]
        full_names.append(" ".join(parts) if parts else None)
    return df.with_columns(pl.Series("full_name", full_names))


# --- name_transliterate (owned i18n-name kernel) -----------------------------
#
# Pure-Python reference for goldenflow-core's ``names::name_transliterate``
# kernel. MUST reproduce the Rust kernel byte-for-byte (asserted by
# tests/transforms/test_identifiers_parity.py over
# tests/parity/identifiers_corpus.jsonl) -- same explicit char map, same
# ASCII-passthrough, same "drop unmapped non-ASCII" behavior. Deliberately
# NOT implemented via ``unicodedata.normalize`` (NFD) -- that depends on the
# interpreter's bundled Unicode version and could silently drift from the
# Rust oracle; this dict must stay byte-identical to
# ``goldenflow-core/src/names.rs::transliterate_char``.
#
# Map coverage (common Latin-script diacritics; not exhaustive -- any char
# not listed here is dropped):
# - a/e/i/o/u with acute, grave, circumflex, diaeresis -> the base vowel.
# - a/o with tilde, a with ring -> the base vowel (the common precomposed
#   vowel-tilde/-ring codepoints).
# - n-tilde (n~), c-cedilla (c,), y-acute, y-diaeresis -> n, c, y.
# - s/z/c/r/e with caron, c/z with acute -> s z c r e (one Latin base
#   letter each).
# - Ligatures/specials: ss-eszett -> ss, ae/AE, oe/OE ligatures, o-slash,
#   d-stroke, l-stroke, thorn, eth.
_TRANSLITERATE_MAP: dict[str, str] = {
    # acute
    "á": "a", "Á": "A", "é": "e", "É": "E",
    "í": "i", "Í": "I", "ó": "o", "Ó": "O",
    "ú": "u", "Ú": "U",
    # grave
    "à": "a", "À": "A", "è": "e", "È": "E",
    "ì": "i", "Ì": "I", "ò": "o", "Ò": "O",
    "ù": "u", "Ù": "U",
    # circumflex
    "â": "a", "Â": "A", "ê": "e", "Ê": "E",
    "î": "i", "Î": "I", "ô": "o", "Ô": "O",
    "û": "u", "Û": "U",
    # diaeresis
    "ä": "a", "Ä": "A", "ë": "e", "Ë": "E",
    "ï": "i", "Ï": "I", "ö": "o", "Ö": "O",
    "ü": "u", "Ü": "U",
    # tilde (a, o -- the common precomposed vowel-tilde chars)
    "ã": "a", "Ã": "A", "õ": "o", "Õ": "O",
    # ring (a -- the common precomposed vowel-ring char)
    "å": "a", "Å": "A",
    # n-tilde / c-cedilla / y-acute / y-diaeresis
    "ñ": "n", "Ñ": "N", "ç": "c", "Ç": "C",
    "ý": "y", "Ý": "Y", "ÿ": "y", "Ÿ": "Y",
    # caron/acute consonants
    "š": "s", "Š": "S", "ž": "z", "Ž": "Z",
    "ź": "z", "Ź": "Z", "č": "c", "Č": "C",
    "ć": "c", "Ć": "C", "ř": "r", "Ř": "R",
    "ě": "e", "Ě": "E",
    # ligatures / specials
    "ß": "ss", "æ": "ae", "Æ": "AE", "œ": "oe",
    "Œ": "OE", "ø": "o", "Ø": "O", "đ": "d",
    "Đ": "D", "ł": "l", "Ł": "L", "þ": "th",
    "Þ": "Th", "ð": "d", "Ð": "D",
}


def _name_transliterate_py(val: str | None) -> str | None:
    if val is None:
        return None
    out: list[str] = []
    for c in val:
        if ord(c) < 128:
            out.append(c)
        else:
            rep = _TRANSLITERATE_MAP.get(c)
            if rep is not None:
                out.append(rep)
        # else: unmapped non-ASCII -- drop.
    return "".join(out)


@register_transform(
    name="name_transliterate",
    input_types=["name", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def name_transliterate(series: pl.Series) -> pl.Series:
    """ASCII-fold a name via an explicit curated diacritic map. Non-ASCII
    chars not in the map are dropped."""
    native = name_transliterate_native()
    if native is not None:
        return native(series)
    return series.map_elements(_name_transliterate_py, return_dtype=pl.Utf8)
