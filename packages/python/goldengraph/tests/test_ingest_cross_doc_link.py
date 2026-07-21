"""Cross-document linking unit tests -- offline, deterministic. Exercises the
COMPOUND-key linker: per-entity feature rows (name/type/aliases + graph
neighborhood) are fed to a matcher (goldenmatch by default; a deterministic stub
here). _record_key is stubbed, the fake store serves entities() + query(), so no
native PyStore and no goldenmatch are needed. Locks (a) the neighborhood feature
construction and (b) the key-injection that unions cross-document entities."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Package rebinds `goldengraph.ingest` to the ingest() fn; grab the MODULE.
ingest = importlib.import_module("goldengraph.ingest")


@pytest.fixture(autouse=True)
def _stub_record_key(monkeypatch):
    monkeypatch.setattr(ingest, "_record_key", lambda name, typ: f"{typ}|{name}")


class _FakeSlice:
    def __init__(self, ents, edges):
        self._ents = ents
        self._edges = edges

    def entities(self):
        return self._ents

    def query(self, ids, hops):
        return {"entities": self._ents, "edges": self._edges}


class _FakeStore:
    def __init__(self, ents, edges=()):
        self._ents = ents
        self._edges = list(edges)

    def as_of(self, valid_t, tx_t):
        return _FakeSlice(self._ents, self._edges)


def _existing(eid, name, typ, *surfaces):
    return {"entity_id": eid, "canonical_name": name, "typ": typ,
            "surface_names": list(surfaces) or [name]}


def _batch_entity(lid, name, typ, *surfaces):
    surfaces = list(surfaces) or [name]
    return {"local_id": lid, "canonical_name": name, "typ": typ,
            "surface_names": surfaces, "record_keys": [f"{typ}|{s}" for s in surfaces]}


# --- feature construction -------------------------------------------------

def test_existing_features_fold_in_neighborhood():
    ents = [_existing(1, "Nabbes", "PERSON"), _existing(2, "Worcester College", "ORG")]
    edges = [{"subj": 1, "predicate": "educated_at", "obj": 2}]
    slice_ = _FakeSlice(ents, edges)
    _, feats, keys = ingest._existing_features(slice_)
    nabbes = feats[0]
    assert nabbes["name"] == "Nabbes"
    assert nabbes["rel"] == "educated_at"
    assert nabbes["nbr"] == "Worcester College"
    assert keys[0] == {"PERSON|Nabbes"}


def test_new_features_from_batch_edges():
    batch = {
        "entities": [_batch_entity(0, "Nabbes", "PERSON"), _batch_entity(1, "Oxford", "ORG")],
        "edges": [{"subj_local": 0, "predicate": "studied_at", "obj_local": 1}],
    }
    _, feats = ingest._new_features(batch)
    assert feats[0]["rel"] == "studied_at"
    assert feats[0]["nbr"] == "Oxford"


# --- embedding-threshold matcher ------------------------------------------

class _StubEmbedder:
    """Maps each text to a fixed vector so cosine is deterministic in tests."""

    def __init__(self, mapping):
        self._m = mapping

    def embed(self, texts):
        import numpy as np
        return np.array([self._m[t] for t in texts], dtype=float)


def test_embed_cluster_groups_by_cosine():
    emb = _StubEmbedder({
        "Thomas Nabbes": [1.0, 0.0],
        "Nabbes": [0.999, 0.045],   # ~1.0 cosine with "Thomas Nabbes"
        "Oxford": [0.0, 1.0],       # orthogonal
    })
    rows = [
        {"surfaces": "Thomas Nabbes", "type": "PERSON"},
        {"surfaces": "Nabbes", "type": "PERSON"},
        {"surfaces": "Oxford", "type": "ORG"},
    ]
    assert ingest._embed_cluster(rows, emb, threshold=0.9) == [[0, 1]]


def test_embed_cluster_same_type_guard():
    # Near-identical vectors but different types -> NOT clustered.
    emb = _StubEmbedder({"Lincoln(p)": [1.0, 0.0], "Lincoln(o)": [1.0, 0.0]})
    rows = [
        {"surfaces": "Lincoln(p)", "type": "PERSON"},
        {"surfaces": "Lincoln(o)", "type": "PLACE"},
    ]
    assert ingest._embed_cluster(rows, emb, threshold=0.9) == []


def test_cross_doc_link_uses_embedder_when_given():
    store = _FakeStore([_existing(1, "Thomas Nabbes", "PERSON")])
    batch = {"entities": [_batch_entity(0, "Nabbes", "PERSON")], "edges": []}
    emb = _StubEmbedder({"Thomas Nabbes": [1.0, 0.0], "Nabbes": [0.999, 0.045]})
    linked = ingest._cross_doc_link(store, batch, at=5, embedder=emb)
    assert linked == 1
    assert "PERSON|Thomas Nabbes" in batch["entities"][0]["record_keys"]


# --- linking via injected matcher -----------------------------------------

def test_existing_and_new_in_one_cluster_links():
    store = _FakeStore([_existing(1, "Thomas Nabbes", "PERSON")])
    batch = {"entities": [_batch_entity(0, "Nabbes", "PERSON")], "edges": []}
    linked = ingest._cross_doc_link(store, batch, at=5, cluster_fn=lambda rows: [[0, 1]])
    assert linked == 1
    assert "PERSON|Thomas Nabbes" in batch["entities"][0]["record_keys"]


def test_matcher_receives_compound_rows_existing_first():
    captured = {}

    def _spy(rows):
        captured["rows"] = rows
        return []

    store = _FakeStore(
        [_existing(1, "Old", "ORG")],
        edges=[],
    )
    batch = {"entities": [_batch_entity(0, "New", "ORG")], "edges": []}
    ingest._cross_doc_link(store, batch, at=5, cluster_fn=_spy)
    rows = captured["rows"]
    assert [r["name"] for r in rows] == ["Old", "New"]
    assert set(rows[0]) == set(ingest._FEATURE_COLS)  # compound key columns present


def test_no_cluster_leaves_keys_untouched():
    store = _FakeStore([_existing(1, "Genesis", "ORG")])
    batch = {"entities": [_batch_entity(0, "Exeter College", "ORG")], "edges": []}
    assert ingest._cross_doc_link(store, batch, at=5, cluster_fn=lambda rows: []) == 0
    assert batch["entities"][0]["record_keys"] == ["ORG|Exeter College"]


def test_cluster_without_existing_does_not_inject():
    store = _FakeStore([_existing(1, "Genesis", "ORG")])
    batch = {"entities": [_batch_entity(0, "A", "ORG"), _batch_entity(1, "B", "ORG")], "edges": []}
    # rows: [existing=0, new A=1, new B=2]; cluster the two new ones only.
    assert ingest._cross_doc_link(store, batch, at=5, cluster_fn=lambda rows: [[1, 2]]) == 0


def test_same_type_guard_blocks_cross_type():
    store = _FakeStore([_existing(1, "Lincoln", "PERSON")])
    batch = {"entities": [_batch_entity(0, "Lincoln", "PLACE")], "edges": []}
    linked = ingest._cross_doc_link(store, batch, at=5, cluster_fn=lambda rows: [[0, 1]])
    assert linked == 0
    assert batch["entities"][0]["record_keys"] == ["PLACE|Lincoln"]


def test_empty_existing_is_noop():
    store = _FakeStore([])
    batch = {"entities": [_batch_entity(0, "X", "ORG")], "edges": []}
    assert ingest._cross_doc_link(store, batch, at=5, cluster_fn=lambda rows: [[0]]) == 0


def test_store_without_as_of_is_a_noop():
    class _Bare:
        pass

    batch = {"entities": [_batch_entity(0, "X", "ORG")], "edges": []}
    assert ingest._cross_doc_link(_Bare(), batch, at=5) == 0


def test_gating_default_on(monkeypatch):
    # DEFAULT ON since the 2026-07-21 anti-shatter flip; =0 opts out (case-insensitive).
    monkeypatch.delenv("GOLDENGRAPH_CROSS_DOC_LINK", raising=False)
    assert ingest._cross_doc_link_enabled() is True
    monkeypatch.setenv("GOLDENGRAPH_CROSS_DOC_LINK", "1")
    assert ingest._cross_doc_link_enabled() is True
    for off in ("0", "false", "False", ""):
        monkeypatch.setenv("GOLDENGRAPH_CROSS_DOC_LINK", off)
        assert ingest._cross_doc_link_enabled() is False, off


# --- goldenprofile anti-shatter matcher (PR #1217 engine) -----------------

def test_profile_link_gating_auto_default(monkeypatch):
    # DEFAULT 'auto': ON iff goldenprofile-native is importable, so a default-on build
    # degrades to the embedding matcher instead of hard-failing without the wheel.
    monkeypatch.delenv("GOLDENGRAPH_PROFILE_LINK", raising=False)
    ingest._goldenprofile_available.cache_clear()
    monkeypatch.setattr(ingest, "_goldenprofile_available", lambda: True)
    assert ingest._profile_link_enabled() is True   # auto + wheel present -> on
    monkeypatch.setattr(ingest, "_goldenprofile_available", lambda: False)
    assert ingest._profile_link_enabled() is False  # auto + wheel absent -> off (degrade)
    # explicit overrides ignore availability
    monkeypatch.setenv("GOLDENGRAPH_PROFILE_LINK", "1")
    assert ingest._profile_link_enabled() is True
    monkeypatch.setenv("GOLDENGRAPH_PROFILE_LINK", "0")
    assert ingest._profile_link_enabled() is False


def test_profile_cluster_repairs_shatter_but_gates_distinct_names():
    """The anti-shatter contract on the cross-doc matcher: two mentions of the
    same entity with DISJOINT neighborhoods reunite (Row-3 under-merge fixed,
    because the neighborhood lands in the non-vetoing attribute slot), while a
    distinct entity sharing a relationship never joins (Row-4 over-merge gated by
    the hard name+category gate). Needs the built engine wheel; skipped offline."""
    pytest.importorskip("goldenprofile_native")
    rows = [
        {"name": "Nabbes", "type": "person", "surfaces": "Nabbes",
         "rel": "wrote", "nbr": "Play X"},
        {"name": "Nabbes", "type": "person", "surfaces": "Nabbes",
         "rel": "born in", "nbr": "1605"},
        {"name": "Shakespeare", "type": "person", "surfaces": "Shakespeare",
         "rel": "wrote", "nbr": "Hamlet"},
    ]
    clusters = ingest._profile_cluster(rows, embedder=None)
    assert any(set(c) == {0, 1} for c in clusters), clusters
    assert all(2 not in c for c in clusters), clusters


def test_profile_cluster_trivial_inputs():
    assert ingest._profile_cluster([], embedder=None) == []
    assert ingest._profile_cluster([{"name": "A", "type": "t"}], embedder=None) == []


# --- LLM-synthesized node fingerprints (full PR #1217 design) --------------

class _StubMention:
    def __init__(self, name, typ="person", context=""):
        self.name = name
        self.typ = typ
        self.context = context


class _StubRel:
    def __init__(self, subj, predicate, obj):
        self.subj, self.predicate, self.obj = subj, predicate, obj


class _StubExtraction:
    def __init__(self, mentions, relationships=()):
        self.mentions = list(mentions)
        self.relationships = list(relationships)


class _StubEntity:
    def __init__(self, local_id, canonical_name, member_idx, typ="person"):
        self.local_id = local_id
        self.canonical_name = canonical_name
        self.member_idx = list(member_idx)
        self.typ = typ


class _SeqLLM:
    """Returns a canned fingerprints JSON; records prompts."""

    def __init__(self, payload):
        self._payload = payload
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return self._payload


def test_entity_fps_picks_representative_member_fingerprint():
    import json as _json

    ext = _StubExtraction(
        [_StubMention("Nabbes"), _StubMention("Thomas Nabbes"), _StubMention("London", "place")],
        [_StubRel(0, "born in", 2)],
    )
    # entity 0 = {Nabbes, Thomas Nabbes} canonical "Thomas Nabbes"; entity 1 = London
    entities = [
        _StubEntity(0, "Thomas Nabbes", [0, 1]),
        _StubEntity(1, "London", [2], typ="place"),
    ]
    payload = _json.dumps({"fingerprints": [
        "Nabbes | person | UNKNOWN | wrote plays",
        "Thomas Nabbes | playwright | 1605 | wrote Hannibal and Scipio",
        "London | place | UNKNOWN | capital",
    ]})
    fps = ingest._entity_fps(ext, entities, _SeqLLM(payload))
    # entity 0's representative is mention 1 (name == canonical "Thomas Nabbes")
    assert fps[0] == "Thomas Nabbes | playwright | 1605 | wrote Hannibal and Scipio"
    assert fps[1] == "London | place | UNKNOWN | capital"


def test_assemble_fp_texts_recovers_existing_from_index():
    existing = [{"typ": "person"}, {"typ": "place"}]
    ex_keys = [{"person|Nabbes"}, {"place|London"}]
    new_ents = [{"local_id": 7}, {"local_id": 8}]
    fp_index = {"person|Nabbes": "Nabbes | playwright | 1605 | wrote X"}
    new_fps = {7: "Nabbes | playwright | UNKNOWN | born 1605"}
    out = ingest._assemble_fp_texts(existing, ex_keys, new_ents, new_fps, fp_index)
    assert out == [
        "Nabbes | playwright | 1605 | wrote X",  # existing 0 recovered from index
        None,                                     # existing 1 absent from index
        "Nabbes | playwright | UNKNOWN | born 1605",  # new 7 from new_fps
        None,                                     # new 8 has no fp
    ]


def test_profile_cluster_honors_supplied_fingerprint_texts():
    pytest.importorskip("goldenprofile_native")
    # Two rows the deterministic path could not tell apart by neighborhood, but the
    # supplied fingerprints share name+category -> the engine merges them; a third
    # with a distinct name stays apart.
    rows = [{"name": "x", "type": "person"}, {"name": "y", "type": "person"},
            {"name": "z", "type": "person"}]
    fp_texts = [
        "Nabbes | playwright | 1605 | wrote Hannibal and Scipio",
        "Nabbes | playwright | UNKNOWN | educated at Exeter College",
        "Shakespeare | playwright | UNKNOWN | wrote Hamlet",
    ]
    clusters = ingest._profile_cluster(rows, embedder=None, fp_texts=fp_texts)
    assert any(set(c) == {0, 1} for c in clusters), clusters
    assert all(2 not in c for c in clusters), clusters


# --- parallel corpus build (perf: concurrent LLM, serial commit) -----------

class _RecordingStore:
    """Captures appended batch JSON in commit order."""

    def __init__(self):
        self.appends = []

    def append(self, payload):
        import json as _json
        self.appends.append(_json.loads(payload))


def _stub_extraction(name):
    # one mention, no relationships; resolver below maps it to one entity.
    from goldengraph.extract import Extraction, Mention
    return Extraction(mentions=[Mention(name=name, typ="thing")], relationships=[])


def test_ingest_corpus_parallel_commits_in_document_order(monkeypatch):
    # Stub extraction so each doc text -> a one-entity extraction with that name;
    # resolver maps it through. No LLM, no store linking (cross_doc off).
    monkeypatch.setattr(ingest, "_extract", lambda text, llm: _stub_extraction(text))
    _resolve_mod = importlib.import_module("goldengraph.resolve")

    def _resolver(mentions):
        return [
            _resolve_mod.ResolvedEntity(
                local_id=0, canonical_name=mentions[0].name, typ=mentions[0].typ,
                surface_names=[mentions[0].name],
                record_keys=[f"{mentions[0].typ}|{mentions[0].name}"], member_idx=[0],
            )
        ]

    docs = [f"doc{i}" for i in range(12)]
    store = _RecordingStore()
    ingest.ingest_corpus(docs, store, llm=object(), resolver=_resolver, max_workers=4)

    # commit happened once per doc, IN ORDER, with at = i+1
    assert [a["ingested_at"] for a in store.appends] == list(range(1, 13))
    assert [a["entities"][0]["canonical_name"] for a in store.appends] == docs


def test_ingest_corpus_serial_path_matches(monkeypatch):
    monkeypatch.setattr(ingest, "_extract", lambda text, llm: _stub_extraction(text))
    _resolve_mod = importlib.import_module("goldengraph.resolve")

    def _resolver(mentions):
        return [_resolve_mod.ResolvedEntity(
            local_id=0, canonical_name=mentions[0].name, typ=mentions[0].typ,
            surface_names=[mentions[0].name],
            record_keys=[f"{mentions[0].typ}|{mentions[0].name}"], member_idx=[0])]

    docs = [f"d{i}" for i in range(5)]
    store = _RecordingStore()
    ingest.ingest_corpus(docs, store, llm=object(), resolver=_resolver, max_workers=1)
    assert [a["entities"][0]["canonical_name"] for a in store.appends] == docs


# --- incremental blocked link index (O(N) commit phase) --------------------

def test_link_index_blocks_by_name_token():
    idx = ingest._LinkIndex()
    idx.add({"name": "Nabbes", "type": "person", "surfaces": "Nabbes"}, {"k1"}, "fp1")
    idx.add({"name": "London", "type": "place", "surfaces": "London"}, {"k2"}, "fp2")
    # a new row sharing the 'nabbes' token sees only the Nabbes entry as candidate
    cand = idx.candidates([{"name": "Thomas Nabbes", "surfaces": "Thomas Nabbes"}])
    assert cand == [0]
    # a row sharing no token sees nothing
    assert idx.candidates([{"name": "Paris", "surfaces": "Paris"}]) == []


def test_cross_doc_link_incremental_injects_and_indexes(monkeypatch):
    # stub matcher: cluster rows sharing their LAST name token (no wheel needed)
    from collections import defaultdict as _dd

    def _stub(rows, embedder, fp_texts=None):
        groups = _dd(list)
        for i, r in enumerate(rows):
            groups[r["name"].split()[-1].lower()].append(i)
        return [g for g in groups.values() if len(g) > 1]

    monkeypatch.setattr(ingest, "_profile_cluster", _stub)
    idx = ingest._LinkIndex()
    idx.add({"name": "Nabbes", "type": "person", "surfaces": "Nabbes"},
            {"person|Nabbes"}, "Nabbes | person | UNKNOWN | x")

    batch = {"entities": [_batch_entity(0, "Thomas Nabbes", "person")], "edges": []}
    linked = ingest._cross_doc_link_incremental(
        batch, embedder=None, new_fps=None, index=idx
    )
    assert linked == 1
    assert "person|Nabbes" in batch["entities"][0]["record_keys"]  # bridge merged
    assert len(idx._rows) == 2  # new entity indexed for later docs


def test_cross_doc_link_incremental_no_candidates_is_noop(monkeypatch):
    monkeypatch.setattr(ingest, "_profile_cluster",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
    idx = ingest._LinkIndex()
    idx.add({"name": "Paris", "type": "place", "surfaces": "Paris"}, {"k"}, "fp")
    batch = {"entities": [_batch_entity(0, "Nabbes", "person")], "edges": []}
    # no shared token -> matcher never invoked, but the new entity is still indexed
    assert ingest._cross_doc_link_incremental(batch, embedder=None, new_fps=None, index=idx) == 0
    assert len(idx._rows) == 2


# --- build timing instrumentation (hotspot analysis foundation) ------------

def test_build_timers_ranks_by_summed_time():
    t = ingest._BuildTimers()
    t.add("extract", 1.0); t.add("extract", 1.0)
    t.add("resolve", 0.5)
    rep = t.report(wall=2.0, n_docs=3)
    assert "extract" in rep and "resolve" in rep
    # extract (sum 2.0) ranked above resolve (0.5)
    assert rep.index("extract") < rep.index("resolve")
    assert "calls=     2" in rep  # two extract calls counted


def test_ingest_corpus_emits_debug_report(monkeypatch, capsys):
    monkeypatch.setenv("GOLDENGRAPH_BUILD_DEBUG", "1")
    monkeypatch.setattr(ingest, "_extract", lambda text, llm: _stub_extraction(text))
    _resolve_mod = importlib.import_module("goldengraph.resolve")

    def _resolver(mentions):
        return [_resolve_mod.ResolvedEntity(
            local_id=0, canonical_name=mentions[0].name, typ=mentions[0].typ,
            surface_names=[mentions[0].name],
            record_keys=[f"{mentions[0].typ}|{mentions[0].name}"], member_idx=[0])]

    store = _RecordingStore()
    ingest.ingest_corpus([f"d{i}" for i in range(4)], store, llm=object(),
                         resolver=_resolver, max_workers=2)
    out = capsys.readouterr().out
    assert "[build-debug]" in out
    assert "extract" in out and "resolve" in out


# --- distillation capture + injectable local extractor ---------------------

def test_distill_logger_writes_jsonl(tmp_path):
    import json as _json
    p = tmp_path / "distill.jsonl"
    logger = ingest._DistillLogger(str(p))
    ext = _stub_extraction("Acme made Rocket")
    logger.log("Acme made Rocket", ext, {0: "Acme | org | UNKNOWN | maker"})
    rec = _json.loads(p.read_text().strip())
    assert rec["text"] == "Acme made Rocket"
    assert rec["entities"][0]["name"] == "Acme made Rocket"  # stub: one mention
    assert rec["fingerprints"] == {"0": "Acme | org | UNKNOWN | maker"}
    assert rec["attributes"] == []  # back-compat: attribute-less extraction


def test_distill_logger_captures_attributes(tmp_path):
    """The capture must record literal attributes (entity -[predicate]-> typed
    value) -- otherwise the literal/phrase-span extraction channel is invisible in
    the distillation log it is meant to train."""
    import json as _json

    from goldengraph.extract import Attribute, Extraction, Mention

    p = tmp_path / "distill.jsonl"
    ext = Extraction(
        mentions=[Mention(name="Cairo University", typ="university")],
        relationships=[],
        attributes=[
            Attribute(subj=0, predicate="ranked", value="551-600", typ="range"),
            Attribute(subj=0, predicate="located in", value="Egypt", typ="region"),
        ],
    )
    ingest._DistillLogger(str(p)).log("Cairo University ...", ext, None)
    rec = _json.loads(p.read_text().strip())
    assert rec["attributes"] == [
        {"subj": 0, "predicate": "ranked", "value": "551-600", "type": "range"},
        {"subj": 0, "predicate": "located in", "value": "Egypt", "type": "region"},
    ]


def test_resolve_extractor_default_and_unknown(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_EXTRACTOR", raising=False)
    assert ingest._resolve_extractor() is None  # api default -> module _extract
    monkeypatch.setenv("GOLDENGRAPH_EXTRACTOR", "api")
    assert ingest._resolve_extractor() is None
    monkeypatch.setenv("GOLDENGRAPH_EXTRACTOR", "bogus")
    import pytest as _pytest
    with _pytest.raises(ValueError):
        ingest._resolve_extractor()


def test_prepare_doc_uses_injected_extractor():
    calls = {"n": 0}

    def _stub_extractor(text, llm=None):
        calls["n"] += 1
        return _stub_extraction("FROM-LOCAL-" + text)

    def _resolver(mentions):
        from goldengraph.resolve import ResolvedEntity
        return [ResolvedEntity(local_id=0, canonical_name=mentions[0].name,
                               typ=mentions[0].typ, surface_names=[mentions[0].name],
                               record_keys=["k"], member_idx=[0])]

    ext, ents, _ = ingest._prepare_doc("doc1", llm=object(), resolver=_resolver,
                                       profile_fps=False, extractor=_stub_extractor)
    assert calls["n"] == 1
    assert ext.mentions[0].name == "FROM-LOCAL-doc1"  # the injected extractor ran
