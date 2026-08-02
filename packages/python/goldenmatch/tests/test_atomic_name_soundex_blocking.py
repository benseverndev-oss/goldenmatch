"""Atomic-name-soundex blocking lever (GOLDENMATCH_FS_ATOMIC_NAME_BLOCKING).

`build_blocking` emits `full_name`/`first_and_surname` soundex passes, so one
corrupted name breaks the whole composite key -- a pair with a mangled first name
but an intact surname never co-blocks, even though a `surname` soundex would catch
it (historical_50k: surname-soundex alone recovers 49% of the missed true pairs).

`_add_atomic_name_soundex_blocking` adds an additive atomic single-name SOUNDEX
pass for each given/family field when a composite-name soundex pass exists but the
atomic one doesn't.

KNOWN-NEGATIVE (corrected 2026-08-02): the lever raises candidate/blocking recall
(+6pp on historical_50k) but REGRESSES end-to-end quality on the canonical
bench_er_headtohead panel (historical_50k pairwise F1 -0.0148 / B3 -0.0078) --
the corrupted-name candidates fall below the FS threshold and the EM shift
degrades the operating point. Default OFF; do NOT enable. These tests only assert
the pass-construction MECHANISM (gating, dedup, additive), not any F1 claim.
"""

from __future__ import annotations

import pytest
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.autoconfig import (
    ColumnProfile,
    _add_atomic_name_soundex_blocking,
    _fs_atomic_name_blocking_mode,
)

FLAG = "GOLDENMATCH_FS_ATOMIC_NAME_BLOCKING"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")


def _p(name, col_type="name", card=0.5, null=0.0):
    return ColumnProfile(
        name=name, dtype="Utf8", col_type=col_type, confidence=0.9,
        null_rate=null, cardinality_ratio=card, avg_len=8,
    )


def _composite_name_blocking():
    """Blocking keyed on the name composites via soundex (the historical shape)."""
    return BlockingConfig(
        strategy="multi_pass",
        passes=[
            BlockingKeyConfig(fields=["full_name"], transforms=["lowercase", "soundex"]),
            BlockingKeyConfig(
                fields=["first_and_surname"], transforms=["lowercase", "soundex"]
            ),
            BlockingKeyConfig(fields=["dob"], transforms=["substring:0:4"]),
        ],
    )


def _person_profiles():
    return [
        _p("first_name"),
        _p("surname"),
        _p("full_name"),
        _p("dob", col_type="date"),
    ]


def _pass_field_lists(cfg):
    return [list(k.fields) for k in (cfg.passes or [])]


class TestMode:
    def test_default_is_off(self, monkeypatch):
        assert _fs_atomic_name_blocking_mode() == "off"

    @pytest.mark.parametrize("val,expect", [
        ("on", "on"), ("1", "on"), ("true", "on"), ("yes", "on"),
        ("auto", "auto"),
        ("off", "off"), ("0", "off"), ("no", "off"), ("garbage", "off"),
    ])
    def test_resolution(self, monkeypatch, val, expect):
        monkeypatch.setenv(FLAG, val)
        assert _fs_atomic_name_blocking_mode() == expect


class TestLever:
    def test_default_off_is_inert(self, monkeypatch):
        blk = _composite_name_blocking()
        out = _add_atomic_name_soundex_blocking(blk, _person_profiles())
        assert _pass_field_lists(out) == _pass_field_lists(blk)

    def test_on_adds_atomic_name_soundex(self, monkeypatch):
        monkeypatch.setenv(FLAG, "on")
        out = _add_atomic_name_soundex_blocking(
            _composite_name_blocking(), _person_profiles()
        )
        fields = _pass_field_lists(out)
        assert ["first_name"] in fields
        assert ["surname"] in fields
        # the added passes are soundex + additive
        added = [k for k in out.passes if list(k.fields) in (["first_name"], ["surname"])]
        for k in added:
            assert "soundex" in k.transforms
            assert k.additive is True

    def test_auto_person_shaped_fires(self, monkeypatch):
        monkeypatch.setenv(FLAG, "auto")
        out = _add_atomic_name_soundex_blocking(
            _composite_name_blocking(), _person_profiles()
        )
        assert ["surname"] in _pass_field_lists(out)

    def test_auto_non_person_is_noop(self, monkeypatch):
        monkeypatch.setenv(FLAG, "auto")
        # bibliographic shape: a name but NO date col_type -> not person-shaped
        profiles = [_p("author"), _p("title", col_type="description"),
                    _p("venue", col_type="name")]
        blk = _composite_name_blocking()
        out = _add_atomic_name_soundex_blocking(blk, profiles)
        assert _pass_field_lists(out) == _pass_field_lists(blk)

    def test_gap_condition_requires_composite_name_soundex(self, monkeypatch):
        monkeypatch.setenv(FLAG, "on")
        # names are NOT soundex-blocked (only dob/postcode) -> don't invent a name
        # signal the config deliberately doesn't use.
        blk = BlockingConfig(
            strategy="multi_pass",
            passes=[
                BlockingKeyConfig(fields=["dob"], transforms=["substring:0:4"]),
                BlockingKeyConfig(fields=["postcode"], transforms=["strip"]),
            ],
        )
        out = _add_atomic_name_soundex_blocking(blk, _person_profiles())
        assert _pass_field_lists(out) == _pass_field_lists(blk)

    def test_no_duplicate_when_atomic_soundex_present(self, monkeypatch):
        monkeypatch.setenv(FLAG, "on")
        blk = _composite_name_blocking()
        # surname already has its own atomic soundex pass
        blk.passes = list(blk.passes) + [
            BlockingKeyConfig(fields=["surname"], transforms=["lowercase", "soundex"])
        ]
        out = _add_atomic_name_soundex_blocking(blk, _person_profiles())
        surname_passes = [k for k in out.passes if list(k.fields) == ["surname"]]
        assert len(surname_passes) == 1  # not re-added
        assert ["first_name"] in _pass_field_lists(out)  # still adds the missing one

    def test_high_null_name_skipped(self, monkeypatch):
        monkeypatch.setenv(FLAG, "on")
        profiles = [
            _p("first_name", null=0.0),
            _p("surname", null=0.9),  # mostly empty -> giant null block, skip
            _p("full_name"),
            _p("dob", col_type="date"),
        ]
        out = _add_atomic_name_soundex_blocking(_composite_name_blocking(), profiles)
        fields = _pass_field_lists(out)
        assert ["first_name"] in fields
        assert ["surname"] not in fields

    def test_none_blocking_is_safe(self, monkeypatch):
        monkeypatch.setenv(FLAG, "on")
        assert _add_atomic_name_soundex_blocking(None, _person_profiles()) is None

    def test_static_keys_config_preserves_the_key(self, monkeypatch):
        monkeypatch.setenv(FLAG, "on")
        # a static config carries its blocking key in `keys`, passes=None:
        # promoting to multi_pass must KEEP the original key, not drop it.
        blk = BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["full_name"],
                                    transforms=["lowercase", "soundex"])],
        )
        out = _add_atomic_name_soundex_blocking(blk, _person_profiles())
        assert out.strategy == "multi_pass"
        fields = _pass_field_lists(out)
        assert ["full_name"] in fields  # original static key preserved
        assert ["surname"] in fields    # atomic passes added
        assert ["first_name"] in fields
