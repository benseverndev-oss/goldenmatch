from __future__ import annotations

import re

from goldenflow._polars_lazy import pl
from goldenflow.transforms import register_transform
from goldenflow.transforms._native import (
    has_initial_native,
    merge_name_native,
    name_initials_native,
    name_proper_native,
    name_script_native,
    name_transliterate_native,
    nickname_standardize_native,
    split_name_native,
    split_name_reverse_native,
    strip_middle_native,
    strip_suffixes_native,
    strip_titles_native,
)

_TITLES = re.compile(
    r"^(Mr\.?|Mrs\.?|Ms\.?|Miss\.?|Dr\.?|Prof\.?|Rev\.?|Sr\.?|Sra\.?)\s+", re.IGNORECASE
)
_SUFFIXES = re.compile(
    r"\s+(Jr\.?|Sr\.?|II|III|IV|MD|PhD|PharmD|DDS|DVM|Esq\.?|CPA|RN|DO)$", re.IGNORECASE
)
_INITIAL_PATTERN = re.compile(r"\b[A-Z]\.\s")

_MC_PATTERN = re.compile(r"\bMc(\w)")
_O_PATTERN = re.compile(r"\bO'(\w)")


# Pure-Python references for goldenflow-core's ``names`` kernels. Each MUST
# reproduce the Rust kernel byte-for-byte (kernel = spec under reference-mode).
# The scalar ones are asserted over tests/parity/identifiers_corpus.jsonl; the
# multi-output ones (split_name/split_name_reverse/merge_name) over
# tests/transforms/test_name_kernels.py.


def _split_name_py(val: str | None) -> tuple[str | None, str | None]:
    if val is None:
        return None, None
    parts = val.strip().rsplit(" ", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return parts[0], ""


@register_transform(
    name="split_name", input_types=["name"], auto_apply=False, priority=50, mode="dataframe"
)
def split_name(df: pl.DataFrame, column: str) -> pl.DataFrame:
    """Split 'First Last' into first_name and last_name columns.

    Native-first (goldenflow-core's ``names::split_name`` kernel, returning a
    pair of Arrow arrays); the pure-Python fallback is the byte-exact reference.
    """
    native = split_name_native()
    if native is not None:
        first, last = native(df[column])
        return df.with_columns(first.rename("first_name"), last.rename("last_name"))
    first_names: list[str | None] = []
    last_names: list[str | None] = []
    for val in df[column].to_list():
        f, l = _split_name_py(val)
        first_names.append(f)
        last_names.append(l)
    return df.with_columns(
        pl.Series("first_name", first_names),
        pl.Series("last_name", last_names),
    )


def _split_name_reverse_py(val: str | None) -> tuple[str | None, str | None]:
    if val is None:
        return None, None
    parts = val.split(",", 1)
    if len(parts) == 2:
        return parts[1].strip(), parts[0].strip()
    return val.strip(), ""


@register_transform(
    name="split_name_reverse", input_types=["name"], auto_apply=False, priority=50, mode="dataframe"
)
def split_name_reverse(df: pl.DataFrame, column: str) -> pl.DataFrame:
    """Split 'Last, First' into first_name and last_name columns.

    Native-first (goldenflow-core's ``names::split_name_reverse`` kernel); the
    pure-Python fallback is the byte-exact reference.
    """
    native = split_name_reverse_native()
    if native is not None:
        first, last = native(df[column])
        return df.with_columns(first.rename("first_name"), last.rename("last_name"))
    first_names: list[str | None] = []
    last_names: list[str | None] = []
    for val in df[column].to_list():
        f, l = _split_name_reverse_py(val)
        first_names.append(f)
        last_names.append(l)
    return df.with_columns(
        pl.Series("first_name", first_names),
        pl.Series("last_name", last_names),
    )


def _strip_titles_py(val: str | None) -> str | None:
    if val is None:
        return None
    return _TITLES.sub("", val).strip()


def _strip_titles_series(series: pl.Series) -> pl.Series:
    native = strip_titles_native()
    if native is not None:
        return native(series)
    return series.map_elements(_strip_titles_py, return_dtype=pl.Utf8)


@register_transform(
    name="strip_titles", input_types=["name"], auto_apply=True, priority=70, mode="expr"
)
def strip_titles(column: str) -> pl.Expr:
    """Strip leading personal titles (Mr/Mrs/Ms/Dr/Prof/etc.) from names.

    Native-first (goldenflow-core's ``names::strip_titles`` kernel), dispatched
    via ``map_batches`` so the transform keeps its ``expr``-mode signature; the
    pure-Python fallback is the byte-exact reference this kernel replicates.
    """
    return pl.col(column).map_batches(_strip_titles_series, return_dtype=pl.Utf8)


def _strip_suffixes_py(val: str | None) -> str | None:
    if val is None:
        return None
    return _SUFFIXES.sub("", val).strip()


def _strip_suffixes_series(series: pl.Series) -> pl.Series:
    native = strip_suffixes_native()
    if native is not None:
        return native(series)
    return series.map_elements(_strip_suffixes_py, return_dtype=pl.Utf8)


@register_transform(
    name="strip_suffixes", input_types=["name"], auto_apply=False, priority=60, mode="expr"
)
def strip_suffixes(column: str) -> pl.Expr:
    """Strip trailing professional suffixes (Jr/Sr/II/MD/PhD/etc.) from names.

    Native-first (goldenflow-core's ``names::strip_suffixes`` kernel), dispatched
    via ``map_batches`` so the transform keeps its ``expr``-mode signature; the
    pure-Python fallback is the byte-exact reference this kernel replicates.
    """
    return pl.col(column).map_batches(_strip_suffixes_series, return_dtype=pl.Utf8)


def _name_proper_py(val: str | None) -> str | None:
    if val is None:
        return None
    result = val.title()
    result = _MC_PATTERN.sub(lambda m: f"Mc{m.group(1).upper()}", result)
    result = _O_PATTERN.sub(lambda m: f"O'{m.group(1).upper()}", result)
    return result


@register_transform(
    name="name_proper", input_types=["name"], auto_apply=False, priority=45, mode="series"
)
def name_proper(series: pl.Series) -> pl.Series:
    """Proper-case a name (title-case + Mc/O' fixups).

    Native-first (goldenflow-core's ``names::name_proper`` kernel); the
    pure-Python fallback is the byte-exact reference this kernel replicates.
    """
    native = name_proper_native()
    if native is not None:
        return native(series)
    return series.map_elements(_name_proper_py, return_dtype=pl.Utf8)


@register_transform(
    name="initial_expand", input_types=["name"], auto_apply=False, priority=40, mode="series"
)
def initial_expand(series: pl.Series) -> tuple[pl.Series, list[int]]:
    """Returns (series, flagged_rows). Values with initials are unchanged but flagged.

    Native-first for the flag predicate (goldenflow-core's ``names::has_initial``
    kernel); the pure-Python fallback (``_INITIAL_PATTERN``) is the byte-exact
    reference. The value output is the input series unchanged either way.
    """
    native = has_initial_native()
    if native is not None:
        flags = native(series).to_list()
        flagged = [i for i, f in enumerate(flags) if f]
    else:
        flagged = [
            i for i, val in enumerate(series.to_list()) if val and _INITIAL_PATTERN.search(val)
        ]
    return series, flagged


def _has_initial_py(val: str | None) -> bool | None:
    """Scalar reference for goldenflow-core's ``names::has_initial`` -- the flag
    predicate behind ``initial_expand``. ``None`` in -> ``None`` out (the null
    row); else whether the middle-initial pattern is present."""
    if val is None:
        return None
    return bool(_INITIAL_PATTERN.search(val))


def _has_initial_series(series: pl.Series) -> pl.Series:
    """Series-level ``has_initial`` (native-first) -- used by the byte-parity
    harness to assert the predicate cross-surface. Not a registered transform;
    ``initial_expand`` is the user-facing transform that consumes it."""
    native = has_initial_native()
    if native is not None:
        return native(series)
    return series.map_elements(_has_initial_py, return_dtype=pl.Boolean)


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


def _nickname_standardize_py(val: str | None) -> str | None:
    if val is None:
        return None
    return _NICKNAMES.get(val.strip().lower(), val)


@register_transform(
    name="nickname_standardize",
    input_types=["name"],
    auto_apply=False,
    priority=42,
    mode="series",
)
def nickname_standardize(series: pl.Series) -> pl.Series:
    """Map common nicknames to formal first names.

    Native-first (goldenflow-core's ``names::nickname_standardize`` kernel, whose
    ~70-entry map is an in-crate copy of ``_NICKNAMES``); the pure-Python
    fallback is the byte-exact reference this kernel replicates.
    """
    native = nickname_standardize_native()
    if native is not None:
        return native(series)
    return series.map_elements(_nickname_standardize_py, return_dtype=pl.Utf8)


def _merge_name_py(first: str | None, last: str | None) -> str | None:
    parts = [p for p in (first, last) if p is not None and p.strip()]
    return " ".join(parts) if parts else None


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
    """Merge first_name and last_name columns into a full_name column.

    Native-first (goldenflow-core's ``names::merge_name`` kernel over the two
    input columns); the pure-Python fallback is the byte-exact reference.
    """
    if last_name_col not in df.columns:
        return df
    native = merge_name_native()
    if native is not None:
        full = native(df[column], df[last_name_col])
        return df.with_columns(full.rename("full_name"))
    full_names: list[str | None] = []
    first_list = df[column].to_list()
    last_list = df[last_name_col].to_list()
    for first, last in zip(first_list, last_list):
        full_names.append(_merge_name_py(first, last))
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


# --- name_script (owned i18n-name kernel) ------------------------------------
#
# Pure-Python reference for goldenflow-core's ``names::name_script`` kernel.
# MUST reproduce the Rust kernel byte-for-byte (asserted by
# tests/transforms/test_identifiers_parity.py over
# tests/parity/identifiers_corpus.jsonl) -- same explicit Unicode codepoint
# ranges, same tie-break order. Deliberately NOT implemented via a
# general-purpose Unicode script database -- that could drift by runtime
# Unicode version; this table must stay byte-identical to
# ``goldenflow-core/src/names.rs::classify_char``.
#
# Tie-break: highest per-script count wins; an EXACT count tie resolves to
# whichever label appears earliest in ``_SCRIPT_PRIORITY``.
_SCRIPT_PRIORITY: tuple[str, ...] = (
    "Latin", "Cyrillic", "Greek", "Han", "Hiragana", "Katakana",
    "Hangul", "Arabic", "Hebrew", "Devanagari",
)
_SCRIPT_RANGES: tuple[tuple[str, tuple[tuple[int, int], ...]], ...] = (
    ("Latin", ((0x41, 0x5A), (0x61, 0x7A), (0x00C0, 0x024F))),
    ("Cyrillic", ((0x0400, 0x04FF),)),
    ("Greek", ((0x0370, 0x03FF),)),
    ("Han", ((0x4E00, 0x9FFF),)),
    ("Hiragana", ((0x3040, 0x309F),)),
    ("Katakana", ((0x30A0, 0x30FF),)),
    ("Hangul", ((0xAC00, 0xD7A3),)),
    ("Arabic", ((0x0600, 0x06FF),)),
    ("Hebrew", ((0x0590, 0x05FF),)),
    ("Devanagari", ((0x0900, 0x097F),)),
)


def _classify_char(c: str) -> str | None:
    cp = ord(c)
    for label, ranges in _SCRIPT_RANGES:
        for lo, hi in ranges:
            if lo <= cp <= hi:
                return label
    return None


def _name_script_py(val: str | None) -> str | None:
    if val is None:
        return None
    if val == "":
        return "Unknown"
    counts: dict[str, int] = {}
    for c in val:
        label = _classify_char(c)
        if label is not None:
            counts[label] = counts.get(label, 0) + 1
    if not counts:
        return "Common"
    best_label = _SCRIPT_PRIORITY[0]
    best_count = -1
    for label in _SCRIPT_PRIORITY:
        c = counts.get(label, 0)
        if c > best_count:
            best_count = c
            best_label = label
    return best_label


@register_transform(
    name="name_script",
    input_types=["name", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
    scalar=_name_script_py,
)
def name_script(series: pl.Series) -> pl.Series:
    """Detect the dominant Unicode script in a name: ``Unknown`` for empty
    string, ``Common`` when no tracked-script char is present, else the
    script with the highest char count (ties -> earliest in priority
    order: Latin, Cyrillic, Greek, Han, Hiragana, Katakana, Hangul, Arabic,
    Hebrew, Devanagari)."""
    native = name_script_native()
    if native is not None:
        return native(series)
    return series.map_elements(_name_script_py, return_dtype=pl.Utf8)


def _name_initials_py(val: str | None) -> str | None:
    if val is None:
        return None
    out = []
    for tok in val.split():
        first = tok[0]
        if first.isascii() and first.isalpha():
            out.append(first.upper())
    return "".join(out)


def _strip_middle_py(val: str | None) -> str | None:
    if val is None:
        return None
    tokens = val.split()
    if not tokens:
        return ""
    if len(tokens) == 1:
        return tokens[0]
    return f"{tokens[0]} {tokens[-1]}"


@register_transform(
    name="name_initials",
    input_types=["name", "string"],
    auto_apply=False,
    priority=40,
    mode="series",
)
def name_initials(series: pl.Series) -> pl.Series:
    """Initials of each whitespace token (letter-leading tokens only).
    Native-first over goldenflow-core."""
    native = name_initials_native()
    if native is not None:
        return native(series)
    return series.map_elements(_name_initials_py, return_dtype=pl.Utf8)


@register_transform(
    name="strip_middle",
    input_types=["name", "string"],
    auto_apply=False,
    priority=40,
    mode="series",
)
def strip_middle(series: pl.Series) -> pl.Series:
    """Keep only the first and last whitespace tokens (drop the middle).
    Native-first over goldenflow-core."""
    native = strip_middle_native()
    if native is not None:
        return native(series)
    return series.map_elements(_strip_middle_py, return_dtype=pl.Utf8)
