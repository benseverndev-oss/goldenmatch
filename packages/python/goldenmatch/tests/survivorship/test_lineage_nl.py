from goldenmatch.core.golden import ClusterProvenance, GroupProvenance, FieldProvenance
from goldenmatch.core.lineage import (
    render_cluster_provenance_nl, render_group_provenance_line, render_field_condition_line,
)


def test_group_line_exact():
    gp = GroupProvenance(name="mailing_address", columns=["street", "city", "state", "zip"],
                         strategy="most_complete", winner_row_id=7, winner_source="crm",
                         values={}, tie=False, confidence=0.75)
    assert (render_group_provenance_line(gp)
            == "street, city, state, zip promoted together from record 7 via most_complete (group 'mailing_address')")


def test_condition_line_exact():
    fp = FieldProvenance(value="x", source_row_id=11, strategy="most_recent", confidence=1.0,
                         condition="state in ['CA','NY']")
    assert render_field_condition_line("phone", fp) == "phone used most_recent because state in ['CA','NY']"


def test_validation_suffix_only_when_dropped():
    fp_clean = FieldProvenance(value="x", source_row_id=1, strategy="most_complete", confidence=1.0)
    assert render_field_condition_line("phone", fp_clean) is None
    fp_drop = FieldProvenance(value="x", source_row_id=1, strategy="most_complete", confidence=1.0,
                              validator="nanp", dropped_invalid=2)
    assert "2 candidate(s) dropped by nanp" in render_field_condition_line("phone", fp_drop)


def test_cluster_nl_combines_group_and_condition():
    cp = ClusterProvenance(
        cluster_id=1, cluster_quality="strong", cluster_confidence=0.9,
        fields={"phone": FieldProvenance(value="5553334444", source_row_id=11,
                                         strategy="most_recent", confidence=1.0,
                                         condition="state in ['CA','NY']")},
        groups=[GroupProvenance(name="mailing_address", columns=["street", "city", "state", "zip"],
                                strategy="most_complete", winner_row_id=7, winner_source="crm",
                                values={}, tie=False, confidence=0.75)],
    )
    text = render_cluster_provenance_nl(cp)
    assert "promoted together from record 7 via most_complete (group 'mailing_address')" in text
    assert "phone used most_recent because state in ['CA','NY']" in text


def test_empty_provenance_renders_empty_string():
    cp = ClusterProvenance(cluster_id=0, cluster_quality="strong", cluster_confidence=0.0)
    assert render_cluster_provenance_nl(cp) == ""


def test_suffix_only_line_exact():
    fp = FieldProvenance(value="x", source_row_id=1, strategy="most_complete", confidence=1.0,
                         validator="nanp", dropped_invalid=2)
    assert render_field_condition_line("phone", fp) == "phone: 2 candidate(s) dropped by nanp"
