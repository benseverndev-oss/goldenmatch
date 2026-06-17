"""E3 tests: GroupProvenance + extended FieldProvenance + ClusterProvenance.groups."""
from dataclasses import asdict

from goldenmatch.core.golden import ClusterProvenance, FieldProvenance, GroupProvenance


def test_group_provenance_serializes_via_asdict():
    gp = GroupProvenance(
        name="addr",
        columns=["street", "city"],
        strategy="most_complete",
        winner_row_id=7,
        winner_source="crm",
        values={"street": "1 Main", "city": "LA"},
        tie=False,
        confidence=1.0,
    )
    cp = ClusterProvenance(
        cluster_id=1,
        cluster_quality="strong",
        cluster_confidence=0.9,
        groups=[gp],
    )
    d = asdict(cp)
    assert d["groups"][0]["name"] == "addr"


def test_field_provenance_new_fields_default():
    fp = FieldProvenance(value="x", source_row_id=1, strategy="most_complete", confidence=1.0)
    assert fp.condition is None and fp.validator is None and fp.dropped_invalid == 0
