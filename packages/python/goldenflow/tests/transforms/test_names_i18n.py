"""Unit tests for the owned i18n-name kernels: ``name_transliterate`` and
``name_script``. Byte-parity against the Rust oracle is covered separately
by ``tests/transforms/test_identifiers_parity.py`` over
``tests/parity/identifiers_corpus.jsonl``; this file exercises the
registered transforms directly (registration, series semantics, null
handling)."""
from __future__ import annotations

import polars as pl
from goldenflow.transforms import registry
from goldenflow.transforms.names import name_script, name_transliterate


def test_name_transforms_registered():
    names = set(registry())
    assert "name_transliterate" in names
    assert "name_script" in names


def test_name_transliterate_common_diacritics():
    s = pl.Series(
        "v", ["José", "Müller", "Straße", "Łódź", "Renée", "Æsir", "Smith"]
    )
    result = name_transliterate(s).to_list()
    assert result == ["Jose", "Muller", "Strasse", "Lodz", "Renee", "AEsir", "Smith"]


def test_name_transliterate_empty_and_null():
    s = pl.Series("v", ["", None])
    result = name_transliterate(s).to_list()
    assert result == ["", None]


def test_name_transliterate_drops_unmapped_non_ascii():
    s = pl.Series("v", ["张\U0001f600"])
    assert name_transliterate(s).to_list() == [""]


def test_name_script_detects_major_scripts():
    s = pl.Series(
        "v",
        ["Smith", "José", "Иван", "Ολγα", "张伟", "ひらがな", "カタカナ", "홍길동", "محمد", "राम"],
    )
    result = name_script(s).to_list()
    assert result == [
        "Latin",
        "Latin",
        "Cyrillic",
        "Greek",
        "Han",
        "Hiragana",
        "Katakana",
        "Hangul",
        "Arabic",
        "Devanagari",
    ]


def test_name_script_common_and_unknown():
    s = pl.Series("v", ["123", "", None])
    assert name_script(s).to_list() == ["Common", "Unknown", None]
