"""SP-C substrate config suggester: parse / propose / self-verify (pure, box-safe with fakes)."""
from __future__ import annotations

from collections import namedtuple

from erkgbench import substrate_suggest as ss
from goldengraph.config import CorpusProfile, for_profile, profile_corpus


# --- Task 1: _parse_flags -----------------------------------------------------------------------------
def test_parse_flags_clean_json():
    f = ss._parse_flags('{"expect_homographs": true, "has_known_schema": false, '
                        '"relation_vocab": ["acquired", "works_at"], "entity_type_vocab": ["person"]}')
    assert f.expect_homographs is True and f.has_known_schema is False
    assert f.relation_vocab == ("acquired", "works_at") and f.entity_type_vocab == ("person",)


def test_parse_flags_fenced():
    raw = "```json\n{\"expect_homographs\": true}\n```"
    assert ss._parse_flags(raw).expect_homographs is True


def test_parse_flags_garbage_defaults():
    for raw in ("not json", "", "{oops", "[1,2,3]"):
        f = ss._parse_flags(raw)
        assert f == ss.CorpusFlags()  # all-off default, never raises


def test_parse_flags_drops_unknown_and_coerces():
    f = ss._parse_flags('{"expect_homographs": 1, "bogus": "x", "relation_vocab": "acquired"}')
    assert f.expect_homographs is True          # truthy coerced to bool
    assert f.relation_vocab == ()               # a non-list vocab is ignored, not split


# --- Task 2: propose_corpus_flags ---------------------------------------------------------------------
def test_propose_flags_calls_chat_once_and_parses():
    calls = []

    def fake_chat(prompt):
        calls.append(prompt)
        return '{"expect_homographs": true, "entity_type_vocab": ["person", "organization"]}'

    f = ss.propose_corpus_flags(["Apple grows.", "Apple ships iPhones."], chat=fake_chat)
    assert len(calls) == 1
    assert "Apple grows." in calls[0]  # the sample is in the prompt
    assert f.expect_homographs is True and f.entity_type_vocab == ("person", "organization")


def test_propose_flags_bad_read_is_default():
    f = ss.propose_corpus_flags(["doc"], chat=lambda p: "sorry I cannot help")
    assert f == ss.CorpusFlags()


# --- Task 3: suggest_substrate_config self-verify -----------------------------------------------------
_Doc = namedtuple("_Doc", "text")


def _sc(rel_f1):
    """A presence=None scorecard (engineered path) with a given relational F1 -> _score == rel_f1."""
    return {"presence": None, "relational": {"f1": rel_f1, "recall": rel_f1, "precision": 1.0},
            "connectivity": {"coverage": None, "f1": None, "edge_recall": 0.9},
            "coherence": {"components": 1, "largest_fraction": 1.0}}


def _short_profile():
    return CorpusProfile(n_docs=4, mean_sentences_per_doc=2.0, mean_chars_per_doc=40.0)


def _fake_chat_homograph(_prompt):
    return '{"expect_homographs": true, "entity_type_vocab": ["person", "organization"]}'


def _bykey(name_ci_score, name_ci_type_score):
    """Fake build_and_score keyed on config.xdoc_key so accept/fallback is deterministic."""
    def fake(config, dataset):
        return _sc(name_ci_type_score if config.xdoc_key == "name_ci_type" else name_ci_score)
    return fake


def test_suggest_accepts_when_proposed_beats_baseline():
    docs = [_Doc("Apple grows."), _Doc("Apple Inc ships.")]
    res = ss.suggest_substrate_config(
        docs, gold=[], qid_aliases=None, profile=_short_profile(),
        build_and_score=_bykey(0.40, 0.70), chat=_fake_chat_homograph)
    assert res.accepted is True and res.config.xdoc_key == "name_ci_type"


def test_suggest_falls_back_when_proposed_worse():
    docs = [_Doc("a"), _Doc("b")]
    res = ss.suggest_substrate_config(
        docs, gold=[], qid_aliases=None, profile=_short_profile(),
        build_and_score=_bykey(0.70, 0.40), chat=_fake_chat_homograph)
    assert res.accepted is False
    assert res.config == for_profile(_short_profile())   # exactly the baseline, no vocab stamped


def test_suggest_homograph_stamps_type_and_vocab():
    docs = [_Doc("a"), _Doc("b")]
    res = ss.suggest_substrate_config(
        docs, gold=[], qid_aliases=None, profile=_short_profile(),
        build_and_score=_bykey(0.40, 0.70), chat=_fake_chat_homograph)
    assert res.config.xdoc_key == "name_ci_type" and res.config.entity_type_canon is True
    assert res.config.entity_type_vocab == ("person", "organization")


def test_suggest_schema_only_vocab_not_stamped():
    # expect_homographs False -> canon OFF -> entity_type_vocab must NOT be stamped even though accepted
    def chat(_p):
        return '{"has_known_schema": true, "relation_vocab": ["acquired"], "entity_type_vocab": ["person"]}'
    docs = [_Doc("a"), _Doc("b")]
    # proposed differs from baseline via schema_canon (xdoc_key stays name_ci in BOTH) -> key the fake on
    # schema_canon instead of xdoc_key so proposed scores higher.
    def fake(config, dataset):
        return _sc(0.70 if config.schema_canon else 0.40)
    res = ss.suggest_substrate_config(docs, gold=[], qid_aliases=None, profile=_short_profile(),
                                      build_and_score=fake, chat=chat)
    assert res.accepted is True and res.config.schema_canon is True
    assert res.config.entity_type_canon is False and res.config.entity_type_vocab == ()


def test_suggest_bad_llm_read_is_safe():
    docs = [_Doc("a"), _Doc("b")]
    res = ss.suggest_substrate_config(
        docs, gold=[], qid_aliases=None, profile=_short_profile(),
        build_and_score=_bykey(0.50, 0.50), chat=lambda p: "garbage")
    # flags default -> proposed == baseline -> equal scores -> not accepted -> baseline
    assert res.accepted is False and res.config == for_profile(_short_profile())


# --- Task 4: no-gold MCP surface ----------------------------------------------------------------------
def test_mcp_unverified_returns_config_and_flag():
    out = ss.suggest_substrate_config_unverified(
        ["Apple grows.", "Apple Inc ships."], chat=_fake_chat_homograph)
    assert out["verified"] is False
    assert out["config"].xdoc_key == "name_ci_type"           # homograph -> name_ci_type
    assert out["config"].entity_type_vocab == ("person", "organization")
    assert out["flags"].expect_homographs is True and "verify" in out["note"].lower()


def test_mcp_unverified_bad_read_is_baseline():
    out = ss.suggest_substrate_config_unverified(["doc"], chat=lambda p: "nope")
    assert out["config"] == for_profile(profile_corpus(["doc"]))  # baseline, no vocab
