"""goldenmatch-embed: local ONNX embedder (goldenembed-rs) for SQL UDFs.

Wraps the pyo3-free `goldenembed` Rust crate; `GoldenEmbed.load(dir).embed([...])`
runs the in-house char-n-gram + ONNX projection with no torch / no network.
"""

from goldenmatch_embed._embed import GoldenEmbed, __version__

__all__ = ["GoldenEmbed", "__version__"]
