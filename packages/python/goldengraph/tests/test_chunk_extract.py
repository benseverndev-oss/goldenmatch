"""Chunked extraction: sentence splitting, overlapping windows, and index-offset union."""
from goldengraph.chunk_extract import sentence_windows, split_sentences


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
