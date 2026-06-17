"""Phase B: per-cluster survivorship audit NL string in golden_records section."""

import json

from goldenmatch.core.golden import ClusterProvenance, GroupProvenance
from goldenmatch.core.lineage import save_lineage


def _prov():
    g = GroupProvenance(
        name="addr",
        columns=["street", "city"],
        strategy="most_complete",
        winner_row_id=7,
        winner_source=None,
        values={"street": "1 Main", "city": "LA"},
        tie=False,
        confidence=1.0,
    )
    return [ClusterProvenance(
        cluster_id=5,
        cluster_quality="strong",
        cluster_confidence=0.9,
        fields={},
        groups=[g],
    )]


def test_golden_records_section_has_groups_and_audit(tmp_path):
    path = save_lineage([], tmp_path, "run", golden_provenance=_prov())
    data = json.loads(path.read_text(encoding="utf-8"))
    rec = data["golden_records"][0]
    assert rec["groups"][0]["name"] == "addr"                # structured (asdict)
    assert "promoted together from record 7" in rec["audit"]  # NL line


def test_plain_provenance_has_no_audit(tmp_path):
    plain = [ClusterProvenance(
        cluster_id=1,
        cluster_quality="strong",
        cluster_confidence=0.0,
        fields={},
        groups=[],
    )]
    path = save_lineage([], tmp_path, "run", golden_provenance=plain)
    rec = json.loads(path.read_text(encoding="utf-8"))["golden_records"][0]
    assert "audit" not in rec                                 # nothing survivorship-specific
