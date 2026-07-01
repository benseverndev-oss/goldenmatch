"""Wikilink parser + committed-snapshot loader for the level-2 real-prose corpus."""
import json

from erkgbench.qa_e2e.wiki_corpus import load_wiki_corpus, parse_wikilinks


def test_parse_wikilinks_piped_and_bare():
    wt = "[[IBM]] acquired [[Red Hat|Red Hat, Inc.]] in 2019."
    assert parse_wikilinks(wt) == [("IBM", "IBM"), ("Red Hat, Inc.", "Red Hat")]


def test_parse_wikilinks_skips_namespaced_and_strips_section():
    wt = "[[File:logo.png|thumb]] see [[Apple Inc.#History|Apple]] and [[Category:Tech]]"
    assert parse_wikilinks(wt) == [("Apple", "Apple Inc.")]  # File:/Category: skipped, #section stripped


def test_load_wiki_corpus_flattens_gold(tmp_path):
    snap = tmp_path / "wiki_corpus.jsonl"
    snap.write_text(
        json.dumps({"doc_id": "Q37156", "title": "IBM", "revid": 1,
                    "text": "IBM acquired Red Hat.", "gold": [["Q37156", "IBM"], ["Q_rh", "Red Hat"]]}) + "\n"
        + json.dumps({"doc_id": "Q_rh", "title": "Red Hat", "revid": 2,
                      "text": "Red Hat is a company IBM bought.", "gold": [["Q_rh", "Red Hat"], ["Q37156", "IBM"]]}) + "\n",
        encoding="utf-8",
    )
    docs, gold = load_wiki_corpus(snap)
    assert {d.id for d in docs} == {"Q37156", "Q_rh"}
    assert all(len(g) == 3 for g in gold)                 # (qid, surface, doc_id)
    # cross-doc: Q37156 (IBM) is gold in BOTH docs -> real cross-document co-reference
    assert sum(1 for qid, _s, _d in gold if qid == "Q37156") == 2
