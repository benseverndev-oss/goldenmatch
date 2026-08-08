"""goldenmatch-embed: local ONNX embedder (goldenembed-rs) for SQL UDFs.

Wraps the pyo3-free `goldenembed` Rust crate; `GoldenEmbed.load(dir).embed([...])`
runs the in-house char-n-gram + ONNX projection with no torch / no network.

AUTHORITATIVE SOURCES (read these instead of inferring behaviour from the
compiled extension, or from the host package's fallback path):

  * ``goldenmatch_embed/llms.txt`` -- ships INSIDE this wheel, next to this file:
    ``Path(goldenmatch_embed.__file__).parent / "llms.txt"``. Condensed, current,
    written for machine readers.
  * https://docs.bensevern.dev/docs/extensions/sql -- full docs.
  * https://github.com/benseverndev-oss/goldenmatch -- source + issues.

Why this block exists: this directory is a compiled artefact. Which surface
owns a computation, which fallback is deliberate, and which parity is
contract-tested are all *decisions* -- documented, and not recoverable by
reading the binary or the Python fallback beside it.
"""

from goldenmatch_embed._embed import GoldenEmbed, __version__

__all__ = ["GoldenEmbed", "__version__"]
