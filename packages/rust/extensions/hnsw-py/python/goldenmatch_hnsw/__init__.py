"""goldenmatch-hnsw: native HNSW (IndexHNSWFlat) ANN index for goldenmatch.

Wraps the pyo3-free `goldenhnsw` Rust crate. `HnswIndex(dim).add(vec)` /
`.search(query, k)` runs a FAISS-free graph ANN with inner-product scores
byte-identical to FAISS `IndexFlatIP`. Consumed by
`goldenmatch.core.ann_blocker.ANNBlocker` as the native ANN backend.
"""

from goldenmatch_hnsw._hnsw import HnswIndex, __version__

__all__ = ["HnswIndex", "__version__"]
