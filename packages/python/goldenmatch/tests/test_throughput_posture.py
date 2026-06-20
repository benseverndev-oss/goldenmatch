"""Tests for ThroughputPosture + build_posture (#1083)."""
import pytest
from goldenmatch.core.throughput_verify import ThroughputPosture, build_posture


def test_build_posture_fields():
    p = build_posture(metric="jaccard", recall_target=0.95, similarity=0.8,
                      bands=16, rows=8, n_rows=1000, candidate_pairs=500,
                      verified_pairs=480, semantic_fell_back=False)
    assert isinstance(p, ThroughputPosture)
    assert p.metric == "jaccard" and p.bands == 16 and p.rows_per_band == 8
    assert p.candidate_pairs == 500 and p.verified_pairs == 480
    assert 0.0 <= p.expected_recall <= 1.0
    assert p.reduction_ratio == pytest.approx(500 / (1000 * 999 / 2))
    assert "not a measured F1" in p.notes


def test_posture_notes_flags_semantic_fallback():
    p = build_posture(metric="jaccard", recall_target=0.95, similarity=0.8,
                      bands=16, rows=8, n_rows=10, candidate_pairs=1,
                      verified_pairs=1, semantic_fell_back=True)
    assert "fell back to lexical" in p.notes
