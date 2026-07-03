# goldenmatch-hnsw

Native **HNSW (`IndexHNSWFlat`)** approximate-nearest-neighbor index for
[GoldenMatch](https://github.com/benseverndev-oss/goldenmatch), shipped as a
small maturin/abi3 wheel with **zero C dependencies** (no FAISS, no ONNX, no
OpenSSL).

It is the native ANN backend behind `goldenmatch.core.ann_blocker.ANNBlocker`.
Where the FAISS `IndexFlatIP` / numpy paths are exact and scale linearly (or
worse) in the corpus size, HNSW gives sub-linear queries with recall
approaching 1.0 — and installs everywhere the pure-Python package does.

## Design

A thin PyO3 wrapper over the pyo3-free [`goldenhnsw`](../goldenhnsw) Rust crate,
mirroring the sibling `goldenmatch-embed` / `goldenmatch-native` wheels:

- **pyo3-free core.** `goldenhnsw` carries the algorithm and no CPython, so it
  can also be linked from the pgrx `postgres` crate or a DataFusion FFI surface
  later without embedding an interpreter.
- **No `rayon`.** Insertion is single-threaded by construction — the Python
  caller already parallelizes across probes/buckets, and the #688 rayon
  `LockLatch` futex-park (see the monorepo CLAUDE.md) cannot recur.
- **Inner-product scores** identical to FAISS `IndexFlatIP`. On the normal
  GoldenMatch path the embedder emits L2-normalized vectors, so the inner
  product *is* the cosine similarity.
- **Deterministic** graph for a given `(seed, insertion order)`; `ef_search`
  auto-scales to the corpus size for small indexes, so recall is *exact* at the
  scales the fallback-parity tests exercise.

## Usage

```python
import numpy as np
from goldenmatch_hnsw import HnswIndex

vecs = np.random.randn(10_000, 64).astype("<f4")
vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)

idx = HnswIndex(dim=64, m=16, ef_construction=200, ef_search=64)
idx.add_batch(vecs.tobytes(), n=len(vecs))          # fast bulk load

q = vecs[0]
idx.search(q.tolist(), k=10)                          # -> [(id, inner_product), ...]
idx.search_batch(vecs[:32].tobytes(), n=32, k=10)     # one list per query row
```

`add(vec)` (single incremental insert) and `__len__` / `.size` / `.dim` round
out the surface.
