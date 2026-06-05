"""goldenmatch_embed FFI UDF: shape + determinism + parity vs Python embed.

Mirrors test_datafusion_ffi_udf.py: pyarrow/datafusion are soft deps; the crate
is a HARD import (CI builds goldenmatch_datafusion_udf into .venv). The UDF reads
GOLDENEMBED_MODEL_DIR at construction, so it is set (via monkeypatch) BEFORE
EmbedUDF() is instantiated. The fixture is the tiny model committed alongside the
goldenembed crate.
"""
from pathlib import Path

import numpy as np
import pytest

pa = pytest.importorskip("pyarrow")
datafusion = pytest.importorskip("datafusion")  # noqa: F841  registered below
import goldenmatch_datafusion_udf  # noqa: E402,F401  HARD import (loud guard)
from goldenmatch.embeddings.inhouse.model import GoldenEmbedModel  # noqa: E402

# tests -> goldenmatch -> python -> packages -> <repo root>
_REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURE = _REPO_ROOT / "packages/rust/extensions/goldenembed/tests/fixtures/tiny_model"


def test_embed_udf_shape_and_parity(monkeypatch):
    monkeypatch.setenv("GOLDENEMBED_MODEL_DIR", str(FIXTURE))
    from datafusion import SessionContext, udf
    from goldenmatch_datafusion_udf import EmbedUDF

    ctx = SessionContext()
    ctx.register_udf(udf(EmbedUDF()))
    ctx.from_arrow(
        pa.table({"t": pa.array(["acme corp", "acme corp"], pa.string())}),
        name="rows",
    )
    batches = ctx.sql("SELECT goldenmatch_embed(t) AS e FROM rows").collect()
    out = batches[0].column(0).to_pylist()

    assert len(out) == 2
    assert len(out[0]) == 8  # model dim
    assert out[0] == out[1]  # determinism: identical input -> identical vector

    py = GoldenEmbedModel.load(str(FIXTURE)).embed(["acme corp"], backend="onnx")[0]
    v = np.asarray(out[0], dtype=np.float32)
    cos = float(np.dot(v, py) / (np.linalg.norm(v) * np.linalg.norm(py) + 1e-9))
    assert cos > 0.999, f"UDF vs python embed cosine {cos}"
