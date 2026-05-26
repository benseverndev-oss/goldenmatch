"""In-house embedding model: char n-gram features -> learned linear projection.

The projection (and optional bias) is the only trained part and the only thing
exported to ONNX. Inference is ``L2norm((featurize(text) @ W) + b)``. The numpy
forward pass is the source of truth; an ONNX/onnxruntime backend runs the exact
same graph and is asserted to match numerically.

A freshly-constructed model is already a usable embedder: ``W`` initializes as a
scaled Gaussian random projection (Johnson-Lindenstrauss), so cosine similarity
of the embeddings approximates char-n-gram overlap before any training. The
trainer (``trainer.py``) refines ``W`` from labeled match pairs.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from goldenmatch.embeddings.inhouse.featurizer import (
    CharNGramFeaturizer,
    FeaturizerConfig,
)


@dataclass(frozen=True)
class EmbedModelConfig:
    dim: int = 64
    use_bias: bool = False
    featurizer: FeaturizerConfig = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.featurizer is None:
            object.__setattr__(self, "featurizer", FeaturizerConfig())
        if self.dim <= 0:
            raise ValueError("dim must be positive")


def _random_projection(n_features: int, dim: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal((n_features, dim)) / np.sqrt(dim)).astype(np.float32)


class GoldenEmbedModel:
    """A char-n-gram + linear-projection embedder with an ONNX export path."""

    def __init__(
        self,
        config: EmbedModelConfig | None = None,
        weights: np.ndarray | None = None,
        bias: np.ndarray | None = None,
        seed: int = 0,
    ) -> None:
        self.config = config or EmbedModelConfig()
        self.featurizer = CharNGramFeaturizer(self.config.featurizer)
        f = self.featurizer.n_features
        d = self.config.dim
        if weights is None:
            weights = _random_projection(f, d, seed)
        weights = np.ascontiguousarray(weights, dtype=np.float32)
        if weights.shape != (f, d):
            raise ValueError(f"weights must be {(f, d)}, got {weights.shape}")
        self.weights = weights
        if self.config.use_bias:
            self.bias = (
                np.zeros(d, dtype=np.float32)
                if bias is None
                else np.ascontiguousarray(bias, dtype=np.float32)
            )
        else:
            self.bias = None
        self._ort_session: Any = None

    # ----- identity (cache namespace) -----
    @property
    def model_id(self) -> str:
        """Stable id tied to the actual weights, so a retrained model gets a
        fresh embedding-cache namespace."""
        h = hashlib.blake2b(self.weights.tobytes(), digest_size=8)
        if self.bias is not None:
            h.update(self.bias.tobytes())
        return f"inhouse:d{self.config.dim}:{h.hexdigest()}"

    # ----- forward -----
    def project(self, feats: np.ndarray) -> np.ndarray:
        z = feats @ self.weights
        if self.bias is not None:
            z = z + self.bias
        norms = np.linalg.norm(z, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        return (z / norms).astype(np.float32)

    def embed(self, texts: list[str | None], backend: str = "auto") -> np.ndarray:
        """Embed ``texts`` -> ``(n, dim)`` L2-normalized matrix.

        ``backend``: ``"numpy"`` (reference), ``"onnx"`` (onnxruntime), or
        ``"auto"`` — the fused native featurize+project kernel when available
        (no dense feature matrix; see ``_embed_fused``), else onnx, else numpy.
        """
        if not texts:
            return np.zeros((0, self.config.dim), dtype=np.float32)
        if backend == "auto":
            fused = self._embed_fused(texts)
            if fused is not None:
                return fused
        feats = self.featurizer.transform(texts)
        if backend == "numpy":
            return self.project(feats)
        if backend == "onnx":
            return self._project_onnx(feats)
        # auto, fused unavailable
        try:
            return self._project_onnx(feats)
        except ImportError:
            return self.project(feats)

    def _embed_fused(self, texts: list[str | None]) -> np.ndarray | None:
        """Native fused featurize+project: accumulate ``sign * W[idx]`` straight
        into the ``(n, dim)`` output, skipping the dense ``(n, n_features)``
        feature matrix and the dense matmul. Output is identical to the dense
        path (the feature-norm cancels under L2-normalization). Returns ``None``
        — so the caller falls back — when the native kernel is unavailable or the
        head has a bias (the bias breaks the feature-norm cancellation)."""
        if self.bias is not None:
            return None
        from goldenmatch.core._native_loader import native_enabled, native_module
        if not native_enabled("featurize"):
            return None
        mod = native_module()
        if not hasattr(mod, "char_ngram_project"):
            return None  # native ext present but predates the fused kernel
        fc = self.config.featurizer
        raw = mod.char_ngram_project(
            list(texts), self.weights.tobytes(), fc.n_features, self.config.dim,
            fc.ngram_min, fc.ngram_max, fc.lowercase, fc.boundary, fc.seed,
        )
        return np.frombuffer(raw, dtype=np.float32).reshape(len(texts), self.config.dim)

    # ----- ONNX -----
    def to_onnx_model(self):
        """Build the projection-head ONNX model (``onnx.ModelProto``).

        Input ``features`` ``[batch, n_features]`` -> ``embedding``
        ``[batch, dim]``. Featurization stays in the caller; this graph is the
        portable, language-agnostic learned head (what ``goldenembed-rs`` runs).
        """
        from onnx import TensorProto, checker, helper, numpy_helper

        f = self.featurizer.n_features
        d = self.config.dim
        inp = helper.make_tensor_value_info("features", TensorProto.FLOAT, [None, f])
        out = helper.make_tensor_value_info("embedding", TensorProto.FLOAT, [None, d])
        initializers = [numpy_helper.from_array(self.weights, "W")]
        nodes = [helper.make_node("MatMul", ["features", "W"], ["proj"])]
        norm_in = "proj"
        if self.bias is not None:
            initializers.append(numpy_helper.from_array(self.bias, "b"))
            nodes.append(helper.make_node("Add", ["proj", "b"], ["biased"]))
            norm_in = "biased"
        nodes.append(
            helper.make_node("LpNormalization", [norm_in], ["embedding"], p=2, axis=1)
        )
        graph = helper.make_graph(nodes, "goldenembed", [inp], [out], initializers)
        model = helper.make_model(
            graph, opset_imports=[helper.make_opsetid("", 13)], producer_name="goldenmatch"
        )
        model.ir_version = 9
        checker.check_model(model)
        return model

    def to_onnx(self, path: str | Path) -> Path:
        """Write the ONNX projection head to ``path``."""
        import onnx

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        onnx.save_model(self.to_onnx_model(), str(path))
        return path

    def _project_onnx(self, feats: np.ndarray) -> np.ndarray:
        if self._ort_session is None:
            import onnxruntime as ort  # raises ImportError if absent

            self._ort_session = ort.InferenceSession(
                self.to_onnx_model().SerializeToString(),
                providers=["CPUExecutionProvider"],
            )
        out = self._ort_session.run(
            ["embedding"], {"features": np.ascontiguousarray(feats, dtype=np.float32)}
        )
        return out[0]

    # ----- persistence -----
    def save(self, path: str | Path) -> Path:
        """Save weights (``.npz``), config (``.json``), and the ONNX head."""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        arrays = {"weights": self.weights}
        if self.bias is not None:
            arrays["bias"] = self.bias
        np.savez(path / "weights.npz", **arrays)
        cfg = {
            "dim": self.config.dim,
            "use_bias": self.config.use_bias,
            "featurizer": asdict(self.config.featurizer),
        }
        (path / "config.json").write_text(json.dumps(cfg, indent=2))
        try:
            self.to_onnx(path / "model.onnx")
        except ImportError:
            pass  # onnx optional; numpy weights are the source of truth
        return path

    @classmethod
    def load(cls, path: str | Path) -> GoldenEmbedModel:
        path = Path(path)
        cfg = json.loads((path / "config.json").read_text())
        config = EmbedModelConfig(
            dim=cfg["dim"],
            use_bias=cfg["use_bias"],
            featurizer=FeaturizerConfig(**cfg["featurizer"]),
        )
        data = np.load(path / "weights.npz")
        bias = data["bias"] if "bias" in data.files else None
        return cls(config=config, weights=data["weights"], bias=bias)
