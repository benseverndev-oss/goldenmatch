"""Phonetic key transforms -- blocking/match keys for entity resolution.

Native-first over ``goldenflow-core::phonetic``; ``_soundex_py`` is the
byte-exact pure-Python reference (byte-parity harness).
"""
from __future__ import annotations

import polars as pl

from goldenflow.transforms import register_transform
from goldenflow.transforms._native import soundex_native

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
