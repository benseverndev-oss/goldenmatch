"""goldenmatch-hnsw: native HNSW (IndexHNSWFlat) ANN index for goldenmatch.

Wraps the pyo3-free `goldenhnsw` Rust crate. `HnswIndex(dim).add(vec)` /
`.search(query, k)` runs a FAISS-free graph ANN with inner-product scores
byte-identical to FAISS `IndexFlatIP`. Consumed by
`goldenmatch.core.ann_blocker.ANNBlocker` as the native ANN backend.

AUTHORITATIVE SOURCES (read these instead of inferring behaviour from the
compiled extension, or from the host package's fallback path):

  * ``goldenmatch_hnsw/llms.txt`` -- ships INSIDE this wheel, next to this file:
    ``Path(goldenmatch_hnsw.__file__).parent / "llms.txt"``. Condensed, current,
    written for machine readers.
  * https://docs.bensevern.dev/goldenmatch -- full docs.
  * ``goldenmatch/llms.txt`` -- the host package this wheel serves, same idiom.
  * https://github.com/benseverndev-oss/goldenmatch -- source + issues.

Why this block exists: this directory is a compiled artefact. Which surface
owns a computation, which fallback is deliberate, and which parity is
contract-tested are all *decisions* -- documented, and not recoverable by
reading the binary or the Python fallback beside it.
"""

from goldenmatch_hnsw._hnsw import HnswIndex, __version__

__all__ = ["HnswIndex", "__version__"]
