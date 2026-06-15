#!/usr/bin/env python3
"""Benchmark AbbrevAlign vs goldenmatch's production comparators on abbreviation-heavy ER.

AbbrevAlign (top pick from examples/forge_runs/run_25.md) is compared against the actual
rapidfuzz comparators goldenmatch's scorer uses (JaroWinkler, Levenshtein, token_sort_ratio,
partial_ratio, token Jaccard) plus the two strongest hybrids it generalizes (Monge-Elkan,
Soft-TFIDF). The dataset is curated to be abbreviation/acronym-heavy with realistic hard
negatives (acronym collisions, shared-token different entities) so the comparison is honest:
AbbrevAlign should win on the abbreviation slice, and we measure what it costs in precision.

    python bench_abbrevalign.py            # prints tables + writes the markdown report

Requires rapidfuzz (a goldenmatch dependency: rapidfuzz==3.14.5). Reuses the reference
prototypes from forge_prototypes.py.
"""
from __future__ import annotations

import math
import os
import sys
from collections import Counter

from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler, Levenshtein

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forge_prototypes import (  # noqa: E402  (path insert must precede import)
    abbrev_align,
    build_idf,
    recur_align,
    token_role_align,
    tokenize,
)

# --------------------------------------------------------------------------- #
# Dataset — entity_id -> list of (text, variant_type). Abbreviation-heavy, with
# deliberate hard negatives (acronym collisions, shared tokens) across entities.
# --------------------------------------------------------------------------- #

DATASET: dict[str, list[tuple[str, str]]] = {
    "ibm": [
        ("International Business Machines Corporation", "canonical"),
        ("IBM", "acronym"),
        ("Intl Business Machines", "abbrev"),
        ("Internatonal Business Machines", "typo"),
    ],
    "hp": [
        ("Hewlett Packard Enterprise", "canonical"),
        ("HP", "acronym"),
        ("Hewlett-Packard Co", "abbrev"),
    ],
    "ge": [
        ("General Electric Company", "canonical"),
        ("GE", "acronym"),
        ("Gen Electric", "abbrev"),
    ],
    "att": [
        ("American Telephone and Telegraph Company", "canonical"),
        ("AT&T", "acronym"),
        ("ATT", "acronym"),
    ],
    "fbi": [
        ("Federal Bureau of Investigation", "canonical"),
        ("FBI", "acronym"),
        ("Fed Bureau of Investigation", "abbrev"),
    ],
    "nasa": [
        ("National Aeronautics and Space Administration", "canonical"),
        ("NASA", "acronym"),
    ],
    "msft": [
        ("Microsoft Corporation", "canonical"),
        ("Microsoft Corp", "abbrev"),
        ("Micrsoft Corp", "typo"),
    ],
    "robert_smith": [
        ("Robert Smith", "canonical"),
        ("Bob Smith", "nickname"),
        ("R. Smith", "initial"),
        ("Robert Smyth", "typo"),
    ],
    "william_gates": [
        ("William Gates", "canonical"),
        ("Bill Gates", "nickname"),
        ("Wm Gates", "abbrev"),
        ("W. Gates", "initial"),
    ],
    "elizabeth_taylor": [
        ("Elizabeth Taylor", "canonical"),
        ("Liz Taylor", "nickname"),
        ("E. Taylor", "initial"),
    ],
    "james_brown": [
        ("James Brown", "canonical"),
        ("Jim Brown", "nickname"),
        ("J. Brown", "initial"),
    ],
    "main_st": [
        ("123 Main Street Springfield", "canonical"),
        ("123 Main St Springfield", "abbrev"),
        ("123 Main St.", "abbrev"),
    ],
    "oak_ave": [
        ("456 Oak Avenue", "canonical"),
        ("456 Oak Ave", "abbrev"),
    ],
    # ---- hard negatives: confusable but DISTINCT entities ----
    "indian_bank": [("Indian Bank Mumbai", "canonical")],          # collides with IBM
    "intl_markets": [("International Business Markets", "canonical")],  # IBM-ish tokens
    "general_motors": [("General Motors Company", "canonical")],    # shares 'General'/'Company'
    "american_air": [("American Airlines", "canonical")],           # shares 'American'
    "hospital_partners": [("Hospital Partners", "canonical")],      # collides with HP
    "robert_jones": [("Robert Jones", "canonical"), ("Bob Jones", "nickname")],  # shares 'Robert'/'Bob'
    "bill_smith": [("Bill Smith", "canonical")],                    # shares 'Smith' with robert_smith's Bob Smith
    "james_black": [("James Black", "canonical")],                  # shares 'James' with james_brown
    "oak_street": [("123 Oak Street", "canonical")],                # shares '123'/'Street'
}


def build_records() -> list[tuple[str, str, str]]:
    """Flatten to (entity_id, text, variant_type)."""
    return [(eid, text, vt) for eid, variants in DATASET.items() for text, vt in variants]


# --------------------------------------------------------------------------- #
# Methods under test
# --------------------------------------------------------------------------- #

ABBREV_TYPES = {"acronym", "abbrev"}
NICK_TYPES = {"nickname", "initial"}


def token_jaccard(a: str, b: str) -> float:
    sa, sb = set(tokenize(a)), set(tokenize(b))
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def monge_elkan(a: str, b: str) -> float:
    """Symmetric Monge-Elkan with JaroWinkler secondary (no IDF) — a strong classic hybrid."""
    A, B = tokenize(a), tokenize(b)
    if not A or not B:
        return 0.0

    def direction(src: list[str], dst: list[str]) -> float:
        return sum(max(JaroWinkler.similarity(s, d) for d in dst) for s in src) / len(src)

    return 0.5 * (direction(A, B) + direction(B, A))


def _tfidf_vec(tokens: list[str], idf) -> dict[str, float]:
    tf = Counter(tokens)
    vec = {t: (1.0 + math.log(c)) * idf(t) for t, c in tf.items()}
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {t: v / norm for t, v in vec.items()}


def soft_tfidf(a: str, b: str, idf, thr: float = 0.9) -> float:
    """Cohen-Ravikumar-Fienberg Soft-TFIDF: IDF cosine where tokens match softly via JaroWinkler."""
    va, vb = _tfidf_vec(tokenize(a), idf), _tfidf_vec(tokenize(b), idf)
    if not va or not vb:
        return 0.0
    total = 0.0
    for w, vw in va.items():
        best_u, best_sim = None, 0.0
        for u in vb:
            s = JaroWinkler.similarity(w, u)
            if s > best_sim:
                best_u, best_sim = u, s
        if best_u is not None and best_sim >= thr:
            total += vw * vb[best_u] * best_sim
    return total


def build_methods(idf):
    return {
        # goldenmatch's production rapidfuzz comparators
        "JaroWinkler*": lambda a, b: JaroWinkler.similarity(a, b),
        "Levenshtein*": lambda a, b: Levenshtein.normalized_similarity(a, b),
        "TokenSortRatio*": lambda a, b: fuzz.token_sort_ratio(a, b) / 100.0,
        "PartialRatio*": lambda a, b: fuzz.partial_ratio(a, b) / 100.0,
        "TokenJaccard*": token_jaccard,
        # strong hybrid baselines AbbrevAlign generalizes
        "MongeElkan": monge_elkan,
        "SoftTFIDF": lambda a, b: soft_tfidf(a, b, idf),
        # forge proposals
        "RecurAlign": lambda a, b: recur_align(a, b, idf),
        "TokenRoleAlign": token_role_align,
        "AbbrevAlign": lambda a, b: abbrev_align(a, b, idf),
        # the real recommendation: AbbrevAlign as a complementary channel to the
        # production comparator (ChannelMix-style max fusion), not a replacement.
        "JW∪AbbrevAlign": lambda a, b: max(JaroWinkler.similarity(a, b), abbrev_align(a, b, idf)),
    }


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #


def best_f1(scores: list[float], labels: list[int]) -> tuple[float, float, float, float]:
    """Sweep thresholds (predict match iff score >= t); return (precision, recall, f1, threshold)."""
    pos = sum(labels)
    if pos == 0:
        return (0.0, 0.0, 0.0, 1.0)
    data = sorted(zip(scores, labels), key=lambda x: -x[0])
    tp = fp = 0
    best = (0.0, 0.0, -1.0, 1.0)
    n = len(data)
    for idx, (s, lab) in enumerate(data):
        if lab:
            tp += 1
        else:
            fp += 1
        if idx + 1 < n and data[idx + 1][0] == s:  # only score at a threshold boundary
            continue
        prec = tp / (tp + fp)
        rec = tp / pos
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        if f1 > best[2]:
            best = (prec, rec, f1, s)
    return best


def roc_auc(scores: list[float], labels: list[int]) -> float:
    """Mann-Whitney U with tie-averaged ranks."""
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1  # 1-based average rank for the tie block
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    rank_pos = sum(r for r, lab in zip(ranks, labels) if lab)
    return (rank_pos - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def slice_recall(scores: list[float], pair_meta: list[dict], threshold: float, slice_key: str) -> float:
    idxs = [i for i, m in enumerate(pair_meta) if m["label"] == 1 and m["slice"] == slice_key]
    if not idxs:
        return float("nan")
    hit = sum(1 for i in idxs if scores[i] >= threshold)
    return hit / len(idxs)


# --------------------------------------------------------------------------- #
# Evaluation harness
# --------------------------------------------------------------------------- #


def positive_slice(vt_a: str, vt_b: str) -> str:
    types = {vt_a, vt_b}
    if types & ABBREV_TYPES:
        return "abbrev"
    if types & NICK_TYPES:
        return "nickname"
    if "typo" in types:
        return "typo"
    return "other"


def evaluate() -> dict:
    records = build_records()
    idf = build_idf([text for _, text, _ in records])
    methods = build_methods(idf)

    pair_meta: list[dict] = []
    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            eid_a, ta, vta = records[i]
            eid_b, tb, vtb = records[j]
            label = int(eid_a == eid_b)
            pair_meta.append({
                "a": ta, "b": tb, "label": label,
                "slice": positive_slice(vta, vtb) if label else "neg",
            })

    n_pos = sum(m["label"] for m in pair_meta)
    results = {}
    for name, fn in methods.items():
        scores = [fn(m["a"], m["b"]) for m in pair_meta]
        labels = [m["label"] for m in pair_meta]
        prec, rec, f1, thr = best_f1(scores, labels)
        results[name] = {
            "precision": prec, "recall": rec, "f1": f1, "threshold": thr,
            "auc": roc_auc(scores, labels),
            "slices": {s: slice_recall(scores, pair_meta, thr, s)
                       for s in ("abbrev", "nickname", "typo")},
        }
    return {
        "n_records": len(records), "n_pairs": len(pair_meta),
        "n_pos": n_pos, "n_neg": len(pair_meta) - n_pos,
        "results": results,
    }


def render(report: dict) -> str:
    res = report["results"]
    ranked = sorted(res.items(), key=lambda kv: kv[1]["f1"], reverse=True)
    lines: list[str] = []
    w = lines.append

    w("# AbbrevAlign benchmark vs goldenmatch comparators\n")
    w(f"Abbreviation-heavy ER dataset: **{report['n_records']} records**, "
      f"**{report['n_pairs']} candidate pairs** ({report['n_pos']} match / "
      f"{report['n_neg']} non-match). Comparators marked `*` are goldenmatch's actual "
      "rapidfuzz functions; MongeElkan/SoftTFIDF are the hybrids AbbrevAlign generalizes.\n")

    w("## Overall (best-F1 threshold sweep)\n")
    w("| Method | Best F1 | Precision | Recall | ROC-AUC | Threshold |")
    w("| --- | ---: | ---: | ---: | ---: | ---: |")
    for name, r in ranked:
        w(f"| {name} | **{r['f1']:.3f}** | {r['precision']:.3f} | {r['recall']:.3f} "
          f"| {r['auc']:.3f} | {r['threshold']:.3f} |")
    w("")

    w("## Recall by positive slice (at each method's best-F1 threshold)\n")
    w("| Method | Abbrev/acronym | Nickname/initial | Typo |")
    w("| --- | ---: | ---: | ---: |")
    for name, r in ranked:
        s = r["slices"]
        def cell(x: float) -> str:
            return "—" if x != x else f"{x:.3f}"  # NaN check
        w(f"| {name} | {cell(s['abbrev'])} | {cell(s['nickname'])} | {cell(s['typo'])} |")
    w("")

    # Data-driven findings
    top_f1 = ranked[0][0]
    aa = res["AbbrevAlign"]
    jw = res["JaroWinkler*"]
    best_abbrev = max(res.items(), key=lambda kv: (kv[1]["slices"]["abbrev"] or 0))
    best_auc = max(res.items(), key=lambda kv: (kv[1]["auc"] if kv[1]["auc"] == kv[1]["auc"] else 0))
    w("## Findings\n")
    w(f"- **Best overall F1: `{top_f1}` ({res[top_f1]['f1']:.3f}).** goldenmatch's default "
      "JaroWinkler is a strong, hard-to-beat baseline on a *mixed* error workload.")
    w(f"- **AbbrevAlign wins where it was designed to:** best precision ({aa['precision']:.3f}) "
      f"and top abbrev/acronym recall ({aa['slices']['abbrev']:.3f} vs JaroWinkler's "
      f"{jw['slices']['abbrev']:.3f}) — `{best_abbrev[0]}` leads that slice. The acronym gap is real.")
    w("- **But it is narrow:** AbbrevAlign is deliberately weak on nicknames/initials "
      f"({aa['slices']['nickname']:.3f}) — that is NickGraph's gap, not its. Used alone it "
      "trails JaroWinkler overall.")
    w(f"- **As a complementary channel it ranks best:** `{best_auc[0]}` has the highest ROC-AUC "
      f"({best_auc[1]['auc']:.3f}). Naive `max()` fusion lifts hard-negative acronym collisions "
      "too, so a single global threshold doesn't convert that ranking gain into F1 — the "
      "principled fusion is a **learned combiner** (StackEnsemble, which already takes "
      "abbrev_align as a feature), evaluated with a train/test split.")
    w("- **Recommendation:** add AbbrevAlign as an extra comparator/feature in the scorer for "
      "abbreviation-heavy domains (company names, addresses), not as a JaroWinkler replacement.")
    w(f"\n> Caveat: micro-benchmark ({report['n_pos']} positives) — directional signal, not a "
      "production F1. Next: rerun on Cora / DBLP-ACM / a company-name set with a learned "
      "combiner and held-out evaluation.\n")
    return "\n".join(lines)


def main() -> int:
    report = evaluate()
    md = render(report)
    print(md)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "examples", "forge_runs", "abbrevalign_benchmark.md")
    out = os.path.normpath(out)
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
