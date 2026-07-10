"""Facility full-name negative-evidence lever (opt-in GOLDENMATCH_FACILITY_NAME_NE).

On company/location-mode data (a workplace attribute demoted because its
shared-value records don't co-agree on the person name), promote the person
FULL NAME as token_sort negative evidence so distinct colleagues at one facility
don't fuse. The full name is a synthesized column (NegativeEvidenceField.derive_from).
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    MatchkeyConfig,
    MatchkeyField,
    NegativeEvidenceField,
)
from goldenmatch.core.matchkey import precompute_matchkey_transforms


class TestDeriveFromSchema:
    def test_derive_from_requires_two_sources(self):
        with pytest.raises(ValueError, match="at least 2"):
            NegativeEvidenceField(
                field="__gm_person_fullname__", scorer="token_sort",
                threshold=0.65, penalty=0.5, derive_from=["first_name"],
            )

    def test_derive_from_two_sources_ok(self):
        ne = NegativeEvidenceField(
            field="__gm_person_fullname__", scorer="token_sort",
            threshold=0.65, penalty=0.5, derive_from=["first_name", "last_name"],
        )
        assert ne.derive_from == ["first_name", "last_name"]

    def test_derive_from_default_none(self):
        ne = NegativeEvidenceField(field="phone", scorer="exact", threshold=0.5, penalty=0.5)
        assert ne.derive_from is None


class TestDerivedColumnMaterialization:
    def test_precompute_materializes_full_name(self):
        df = pl.DataFrame({
            "first_name": ["John", "Jane"],
            "last_name": ["Smith", "Doe"],
            "zip": ["10001", "20002"],
        })
        mk = MatchkeyConfig(
            name="k", type="exact", fields=[MatchkeyField(field="zip")],
            negative_evidence=[NegativeEvidenceField(
                field="__gm_person_fullname__", scorer="token_sort",
                threshold=0.65, penalty=0.5, derive_from=["first_name", "last_name"],
            )],
        )
        out = precompute_matchkey_transforms(df, [mk])
        assert "__gm_person_fullname__" in out.columns
        assert out["__gm_person_fullname__"].to_list() == ["John Smith", "Jane Doe"]

    def test_missing_source_is_safe_noop(self):
        # last_name absent -> derived column skipped, no raise
        df = pl.DataFrame({"first_name": ["John"], "zip": ["1"]})
        mk = MatchkeyConfig(
            name="k", type="exact", fields=[MatchkeyField(field="zip")],
            negative_evidence=[NegativeEvidenceField(
                field="__gm_person_fullname__", scorer="token_sort",
                threshold=0.65, penalty=0.5, derive_from=["first_name", "last_name"],
            )],
        )
        out = precompute_matchkey_transforms(df, [mk])
        assert "__gm_person_fullname__" not in out.columns


class TestFacilityPromotion:
    def _weighted_mk(self):
        return MatchkeyConfig(
            name="fuzzy_match", type="weighted", threshold=0.8,
            fields=[MatchkeyField(field="address1", scorer="jaro_winkler", weight=1.0),
                    MatchkeyField(field="first_name", scorer="jaro_winkler", weight=1.0)],
        )

    def test_promoted_when_flag_on_and_facility_mode(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_FACILITY_NAME_NE", "1")
        from goldenmatch.core.autoconfig import _promote_facility_fullname_ne
        mks = [self._weighted_mk()]
        skipped = [("phone", "group-attribute: shared facility value, not identity")]
        basket = [("first_name", True), ("last_name", True)]
        _promote_facility_fullname_ne(mks, skipped, basket)
        ne = mks[0].negative_evidence or []
        assert any(n.field == "__gm_person_fullname__" and n.scorer == "token_sort"
                   and n.derive_from == ["first_name", "last_name"] for n in ne)

    def test_not_promoted_when_flag_off(self, monkeypatch):
        monkeypatch.delenv("GOLDENMATCH_FACILITY_NAME_NE", raising=False)
        from goldenmatch.core.autoconfig import _promote_facility_fullname_ne
        mks = [self._weighted_mk()]
        skipped = [("phone", "group-attribute: shared facility value, not identity")]
        _promote_facility_fullname_ne(mks, skipped, [("first_name", True), ("last_name", True)])
        assert not (mks[0].negative_evidence or [])

    def test_not_promoted_without_facility_mode(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_FACILITY_NAME_NE", "1")
        from goldenmatch.core.autoconfig import _promote_facility_fullname_ne
        mks = [self._weighted_mk()]
        skipped = [("email", "cardinality_ratio 0.2 < 0.50")]  # not a facility reason
        _promote_facility_fullname_ne(mks, skipped, [("first_name", True), ("last_name", True)])
        assert not (mks[0].negative_evidence or [])
