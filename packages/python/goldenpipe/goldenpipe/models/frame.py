"""Relocatable-stage seam: an Arrow-capable frame handle (Phase A).

Design: ``docs/design/2026-07-06-goldenpipe-relocatable-stage-contract.md``.

Core principle -- **Arrow-CAPABLE, not Arrow-MANDATORY.** A ``Frame`` is the data
handle at a stage boundary. In-process it is backed by a Polars DataFrame and
``.polars()`` returns that frame **by reference** (zero copy, no Arrow
round-trip) -- which is why introducing this seam does not regress the
single-process pipeline (Stage 0: the handoff is 0.2% of the wall). The
``arrow_batches()`` / ``from_arrow()`` seam materializes Arrow **only** when a
stage actually crosses a process / language / engine boundary; a remote or
streaming ``Frame`` (Phases B/C) plugs in behind this same contract without
touching the in-process path.

Phase A ships only ``LocalFrame``. Remote execution is not implemented -- the
Runner raises for any stage that declares a non-local location.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, Protocol, runtime_checkable

import polars as pl


@runtime_checkable
class Frame(Protocol):
    """Data handle at a stage boundary: DataFrame-native in-process, Arrow-
    representable when crossing a boundary."""

    def polars(self) -> pl.DataFrame:
        """The data as a Polars DataFrame. In-process impls return the backing
        frame BY REFERENCE (no copy)."""
        ...

    def arrow_batches(self) -> Iterator[Any]:
        """The data as an iterator of ``pyarrow.RecordBatch`` -- the wire form for
        a boundary-crossing (remote) stage. Not called on the in-process path."""
        ...


class LocalFrame:
    """In-process ``Frame`` backed by a Polars DataFrame (the only impl in Phase A)."""

    __slots__ = ("_df",)

    def __init__(self, df: pl.DataFrame) -> None:
        self._df = df

    def polars(self) -> pl.DataFrame:
        # In-process fast path: return the backing frame BY REFERENCE. No copy,
        # no Arrow round-trip -- this is why the seam doesn't regress Stage 0.
        return self._df

    def arrow_batches(self) -> Iterator[Any]:
        # Lazy: only a boundary-crossing adapter calls this. Polars -> Arrow
        # shares the underlying column buffers. ``to_arrow`` pulls pyarrow in on
        # demand, so the pure in-process ``polars()`` path never needs it.
        yield from self._df.to_arrow().to_batches()

    @classmethod
    def from_arrow(cls, batches: Iterable[Any]) -> LocalFrame:
        """Build a ``LocalFrame`` from ``pyarrow.RecordBatch`` es -- the inverse of
        ``arrow_batches`` (used when a remote stage returns Arrow)."""
        import pyarrow as pa

        return cls(pl.from_arrow(pa.Table.from_batches(list(batches))))


class DuckDBFrame:
    """Engine-resident ``Frame`` backed by a **lazy** DuckDB relation (Phase C).

    The data lives in DuckDB, not in Python. A DuckDB relation is lazy -- chaining
    ``.project(...)`` / ``.filter(...)`` builds an unexecuted query; nothing
    materializes until ``.polars()`` / ``.arrow_batches()`` is called. That is the
    whole point of Phase C: a chain of in-engine stages stays in the engine, and
    the Python round-trip (the "crossing" that Phase C's baseline measured at ~89%
    of the pull path) is paid **once**, at the pipeline's egress -- or never, if
    the result lands back in the warehouse.

    Satisfies the ``Frame`` protocol, so it flows through the same
    ``PipeContext.frame`` seam as ``LocalFrame`` -- a stage that only reads
    ``.polars()`` still works (it just triggers the materialization); a
    remote-aware stage transforms the relation in-engine via ``.project`` and
    hands back a new ``DuckDBFrame``.
    """

    __slots__ = ("_rel",)

    def __init__(self, relation: Any) -> None:
        # `relation` is a duckdb.DuckDBPyRelation (from `con.sql(...)` /
        # `con.table(...)` / another relation's `.project(...)`).
        self._rel = relation

    def relation(self) -> Any:
        """The underlying lazy DuckDB relation (for in-engine chaining)."""
        return self._rel

    def polars(self) -> pl.DataFrame:
        # Materialize -- the egress crossing (DuckDB -> Arrow -> Polars). This is
        # the ONE round-trip Phase C confines to the boundary; in-engine stages
        # avoid it by staying on ``.project`` / ``.relation``.
        return self._rel.pl()

    def arrow_batches(self) -> Iterator[Any]:
        # Stream the result out of the engine as pyarrow RecordBatches, without a
        # full Polars materialization -- the wire form for a cross-process egress.
        # ``rel.arrow()`` yields a RecordBatchReader (DuckDB >=1.3) or a Table
        # (older); iterate the reader, else batch the table.
        obj = self._rel.arrow()
        if hasattr(obj, "to_batches"):
            yield from obj.to_batches()
        else:
            yield from obj

    def project(self, select_expr: str) -> DuckDBFrame:
        """Apply an in-engine SQL projection, returning a new (still lazy)
        ``DuckDBFrame``. The data never leaves DuckDB. Example:
        ``frame.project("id, lower(trim(email)) AS email")``."""
        return DuckDBFrame(self._rel.project(select_expr))
