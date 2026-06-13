"""Pluggable candidate-row stores for single-record matching.

``match_one`` / ``streaming`` / ``incremental`` retrieve a small set of candidate
rows per probe — ANN top-K positions, or a block key's members — and score them.
By default the base lives in a polars frame in RAM (``FrameCandidateStore``); the
optional ``LanceCandidateStore`` serves the same gathers from disk so the base
need not fit in memory.

Spec: docs/superpowers/specs/2026-06-13-lance-match-one-base-store-design.md.
Default behaviour is byte-identical to the legacy ``df.to_dicts()`` path — but
gathers only the requested rows, not the whole frame per probe.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

_BLOCK_COL_DEFAULT = "__block_key__"
_ROW_ID = "__row_id__"


@runtime_checkable
class CandidateStore(Protocol):
    """Retrieves candidate rows + their ``__row_id__`` for scoring."""

    def take(self, positions: Sequence[int]) -> tuple[list[dict], list[int]]:
        """Rows at the given base positions (ANN/FAISS index positions), aligned;
        out-of-range positions are dropped. Returns (rows, row_ids)."""
        ...

    def gather_block(self, key: object, column: str = _BLOCK_COL_DEFAULT) -> tuple[list[dict], list[int]]:
        """All rows whose ``column`` equals ``key``. Returns (rows, row_ids)."""
        ...

    def __len__(self) -> int:
        ...


class FrameCandidateStore:
    """In-memory store wrapping a polars frame (the default).

    Byte-identical results to the legacy path, but ``take`` materializes only the
    requested rows instead of ``df.to_dicts()`` over the entire frame on every
    probe (the O(N)-per-call cost in the old ``_match_one_ann``).
    """

    def __init__(self, df) -> None:
        self._df = df
        self._n = int(df.height)

    def take(self, positions: Sequence[int]) -> tuple[list[dict], list[int]]:
        pos = [int(p) for p in positions if 0 <= int(p) < self._n]
        if not pos:
            return [], []
        sub = self._df[pos]
        return sub.to_dicts(), [int(x) for x in sub[_ROW_ID].to_list()]

    def gather_block(self, key: object, column: str = _BLOCK_COL_DEFAULT) -> tuple[list[dict], list[int]]:
        import polars as pl

        sub = self._df.filter(pl.col(column) == key)
        return sub.to_dicts(), [int(x) for x in sub[_ROW_ID].to_list()]

    def __len__(self) -> int:
        return self._n


class LanceCandidateStore:
    """Disk-backed store over a Lance dataset (optional; ``pip install
    goldenmatch[lance]``). Serves per-probe gathers without holding the base in
    RAM. ``take`` uses Lance random ``take``; ``gather_block`` uses a BTREE scalar
    index on the block column when one was built.
    """

    def __init__(self, dataset, columns: list[str] | None = None) -> None:
        self._ds = dataset
        self._columns = columns
        self._n = dataset.count_rows()

    @classmethod
    def from_frame(cls, df, path: str, block_column: str = _BLOCK_COL_DEFAULT, build_index: bool = True):
        """Write ``df`` to a Lance dataset at ``path`` and (optionally) build a
        BTREE scalar index on ``block_column`` for fast block gathers."""
        import lance
        import pyarrow as pa

        tbl = df.to_arrow()
        # Lance's BTREE index rejects Arrow `large_string`; polars exports Utf8
        # as large_string, so cast the index column down to `string`.
        if block_column in tbl.schema.names and pa.types.is_large_string(
            tbl.schema.field(block_column).type
        ):
            ci = tbl.schema.get_field_index(block_column)
            tbl = tbl.set_column(ci, block_column, tbl.column(block_column).cast(pa.string()))
        ds = lance.write_dataset(tbl, str(path))
        if build_index and block_column in tbl.schema.names:
            ds.create_scalar_index(block_column, "BTREE")
        return cls(lance.dataset(str(path)), columns=list(df.columns))

    @classmethod
    def open(cls, path: str, columns: list[str] | None = None):
        import lance

        return cls(lance.dataset(str(path)), columns=columns)

    def take(self, positions: Sequence[int]) -> tuple[list[dict], list[int]]:
        pos = [int(p) for p in positions if 0 <= int(p) < self._n]
        if not pos:
            return [], []
        tbl = self._ds.take(pos, columns=self._columns)
        rows = tbl.to_pylist()
        return rows, [int(r[_ROW_ID]) for r in rows]

    def gather_block(self, key: object, column: str = _BLOCK_COL_DEFAULT) -> tuple[list[dict], list[int]]:
        tbl = self._ds.scanner(columns=self._columns, filter=f"{column} = '{key}'").to_table()
        rows = tbl.to_pylist()
        return rows, [int(r[_ROW_ID]) for r in rows]

    def __len__(self) -> int:
        return int(self._n)


def lance_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("lance") is not None


def resolve_base_store_kind(n_rows: int, *, configured: str | None = None, threshold_rows: int = 2_000_000) -> str:
    """Decide which base store to use: ``"memory"`` or ``"lance"``.

    Precedence: explicit ``configured`` (or ``GOLDENMATCH_BASE_STORE`` env) wins;
    otherwise pick ``lance`` only when the base exceeds ``threshold_rows`` AND lance
    is importable. Always falls back to ``memory`` when lance is unavailable, so a
    plain install never fails.
    """
    choice = (configured or os.environ.get("GOLDENMATCH_BASE_STORE") or "auto").lower()
    if choice == "lance":
        return "lance" if lance_available() else "memory"
    if choice == "memory":
        return "memory"
    # auto
    if n_rows >= threshold_rows and lance_available():
        return "lance"
    return "memory"
