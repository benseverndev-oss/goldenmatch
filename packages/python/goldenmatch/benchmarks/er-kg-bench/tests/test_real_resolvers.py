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
    cognee_clusters,
    graphiti_clusters,
    graphrag_clusters,
    lightrag_clusters,
    neo4j_graphrag_exact_clusters,
    neo4j_graphrag_fuzzy_clusters,
    neo4j_graphrag_spacy_clusters,
)

DATASET = _BENCH_ROOT / "dataset" / "records.csv"

# Observed real F1 on the corpus, pinned from a CI run (Linux) so a local Windows
# run that gives the same pure-exact-bucket result also passes. Set to None to
# observe-then-pin (the test SKIPS reporting the value until pinned).
# Both = 0.066: an exact key (GraphRAG upper-fold / Cognee uuid5) recalls almost
# nothing on this surface-form-variation corpus -- the variants differ by more than
# case/whitespace, so the precise normalization doesn't move F1 (same 0.066 as
# neo4j-graphrag(exact)). Pure deterministic exact-bucket on string keys -> platform-
# stable, so the observed local Windows value matches Linux CI.
_GRAPHRAG_F1_PIN: float | None = 0.066
_COGNEE_F1_PIN: float | None = 0.066


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


# -- GraphRAG + Cognee (validated reproductions of exact-key rules) ------------

def test_graphrag_key_is_faithful():
    from erkgbench.real_resolvers import _graphrag_key
    # clean_str(name.upper()): upper + edge-strip + html-unescape + control-char
    # strip, NO internal-whitespace collapse, NO quote strip.
    assert _graphrag_key("  Acme &amp; Co  ") == "ACME & CO"
    assert _graphrag_key("New  York") != _graphrag_key("New York")   # 2 spaces NOT collapsed
    assert _graphrag_key("acme") == _graphrag_key("ACME")            # case-folded (clustering-equiv)
    assert _graphrag_key('the "best"') == 'THE "BEST"'              # quotes NOT stripped


def test_graphrag_reproduces_observed_f1():
    items, entity_ids, classes = _load()
    clustering = graphrag_clusters(items)
    flat = [i for c in clustering for i in c]
    assert sorted(flat) == sorted(i for i, _m, _t in items)          # full partition
    f1 = metrics.score_by_class(entity_ids, classes, clustering)["__overall__"].f1
    if _GRAPHRAG_F1_PIN is None:
        pytest.skip(f"GraphRAG F1 observed = {round(f1, 3)} -- set _GRAPHRAG_F1_PIN to lock it")
    assert round(f1, 3) == _GRAPHRAG_F1_PIN


def test_cognee_key_fixes_the_modeled_bug():
    from erkgbench.real_resolvers import _cognee_key
    # real generate_node_id: lower -> " "->"_" -> strip "'". The old model used
    # _norm (lower + whitespace-collapse) citing generate_node_NAME -- this is the FIX.
    assert _cognee_key("O'Brien") == _cognee_key("OBrien")           # apostrophe stripped
    assert _cognee_key("John  Smith") != _cognee_key("John Smith")   # 2 spaces -> "__" != "_"
    assert _cognee_key("Acme") == _cognee_key("acme")                # lowercased


def test_cognee_reproduces_observed_f1():
    items, entity_ids, classes = _load()
    clustering = cognee_clusters(items)
    flat = [i for c in clustering for i in c]
    assert sorted(flat) == sorted(i for i, _m, _t in items)          # full partition
    f1 = metrics.score_by_class(entity_ids, classes, clustering)["__overall__"].f1
    if _COGNEE_F1_PIN is None:
        pytest.skip(f"Cognee F1 observed = {round(f1, 3)} -- set _COGNEE_F1_PIN to lock it")
    assert round(f1, 3) == _COGNEE_F1_PIN


# -- SpaCySemanticMatchResolver (real-inproc; needs the spaCy vector model) -----

# Observed real F1 of the spaCy resolver on the corpus, pinned from the CI run
# (en_core_web_lg installed): P 0.699 / R 0.281 / F1 0.401. Locks against drift.
_SPACY_F1_PIN: float | None = 0.401


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


# -- LightRAG (real-inproc; needs lightrag-hku) ---------------------------------

# Observed real F1 pinned from CI (lightrag-hku installed, run 27703432403): P 0.875
# / R 0.034 / F1 0.066 -- faithful to LightRAG's case-sensitive exact key (same 0.066
# as the exact family; the key recalls ~nothing on surface-form variation). Pure
# exact-bucket on the real normalized key -> platform-stable.
_LIGHTRAG_F1_PIN: float | None = 0.066


def _have(mod: str) -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def test_lightrag_reproduces_observed_f1():
    if not _have("lightrag"):
        pytest.skip("lightrag-hku not installed (CI-only real-inproc row)")
    items, entity_ids, classes = _load()
    clustering = lightrag_clusters(items)
    flat = [i for c in clustering for i in c]
    assert sorted(flat) == sorted(i for i, _m, _t in items)  # full partition
    f1 = metrics.score_by_class(entity_ids, classes, clustering)["__overall__"].f1
    if _LIGHTRAG_F1_PIN is None:
        pytest.skip(f"LightRAG F1 observed = {round(f1, 3)} -- set _LIGHTRAG_F1_PIN to lock it")
    assert round(f1, 3) == _LIGHTRAG_F1_PIN


def test_lightrag_key_is_case_sensitive():
    # LightRAG's real normalize_extracted_info applies NO lower/upper, so "Apple" and
    # "apple" are DISTINCT entities (unlike the old _norm model that lowercased).
    if not _have("lightrag"):
        pytest.skip("lightrag-hku not installed (CI-only real-inproc row)")
    items = [(0, "Apple", "org"), (1, "apple", "org"), (2, "Apple", "org")]
    clustering = lightrag_clusters(items)
    assert any(set(c) == {0, 2} for c in clustering)  # identical case -> merged
    assert [1] in clustering                          # different case -> own cluster


def test_lightrag_is_deterministic():
    if not _have("lightrag"):
        pytest.skip("lightrag-hku not installed (CI-only real-inproc row)")
    items, _, _ = _load()
    assert metrics.clusterings_equal(lightrag_clusters(items), lightrag_clusters(items))


# -- Graphiti (real-inproc deterministic floor; needs graphiti-core) ------------

# Observed real F1 pinned from CI (graphiti-core installed, run 27703432403): P 0.909
# / R 0.049 / F1 0.093 -- the deterministic MinHash/Jaccard>=0.9 floor recalls a touch
# more than a pure exact key (abbr 0.071, nick 0.038, temp 0.5) but is still low
# without the LLM fallback. BLAKE2b MinHash + fixed permutations -> deterministic /
# platform-stable.
_GRAPHITI_F1_PIN: float | None = 0.093


def test_graphiti_reproduces_observed_f1():
    if not _have("graphiti_core"):
        pytest.skip("graphiti-core not installed (CI-only real-inproc row)")
    items, entity_ids, classes = _load()
    clustering = graphiti_clusters(items)
    flat = [i for c in clustering for i in c]
    assert sorted(flat) == sorted(i for i, _m, _t in items)  # full partition
    f1 = metrics.score_by_class(entity_ids, classes, clustering)["__overall__"].f1
    if _GRAPHITI_F1_PIN is None:
        pytest.skip(f"Graphiti F1 observed = {round(f1, 3)} -- set _GRAPHITI_F1_PIN to lock it")
    assert round(f1, 3) == _GRAPHITI_F1_PIN


def test_graphiti_floor_merges_exact_and_close_fuzzy():
    # The deterministic floor merges exact-normalized names and MinHash/Jaccard>=0.9
    # near-duplicates; unrelated names stay separate (no LLM). Long names so the
    # entropy/min-length gate (>=6 chars, >=2 tokens) doesn't punt them.
    if not _have("graphiti_core"):
        pytest.skip("graphiti-core not installed (CI-only real-inproc row)")
    items = [
        (0, "International Business Machines", "org"),
        (1, "international business machines", "org"),  # case-only -> exact-normalized merge
        (2, "Completely Unrelated Organization", "org"),
    ]
    clustering = graphiti_clusters(items)
    assert any(set(c) == {0, 1} for c in clustering)
    assert [2] in clustering


def test_graphiti_is_deterministic():
    if not _have("graphiti_core"):
        pytest.skip("graphiti-core not installed (CI-only real-inproc row)")
    items, _, _ = _load()
    assert metrics.clusterings_equal(graphiti_clusters(items), graphiti_clusters(items))
