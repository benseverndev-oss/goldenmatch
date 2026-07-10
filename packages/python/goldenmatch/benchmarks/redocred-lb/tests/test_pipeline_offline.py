"""Offline tests for the Re-DocRED leaderboard pipeline's pure parts: the official
scorer and the ATLOP feature construction (marker insertion, entity positions,
pair/label matrix). No torch, transformers, network, or model download -- a stub
tokenizer stands in for the real one. The GPU model is validated by a tiny live
Modal run, not here."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from prepro import NUM_CLASS, build_rel2id, read_docred  # noqa: E402
from scoring import facts_in_train, official_evaluate, to_submission  # noqa: E402


class StubTokenizer:
    """Deterministic word-piece stand-in: each whitespace token -> itself, '*' markers
    kept verbatim. ids are hashes; special tokens wrap with sentinel ids 101/102."""

    def tokenize(self, token):
        return [token]

    def convert_tokens_to_ids(self, toks):
        return [1 if t == "*" else (hash(t) % 30000) + 200 for t in toks]

    def build_inputs_with_special_tokens(self, ids):
        return [101] + ids + [102]


# one toy doc: 2 sents, 3 entities, 2 gold relations
_DOC = {
    "title": "Toy",
    "sents": [["Acme", "hired", "Jane", "."],
              ["Acme", "is", "in", "Portland", "."]],
    "vertexSet": [
        [{"name": "Acme", "sent_id": 0, "pos": [0, 1], "type": "ORG"},
         {"name": "Acme", "sent_id": 1, "pos": [0, 1], "type": "ORG"}],
        [{"name": "Jane", "sent_id": 0, "pos": [2, 3], "type": "PER"}],
        [{"name": "Portland", "sent_id": 1, "pos": [3, 4], "type": "LOC"}],
    ],
    "labels": [{"h": 0, "t": 1, "r": "P108", "evidence": [0]},
               {"h": 0, "t": 2, "r": "P159", "evidence": [1]}],
}


def test_rel2id_is_deterministic_sorted_union():
    r2i = build_rel2id([_DOC])
    assert r2i == {"P108": 1, "P159": 2}
    # class 0 is reserved for TH/NA, relations start at 1
    assert min(r2i.values()) == 1


def test_read_docred_markers_positions_and_pairs():
    r2i = build_rel2id([_DOC])
    feats = read_docred([_DOC], StubTokenizer(), r2i)
    assert len(feats) == 1
    f = feats[0]
    # 3 entities -> 3*2 = 6 ordered pairs, all present (2 positive + 4 negative)
    assert len(f["hts"]) == 6
    assert len(f["labels"]) == 6
    # positives come first and carry the right relation class; index 0 (TH) is 0 for them
    pos = {tuple(ht): lab for ht, lab in zip(f["hts"][:2], f["labels"][:2])}
    assert pos[(0, 1)][r2i["P108"]] == 1 and pos[(0, 1)][0] == 0
    assert pos[(0, 2)][r2i["P159"]] == 1
    # negatives carry TH (index 0) = 1 and no relation bits
    for lab in f["labels"][2:]:
        assert lab[0] == 1 and sum(lab) == 1
    # every label vector is 97-dim
    assert all(len(lab) == NUM_CLASS for lab in f["labels"])
    # entity_pos: the '*' marker sits just before each mention; entity 0 has 2 mentions
    assert len(f["entity_pos"]) == 3
    assert len(f["entity_pos"][0]) == 2
    # marker token id (1) appears at each recorded start offset (offsets are pre-CLS)
    core = f["input_ids"][1:-1]  # strip special tokens -> aligns with entity_pos offsets
    for spans in f["entity_pos"]:
        for start, _end in spans:
            assert core[start] == 1  # the '*' marker


def test_read_docred_evidence_fields():
    r2i = build_rel2id([_DOC])
    # default: no evidence fields (byte-identical to plain ATLOP path)
    plain = read_docred([_DOC], StubTokenizer(), r2i)[0]
    assert "sent_pos" not in plain and "evidence" not in plain
    # with_evidence: sent_pos spans every sentence; evidence aligns with hts
    f = read_docred([_DOC], StubTokenizer(), r2i, with_evidence=True)[0]
    assert len(f["sent_pos"]) == 2  # 2 sentences
    # each sentence span is (start, end) into the non-special-token stream, contiguous
    (a0, b0), (a1, b1) = f["sent_pos"]
    assert a0 == 0 and a1 == b0 and b1 == len(f["input_ids"]) - 2
    assert len(f["evidence"]) == len(f["hts"])
    # positives (first 2) carry their gold evidence sentences; P108 -> sent 0, P159 -> sent 1
    pos_ev = {tuple(ht): ev for ht, ev in zip(f["hts"][:2], f["evidence"][:2])}
    assert pos_ev[(0, 1)] == [0] and pos_ev[(0, 2)] == [1]
    # negatives carry no evidence
    assert all(ev == [] for ev in f["evidence"][2:])


def test_scorer_perfect_and_partial_and_ign():
    gold = [_DOC]
    train_facts = facts_in_train([_DOC])  # same doc as "train" -> both facts are in-train

    # perfect prediction: both gold triples
    preds = [[(0, 1, "P108"), (0, 2, "P159")]]
    sub = to_submission(preds, gold)
    res = official_evaluate(sub, gold, train_facts)
    assert res["f1"] == 1.0 and res["correct"] == 2 and res["n_gold"] == 2
    # both facts are in train -> ign precision numerator/denominator both drop the 2
    # correct-in-train, leaving 0/0 -> ign precision 0 -> ign_f1 0
    assert res["correct_in_train"] == 2
    assert res["ign_f1"] == 0.0

    # half prediction + one wrong -> P=0.5 R=0.5 F1=0.5
    preds2 = [[(0, 1, "P108"), (0, 2, "P999wrong")]]
    res2 = official_evaluate(to_submission(preds2, gold), gold, train_facts)
    assert res2["correct"] == 1 and abs(res2["f1"] - 0.5) < 1e-9

    # duplicate predictions are collapsed (not double-counted)
    preds3 = [[(0, 1, "P108"), (0, 1, "P108")]]
    res3 = official_evaluate(to_submission(preds3, gold), gold, train_facts)
    assert res3["n_pred"] == 1 and res3["correct"] == 1


def test_scorer_ign_removes_only_memorised_facts():
    gold = [_DOC]
    # train facts that do NOT include this doc's entity-name pairs -> nothing is in-train,
    # so Ign F1 == F1
    other_train = [{
        "vertexSet": [[{"name": "Foo"}], [{"name": "Bar"}]],
        "labels": [{"h": 0, "t": 1, "r": "P108"}],
    }]
    facts = facts_in_train(other_train)
    preds = [[(0, 1, "P108"), (0, 2, "P159")]]
    res = official_evaluate(to_submission(preds, gold), gold, facts)
    assert res["correct_in_train"] == 0
    assert abs(res["ign_f1"] - res["f1"]) < 1e-9
