"""build_batch stamps source_refs on entity nodes (mirroring edges) for per-doc alignment."""
from goldengraph.extract import Extraction, Mention, Relationship
from goldengraph.ingest import build_batch
from goldengraph.resolve import ResolvedEntity


def test_build_batch_stamps_entity_source_refs():
    extraction = Extraction(
        mentions=[Mention(name="Amazon", typ="org"), Mention(name="Jeff Bezos", typ="person")],
        relationships=[Relationship(subj=0, predicate="founded_by", obj=1)],
    )
    entities = [
        ResolvedEntity(local_id=0, canonical_name="Amazon", typ="org",
                       surface_names=["Amazon"], record_keys=["k:amazon"], member_idx=[0]),
        ResolvedEntity(local_id=1, canonical_name="Jeff Bezos", typ="person",
                       surface_names=["Jeff Bezos"], record_keys=["k:bezos"], member_idx=[1]),
    ]
    batch = build_batch(extraction, entities, at=1, source_ref="docA")
    assert all(e["source_refs"] == ["docA"] for e in batch["entities"])


def test_build_batch_no_source_ref_empty_refs():
    extraction = Extraction(mentions=[Mention(name="X", typ="org")], relationships=[])
    entities = [ResolvedEntity(local_id=0, canonical_name="X", typ="org",
                               surface_names=["X"], record_keys=[], member_idx=[0])]
    batch = build_batch(extraction, entities, at=1)  # no source_ref
    assert all(e["source_refs"] == [] for e in batch["entities"])
