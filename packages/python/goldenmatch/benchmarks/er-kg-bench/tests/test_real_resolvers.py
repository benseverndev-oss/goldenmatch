"""Goldenmatch-free unit test for the real neo4j-graphrag resolver helper."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

import pytest
from erkgbench import metrics  # pyright: ignore[reportMissingImports]
from erkgbench.real_resolvers import (  # pyright: ignore[reportMissingImports]
    SPACY_MODEL,
    neo4j_graphrag_exact_clusters,
    neo4j_graphrag_fuzzy_clusters,
    neo4j_graphrag_spacy_clusters,
)

DATASET = _BENCH_ROOT / "dataset" / "records.csv"


def _load():
    items, entity_ids, classes = [], [], []
    with DATASET.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            items.append((int(row["record_id"]), row["mention"], row["entity_type"]))
            entity_ids.append(row["entity_id"])
            classes.append(row["failure_class"])
    return items, entity_ids, classes


def test_reproduces_poc_f1():
    items, entity_ids, classes = _load()
    clustering = neo4j_graphrag_fuzzy_clusters(items)
    f1 = metrics.score_by_class(entity_ids, classes, clustering)["__overall__"].f1
    # 0.469 after the Phase-2 _merge_overlapping fix (was 0.470 on the malformed,
    # duplicate-id partition `_consolidate_sets` produced; the fix is a valid-partition
    # correctness change, -0.001 F1). The +6.6pp-over-the-model finding is unchanged.
    assert round(f1, 3) == 0.469
    # valid partition: every record id appears exactly once (the bug _merge_overlapping
    # fixes had 12 duplicate ids from _consolidate_sets' single-pass overlap).
    flat = [i for c in clustering for i in c]
    assert sorted(flat) == sorted(i for i, _m, _t in items)


def test_merge_overlapping_unifies_consolidate_sets_overlap():
    from erkgbench.real_resolvers import _merge_overlapping  # pyright: ignore[reportMissingImports]
    # _consolidate_sets' single pass can emit overlapping sets ({3} in both); merging
    # must unify them into one disjoint cluster (what the real graph-merges produce).
    out = _merge_overlapping([{1, 2, 3}, {3, 4}])
    assert len(out) == 1 and out[0] == {1, 2, 3, 4}
    # already-disjoint input is a no-op (so the sparse fuzzy graph is unaffected).
    out2 = sorted(sorted(s) for s in _merge_overlapping([{1, 2}, {3, 4}]))
    assert out2 == [[1, 2], [3, 4]]

def test_no_empty_mentions_in_corpus():
    items, _, _ = _load()
    assert all(m and m.strip() for _i, m, _t in items)

def test_empty_mention_is_skipped_faithfully():
    # the real resolver skips entities whose combined_text is empty; a blank mention
    # must become a singleton, never merged.
    items = [(0, "Acme Inc", "org"), (1, "Acme Inc", "org"), (2, "", "org")]
    clustering = neo4j_graphrag_fuzzy_clusters(items)
    assert [2] in clustering                     # empty -> singleton
    assert any(set(c) == {0, 1} for c in clustering)  # identical non-empty merge


# -- SinglePropertyExactMatchResolver (validated model of the Cypher) ----------

def test_exact_reproduces_observed_f1():
    items, entity_ids, classes = _load()
    clustering = neo4j_graphrag_exact_clusters(items)
    f1 = metrics.score_by_class(entity_ids, classes, clustering)["__overall__"].f1
    # Exact `name` equality per label recalls almost nothing on real surface-form
    # variation (R~0.03); high precision, near-zero recall. Pinned once observed.
    assert round(f1, 3) == 0.066

def test_exact_is_deterministic():
    items, _, _ = _load()
    assert metrics.clusterings_equal(
        neo4j_graphrag_exact_clusters(items), neo4j_graphrag_exact_clusters(items)
    )

def test_exact_merges_identical_skips_null_and_no_normalization():
    # exact merges byte-identical names per label; null/empty skipped; case-SENSITIVE
    # (no normalization) -> "Acme" and "acme" stay distinct, unlike the fuzzy resolver.
    items = [
        (0, "Acme Inc", "org"), (1, "Acme Inc", "org"),  # identical -> merge
        (2, "acme inc", "org"),                            # different case -> own cluster
        (3, "", "org"),                                    # null/empty -> singleton
        (4, "Acme Inc", "person"),                         # same name, different label -> not merged with 0/1
    ]
    clustering = neo4j_graphrag_exact_clusters(items)
    assert any(set(c) == {0, 1} for c in clustering)   # identical, same label -> merged
    assert [2] in clustering                            # case differs -> not merged
    assert [3] in clustering                            # empty -> singleton
    assert [4] in clustering                            # different label -> not merged


# -- SpaCySemanticMatchResolver (real-inproc; needs the spaCy vector model) -----

# Observed real F1 of the spaCy resolver on the corpus. None until the first CI run
# (which has the model) reports it; set it to lock the value against silent drift.
# Read it from the regenerated results/RESULTS.md `neo4j-graphrag(spacy)*` row or the
# skip message below, then pin here (Task 8).
_SPACY_F1_PIN: float | None = None


def _spacy_model_available() -> bool:
    """True only when both spaCy and the vector model are importable. Locally the
    model is usually absent, so the spaCy parity test SKIPS; CI installs the model
    (`python -m spacy download en_core_web_lg`) and runs it for real."""
    try:
        import importlib.util
        if importlib.util.find_spec("spacy") is None:
            return False
        return importlib.util.find_spec(SPACY_MODEL) is not None
    except Exception:
        return False


def test_spacy_reproduces_observed_f1():
    if not _spacy_model_available():
        pytest.skip(f"spaCy model {SPACY_MODEL} not installed (CI-only real row)")
    items, entity_ids, classes = _load()
    clustering = neo4j_graphrag_spacy_clusters(items)
    # Full partition: every record appears exactly once (no dropped/duplicated ids).
    flat = [i for c in clustering for i in c]
    assert sorted(flat) == sorted(i for i, _m, _t in items)
    f1 = metrics.score_by_class(entity_ids, classes, clustering)["__overall__"].f1
    # OBSERVE-THEN-PIN: until _SPACY_F1_PIN is set, surface the observed F1 (so the
    # first CI run reports it) without asserting a guessed value.
    if _SPACY_F1_PIN is None:
        pytest.skip(f"spaCy F1 observed = {round(f1, 3)} -- set _SPACY_F1_PIN to lock it")
    assert round(f1, 3) == _SPACY_F1_PIN


def test_spacy_is_deterministic():
    if not _spacy_model_available():
        pytest.skip(f"spaCy model {SPACY_MODEL} not installed (CI-only real row)")
    items, _, _ = _load()
    assert metrics.clusterings_equal(
        neo4j_graphrag_spacy_clusters(items), neo4j_graphrag_spacy_clusters(items)
    )
