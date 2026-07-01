"""Chunked extraction: sentence splitting, overlapping windows, and index-offset union."""
from goldengraph.chunk_extract import (
    _chunk_params,
    chunk_extract,
    chunk_extract_enabled,
    sentence_windows,
    split_sentences,
)
from goldengraph.extract import Attribute, Extraction, Mention, Relationship


class _WindowStub:
    """Extractor stub: records each window text it saw and returns a fixed
    2-entity/1-relationship extraction per call (rel points 0->1 within the call)."""

    def __init__(self):
        self.seen = []

    def __call__(self, text, llm=None):
        self.seen.append(text)
        k = len(self.seen)
        return Extraction(
            mentions=[Mention(name=f"E{k}a", typ="org"), Mention(name=f"E{k}b", typ="person")],
            relationships=[Relationship(subj=0, predicate="founded_by", obj=1)],
        )


def test_split_sentences_basic():
    s = "Amazon was founded in 1994. Jeff Bezos was the CEO. It sells books."
    assert len(split_sentences(s)) == 3


def test_split_sentences_empty():
    assert split_sentences("") == []
    assert split_sentences("   ") == []


def test_split_sentences_lower_bound_with_abbreviations():
    # "Inc." may over-split; we only require the real breaks are found (lower bound).
    s = "Apple Inc. is a company. Steve Jobs founded it."
    assert len(split_sentences(s)) >= 2


def test_windows_size_and_overlap_spans():
    sents = [f"s{i}." for i in range(9)]  # 9 sentences
    # size=4, overlap=1 -> stride 3 -> windows [0:4], [3:7], [6:9]
    wins = sentence_windows(sents, size=4, overlap=1)
    assert wins == ["s0. s1. s2. s3.", "s3. s4. s5. s6.", "s6. s7. s8."]


def test_windows_shorter_than_size_is_one_window():
    sents = ["a.", "b."]
    assert sentence_windows(sents, size=4, overlap=1) == ["a. b."]


def test_windows_empty_input_no_windows():
    assert sentence_windows([], size=4, overlap=1) == []


def test_windows_overlap_ge_size_clamped_terminates():
    sents = [f"s{i}." for i in range(6)]
    # overlap >= size must clamp to size-1 (stride 1), cover all, and terminate.
    wins = sentence_windows(sents, size=3, overlap=5)
    assert wins[0] == "s0. s1. s2."
    assert wins[-1].endswith("s5.")
    assert len(wins) == 4  # stride 1: [0:3],[1:4],[2:5],[3:6]


def test_windows_size_zero_floored_to_one():
    sents = ["a.", "b.", "c."]
    # size<=0 must floor to 1 (no zero/negative stride, no infinite loop).
    wins = sentence_windows(sents, size=0, overlap=0)
    assert wins == ["a.", "b.", "c."]


def test_windows_negative_overlap_treated_as_zero():
    sents = [f"s{i}." for i in range(6)]
    # overlap<0 must clamp to 0 -> stride == size, no gaps, no crash.
    wins = sentence_windows(sents, size=3, overlap=-2)
    assert wins == ["s0. s1. s2.", "s3. s4. s5."]


def test_chunk_extract_enabled_gate(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_CHUNK_EXTRACT", raising=False)
    assert chunk_extract_enabled() is False
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_EXTRACT", "1")
    assert chunk_extract_enabled() is True
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_EXTRACT", "")  # set-but-empty -> off
    assert chunk_extract_enabled() is False


def test_chunk_extract_enabled_case_insensitive_off(monkeypatch):
    # "False"/"Off"/" 0 " must read as OFF (case-insensitive, stripped), not surprisingly on.
    for off in ("False", "FALSE", "off", "No", " 0 "):
        monkeypatch.setenv("GOLDENGRAPH_CHUNK_EXTRACT", off)
        assert chunk_extract_enabled() is False, off


def test_chunk_params_defaults_and_empty_string(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_CHUNK_SENTENCES", raising=False)
    monkeypatch.delenv("GOLDENGRAPH_CHUNK_OVERLAP", raising=False)
    assert _chunk_params() == (6, 2)  # measured wiki-sweep winner
    # empty-string env must fall back to default, not raise ValueError
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_SENTENCES", "")
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_OVERLAP", "")
    assert _chunk_params() == (6, 2)
    # garbage falls back too
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_SENTENCES", "abc")
    assert _chunk_params() == (6, 2)
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_SENTENCES", "3")
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_OVERLAP", "1")
    assert _chunk_params() == (3, 1)


def test_chunk_extract_unions_and_offsets_indices(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_SENTENCES", "1")
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_OVERLAP", "0")
    text = "Sentence one is here. Sentence two is here. Sentence three is here."
    stub = _WindowStub()
    ex = chunk_extract(text, llm=None, extractor=stub)
    # 3 windows -> 6 mentions, 3 relationships
    assert len(stub.seen) == 3
    assert len(ex.mentions) == 6
    assert len(ex.relationships) == 3
    # window k's relationship must point into window k's mention block (offset applied)
    # window 0: mentions 0,1 -> rel (0,1); window 1: mentions 2,3 -> rel (2,3); window 2: (4,5)
    assert (ex.relationships[0].subj, ex.relationships[0].obj) == (0, 1)
    assert (ex.relationships[1].subj, ex.relationships[1].obj) == (2, 3)
    assert (ex.relationships[2].subj, ex.relationships[2].obj) == (4, 5)


def test_chunk_extract_skips_failing_window(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_SENTENCES", "1")
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_OVERLAP", "0")

    calls = {"n": 0}

    def flaky(text, llm=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return Extraction(mentions=[Mention(name="X", typ="org")], relationships=[])

    text = "One here. Two here. Three here."
    ex = chunk_extract(text, llm=None, extractor=flaky)
    # 3 windows, middle one raises -> 2 mentions survive, no crash
    assert len(ex.mentions) == 2


def test_chunk_extract_offsets_attribute_subj(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_SENTENCES", "1")
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_OVERLAP", "0")

    def attr_extractor(text, llm=None):
        # one entity + one attribute on subj=0 per window
        return Extraction(
            mentions=[Mention(name="Amazon", typ="org")],
            relationships=[],
            attributes=[Attribute(subj=0, predicate="founded", value="1994", typ="date")],
        )

    text = "One here. Two here. Three here."
    ex = chunk_extract(text, llm=None, extractor=attr_extractor)
    # window k's attribute subj must offset to window k's mention block: 0, 1, 2
    assert [a.subj for a in ex.attributes] == [0, 1, 2]
    assert all(a.value == "1994" for a in ex.attributes)


def test_prepare_doc_uses_chunking_only_when_gated(monkeypatch):
    """_prepare_doc calls the extractor once when off, once-per-window when on."""
    import importlib

    from goldengraph.resolve import ResolvedEntity

    # goldengraph/__init__ re-exports the `ingest` FUNCTION, shadowing the submodule,
    # so import the module object explicitly to reach `_prepare_doc`.
    ingest = importlib.import_module("goldengraph.ingest")

    calls = {"n": 0}

    def counting_extractor(text, llm=None):
        calls["n"] += 1
        return Extraction(mentions=[Mention(name="X", typ="org")], relationships=[])

    # identity resolver: one entity per mention (no goldenmatch needed)
    def resolver(mentions):
        return [
            ResolvedEntity(
                local_id=i, canonical_name=m.name, typ=m.typ,
                surface_names=[m.name], record_keys=[], member_idx=[i],
            )
            for i, m in enumerate(mentions)
        ]

    text = "One here. Two here. Three here. Four here. Five here. Six here. Seven here."

    # gate OFF -> single call
    monkeypatch.delenv("GOLDENGRAPH_CHUNK_EXTRACT", raising=False)
    calls["n"] = 0
    ingest._prepare_doc(text, llm=None, resolver=resolver, profile_fps=False,
                        extractor=counting_extractor)
    assert calls["n"] == 1

    # gate ON, size=3 overlap=1 (stride 2) over 7 sentences -> windows [0:3],[2:5],[4:7] = 3 calls
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_EXTRACT", "1")
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_SENTENCES", "3")
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_OVERLAP", "1")
    calls["n"] = 0
    ingest._prepare_doc(text, llm=None, resolver=resolver, profile_fps=False,
                        extractor=counting_extractor)
    assert calls["n"] == 3
