"""goldenphonetic (pyo3 wheel) smoke + byte-identity-vs-jellyfish tests.

jellyfish is a TEST-only oracle here (not a runtime dep of goldenphonetic); these
assert the wheel is bit-for-bit identical to it on all five phonetic functions,
including jellyfish's exact error semantics (codex raises on non-alpha; comparison
returns None, never raises). Accented inputs are built from ASCII + combining
codepoints (via ``unicodedata``) so the source file stays pure-ASCII and each name
is exercised in BOTH precomposed (NFC) and decomposed (NFD) form.
"""
from __future__ import annotations

import unicodedata

import goldenphonetic as gp
import pytest

jellyfish = pytest.importorskip("jellyfish")

# Accented names spelled with combining marks; normalized to NFC + NFD below.
_ACCENTED_BASE = [
    "josé",       # jose (e + acute) -> jose-acute
    "müller",     # muller (u + diaeresis)
    "Ærø",    # AE-ligature r o-slash
    "naïve",      # naive
    "straße",      # strasse (ss)
    "é",          # single accented char
    "beyoncé",
    "gödel",
]

# A batch spanning: real names, all-caps/mixed case, hyphen/apostrophe, accented
# (precomposed + decomposed), digits, whitespace, single-char, empty.
INPUTS = [
    "Robert",
    "ROBERT",
    "rupert",
    "Ashcraft",
    "Tymczak",
    "Pfister",
    "Honeyman",
    "Catherine",
    "Kathryn",
    "Thompson",
    "Knight",
    "Wright",
    "Pneumonia",
    "MacDonald",
    "Schwartz",
    "Jonathan",
    "O'Brien",              # apostrophe -> codex raises
    "Smith-Jones",          # hyphen -> codex raises
    "van der Berg",         # spaces (codex allows)
    "123",                  # digits -> codex raises
    "a1b2",
    "  spaces  ",
    "a",
    "Q",
    "",                     # empty
]
for _name in _ACCENTED_BASE:
    INPUTS.append(unicodedata.normalize("NFC", _name))
    INPUTS.append(unicodedata.normalize("NFD", _name))


@pytest.mark.parametrize("s", INPUTS)
def test_unary_byte_identical_to_jellyfish(s: str) -> None:
    assert gp.soundex(s) == jellyfish.soundex(s)
    assert gp.metaphone(s) == jellyfish.metaphone(s)
    assert gp.nysiis(s) == jellyfish.nysiis(s)

    # match_rating_codex: jellyfish raises ValueError on non-alpha; we must too.
    try:
        theirs = jellyfish.match_rating_codex(s)
    except ValueError:
        with pytest.raises(ValueError):
            gp.match_rating_codex(s)
    else:
        assert gp.match_rating_codex(s) == theirs


PAIRS = [
    ("Byrne", "Boern"),
    ("Smith", "Smyth"),
    ("Catherine", "Kathryn"),
    ("Michael", "Mike"),
    ("Tim", "Timothy"),                 # length diff -> None
    ("O'Brien", "OBrien"),              # non-alpha -> None
    ("", ""),
    ("Robert", "Rupert"),
    (unicodedata.normalize("NFC", "josé"),
     unicodedata.normalize("NFD", "josé")),  # precomposed vs decomposed
    ("müller", "mueller"),
]


@pytest.mark.parametrize("a,b", PAIRS)
def test_match_rating_comparison_byte_identical(a: str, b: str) -> None:
    # jellyfish.match_rating_comparison returns True / False / None, never raises.
    assert gp.match_rating_comparison(a, b) == jellyfish.match_rating_comparison(a, b)


def test_directed_values() -> None:
    assert gp.soundex("Robert") == "R163"
    assert gp.metaphone("Thompson") == "0MPSN"
    assert gp.nysiis("Catherine") == "CATARAN"
    assert gp.match_rating_codex("Byrne") == "BYRN"
    assert gp.match_rating_comparison("Byrne", "Boern") is True
    assert gp.match_rating_comparison("Tim", "Timothy") is None


def test_codex_raises_on_non_alpha() -> None:
    with pytest.raises(ValueError):
        gp.match_rating_codex("O'Brien")


def test_version_present() -> None:
    assert isinstance(gp.__version__, str) and gp.__version__
