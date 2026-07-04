"""Official DocRED / Re-DocRED relation-extraction scorer (F1 + Ign F1).

A faithful port of the reference `evaluation.py` from the DocRED repo (thunlp/DocRED)
and the Re-DocRED release (tonytan48/Re-DocRED): the leaderboard metric. Pure Python
(stdlib only) so it is unit-testable offline with no torch/transformers/network.

- **F1**: standard micro P/R/F1 over predicted vs gold `(title, relation, h_idx, t_idx)`.
- **Ign F1** ("ignore train"): the same, but relational facts whose *entity-name pair*
  already appears in the training set are removed from the precision accounting -- so a
  model cannot be credited for memorising train facts. This is the headline leaderboard
  number for Re-DocRED (recall denominator is unchanged; only precision is adjusted).

The in-train fact key is `(head_name, tail_name, relation)` over EVERY mention-name pair
of the two entities, exactly as the reference scorer computes it.
"""
from __future__ import annotations

from typing import Iterable


def facts_in_train(train_docs: list[dict]) -> set[tuple[str, str, str]]:
    """Set of `(head_mention_name, tail_mention_name, relation)` over all train labels --
    the reference `gen_train_facts`. Used to strip memorised facts for Ign F1."""
    facts: set[tuple[str, str, str]] = set()
    for x in train_docs:
        vs = x["vertexSet"]
        for label in x.get("labels", []):
            r = label["r"]
            for n1 in vs[label["h"]]:
                for n2 in vs[label["t"]]:
                    facts.add((n1["name"], n2["name"], r))
    return facts


def to_submission(preds_per_doc: list[Iterable[tuple[int, int, str]]],
                  gold_docs: list[dict]) -> list[dict]:
    """Flatten per-doc predictions `(h_idx, t_idx, relation_Pid)` into the official
    submission shape `[{title, h_idx, t_idx, r}]`, aligned by position with ``gold_docs``."""
    out: list[dict] = []
    for doc, preds in zip(gold_docs, preds_per_doc):
        title = doc["title"]
        for h, t, r in preds:
            out.append({"title": title, "h_idx": int(h), "t_idx": int(t), "r": r})
    return out


def official_evaluate(submission: list[dict], gold_docs: list[dict],
                      train_facts: set[tuple[str, str, str]]) -> dict:
    """Return {f1, ign_f1, precision, recall, ign_precision, correct, n_pred, n_gold}.

    ``submission`` is the official list of ``{title, h_idx, t_idx, r}``; predictions for
    titles absent from ``gold_docs`` are dropped, and exact duplicates are collapsed
    (both mirror the reference scorer)."""
    std: set[tuple] = set()
    title2vs: dict[str, list] = {}
    titles: set[str] = set()
    for x in gold_docs:
        title = x["title"]
        titles.add(title)
        title2vs[title] = x["vertexSet"]
        for label in x.get("labels", []):
            std.add((title, label["r"], label["h"], label["t"]))
    tot_relations = len(std)

    # keep only in-scope titles, dedup exact (title,h,t,r)
    seen: set[tuple] = set()
    subs: list[dict] = []
    for s in submission:
        if s["title"] not in titles:
            continue
        key = (s["title"], s["r"], s["h_idx"], s["t_idx"])
        if key in seen:
            continue
        seen.add(key)
        subs.append(s)

    correct = 0
    correct_in_train = 0
    for s in subs:
        title, r, h, t = s["title"], s["r"], s["h_idx"], s["t_idx"]
        if (title, r, h, t) in std:
            correct += 1
            vs = title2vs[title]
            in_train = False
            for n1 in vs[h]:
                for n2 in vs[t]:
                    if (n1["name"], n2["name"], r) in train_facts:
                        in_train = True
                        break
                if in_train:
                    break
            if in_train:
                correct_in_train += 1

    n_pred = len(subs)
    precision = correct / n_pred if n_pred else 0.0
    recall = correct / tot_relations if tot_relations else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    ign_denom = n_pred - correct_in_train
    ign_precision = (correct - correct_in_train) / ign_denom if ign_denom else 0.0
    ign_f1 = (2 * ign_precision * recall / (ign_precision + recall)
              if (ign_precision + recall) else 0.0)

    return {
        "f1": f1,
        "ign_f1": ign_f1,
        "precision": precision,
        "recall": recall,
        "ign_precision": ign_precision,
        "correct": correct,
        "correct_in_train": correct_in_train,
        "n_pred": n_pred,
        "n_gold": tot_relations,
    }
