"""Offline ensemble sweep over dumped checkpoint logits -- CPU only, no GPU, no torch.

`modal_app.ensemble_eval` dumps each checkpoint's dev+test pre-threshold logits to the
Volume as `.npy`; pull them (`modal volume get redocred-lb /logits ./logits`) and this
script does the whole subset x threshold-offset x top-k search in vectorised numpy, then
scores the dev-selected config with the exact official DocRED scorer. Because the gold
label matrix and pair order are tokenizer-independent, everything is rebuilt locally from
`data/` with a stub tokenizer -- so re-sweeping costs zero GPU.

    python ensemble_sweep.py --logits ./logits

Reports the honest tuning spectrum (single -> ensemble -> +dev-tuned threshold) plus the
dev-selected best config's official test F1 / Ign F1.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from ensemble_ops import decode_preds, fast_f1, predict_np  # noqa: E402
from prepro import build_rel2id, read_docred  # noqa: E402
from scoring import facts_in_train, official_evaluate, to_submission  # noqa: E402


class _StubTokenizer:
    """Tokenizer stand-in: labels + pair order are tokenizer-independent, so this rebuilds
    the gold matrices and `hts` order that align with any checkpoint's dumped logits."""

    def tokenize(self, t):
        return [t]

    def convert_tokens_to_ids(self, toks):
        return [1 if x == "*" else 2 for x in toks]

    def build_inputs_with_special_tokens(self, ids):
        return [0] + ids + [0]


def _gold_and_hts(docs, rel2id):
    feats = read_docred(docs, _StubTokenizer(), rel2id)
    gold = np.asarray([lab for f in feats for lab in f["labels"]], dtype=np.int8)
    hts = [f["hts"] for f in feats]
    return gold, hts


def main():
    ap = argparse.ArgumentParser(description="Offline ensemble sweep over dumped logits")
    ap.add_argument("--logits", required=True, help="dir with <tag>_{dev,test}.npy")
    ap.add_argument("--data", default=str(HERE / "data"), help="Re-DocRED data dir")
    ap.add_argument("--tags", default="deberta-s13,deberta-s41,deberta-evi,roberta-s7")
    args = ap.parse_args()

    data = Path(args.data)
    train = json.load(open(data / "train_revised.json"))
    dev = json.load(open(data / "dev_revised.json"))
    test = json.load(open(data / "test_revised.json"))
    rel2id = build_rel2id(train, dev, test)
    id2rel = {v: k for k, v in rel2id.items()}
    train_facts = facts_in_train(train)

    gold_dev, _ = _gold_and_hts(dev, rel2id)
    _, hts_test = _gold_and_hts(test, rel2id)

    tags = [t.strip() for t in args.tags.split(",")]
    log = Path(args.logits)
    dev_l = {t: np.load(log / f"{t}_dev.npy") for t in tags}
    test_l = {t: np.load(log / f"{t}_test.npy") for t in tags}
    assert gold_dev.shape[0] == dev_l[tags[0]].shape[0], "gold/logits pair-count mismatch"

    def avg(split, subset):
        return sum(split[t] for t in subset) / len(subset)

    def official(logits, delta, num):
        preds = predict_np(logits, delta, num)
        sub = to_submission(decode_preds(preds, hts_test, id2rel), test)
        return official_evaluate(sub, test, train_facts)

    def best_delta(subset, num, lo=-0.5, hi=6.0, step=0.1):
        da = avg(dev_l, subset)
        ds = np.round(np.arange(lo, hi + 1e-9, step), 3)
        return float(max(ds, key=lambda d: fast_f1(predict_np(da, float(d), num), gold_dev)))

    # honest spectrum
    singles = {t: official(test_l[t], 0.0, 4)["f1"] for t in tags}
    bs = max(singles, key=singles.get)
    print(f"best single ({bs}):            F1 {singles[bs]:.4f}")
    print(f"full-{len(tags)} ensemble, delta=0:     F1 {official(avg(test_l, tags), 0.0, 4)['f1']:.4f}")
    d4 = best_delta(tags, 4)
    r4 = official(avg(test_l, tags), d4, 4)
    print(f"full-{len(tags)} + dev-threshold d*={d4:.1f}: F1 {r4['f1']:.4f}  Ign {r4['ign_f1']:.4f}  "
          f"P {r4['precision']:.3f}  R {r4['recall']:.3f}")

    # full dev-selection over subset x delta x top-k
    best = None
    for num in (2, 3, 4):
        for r in range(2, len(tags) + 1):
            for subset in itertools.combinations(tags, r):
                da = avg(dev_l, subset)
                for d in np.round(np.arange(-0.5, 6.01, 0.1), 3):
                    f1 = fast_f1(predict_np(da, float(d), num), gold_dev)
                    if best is None or f1 > best["dev_f1"]:
                        best = {"subset": list(subset), "delta": float(d),
                                "num_labels": num, "dev_f1": f1}
    rb = official(avg(test_l, best["subset"]), best["delta"], best["num_labels"])
    print(f"dev-selected {best['subset']} d={best['delta']:.1f} k={best['num_labels']} "
          f"(dev F1 {best['dev_f1']:.4f}):")
    print(f"  -> OFFICIAL TEST F1 {rb['f1']:.4f}  Ign {rb['ign_f1']:.4f}  "
          f"P {rb['precision']:.3f}  R {rb['recall']:.3f}")


if __name__ == "__main__":
    main()
