"""``_committed_matchkeys_summary`` (goldenmatch.web.controller_telemetry).

Docstring claim: "Mirrors the format that ``/api/v1/rules`` returns but
reflects the committed (engine-side) view rather than the workbench's
flattened RulesPayload." So the two are deliberately NOT the same shape --
RulesPayload is a flat ``matchkeys: [MatchkeyField, ...]`` list (see
``GoldenMatchConfig.schemas.RulesPayload``), while this helper nests fields
per matchkey. These tests pin the one-line-per-matchkey projection this
helper actually produces, and confirm the per-field values it surfaces
(scorer / weight / column) are the same values a MatchkeyConfig carries --
so the "mirrors the format" claim is about field-naming/spirit, not a
literal RulesPayload-shaped output.
"""
from __future__ import annotations

from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField, NegativeEvidenceField
from goldenmatch.web.controller_telemetry import _committed_matchkeys_summary


class _Cfg:
    """Minimal stand-in: the helper only ever calls ``.get_matchkeys()``."""

    def __init__(self, matchkeys):
        self._matchkeys = matchkeys

    def get_matchkeys(self):
        return self._matchkeys


def test_none_config_returns_empty_list():
    assert _committed_matchkeys_summary(None) == []


def test_one_line_per_matchkey_with_expected_keys():
    mk = MatchkeyConfig(
        name="name_phone",
        type="weighted",
        threshold=0.85,
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", weight=0.6),
            MatchkeyField(field="phone", scorer="exact", weight=0.4),
        ],
    )
    out = _committed_matchkeys_summary(_Cfg([mk]))
    assert len(out) == 1
    row = out[0]
    assert row["name"] == "name_phone"
    assert row["type"] == "weighted"
    assert row["threshold"] == 0.85
    assert row["has_negative_evidence"] is False
    assert [f["column"] for f in row["fields"]] == ["first_name", "phone"]
    assert [f["scorer"] for f in row["fields"]] == ["jaro_winkler", "exact"]
    assert [f["weight"] for f in row["fields"]] == [0.6, 0.4]


def test_column_alias_is_preferred_over_field():
    # MatchkeyField.column is an alias for .field; the summary prefers
    # `.column or .field` -- confirm `column` wins when both are set.
    mk = MatchkeyConfig(
        name="mk",
        type="exact",
        fields=[MatchkeyField(field="raw_field", column="aliased_column")],
    )
    row = _committed_matchkeys_summary(_Cfg([mk]))[0]
    assert row["fields"][0]["column"] == "aliased_column"


def test_has_negative_evidence_reflects_the_ne_list():
    ne = NegativeEvidenceField(field="phone", transforms=["digits_only"], scorer="exact", threshold=0.5, penalty=0.2)
    mk = MatchkeyConfig(
        name="mk",
        type="weighted",
        threshold=0.85,
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
        negative_evidence=[ne],
    )
    row = _committed_matchkeys_summary(_Cfg([mk]))[0]
    assert row["has_negative_evidence"] is True


def test_exact_matchkey_has_no_threshold_or_weight():
    # exact matchkeys carry no per-field weight and no matchkey threshold;
    # the summary must not synthesize either rather than surfacing None.
    mk = MatchkeyConfig(name="mk", type="exact", fields=[MatchkeyField(field="npi")])
    row = _committed_matchkeys_summary(_Cfg([mk]))[0]
    assert row["threshold"] is None
    assert row["fields"][0]["weight"] is None


def test_get_matchkeys_exception_returns_empty_list_not_raise():
    class _Broken:
        def get_matchkeys(self):
            raise RuntimeError("no committed config yet")

    assert _committed_matchkeys_summary(_Broken()) == []
