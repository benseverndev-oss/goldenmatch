"""GOLDENGRAPH_REBEL_FUSE: map REBEL (head,rel,tail) triples onto existing entities as edges."""
from goldengraph.extract import Mention
from goldengraph.rebel_fuse import rebel_fuse, rebel_fuse_enabled


def _mentions():
    return [Mention(name="Amazon", typ="org"), Mention(name="Jeff Bezos", typ="person")]


def _fake_rebel(triples_per_call):
    """Returns a callable that yields a fixed triple list on every call, recording call count."""
    state = {"calls": 0}

    def rebel(text):
        state["calls"] += 1
        return list(triples_per_call)
    rebel.state = state
    return rebel


def test_maps_triple_to_existing_entities(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_REBEL_SENTENCES", "50")  # one window over the short text
    rebel = _fake_rebel([("Amazon", "founded by", "Jeff Bezos")])
    rels = rebel_fuse("Amazon was founded by Jeff Bezos.", _mentions(), rebel=rebel)
    assert len(rels) == 1
    assert (rels[0].subj, rels[0].predicate, rels[0].obj) == (0, "founded by", 1)


def test_substring_and_casefold_match(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_REBEL_SENTENCES", "50")
    rebel = _fake_rebel([("amazon", "employs", "Bezos")])  # cased + substring
    rels = rebel_fuse("t.", _mentions(), rebel=rebel)
    assert [(r.subj, r.predicate, r.obj) for r in rels] == [(0, "employs", 1)]


def test_drops_unmapped_and_self_loops(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_REBEL_SENTENCES", "50")
    rebel = _fake_rebel([
        ("Amazon", "rivals", "Microsoft"),   # tail maps to no mention -> dropped
        ("Amazon", "is", "Amazon"),           # both map to 0 -> self-loop dropped
        ("Amazon", "led by", "Jeff Bezos"),   # valid
    ])
    rels = rebel_fuse("t.", _mentions(), rebel=rebel)
    assert [(r.subj, r.predicate, r.obj) for r in rels] == [(0, "led by", 1)]


def test_runs_once_per_window(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_REBEL_SENTENCES", "2")
    monkeypatch.setenv("GOLDENGRAPH_REBEL_OVERLAP", "0")
    # 4 sentences, size=2 overlap=0 -> 2 windows
    text = "S one here. S two here. S three here. S four here."
    rebel = _fake_rebel([("Amazon", "r", "Jeff Bezos")])
    rels = rebel_fuse(text, _mentions(), rebel=rebel)
    assert rebel.state["calls"] == 2          # one call per window
    assert len(rels) == 2                      # each window contributed the mapped edge


def test_gate_enabled(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_REBEL_FUSE", raising=False)
    assert rebel_fuse_enabled() is False
    monkeypatch.setenv("GOLDENGRAPH_REBEL_FUSE", "1")
    assert rebel_fuse_enabled() is True
    for off in ("", "0", "False", "off", " no "):
        monkeypatch.setenv("GOLDENGRAPH_REBEL_FUSE", off)
        assert rebel_fuse_enabled() is False, off


def test_empty_mentions_no_rebel_call():
    def boom(text):
        raise AssertionError("must not be called on empty mentions")
    assert rebel_fuse("t.", [], rebel=boom) == []
