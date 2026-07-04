"""Unit tests for the owned i18n-name kernels (``name_transliterate`` so
far). Byte-parity against the Rust oracle is covered separately by
``tests/transforms/test_identifiers_parity.py`` over
``tests/parity/identifiers_corpus.jsonl``; this file exercises the
registered transforms directly (registration, series semantics, null
handling)."""
from __future__ import annotations

import polars as pl
from goldenflow.transforms import registry
from goldenflow.transforms.names import name_transliterate


def test_name_transforms_registered():
    names = set(registry())
    assert "name_transliterate" in names


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
