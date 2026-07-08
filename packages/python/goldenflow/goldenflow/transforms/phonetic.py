"""Phonetic key transforms -- blocking/match keys for entity resolution.

Native-first over ``goldenflow-core::phonetic``; ``_soundex_py`` is the
byte-exact pure-Python reference (byte-parity harness).
"""
from __future__ import annotations

from goldenflow._polars_lazy import pl
from goldenflow.transforms import register_transform
from goldenflow.transforms._native import double_metaphone_native, soundex_native

# Soundex consonant classes; vowels + H/W/Y (and anything unmapped) code to "0".
_SOUNDEX_DIGIT = {
    "B": "1", "F": "1", "P": "1", "V": "1",
    "C": "2", "G": "2", "J": "2", "K": "2", "Q": "2", "S": "2", "X": "2", "Z": "2",
    "D": "3", "T": "3",
    "L": "4",
    "M": "5", "N": "5",
    "R": "6",
}


def _soundex_py(val: str | None) -> str | None:
    """American Soundex (NARA rules). Byte-identical to
    ``goldenflow-core::phonetic::soundex``: leading letter + 3 digits; h/w
    transparent, vowels reset the run; ASCII letters only; no-letters -> ``""``."""
    if val is None:
        return None
    letters = [c.upper() for c in val if c.isascii() and c.isalpha()]
    if not letters:
        return ""
    code = letters[0]
    last = _SOUNDEX_DIGIT.get(letters[0], "0")
    for c in letters[1:]:
        if len(code) >= 4:
            break
        d = _SOUNDEX_DIGIT.get(c, "0")
        if d != "0":
            if d != last:
                code += d
            last = d
        elif c not in ("H", "W"):
            last = "0"
    return (code + "000")[:4]


@register_transform(
    name="soundex", input_types=["name", "string"], auto_apply=False, priority=40, mode="series"
)
def soundex(series: pl.Series) -> pl.Series:
    """Soundex phonetic key. Native-first over goldenflow-core."""
    native = soundex_native()
    if native is not None:
        return native(series)
    return series.map_elements(_soundex_py, return_dtype=pl.Utf8)


# --- Double Metaphone ------------------------------------------------------

_DM_VOWELS = frozenset("AEIOUY")


def _dm_slice(chars: list[str], a: int, b: int) -> str:
    """JS ``String.prototype.slice(a, b)`` over the char buffer: negative
    indices count from the end, indices clamp to ``[0, len]``, and
    ``start >= end`` yields ``""``. Byte-identical to ``phonetic.rs::dm_slice``."""
    length = len(chars)
    start = max(length + a, 0) if a < 0 else min(a, length)
    end = max(length + b, 0) if b < 0 else min(b, length)
    if start >= end:
        return ""
    return "".join(chars[start:end])


def _dm_initial_greek_ch(norm: str, at: object) -> bool:
    return (
        norm.startswith("CHIA")
        or norm.startswith("CHEM")
        or norm.startswith("CHYM")
        or norm.startswith("CHARAC")
        or norm.startswith("CHARIS")
        or (norm.startswith("CHOR") and at(4) != "E")  # type: ignore[operator]
    )


def _dm_greek_ch(s: str) -> bool:
    return "ORCHES" in s or "ARCHIT" in s or "ORCHID" in s


def _dm_initial_g_for_kj(s: str) -> bool:
    a = s[0] if len(s) > 0 else "\0"
    b = s[1] if len(s) > 1 else "\0"
    return (
        a == "Y"
        or (a == "E" and b in ("B", "I", "L", "P", "R", "S", "Y"))
        or (a == "I" and b in ("B", "E", "L", "N"))
    )


def _dm_initial_anger_exception(s: str) -> bool:
    return len(s) >= 6 and s[0] in ("D", "M", "R") and s[1:6] == "ANGER"


def _double_metaphone_py(val: str | None) -> tuple[str | None, str | None]:
    """Double Metaphone (Lawrence Philips) -> ``(primary, secondary)``.
    Byte-identical to ``goldenflow-core::phonetic::double_metaphone`` -- a
    faithful port of the canonical ``words/double-metaphone`` reference."""
    if val is None:
        return (None, None)
    primary: list[str] = []
    secondary: list[str] = []
    length = len(val)
    last = length - 1
    chars = list(val.upper()) + [" "] * 5
    norm = "".join(chars)

    is_slavo_germanic = (
        "W" in norm or "K" in norm or "CZ" in norm or "WITZ" in norm
    )
    is_germanic = (
        norm.startswith("VAN ")
        or norm.startswith("VON ")
        or norm.startswith("SCH")
    )

    def at(i: int) -> str:
        return chars[i] if 0 <= i < len(chars) else "\0"

    def slice_(a: int, b: int) -> str:
        return _dm_slice(chars, a, b)

    index = 0

    if (
        norm.startswith("GN")
        or norm.startswith("KN")
        or norm.startswith("PN")
        or norm.startswith("WR")
        or norm.startswith("PS")
    ):
        index += 1

    if at(0) == "X":
        primary.append("S")
        secondary.append("S")
        index += 1

    while index < length:
        previous = at(index - 1)
        next_ = at(index + 1)
        nextnext = at(index + 2)
        c = at(index)

        if c in ("A", "E", "I", "O", "U", "Y", "À", "Ê", "É"):
            if index == 0:
                primary.append("A")
                secondary.append("A")
            index += 1
        elif c == "B":
            primary.append("P")
            secondary.append("P")
            if next_ == "B":
                index += 1
            index += 1
        elif c == "Ç":
            primary.append("S")
            secondary.append("S")
            index += 1
        elif c == "C":
            if (
                previous == "A"
                and next_ == "H"
                and nextnext != "I"
                and at(index - 2) not in _DM_VOWELS
                and (
                    nextnext != "E"
                    or slice_(index - 2, index + 4) in ("BACHER", "MACHER")
                )
            ):
                primary.append("K")
                secondary.append("K")
                index += 2
            elif index == 0 and slice_(index + 1, index + 6) == "AESAR":
                primary.append("S")
                secondary.append("S")
                index += 2
            elif slice_(index + 1, index + 4) == "HIA":
                primary.append("K")
                secondary.append("K")
                index += 2
            elif next_ == "H":
                if index > 0 and nextnext == "A" and at(index + 3) == "E":
                    primary.append("K")
                    secondary.append("X")
                    index += 2
                elif index == 0 and _dm_initial_greek_ch(norm, at):
                    primary.append("K")
                    secondary.append("K")
                    index += 2
                else:
                    if (
                        is_germanic
                        or _dm_greek_ch(slice_(index - 2, index + 4))
                        or nextnext == "T"
                        or nextnext == "S"
                        or (
                            (
                                index == 0
                                or previous in ("A", "E", "O", "U")
                            )
                            and nextnext
                            in (" ", "B", "F", "H", "L", "M", "N", "R", "V", "W")
                        )
                    ):
                        primary.append("K")
                        secondary.append("K")
                    elif index == 0:
                        primary.append("X")
                        secondary.append("X")
                    elif slice_(0, 2) == "MC":
                        primary.append("K")
                        secondary.append("K")
                    else:
                        primary.append("X")
                        secondary.append("K")
                    index += 2
            elif next_ == "Z" and slice_(index - 2, index) != "WI":
                primary.append("S")
                secondary.append("X")
                index += 2
            elif slice_(index + 1, index + 4) == "CIA":
                primary.append("X")
                secondary.append("X")
                index += 3
            elif next_ == "C" and not (index == 1 and at(0) == "M"):
                if (
                    nextnext in ("I", "E", "H")
                    and slice_(index + 2, index + 4) != "HU"
                ):
                    sv = slice_(index - 1, index + 4)
                    if (index == 1 and previous == "A") or sv in (
                        "UCCEE",
                        "UCCES",
                    ):
                        primary.append("KS")
                        secondary.append("KS")
                    else:
                        primary.append("X")
                        secondary.append("X")
                    index += 3
                else:
                    primary.append("K")
                    secondary.append("K")
                    index += 2
            elif next_ in ("G", "K", "Q"):
                primary.append("K")
                secondary.append("K")
                index += 2
            elif next_ == "I" and nextnext in ("E", "O"):
                primary.append("S")
                secondary.append("X")
                index += 2
            elif next_ in ("I", "E", "Y"):
                primary.append("S")
                secondary.append("S")
                index += 2
            else:
                primary.append("K")
                secondary.append("K")
                if next_ == " " and nextnext in ("C", "G", "Q"):
                    index += 3
                else:
                    index += 1
        elif c == "D":
            if next_ == "G":
                if nextnext in ("E", "I", "Y"):
                    primary.append("J")
                    secondary.append("J")
                    index += 3
                else:
                    primary.append("TK")
                    secondary.append("TK")
                    index += 2
            elif next_ in ("T", "D"):
                primary.append("T")
                secondary.append("T")
                index += 2
            else:
                primary.append("T")
                secondary.append("T")
                index += 1
        elif c == "F":
            if next_ == "F":
                index += 1
            index += 1
            primary.append("F")
            secondary.append("F")
        elif c == "G":
            if next_ == "H":
                if index > 0 and previous not in _DM_VOWELS:
                    primary.append("K")
                    secondary.append("K")
                    index += 2
                elif index == 0:
                    if nextnext == "I":
                        primary.append("J")
                        secondary.append("J")
                    else:
                        primary.append("K")
                        secondary.append("K")
                    index += 2
                elif (
                    at(index - 2) in ("B", "H", "D")
                    or at(index - 3) in ("B", "H", "D")
                    or at(index - 4) in ("B", "H")
                ):
                    index += 2
                else:
                    if (
                        index > 2
                        and previous == "U"
                        and at(index - 3) in ("C", "G", "L", "R", "T")
                    ):
                        primary.append("F")
                        secondary.append("F")
                    elif index > 0 and previous != "I":
                        primary.append("K")
                        secondary.append("K")
                    index += 2
            elif next_ == "N":
                if index == 1 and at(0) in _DM_VOWELS and not is_slavo_germanic:
                    primary.append("KN")
                    secondary.append("N")
                elif (
                    slice_(index + 2, index + 4) != "EY"
                    and slice_(index + 1, len(chars)) != "Y"
                    and not is_slavo_germanic
                ):
                    primary.append("N")
                    secondary.append("KN")
                else:
                    primary.append("KN")
                    secondary.append("KN")
                index += 2
            elif slice_(index + 1, index + 3) == "LI" and not is_slavo_germanic:
                primary.append("KL")
                secondary.append("L")
                index += 2
            elif (
                (index == 0 and _dm_initial_g_for_kj(slice_(1, 3)))
                or (
                    slice_(index + 1, index + 3) == "ER"
                    and previous != "I"
                    and previous != "E"
                    and not _dm_initial_anger_exception(slice_(0, 6))
                )
                or (next_ == "Y" and previous not in ("E", "G", "I", "R"))
            ):
                primary.append("K")
                secondary.append("J")
                index += 2
            elif (
                next_ in ("E", "I", "Y")
                or (previous in ("A", "O") and next_ == "G" and nextnext == "I")
            ):
                if slice_(index + 1, index + 3) == "ET" or is_germanic:
                    primary.append("K")
                    secondary.append("K")
                else:
                    primary.append("J")
                    secondary.append(
                        "J" if slice_(index + 1, index + 5) == "IER " else "K"
                    )
                index += 2
            else:
                if next_ == "G":
                    index += 1
                index += 1
                primary.append("K")
                secondary.append("K")
        elif c == "H":
            if (next_ in _DM_VOWELS) and (index == 0 or previous in _DM_VOWELS):
                primary.append("H")
                secondary.append("H")
                index += 1
            index += 1
        elif c == "J":
            if slice_(index, index + 4) == "JOSE" or slice_(0, 4) == "SAN ":
                if slice_(0, 4) == "SAN " or (index == 0 and at(index + 4) == " "):
                    primary.append("H")
                    secondary.append("H")
                else:
                    primary.append("J")
                    secondary.append("H")
                index += 1
            else:
                if index == 0:
                    primary.append("J")
                    secondary.append("A")
                elif (
                    not is_slavo_germanic
                    and next_ in ("A", "O")
                    and previous in _DM_VOWELS
                ):
                    primary.append("J")
                    secondary.append("H")
                elif index == last:
                    primary.append("J")
                elif previous not in ("S", "K", "L") and next_ not in (
                    "L",
                    "T",
                    "K",
                    "S",
                    "N",
                    "M",
                    "B",
                    "Z",
                ):
                    primary.append("J")
                    secondary.append("J")
                elif next_ == "J":
                    index += 1
                index += 1
        elif c == "K":
            if next_ == "K":
                index += 1
            primary.append("K")
            secondary.append("K")
            index += 1
        elif c == "L":
            if next_ == "L":
                if (
                    index == length - 3
                    and (
                        (previous == "A" and nextnext == "E")
                        or (previous == "I" and nextnext in ("O", "A"))
                    )
                ) or (
                    previous == "A"
                    and nextnext == "E"
                    and (
                        at(last) in ("A", "O")
                        or (
                            "AS" in slice_(last - 1, length)
                            or "OS" in slice_(last - 1, length)
                        )
                    )
                ):
                    primary.append("L")
                    index += 2
                else:
                    index += 1
                    primary.append("L")
                    secondary.append("L")
                    index += 1
            else:
                primary.append("L")
                secondary.append("L")
                index += 1
        elif c == "M":
            if next_ == "M" or (
                previous == "U"
                and next_ == "B"
                and (index + 1 == last or slice_(index + 2, index + 4) == "ER")
            ):
                index += 1
            index += 1
            primary.append("M")
            secondary.append("M")
        elif c == "N":
            if next_ == "N":
                index += 1
            index += 1
            primary.append("N")
            secondary.append("N")
        elif c == "Ñ":
            index += 1
            primary.append("N")
            secondary.append("N")
        elif c == "P":
            if next_ == "H":
                primary.append("F")
                secondary.append("F")
                index += 2
            else:
                if next_ in ("P", "B"):
                    index += 1
                index += 1
                primary.append("P")
                secondary.append("P")
        elif c == "Q":
            if next_ == "Q":
                index += 1
            index += 1
            primary.append("K")
            secondary.append("K")
        elif c == "R":
            if (
                index == last
                and not is_slavo_germanic
                and previous == "E"
                and at(index - 2) == "I"
                and at(index - 4) != "M"
                and at(index - 3) not in ("E", "A")
            ):
                secondary.append("R")
            else:
                primary.append("R")
                secondary.append("R")
            if next_ == "R":
                index += 1
            index += 1
        elif c == "S":
            if next_ == "L" and previous in ("I", "Y"):
                index += 1
            elif index == 0 and slice_(1, 5) == "UGAR":
                primary.append("X")
                secondary.append("S")
                index += 1
            elif next_ == "H":
                s = slice_(index + 1, index + 5)
                if "EIM" in s or "OEK" in s or "OLM" in s or "OLZ" in s:
                    primary.append("S")
                    secondary.append("S")
                else:
                    primary.append("X")
                    secondary.append("X")
                index += 2
            elif next_ == "I" and nextnext in ("O", "A"):
                if is_slavo_germanic:
                    primary.append("S")
                    secondary.append("S")
                else:
                    primary.append("S")
                    secondary.append("X")
                index += 3
            elif next_ == "Z" or (
                index == 0 and next_ in ("L", "M", "N", "W")
            ):
                primary.append("S")
                secondary.append("X")
                if next_ == "Z":
                    index += 1
                index += 1
            elif next_ == "C":
                if nextnext == "H":
                    sv = slice_(index + 3, index + 5)
                    if (
                        (
                            sv.startswith("E")
                            and (sv[1] if len(sv) > 1 else "")
                            in ("D", "M", "N", "R")
                        )
                        or sv == "UY"
                        or sv == "OO"
                    ):
                        if sv in ("ER", "EN"):
                            primary.append("X")
                            secondary.append("SK")
                        else:
                            primary.append("SK")
                            secondary.append("SK")
                        index += 3
                    elif index == 0 and at(3) not in _DM_VOWELS and at(3) != "W":
                        primary.append("X")
                        secondary.append("S")
                        index += 3
                    else:
                        primary.append("X")
                        secondary.append("X")
                        index += 3
                elif nextnext in ("I", "E", "Y"):
                    primary.append("S")
                    secondary.append("S")
                    index += 3
                else:
                    primary.append("SK")
                    secondary.append("SK")
                    index += 3
            else:
                sv = slice_(index - 2, index)
                if index == last and sv in ("AI", "OI"):
                    secondary.append("S")
                else:
                    primary.append("S")
                    secondary.append("S")
                if next_ == "S":
                    index += 1
                index += 1
        elif c == "T":
            if (
                (next_ == "I" and nextnext == "O" and at(index + 3) == "N")
                or (next_ == "I" and nextnext == "A")
                or (next_ == "C" and nextnext == "H")
            ):
                primary.append("X")
                secondary.append("X")
                index += 3
            elif next_ == "H" or (next_ == "T" and nextnext == "H"):
                if is_germanic or (
                    nextnext in ("O", "A") and at(index + 3) == "M"
                ):
                    primary.append("T")
                    secondary.append("T")
                else:
                    primary.append("0")
                    secondary.append("T")
                index += 2
            else:
                if next_ in ("T", "D"):
                    index += 1
                index += 1
                primary.append("T")
                secondary.append("T")
        elif c == "V":
            if next_ == "V":
                index += 1
            primary.append("F")
            secondary.append("F")
            index += 1
        elif c == "W":
            if next_ == "R":
                primary.append("R")
                secondary.append("R")
                index += 2
            else:
                if index == 0:
                    if next_ in _DM_VOWELS:
                        primary.append("A")
                        secondary.append("F")
                    elif next_ == "H":
                        primary.append("A")
                        secondary.append("A")
                if (
                    (
                        previous in ("E", "O")
                        and next_ == "S"
                        and nextnext == "K"
                        and at(index + 3) in ("I", "Y")
                    )
                    or slice_(0, 3) == "SCH"
                    or (index == last and previous in _DM_VOWELS)
                ):
                    secondary.append("F")
                    index += 1
                elif (
                    next_ == "I"
                    and nextnext in ("C", "T")
                    and at(index + 3) == "Z"
                ):
                    primary.append("TS")
                    secondary.append("FX")
                    index += 4
                else:
                    index += 1
        elif c == "X":
            if not (
                index == last
                and previous == "U"
                and at(index - 2) in ("A", "O")
            ):
                primary.append("KS")
                secondary.append("KS")
            if next_ in ("C", "X"):
                index += 1
            index += 1
        elif c == "Z":
            if next_ == "H":
                primary.append("J")
                secondary.append("J")
                index += 2
            else:
                if (
                    next_ == "Z" and nextnext in ("A", "I", "O")
                ) or (is_slavo_germanic and index > 0 and previous != "T"):
                    primary.append("S")
                    secondary.append("TS")
                else:
                    primary.append("S")
                    secondary.append("S")
                if next_ == "Z":
                    index += 1
                index += 1
        else:
            index += 1

    return ("".join(primary), "".join(secondary))


def _double_metaphone_primary_py(val: str | None) -> str | None:
    """Primary Double Metaphone code (byte-parity corpus helper)."""
    return _double_metaphone_py(val)[0]


def _double_metaphone_alt_py(val: str | None) -> str | None:
    """Alternate Double Metaphone code (byte-parity corpus helper)."""
    return _double_metaphone_py(val)[1]


def _dm_component(series: pl.Series, which: int) -> pl.Series:
    """Shared body for the two component transforms. ``which`` = 0 (primary)
    or 1 (alternate). Native-first over the pair kernel."""
    native = double_metaphone_native()
    if native is not None:
        return native(series)[which]
    return series.map_elements(
        lambda v: _double_metaphone_py(v)[which], return_dtype=pl.Utf8
    )


@register_transform(
    name="double_metaphone_primary",
    input_types=["name", "string"],
    auto_apply=False,
    priority=40,
    mode="series",
)
def double_metaphone_primary(series: pl.Series) -> pl.Series:
    """Primary Double Metaphone code (blocking key). Native-first."""
    return _dm_component(series, 0)


@register_transform(
    name="double_metaphone_alt",
    input_types=["name", "string"],
    auto_apply=False,
    priority=40,
    mode="series",
)
def double_metaphone_alt(series: pl.Series) -> pl.Series:
    """Alternate Double Metaphone code. Native-first."""
    return _dm_component(series, 1)
