"""Embedding providers behind a single contract.

Every provider exposes ``model_id: str`` (used as the cache namespace) and
``embed(texts: list[str]) -> np.ndarray`` returning an ``(n, dim)`` array. Heavy
backends (sentence-transformers, Vertex, OpenAI, Snowflake) are imported lazily
so the ``none`` provider and the cache/dispatch layer work with no optional deps.
"""
from __future__ import annotations

import json
import os
import urllib.request
import uuid
from typing import Any, Protocol, runtime_checkable

import numpy as np

_DEFAULT_LOCAL_MODEL = "all-MiniLM-L6-v2"
_DEFAULT_OPENAI_MODEL = "text-embedding-3-small"
_DEFAULT_SNOWFLAKE_CORTEX_MODEL = "snowflake-arctic-embed-m-v1.5"


@runtime_checkable
class EmbeddingProvider(Protocol):
    model_id: str

    def embed(self, texts: list[str]) -> np.ndarray: ...


class NoneProvider:
    """Returns deterministic zero vectors — the ``provider="none"`` contract.

    Lets callers run the embedding code path with no model and no network; the
    embedding signal is neutral rather than a hard dependency.
    """

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim
        self.model_id = "none"

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.zeros((len(texts), self.dim), dtype=np.float32)


class LocalProvider:
    """Local sentence-transformers embeddings (no cloud dependency)."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model or _DEFAULT_LOCAL_MODEL
        self.model_id = f"local:{self.model}"
        self._embedder = None

    def embed(self, texts: list[str]) -> np.ndarray:
        if self._embedder is None:
            from goldenmatch.core.embedder import Embedder

            self._embedder = Embedder(self.model)
        # Bypass Embedder's whole-array cache (we cache per-text ourselves) by
        # handing it a unique key each call.
        arr = self._embedder.embed_column(list(texts), cache_key=uuid.uuid4().hex)
        return np.asarray(arr, dtype=np.float32)


class VertexProvider:
    """Google Vertex AI embeddings."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model
        self.model_id = f"vertex:{model}" if model else "vertex"
        self._embedder = None

    def embed(self, texts: list[str]) -> np.ndarray:
        if self._embedder is None:
            from goldenmatch.core.vertex_embedder import VertexEmbedder

            self._embedder = (
                VertexEmbedder(model=self.model) if self.model else VertexEmbedder()
            )
        arr = self._embedder.embed_column(list(texts), cache_key=uuid.uuid4().hex)
        return np.asarray(arr, dtype=np.float32)


class OpenAIProvider:
    """OpenAI embeddings via the REST API (stdlib only, no SDK required)."""

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.model = model or _DEFAULT_OPENAI_MODEL
        self.model_id = f"openai:{self.model}"
        self._api_key = api_key

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        api_key = self._api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "provider='openai' requires an API key (pass api_key= or set "
                "OPENAI_API_KEY)"
            )
        payload = json.dumps({"model": self.model, "input": list(texts)}).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/embeddings",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:  # noqa: S310 - fixed https endpoint
            body = json.loads(resp.read().decode())
        rows = sorted(body["data"], key=lambda d: d["index"])
        return np.asarray([r["embedding"] for r in rows], dtype=np.float32)


class SnowflakeCortexProvider:
    """Snowflake Cortex embeddings via ``SNOWFLAKE.CORTEX.EMBED_TEXT_<dim>``.

    Runs the embedding step *inside* the customer's Snowflake account -- no
    data egress, no separate API contract, no extra billing relationship.
    Pairs with the ``cortex_cosine`` matchkey scorer in dbt-goldensuite so a
    full dedupe pipeline can run with embeddings without ever leaving
    Snowflake.

    Model + dim -- pass ``model=`` to override the default. Dim is inferred
    from a small catalog of known Cortex models; pass ``model_dim=`` to
    register a model that isn't listed.

    Connection -- pass a live ``snowflake.connector.connection`` via
    ``connection=`` (the dbt-on-Snowflake adapter already owns one), OR let
    the provider build one from ``SNOWFLAKE_*`` env vars. The provider does
    NOT auto-construct a Snowpark Session; the raw connector is sufficient
    and keeps the dependency footprint minimal.
    """

    # Models bundled with Snowflake Cortex EMBED_TEXT_<dim>. Confirmed on
    # the public Cortex catalog; pass ``model_dim`` to register others.
    _KNOWN_DIMS: dict[str, int] = {
        # Snowflake's own family.
        "snowflake-arctic-embed-xs": 384,
        "snowflake-arctic-embed-s": 384,
        "snowflake-arctic-embed-m": 768,
        "snowflake-arctic-embed-m-v1.5": 768,
        "snowflake-arctic-embed-l-v2.0": 1024,
        # Third-party models surfaced by Cortex.
        "e5-base-v2": 768,
        "nv-embed-qa-4": 1024,
        "voyage-multilingual-2": 1024,
        "multilingual-e5-large": 1024,
    }

    # Chunked SQL avoids a single multi-MB query body on big batches.
    _DEFAULT_CHUNK_SIZE = 500

    def __init__(
        self,
        model: str | None = None,
        *,
        connection: Any = None,
        model_dim: int | None = None,
        chunk_size: int | None = None,
        connection_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self.model = model or _DEFAULT_SNOWFLAKE_CORTEX_MODEL
        self.model_id = f"snowflake-cortex:{self.model}"
        if model_dim is not None:
            self.dim = int(model_dim)
        elif self.model in self._KNOWN_DIMS:
            self.dim = self._KNOWN_DIMS[self.model]
        else:
            raise ValueError(
                f"unknown Snowflake Cortex model {self.model!r}; pass "
                f"model_dim= or use one of {sorted(self._KNOWN_DIMS)}"
            )
        self._fn_name = f"SNOWFLAKE.CORTEX.EMBED_TEXT_{self.dim}"
        self._conn = connection
        self._conn_kwargs = dict(connection_kwargs or {})
        self._chunk_size = chunk_size or self._DEFAULT_CHUNK_SIZE

    def _open_conn(self) -> tuple[Any, bool]:
        """Return ``(connection, owned)``. If ``owned`` is True the caller
        must close it; if False the connection was supplied by the user.

        The env-driven path mirrors goldenmatch's existing Snowflake
        connector usage -- ``SNOWFLAKE_ACCOUNT`` / ``SNOWFLAKE_USER`` plus
        either ``SNOWFLAKE_PASSWORD`` or an OAuth ``SNOWFLAKE_TOKEN``.
        """
        if self._conn is not None:
            return self._conn, False
        try:
            import snowflake.connector  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "provider='snowflake_cortex' requires the snowflake-connector-python "
                "package (`pip install goldenmatch[snowflake]`)"
            ) from exc
        kwargs: dict[str, Any] = {
            "account": os.environ.get("SNOWFLAKE_ACCOUNT"),
            "user": os.environ.get("SNOWFLAKE_USER"),
        }
        for env_key, kw_key in (
            ("SNOWFLAKE_WAREHOUSE", "warehouse"),
            ("SNOWFLAKE_ROLE", "role"),
            ("SNOWFLAKE_DATABASE", "database"),
            ("SNOWFLAKE_SCHEMA", "schema"),
        ):
            v = os.environ.get(env_key)
            if v:
                kwargs[kw_key] = v
        if os.environ.get("SNOWFLAKE_PASSWORD"):
            kwargs["password"] = os.environ["SNOWFLAKE_PASSWORD"]
        elif os.environ.get("SNOWFLAKE_TOKEN"):
            kwargs["authenticator"] = os.environ.get(
                "SNOWFLAKE_AUTHENTICATOR", "OAUTH",
            )
            kwargs["token"] = os.environ["SNOWFLAKE_TOKEN"]
        kwargs.update(self._conn_kwargs)
        missing = [k for k in ("account", "user") if not kwargs.get(k)]
        if missing:
            raise RuntimeError(
                "provider='snowflake_cortex' needs a connection; set "
                f"{', '.join('SNOWFLAKE_' + m.upper() for m in missing)} "
                "or pass connection= / connection_kwargs="
            )
        return snowflake.connector.connect(**kwargs), True

    def _embed_chunk(self, conn: Any, chunk: list[str]) -> np.ndarray:
        """Embed one chunk of texts in a single round-trip.

        Uses ``FLATTEN(PARSE_JSON(?))`` to fan a JSON array of texts back
        into rows so the whole batch ships as one parameterized statement.
        The ``ROW_NUMBER()`` is what preserves input order on the way back.
        """
        sql = (
            f"WITH input AS ("
            f"  SELECT ROW_NUMBER() OVER (ORDER BY SEQ4()) - 1 AS idx, "
            f"         value::STRING AS txt "
            f"  FROM TABLE(FLATTEN(input => PARSE_JSON(%s)))"
            f") "
            f"SELECT idx, {self._fn_name}(%s, txt) AS vec "
            f"FROM input ORDER BY idx"
        )
        cur = conn.cursor()
        try:
            cur.execute(sql, (json.dumps(chunk), self.model))
            rows = cur.fetchall()
        finally:
            cur.close()
        if len(rows) != len(chunk):
            raise RuntimeError(
                f"Cortex returned {len(rows)} rows for a {len(chunk)}-text "
                "batch; query rewriting may have dropped rows"
            )
        out = np.empty((len(chunk), self.dim), dtype=np.float32)
        for i, (_idx, vec) in enumerate(rows):
            # Snowflake returns VECTOR over the connector as a Python list of
            # floats. Older driver versions surface it as a JSON string --
            # accept both.
            if isinstance(vec, str):
                vec = json.loads(vec)
            arr = np.asarray(vec, dtype=np.float32)
            if arr.shape != (self.dim,):
                raise RuntimeError(
                    f"Cortex returned dim={arr.shape[0]} for model "
                    f"{self.model!r}; expected {self.dim}. "
                    "Pass model_dim= to override."
                )
            out[i] = arr
        return out

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        conn, owned = self._open_conn()
        try:
            chunks = [
                texts[i:i + self._chunk_size]
                for i in range(0, len(texts), self._chunk_size)
            ]
            return np.vstack([self._embed_chunk(conn, c) for c in chunks])
        finally:
            if owned:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass


class InHouseProvider:
    """The local, in-house char-n-gram + learned-projection embedder.

    Wraps a trained :class:`~goldenmatch.embeddings.inhouse.model.GoldenEmbedModel`
    (passed directly or loaded from a saved-model path). Runs the projection via
    onnxruntime when available, else the numpy reference forward pass.
    """

    def __init__(self, model: object, *, backend: str = "auto") -> None:
        from goldenmatch.embeddings.inhouse.model import GoldenEmbedModel

        if isinstance(model, GoldenEmbedModel):
            self._model = model
        elif model:
            self._model = GoldenEmbedModel.load(model)  # type: ignore[arg-type]
        else:
            raise ValueError(
                "provider='inhouse' requires model= (a saved-model path or a "
                "GoldenEmbedModel instance). Train one with "
                "goldenmatch.embeddings.inhouse.train_embedder(...)."
            )
        self.backend = backend
        self.model_id = self._model.model_id

    def embed(self, texts: list[str]) -> np.ndarray:
        return self._model.embed(list(texts), backend=self.backend)


def resolve_provider(
    provider: str | EmbeddingProvider,
    *,
    model: str | None = None,
    dim: int = 384,
) -> EmbeddingProvider:
    """Turn a provider name into a provider object, or pass an object through."""
    if not isinstance(provider, str):
        return provider
    name = provider.lower()
    if name == "none":
        return NoneProvider(dim=dim)
    if name == "local":
        return LocalProvider(model)
    if name == "vertex":
        return VertexProvider(model)
    if name == "openai":
        return OpenAIProvider(model)
    if name in ("snowflake_cortex", "snowflake-cortex", "cortex"):
        return SnowflakeCortexProvider(model)
    if name == "inhouse":
        return InHouseProvider(model)
    raise ValueError(
        f"unknown embedding provider {provider!r} (expected 'local', 'vertex', "
        "'openai', 'snowflake_cortex', 'inhouse', 'none', or a provider object)"
    )
