"""Throughput tier column-selection robustness (#1086).

Real corpus text (FineWeb/C4 web crawl) is routinely mis-classified by the
semantic col_type heuristics -- a long document that embeds street names lands
as "address", not "description". The throughput tier must still sketch on the
longest text-bearing column rather than raise ThroughputNotApplicableError.
"""
import pytest
from goldenmatch.core.autoconfig import ColumnProfile, _throughput_blocking
from goldenmatch.core.throughput_verify import ThroughputNotApplicableError


def _p(name: str, col_type: str, avg_len: float, card: float = 1.0) -> ColumnProfile:
    return ColumnProfile(
        name=name, dtype="str", col_type=col_type, confidence=0.7,
        avg_len=avg_len, cardinality_ratio=card,
    )


def _sketch_col(blk) -> str:
    return blk.lsh.column if blk.lsh is not None else blk.simhash.column


def test_falls_back_on_misclassified_corpus_text():
    # Measured: real FineWeb doc text classifies as "address" (avg_len ~2.9k).
    profiles = [_p("doc_id", "identifier", 10), _p("text", "address", 2916)]
    blk = _throughput_blocking(profiles)
    assert blk.strategy in ("lsh", "simhash")
    assert _sketch_col(blk) == "text"


def test_falls_back_on_long_text_typed_as_identifier():
    # At scale, a unique long-text column can be classified "identifier" — the
    # fallback keys on avg_len, not the label, so a long doc column still sketches
    # while the short doc_id identifier is filtered by the length floor.
    profiles = [_p("doc_id", "identifier", 11), _p("text", "identifier", 3018)]
    blk = _throughput_blocking(profiles)
    assert _sketch_col(blk) == "text"


def test_prefers_semantic_text_column_when_present():
    profiles = [_p("doc_id", "identifier", 10), _p("body", "description", 400)]
    blk = _throughput_blocking(profiles)
    assert blk.strategy in ("lsh", "simhash")


def test_refuses_when_only_structured_columns():
    # No long string column -> genuinely no sketch target.
    profiles = [_p("id", "identifier", 8), _p("age", "numeric", 2), _p("z", "zip", 5)]
    with pytest.raises(ThroughputNotApplicableError):
        _throughput_blocking(profiles)
