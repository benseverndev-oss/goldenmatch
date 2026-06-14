#!/usr/bin/env python3
"""Product-matching quality bench: does the local GGUF embedder give usable ER
signal on PRODUCT text (where embeddings actually matter)?

Product matching (Abt-Buy / Amazon-Google) is the domain where lexical/fuzzy
scoring struggles: the same product is listed tersely vs verbosely with
different vocab, while different products of the same brand share lots of words.
This is exactly where semantic embeddings should beat a lexical baseline.

The bench compares, on labeled product pairs:
  * GGUF embedder (llama.cpp, in-process) -- the #951 local backend
  * char-ngram lexical embedder           -- a no-semantics baseline
reporting cosine AUC (separability) + best-threshold P/R/F1 for each.

Data:
  * default: a curated Abt-Buy-style electronics set (hard same-brand negatives),
    run live here.
  * real Abt-Buy on the bench box:
      --tableA Abt.csv --tableB Buy.csv --matches abt_buy_perfectMapping.csv
    (id + name/description columns; matches = the id pairs). Negatives are
    sampled random cross-table non-matches.

Needs: llama-cpp-python + a GGUF embedding model (GOLDENMATCH_LLAMA_GGUF).
Usage:
    GOLDENMATCH_LLAMA_GGUF=/path/bge-small.gguf \
        python scripts/bench_llama_product_matching.py
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# Curated Abt-Buy-style pairs: (listing_a, listing_b, is_match). Positives are the
# same product listed tersely vs verbosely; negatives include hard same-brand /
# same-category distractors that share lots of words.
_CURATED: list[tuple[str, str, bool]] = [
    ("Sony DVPNC85H 5 Disc DVD Player", "Sony DVP-NC85H/B 5-Disc CD/DVD Player, Black", True),
    ("Canon PowerShot SD1100IS 8MP Digital Camera Blue", "Canon PowerShot SD1100 IS 8.0 MP Digital ELPH Camera (Blue)", True),
    ("Linksys WRT54GL Wireless-G Broadband Router", "Cisco-Linksys WRT54GL Wireless-G Broadband Router 54 Mbps", True),
    ("Apple iPod touch 8GB 2nd Gen MB528LL", "Apple iPod touch 8 GB (2nd Generation) MB528LL/A", True),
    ("Bose Companion 2 Series II Multimedia Speaker System", "Bose Companion 2 Series II Multimedia Computer Speakers", True),
    ("Logitech Harmony One Universal Remote 915000035", "Logitech Harmony One Advanced Universal Remote Control", True),
    ("Garmin nuvi 260W 4.3-inch GPS Navigator", 'Garmin nuvi 260W Portable GPS Navigation System 4.3" Widescreen', True),
    ("Western Digital My Book 1TB External Hard Drive", "WD My Book Essential 1 TB USB 2.0 External Hard Drive WDBAAF0010HBK", True),
    ("Panasonic Lumix DMC-FZ28 10MP Digital Camera", "Panasonic Lumix DMC-FZ28K 10.1 MP Digital Camera with 18x Zoom Black", True),
    ("Sennheiser HD555 Open Dynamic Hi-Fi Headphones", "Sennheiser HD 555 Professional Open Dynamic Stereo Headphones", True),
    # hard negatives: same brand / same category, different product
    ("Sony DVP-NC85H 5-Disc DVD Player", "Sony DVP-SR210P DVD Player Progressive Scan", False),
    ("Canon PowerShot SD1100IS 8MP Camera Blue", "Canon PowerShot SD1200 IS 10MP Camera Silver", False),
    ("Linksys WRT54GL Wireless-G Router", "Netgear WGR614 Wireless-G Router 54 Mbps", False),
    ("Apple iPod touch 8GB 2nd Gen", "Apple iPod nano 8GB 4th Generation Silver", False),
    ("Bose Companion 2 Series II Speakers", "Logitech Z-2300 THX 2.1 Speaker System", False),
    ("Garmin nuvi 260W GPS Navigator", "TomTom ONE 130 GPS Navigator", False),
    ("Western Digital My Book 1TB Hard Drive", "Seagate FreeAgent Go 500GB Portable Hard Drive", False),
    ("Panasonic Lumix DMC-FZ28 Camera", "Nikon Coolpix P80 10MP Camera 18x Zoom", False),
    ("Sennheiser HD555 Headphones", "Bose QuietComfort 15 Noise Cancelling Headphones", False),
    ("Logitech Harmony One Remote", "Logitech MX Revolution Cordless Laser Mouse", False),
]


def _have(m: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(m) is not None


# ---- embedders ----------------------------------------------------------------

class GGUFEmbedder:
    """In-process GGUF embeddings via llama.cpp (mirrors LlamaGGUFProvider)."""

    def __init__(self, path: str):
        from llama_cpp import Llama

        self._llm = Llama(model_path=path, embedding=True, n_ctx=512, verbose=False)
        self.name = f"gguf:{os.path.basename(path)}"

    def embed(self, texts):
        import numpy as np

        arr = np.asarray(self._llm.embed(list(texts)), dtype="float32")
        n = np.linalg.norm(arr, axis=1, keepdims=True)
        n[n == 0] = 1.0
        return arr / n


class CharNgramEmbedder:
    """Hashed char-3gram lexical embedder — the no-semantics baseline."""

    name = "char-ngram(lexical)"

    def __init__(self, dim: int = 1024):
        self.dim = dim

    def embed(self, texts):
        import numpy as np

        out = np.zeros((len(texts), self.dim), dtype="float32")
        for i, t in enumerate(texts):
            s = (t or "").lower()
            for j in range(max(0, len(s) - 2)):
                out[i, hash(s[j : j + 3]) % self.dim] += 1.0
        n = np.linalg.norm(out, axis=1, keepdims=True)
        n[n == 0] = 1.0
        return out / n


# ---- metrics ------------------------------------------------------------------

def _auc(pos: list[float], neg: list[float]) -> float:
    wins = ties = 0
    for p in pos:
        for q in neg:
            if p > q:
                wins += 1
            elif p == q:
                ties += 1
    denom = len(pos) * len(neg) or 1
    return (wins + 0.5 * ties) / denom


def _best_f1(pos: list[float], neg: list[float]):
    thresholds = sorted(set(pos + neg))
    best = (0.0, 0.0, 0.0, 0.0)  # f1, p, r, thr
    for thr in thresholds:
        tp = sum(1 for x in pos if x >= thr)
        fp = sum(1 for x in neg if x >= thr)
        fn = len(pos) - tp
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        if f1 > best[0]:
            best = (f1, prec, rec, thr)
    return best


def evaluate(embedder, pairs):
    import numpy as np

    texts = sorted({t for a, b, _ in pairs for t in (a, b)})
    t0 = time.perf_counter()
    vecs = embedder.embed(texts)
    dt = time.perf_counter() - t0
    idx = {t: i for i, t in enumerate(texts)}
    pos, neg = [], []
    for a, b, is_match in pairs:
        c = float(np.dot(vecs[idx[a]], vecs[idx[b]]))
        (pos if is_match else neg).append(c)
    auc = _auc(pos, neg)
    f1, prec, rec, thr = _best_f1(pos, neg)
    return {
        "name": embedder.name, "auc": auc, "f1": f1, "prec": prec, "rec": rec,
        "thr": thr, "pos_mean": float(np.mean(pos)), "neg_mean": float(np.mean(neg)),
        "embed_s": dt, "n_texts": len(texts),
    }


def load_real(table_a, table_b, matches, n_neg_per_pos=1):
    """Load an Abt-Buy-style dataset into (textA, textB, is_match) pairs."""
    import random

    import polars as pl

    def _text(row):
        parts = [str(row.get(c, "") or "") for c in ("name", "title", "description", "manufacturer", "price")]
        return " ".join(p for p in parts if p).strip()

    a = {str(r["id"]): _text(r) for r in pl.read_csv(table_a, infer_schema_length=0, encoding="utf8-lossy", ignore_errors=True).to_dicts()}
    b = {str(r["id"]): _text(r) for r in pl.read_csv(table_b, infer_schema_length=0, encoding="utf8-lossy", ignore_errors=True).to_dicts()}
    m = pl.read_csv(matches, infer_schema_length=0, encoding="utf8-lossy", ignore_errors=True).to_dicts()
    cols = list(m[0].keys())
    ka, kb = cols[0], cols[1]
    pairs, rng = [], random.Random(7)
    bids = list(b)
    matched = {(str(r[ka]), str(r[kb])) for r in m}
    for ra, rb in matched:
        if ra in a and rb in b:
            pairs.append((a[ra], b[rb], True))
            for _ in range(n_neg_per_pos):
                rb2 = rng.choice(bids)
                if (ra, rb2) not in matched:
                    pairs.append((a[ra], b[rb2], False))
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--table-a")
    ap.add_argument("--table-b")
    ap.add_argument("--matches")
    args = ap.parse_args()

    gguf = os.environ.get("GOLDENMATCH_LLAMA_GGUF")
    if not gguf or not os.path.exists(gguf):
        print("Set GOLDENMATCH_LLAMA_GGUF to a GGUF embedding model.", file=sys.stderr)
        return 2
    if not (_have("llama_cpp") and _have("numpy")):
        print("Need llama-cpp-python + numpy.", file=sys.stderr)
        return 2

    if args.table_a and args.table_b and args.matches:
        pairs = load_real(args.table_a, args.table_b, args.matches)
        src = f"real Abt-Buy ({args.table_a})"
    else:
        pairs = _CURATED
        src = "curated Abt-Buy-style electronics set"
    npos = sum(1 for _, _, m in pairs if m)
    print(f"dataset: {src}  ({len(pairs)} pairs, {npos} match / {len(pairs)-npos} non-match)\n")

    results = [evaluate(CharNgramEmbedder(), pairs), evaluate(GGUFEmbedder(gguf), pairs)]
    print(f"  {'embedder':<26} {'AUC':>6} {'F1':>6} {'P':>6} {'R':>6}  {'pos/neg cos':>14}")
    print("  " + "-" * 74)
    for r in results:
        print(f"  {r['name']:<26} {r['auc']:>6.3f} {r['f1']:>6.3f} {r['prec']:>6.3f} {r['rec']:>6.3f}  "
              f"{r['pos_mean']:>6.3f}/{r['neg_mean']:<6.3f}")
    gguf_r = results[1]
    lex_r = results[0]
    print(f"\n  GGUF AUC {gguf_r['auc']:.3f} vs lexical {lex_r['auc']:.3f}  "
          f"-> {'GGUF separates products better' if gguf_r['auc'] > lex_r['auc'] else 'no semantic gain here'}")
    print("  (real adopt decision = run --table-a/-b/--matches on Abt-Buy + Amazon-Google on the bench box,")
    print("   and compare vs Vertex/OpenAI embeddings where creds are available.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
