#!/usr/bin/env python3
"""Spike bench: does a local GGUF embedder (llama.cpp) produce useful ER signal,
and how fast is it on CPU? Measures dup-vs-nondup cosine separation + throughput.

This is the "measure before adopting" gate for the local-embedding path: a GGUF
model (bge-small, nomic-embed, ...) run in-process via llama-cpp-python, with no
cloud, no torch, no API cost. Compare the separation it gives on name variants
against the bar you'd need for ANN blocking / embedding scorers.

Needs `goldenmatch[llama]` + a GGUF model:
    GOLDENMATCH_LLAMA_GGUF=/path/bge-small.gguf \
        python scripts/bench_llama_embedder.py
"""

from __future__ import annotations

import os
import sys
import time

# Labeled name pairs: (a, b, is_duplicate). Duplicates are realistic ER variants
# (typo, nickname, dropped middle, transposed), non-dups are unrelated people.
PAIRS = [
    ("John Smith", "Jon Smith", True),
    ("Catherine Jones", "Kathryn Jones", True),
    ("Robert Williams", "Bob Williams", True),
    ("Maria Garcia Lopez", "Maria Garcia", True),
    ("Stephen O'Brien", "Steven OBrien", True),
    ("Elizabeth Taylor", "Liz Taylor", True),
    ("Michael Brown", "Micheal Brown", True),
    ("Jennifer Wilson", "Jenifer Wilson", True),
    ("John Smith", "Maria Garcia", False),
    ("Catherine Jones", "Robert Williams", False),
    ("Stephen O'Brien", "Jennifer Wilson", False),
    ("Elizabeth Taylor", "Michael Brown", False),
    ("Bob Williams", "Kathryn Jones", False),
    ("Liz Taylor", "Jon Smith", False),
]


def main() -> int:
    gguf = os.environ.get("GOLDENMATCH_LLAMA_GGUF")
    if not gguf or not os.path.exists(gguf):
        print("Set GOLDENMATCH_LLAMA_GGUF to a GGUF embedding model.", file=sys.stderr)
        return 2
    try:
        import numpy as np
        from goldenmatch.embeddings.providers import LlamaGGUFProvider
    except ImportError as e:
        print(f"Need goldenmatch[llama] + numpy: {e}", file=sys.stderr)
        return 2

    p = LlamaGGUFProvider(gguf)
    texts = sorted({t for a, b, _ in PAIRS for t in (a, b)})

    t0 = time.perf_counter()
    vecs = p.embed(texts)  # first call loads the model
    load_embed = time.perf_counter() - t0
    idx = {t: i for i, t in enumerate(texts)}

    # Warm throughput: re-embed (model loaded).
    t0 = time.perf_counter()
    for _ in range(5):
        p.embed(texts)
    warm = (time.perf_counter() - t0) / (5 * len(texts))

    dup_cos, nondup_cos = [], []
    for a, b, is_dup in PAIRS:
        c = float(vecs[idx[a]] @ vecs[idx[b]])
        (dup_cos if is_dup else nondup_cos).append(c)

    md, mn = float(np.mean(dup_cos)), float(np.mean(nondup_cos))
    print(f"model      : {os.path.basename(gguf)}  dim={vecs.shape[1]}")
    print(f"load+embed : {load_embed:.2f}s for {len(texts)} texts")
    print(f"throughput : {1.0/warm:,.0f} texts/sec (warm, CPU)")
    print(f"dup    cos : mean={md:.3f}  min={min(dup_cos):.3f}")
    print(f"nondup cos : mean={mn:.3f}  max={max(nondup_cos):.3f}")
    print(f"separation : {md - mn:+.3f}  (dup mean - nondup mean)")
    # A clean margin (e.g. > 0.1) means an ANN threshold can separate the classes.
    ok = md - mn > 0.05 and min(dup_cos) > max(nondup_cos) - 0.15
    print(f"verdict    : {'usable ER signal' if ok else 'weak — try a stronger GGUF model'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
