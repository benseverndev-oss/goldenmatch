"""W2e-1 parity fixtures: the Arrow standardizer twins must reproduce the
Polars `_NATIVE_STANDARDIZERS` chains value-for-value.

PolarsFrame.derive_standardized_column delegates to the exact expressions
`apply_standardization` builds per column (including the mixed-chain
reordering quirk), so PolarsFrame output IS the reference. Hazards pinned:
null-if-empty (empty -> null, null STAYS null), `\\D` Unicode-digit
semantics (`\\P{Nd}` in RE2), titlecase across hyphens/apostrophes/digits/
non-ASCII (probed equal 2026-07-10), zip5 pad/truncate, phone 11-digit
country-code strip + <7 null, email validity gate, and the `address` +
plugin fallbacks through the pure-Python STANDARDIZERS oracle.
"""

from __future__ import annotations

import polars as pl
import pyarrow as pa
import pytest
from goldenmatch.core.frame import ArrowFrame, PolarsFrame

_CORPUS: dict[str, list[str | None]] = {
    "names": ["  mary-jane O'BRIEN ", "", None, "de la cruz", "SÖREN müller", "  x  "],
    "phones": ["1-800-555-0199", "8005550199", "555-0199", "12", "", None, "+1 (415) 555 2671"],
    "zips": ["7030", "07030-1234", "abc", "", None, "123456789"],
    "emails": ["  Bob@EXAMPLE.com ", "bad-email", "a@b", "a@b.c", "", None, "x@@y.z"],
    "addresses": ["123 main st.", "p.o. box 42", "  456  oak   AVE ", None, ""],
    "unicode_digits": ["a1٢b3", "", None],
}

_CHAINS = [
    ["strip"],
    ["name_upper"],
    ["name_lower"],
    ["name_proper"],
    ["state"],
    ["trim_whitespace"],
    ["phone"],
    ["zip5"],
    ["email"],
    ["address"],  # arrow declines -> python std_address oracle
    ["strip", "name_proper"],
    ["trim_whitespace", "name_upper"],
]


def _pair(vals: list[str | None]) -> tuple[PolarsFrame, ArrowFrame]:
    tbl = pa.table({"c": pa.array(vals, type=pa.string())})
    return PolarsFrame(pl.from_arrow(tbl)), ArrowFrame(tbl)


@pytest.mark.parametrize("chain", _CHAINS, ids=["+".join(c) for c in _CHAINS])
@pytest.mark.parametrize("col", sorted(_CORPUS))
def test_standardized_column_parity(col: str, chain: list[str]) -> None:
    pf, af = _pair(_CORPUS[col])
    want = pf.derive_standardized_column("c", chain).to_list()
    got = af.derive_standardized_column("c", chain).to_list()
    assert got == want


def test_null_if_empty_contract() -> None:
    # empty -> null; whitespace-only strips to empty -> null; null stays null.
    pf, af = _pair(["", "   ", None, "x"])
    for frame in (pf, af):
        assert frame.derive_standardized_column("c", ["strip"]).to_list() == [
            None, None, None, "x",
        ]


def test_phone_semantics_pinned() -> None:
    _, af = _pair(["1-800-555-0199", "555-0199", "123456", None])
    got = af.derive_standardized_column("c", ["phone"]).to_list()
    # 11-digit leading-1 stripped; 7-digit kept; <7 -> null; null stays.
    assert got == ["8005550199", "5550199", None, None]


def test_zip5_semantics_pinned() -> None:
    _, af = _pair(["7030", "07030-1234", "abc", None])
    got = af.derive_standardized_column("c", ["zip5"]).to_list()
    assert got == ["07030", "07030", None, None]


def test_unicode_digit_class_parity() -> None:
    # Polars \D is Unicode (keeps U+0662 ARABIC-INDIC TWO as a digit); the
    # arrow twin must use \P{Nd}, not [^0-9].
    pf, af = _pair(["a1٢b3"])
    want = pf.derive_standardized_column("c", ["phone"]).to_list()
    got = af.derive_standardized_column("c", ["phone"]).to_list()
    assert got == want


def test_mixed_chain_reordering_quirk() -> None:
    # A chain containing an unregistered-native name exercises the fallback:
    # both backends must reproduce apply_standardization's native-first
    # reorder. 'address' is native on polars but python-fallback on arrow --
    # parity across that split is the whole point.
    pf, af = _pair(["  123 MAIN st  ", None])
    chain = ["trim_whitespace", "address"]
    assert (
        af.derive_standardized_column("c", chain).to_list()
        == pf.derive_standardized_column("c", chain).to_list()
    )


# ---- W2e-2: matchkey composite parity -----------------------------------------


def _mk_pair(cols: dict[str, list]) -> tuple[PolarsFrame, ArrowFrame]:
    tbl = pa.table({k: pa.array(v, type=pa.string()) for k, v in cols.items()})
    return PolarsFrame(pl.from_arrow(tbl)), ArrowFrame(tbl)


@pytest.mark.parametrize(
    "chains",
    [
        [("a", []), ("b", [])],
        [("a", ["lowercase", "strip"]), ("b", ["digits_only"])],
        [("a", ["soundex"]), ("b", [])],  # per-field fallback + plain cast
        [("a", ["lowercase"])],  # single field skips the join
    ],
    ids=["no-transforms", "per-field-chains", "fallback+cast", "single"],
)
def test_derive_matchkey_parity(chains) -> None:
    pf, af = _mk_pair(
        {
            "a": ["  Smith ", None, "JONES", "x1"],
            "b": ["z1", "z2", None, "z4"],
        }
    )
    want = pf.derive_matchkey(chains).to_list()
    got = af.derive_matchkey(chains).to_list()
    assert got == want


def test_derive_matchkey_null_propagation() -> None:
    # concat null-propagates (any null field -> null key), like the exact
    # matchkey the engine dedupes on.
    pf, af = _mk_pair({"a": ["x", None], "b": ["y", "z"]})
    for frame in (pf, af):
        assert frame.derive_matchkey([("a", []), ("b", [])]).to_list() == ["x||y", None]


def test_derive_ne_joined_never_null_propagates() -> None:
    # fill_null("") joins missing parts as empty -- NEVER null.
    pf, af = _mk_pair({"a": ["x", None], "b": [None, "z"]})
    for frame in (pf, af):
        assert frame.derive_ne_joined(["a", "b"]).to_list() == ["x ", " z"]
