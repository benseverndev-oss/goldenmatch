"""SP-B1 substrate config surface: SubstrateConfig / apply() / for_profile (pure, box-safe)."""
from __future__ import annotations

import dataclasses
import os

import pytest
from goldengraph.config import (
    MANAGED_ENV_VARS,
    CorpusProfile,
    SubstrateConfig,
    for_profile,
    profile_corpus,
)


# --- Task 1: dataclass + validation -----------------------------------------------------------------
def test_config_defaults_construct():
    c = SubstrateConfig()
    assert c.xdoc_key == "" and c.chunk_extract is False and c.extractor == "api"
    assert c.chunk_sentences == 6 and c.chunk_overlap == 2


def test_config_rejects_bad_xdoc_key():
    with pytest.raises(ValueError):
        SubstrateConfig(xdoc_key="nope")


def test_config_rejects_bad_extractor():
    with pytest.raises(ValueError):
        SubstrateConfig(extractor="llm")  # engine alias, but config demands canonical "api"


def test_config_rejects_overlap_ge_sentences():
    with pytest.raises(ValueError):
        SubstrateConfig(chunk_sentences=4, chunk_overlap=4)


def test_config_is_frozen():
    c = SubstrateConfig()
    with pytest.raises(Exception):
        c.xdoc_key = "name_ci"  # frozen dataclass


# --- Task 2: MANAGED_ENV_VARS + to_env ----------------------------------------------------------------
def test_default_config_is_noop_env():
    env = SubstrateConfig().to_env()
    assert env["GOLDENGRAPH_XDOC_KEY"] == ""
    for k in ("GOLDENGRAPH_CHUNK_EXTRACT", "GOLDENGRAPH_ENTITY_TYPE_CANON",
              "GOLDENGRAPH_SCHEMA_CANON", "GOLDENGRAPH_RELATION_REPROMPT",
              "GOLDENGRAPH_REBEL_FUSE", "GOLDENGRAPH_EXTRACT_RECALL"):
        assert env[k] == "0"
    assert env["GOLDENGRAPH_EXTRACTOR"] == "api"
    assert env["GOLDENGRAPH_ENTITY_TYPE_VOCAB"] == "" and env["GOLDENGRAPH_RELATION_VOCAB"] == ""
    assert env["GOLDENGRAPH_CHUNK_SENTENCES"] == "6" and env["GOLDENGRAPH_CHUNK_OVERLAP"] == "2"


def test_to_env_maps_types():
    c = SubstrateConfig(xdoc_key="name_ci_type", chunk_extract=True, entity_type_canon=True,
                        entity_type_vocab=("person", "organization"), schema_canon=True,
                        relation_vocab=("acquired", "works_at"))
    env = c.to_env()
    assert env["GOLDENGRAPH_XDOC_KEY"] == "name_ci_type"
    assert env["GOLDENGRAPH_CHUNK_EXTRACT"] == "1"
    assert env["GOLDENGRAPH_ENTITY_TYPE_VOCAB"] == "person,organization"
    assert env["GOLDENGRAPH_RELATION_VOCAB"] == "acquired,works_at"


def test_schema_discover_guard_in_managed_and_off():
    assert "GOLDENGRAPH_SCHEMA_DISCOVER" in MANAGED_ENV_VARS
    assert SubstrateConfig().to_env()["GOLDENGRAPH_SCHEMA_DISCOVER"] == "0"


def test_to_env_keys_equal_managed():
    # to_env must be TOTAL over the managed set (leak-proof invariant).
    assert set(SubstrateConfig().to_env()) == set(MANAGED_ENV_VARS)


# --- Task 3: apply() ----------------------------------------------------------------------------------
def _clear_managed():
    # Box-safe test isolation: pops all managed keys to test from a known-clean state. Destructive to
    # ambient GOLDENGRAPH_* with no restore -- fine for CI/box (those vars are unset there), not for a
    # shell that has them set. The two tests that need a prior value use try/finally to restore.
    for k in MANAGED_ENV_VARS:
        os.environ.pop(k, None)


def test_apply_sets_and_restores_absent_key():
    _clear_managed()
    c = SubstrateConfig(xdoc_key="name_ci")
    with c.apply():
        assert os.environ["GOLDENGRAPH_XDOC_KEY"] == "name_ci"
        assert os.environ["GOLDENGRAPH_CHUNK_EXTRACT"] == "0"
    # keys absent before must be GONE after (not left as "")
    assert "GOLDENGRAPH_XDOC_KEY" not in os.environ
    assert "GOLDENGRAPH_CHUNK_EXTRACT" not in os.environ


def test_apply_restores_prior_value():
    _clear_managed()
    os.environ["GOLDENGRAPH_XDOC_KEY"] = "name"          # a pre-existing value
    try:
        with SubstrateConfig(xdoc_key="name_ci").apply():
            assert os.environ["GOLDENGRAPH_XDOC_KEY"] == "name_ci"
        assert os.environ["GOLDENGRAPH_XDOC_KEY"] == "name"   # restored to prior
    finally:
        _clear_managed()


def test_apply_forces_schema_discover_off_and_restores():
    _clear_managed()
    os.environ["GOLDENGRAPH_SCHEMA_DISCOVER"] = "1"       # ambient discovery ON
    try:
        with SubstrateConfig(schema_canon=True, relation_vocab=("acquired",)).apply():
            assert os.environ["GOLDENGRAPH_SCHEMA_DISCOVER"] == "0"   # forced off during build
        assert os.environ["GOLDENGRAPH_SCHEMA_DISCOVER"] == "1"       # restored after
    finally:
        _clear_managed()


# --- Task 4: CorpusProfile + profile_corpus -----------------------------------------------------------
def test_profile_corpus_signals():
    docs = ["One. Two. Three.", "Solo sentence"]  # 3 sentences + 1 = mean 2.0
    p = profile_corpus(docs)
    assert p.n_docs == 2
    assert p.mean_sentences_per_doc == 2.0
    assert p.mean_chars_per_doc == (len(docs[0]) + len(docs[1])) / 2


def test_profile_corpus_empty():
    p = profile_corpus([])
    assert p == CorpusProfile(n_docs=0, mean_sentences_per_doc=0.0, mean_chars_per_doc=0.0)


# --- Task 5: for_profile rule table -------------------------------------------------------------------
def _short():
    return CorpusProfile(n_docs=5, mean_sentences_per_doc=2.0, mean_chars_per_doc=40.0)


def _dense():
    return CorpusProfile(n_docs=19, mean_sentences_per_doc=20.0, mean_chars_per_doc=900.0)


def test_for_profile_short_docs_no_chunking():
    c = for_profile(_short())
    assert c.xdoc_key == "name_ci"     # base near-universal relational win
    assert c.chunk_extract is False    # short docs -> chunking off


def test_for_profile_dense_docs_enables_chunking():
    c = for_profile(_dense())
    assert c.chunk_extract is True and c.chunk_sentences == 6 and c.chunk_overlap == 2
    assert c.xdoc_key == "name_ci"


def test_for_profile_homographs_override():
    c = for_profile(_dense(), expect_homographs=True)
    assert c.xdoc_key == "name_ci_type"      # homograph OVERRIDES base name_ci
    assert c.entity_type_canon is True


def test_for_profile_known_schema():
    c = for_profile(_dense(), has_known_schema=True, relation_vocab=("acquired", "works_at"))
    assert c.schema_canon is True and c.relation_vocab == ("acquired", "works_at")


def test_for_profile_never_selects_refuted():
    for kw in ({}, {"expect_homographs": True}, {"has_known_schema": True}):
        c = for_profile(_dense(), **kw)
        assert c.relation_reprompt is False and c.rebel_fuse is False and c.extract_recall is False


# --- Task 6: cross-contract consistency (skip-if-unimportable) ---------------------------------------
def test_config_fields_cover_known_levers():
    # SP-A ships erkgbench.substrate_eval.KNOWN_LEVERS (lever-name -> env). Every such lever must be a
    # SubstrateConfig field, keeping the SP-A contract and the SP-B object in sync. Skips if erkgbench
    # isn't importable on this branch (SP-A #1371 not yet merged/rebased in).
    try:
        from erkgbench.substrate_eval import KNOWN_LEVERS
    except Exception:
        pytest.skip("erkgbench.substrate_eval.KNOWN_LEVERS not importable on this branch (pre-#1371)")
    field_names = {f.name for f in dataclasses.fields(SubstrateConfig)}
    missing = set(KNOWN_LEVERS) - field_names
    assert not missing, f"KNOWN_LEVERS not covered by SubstrateConfig fields: {missing}"
