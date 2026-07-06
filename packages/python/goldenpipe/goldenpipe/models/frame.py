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
