"""MongoDB connector for GoldenMatch.

Reads documents from a Mongo collection into a Polars DataFrame and
writes golden records back. Nested fields are flattened on read via
projection + dot-path expansion, mirroring the shape goldenmatch's
matchkeys expect (one column per leaf field).

Requires: ``pip install goldenmatch[mongo]`` (pulls ``pymongo``).

## Connection sourcing

The connector reads connection settings from three layers, in order
of precedence:

1. ``config['connection']`` -- explicit mongo URI passed at call time
2. ``self._credentials['key']`` -- env-var value if the consumer set
   ``credentials_env`` on the connector config
3. ``MONGO_URI`` env var -- the package-wide default

Database + collection are read from ``config`` on every call:

  - ``database``   -- target database name (required)
  - ``collection`` -- target collection name (required)

## Reading

``config['filter']``      -- Mongo query filter (default ``{}``)
``config['projection']``  -- field projection (default all)
``config['limit']``       -- optional row cap
``config['flatten']``     -- dot-flatten nested fields (default True);
                             ``{'a': {'b': 1}}`` -> column ``a.b``.

## Writing

``config['mode']`` is one of:

  - ``"append"`` (default)   -- ``insert_many``
  - ``"upsert"``             -- ``update_one(... upsert=True)`` per row,
                                keyed by ``config['key']`` (a column
                                name in the DataFrame)
  - ``"replace"``             -- drop the collection, then insert

Polars columns become document fields. ``__cluster_id__`` and other
internal columns are passed through; downstream code can drop them
before write via the ``drop`` kwarg.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import polars as pl

from goldenmatch.connectors.base import BaseConnector, ConnectorError

logger = logging.getLogger(__name__)


_INTERNAL_COLUMNS = {"__row_id__", "__source__"}


def _flatten_doc(doc: dict, prefix: str = "") -> dict:
    """Flatten a nested Mongo document into a one-level dict.

    ``{"name": "Alice", "address": {"city": "Raleigh"}}`` becomes
    ``{"name": "Alice", "address.city": "Raleigh"}``.

    Lists are NOT flattened element-wise (they stay as lists in the
    DataFrame). Mongo's ``_id`` is preserved as-is.
    """
    out: dict[str, Any] = {}
    for k, v in doc.items():
        key = f"{prefix}{k}" if prefix == "" else f"{prefix}.{k}"
        if isinstance(v, dict) and not _looks_like_objectid(v):
            out.update(_flatten_doc(v, key))
        else:
            out[key] = v
    return out


def _looks_like_objectid(obj: Any) -> bool:
    """Heuristic: BSON ObjectId surfaces as a class with ``binary`` +
    ``generation_time`` attrs. Treat it as a leaf value, not a nested
    document."""
    return type(obj).__name__ in {"ObjectId", "Binary"}


def _drop_internal(df: pl.DataFrame) -> pl.DataFrame:
    cols = [c for c in df.columns if c not in _INTERNAL_COLUMNS]
    return df.select(cols)


class MongoConnector(BaseConnector):
    """Read/write documents from MongoDB.

    The Mongo storage model is document-oriented; goldenmatch's
    matchkey pipeline is column-oriented. This connector bridges
    the two by projecting + flattening nested fields on read.
    """

    name = "mongo"

    def _connect(self, config: dict):
        try:
            import pymongo  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ConnectorError(
                "Mongo connector requires pymongo. "
                "Install with: pip install goldenmatch[mongo]"
            ) from exc

        uri = (
            config.get("connection")
            or self._credentials.get("key")
            or os.environ.get("MONGO_URI")
        )
        if not uri:
            raise ConnectorError(
                "Mongo connector needs a connection URI. Pass it as "
                "config['connection'], set MONGO_URI, or wire "
                "credentials_env."
            )
        return pymongo.MongoClient(uri)

    def _collection(self, client, config: dict):
        db_name = config.get("database")
        col_name = config.get("collection")
        if not db_name:
            raise ConnectorError("Mongo connector requires 'database'.")
        if not col_name:
            raise ConnectorError("Mongo connector requires 'collection'.")
        return client[db_name][col_name]

    def read(self, config: dict) -> pl.LazyFrame:
        client = self._connect(config)
        try:
            collection = self._collection(client, config)
            filter_ = config.get("filter") or {}
            projection = config.get("projection")
            limit = config.get("limit")
            flatten = config.get("flatten", True)

            cursor = collection.find(filter_, projection=projection)
            if limit is not None:
                cursor = cursor.limit(int(limit))

            rows: list[dict[str, Any]] = []
            for doc in cursor:
                # ObjectId -> str so Polars can hold it as Utf8.
                if "_id" in doc:
                    doc["_id"] = str(doc["_id"])
                if flatten:
                    rows.append(_flatten_doc(doc))
                else:
                    rows.append(doc)

            if not rows:
                logger.info("Mongo: read 0 rows from %s.%s",
                            config["database"], config["collection"])
                return pl.DataFrame().lazy()

            # Build the DataFrame from row dicts. Polars handles
            # heterogeneous rows by null-filling missing keys.
            df = pl.DataFrame(rows, infer_schema_length=min(1000, len(rows)))
            logger.info(
                "Mongo: read %d rows (%d columns) from %s.%s",
                df.height, df.width, config["database"], config["collection"],
            )
            return df.lazy()
        finally:
            client.close()

    def write(self, df: pl.DataFrame, config: dict) -> None:
        if df.height == 0:
            logger.info("Mongo: write skipped on empty DataFrame.")
            return

        mode = config.get("mode", "append")
        if mode not in ("append", "upsert", "replace"):
            raise ConnectorError(
                f"Mongo connector: unknown mode {mode!r}. "
                "Choose one of: append, upsert, replace."
            )

        client = self._connect(config)
        try:
            collection = self._collection(client, config)
            df_out = _drop_internal(df) if config.get("drop_internal", True) else df
            docs = df_out.to_dicts()

            if mode == "replace":
                collection.delete_many({})
                if docs:
                    collection.insert_many(docs)
            elif mode == "upsert":
                key = config.get("key")
                if not key:
                    raise ConnectorError(
                        "Mongo upsert requires config['key'] -- the column "
                        "name to use as the document filter."
                    )
                for doc in docs:
                    if key not in doc:
                        raise ConnectorError(
                            f"Upsert key {key!r} missing from a row; "
                            "every row must carry the key."
                        )
                    filter_ = {key: doc[key]}
                    # Don't try to mutate the key field; Mongo rejects
                    # updates that touch the query field on upsert.
                    update = {"$set": {k: v for k, v in doc.items() if k != key}}
                    collection.update_one(filter_, update, upsert=True)
            else:  # append
                collection.insert_many(docs)

            logger.info(
                "Mongo: wrote %d rows to %s.%s (mode=%s)",
                df.height, config["database"], config["collection"], mode,
            )
        finally:
            client.close()


__all__ = ["MongoConnector"]
