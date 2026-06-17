from goldenmatch.core.golden import ClusterProvenance, GroupProvenance
from goldenmatch.core.review_queue import ReviewQueue


def _cp():
    g = GroupProvenance(name="addr", columns=["street", "city"], strategy="most_complete",
                        winner_row_id=7, winner_source=None, values={}, tie=False, confidence=1.0)
    return ClusterProvenance(cluster_id=5, cluster_quality="strong", cluster_confidence=0.9, fields={}, groups=[g])


def test_review_item_golden_composition_defaults_none():
    from goldenmatch.core.review_queue import ReviewItem
    it = ReviewItem(job_name="j", id_a=1, id_b=2, score=0.8, explanation="x")
    assert it.golden_composition is None


def test_add_populates_golden_composition_from_cluster_prov():
    rq = ReviewQueue(backend="memory")
    rq.add("job", 10, 11, 0.8, "borderline",
           cluster_provenance_by_id={5: _cp()}, cluster_of={10: 5})
    items = rq.list_pending("job")
    assert items[0].golden_composition is not None
    assert "promoted together from record 7" in items[0].golden_composition


def test_add_no_maps_leaves_composition_none():
    rq = ReviewQueue(backend="memory")
    rq.add("job", 10, 11, 0.8, "borderline")
    assert rq.list_pending("job")[0].golden_composition is None
