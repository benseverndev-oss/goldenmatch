"""Block-key computation + population for the persisted blocking index (C2).

The control-plane-owned block-key index (manifesto §4(ii), decision 0047 §9.1)
lets incremental resolution find candidate persisted records that share a block
key WITHOUT re-blocking the whole corpus in RAM. Slice 1 added the store index +
its write/query API (``IdentityStore.index_record_block_keys`` /
``candidates_by_block_keys``). This slice adds:

* ``compute_record_block_keys`` / ``compute_frame_block_keys`` -- the STATELESS
  COMPUTE half: given a record (or frame) and the blocking config, produce the
  ``(pass_sig, block_key)`` pairs it falls in. Reuses the pipeline's own
  ``_build_block_key_expr`` + the multi_pass ``(fields, transforms)`` pass
  signature, so a record's index keys match the batch blocker exactly.
* ``backfill_block_index`` -- populate the store index for an existing corpus,
  keying each row by the same ``derive_record_id`` the identity graph uses, so
  the index record_ids line up with ``source_records``.

Wiring the candidate QUERY into ``resolve_record_incremental`` (so it stops
materializing the whole corpus) is the next slice; these primitives are what it
will call. Additive -- nothing here changes existing resolve behavior.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from goldenmatch._polars_lazy import pl

if TYPE_CHECKING:
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
    from goldenmatch.identity.store import IdentityStore


def _pass_signature(key_config: BlockingKeyConfig) -> str:
    """Stable signature of a blocking pass -- its fields + transforms.

    Mirrors the ``(tuple(fields), tuple(transforms))`` pass_sig the multi_pass
    blocker dedups on (``blocker._agg_blocks``), serialized to a compact string
    for the store's ``pass_sig`` column. Distinguishes passes whose block-key
    STRINGS collide across a shared namespace (soundex vs substring vs numeric)."""
    fields = ",".join(key_config.fields)
    transforms = ",".join(key_config.transforms or [])
    return f"{fields}::{transforms}"


def _passes_of(blocking: BlockingConfig) -> list[BlockingKeyConfig]:
    """The blocking passes to index: ``passes`` for multi_pass, else ``keys``."""
    if getattr(blocking, "strategy", None) == "multi_pass":
        return list(blocking.passes or [])
    return list(blocking.keys or [])


def compute_frame_block_keys(
    df: Any, blocking: BlockingConfig
) -> dict[int, list[tuple[str, str]]]:
    """Per-``__row_id__`` ``(pass_sig, block_key)`` pairs over all blocking passes.

    Computes each pass's block key with the pipeline's ``_build_block_key_expr``
    (same transforms as batch blocking) and drops null block keys (the blocker's
    own single-field null/sentinel filter). A pass whose fields aren't all
    present in ``df`` is skipped rather than raising. Requires a ``__row_id__``
    column.
    """
    from goldenmatch.core.blocker import _build_block_key_expr
    from goldenmatch.core.frame import to_frame

    frame = to_frame(df)
    if "__row_id__" not in frame.columns:
        raise ValueError("compute_frame_block_keys requires a __row_id__ column")
    native = frame.native if hasattr(frame, "native") else df
    # Operate on polars for the block-key expression path.
    pdf = native if isinstance(native, pl.DataFrame) else pl.from_arrow(native)
    columns = set(pdf.columns)
    row_ids = pdf["__row_id__"].to_list()

    out: dict[int, list[tuple[str, str]]] = {}
    for key_config in _passes_of(blocking):
        if not all(f in columns for f in key_config.fields):
            continue  # a pass this frame can't satisfy -> skip (not an error)
        sig = _pass_signature(key_config)
        keys = pdf.select(_build_block_key_expr(key_config)).to_series().to_list()
        for rid, bk in zip(row_ids, keys):
            if bk is None:  # null block key -> not indexable (matches blocker)
                continue
            out.setdefault(int(rid), []).append((sig, str(bk)))
    return out


def compute_record_block_keys(
    record: dict[str, Any], blocking: BlockingConfig
) -> list[tuple[str, str]]:
    """The ``(pass_sig, block_key)`` pairs a single record falls in.

    The stateless-compute step of the incremental candidate query: hand these to
    ``store.candidates_by_block_keys`` to get the persisted block-mates. A
    ``__row_id__`` is synthesized if absent (the record is a transient probe)."""
    row = dict(record)
    row.setdefault("__row_id__", -1)
    keys = compute_frame_block_keys(pl.DataFrame([row]), blocking)
    return keys.get(int(row["__row_id__"]), [])


def backfill_block_index(
    store: IdentityStore,
    df: Any,
    blocking: BlockingConfig,
    *,
    source: str = "dataframe",
    source_pk_col: str | None = None,
) -> int:
    """Populate the store's block-key index for every row of ``df``.

    Keys each row by the same ``derive_record_id`` the identity graph uses, so
    the index record_ids match ``source_records``; carries each record's current
    ``entity_id`` when it is already resolved (else NULL). Runs inside one
    ``bulk_writes`` transaction. Returns the number of records indexed. Use this
    to prepare an existing corpus for incremental resolution against the index.
    """
    from goldenmatch.core.frame import to_frame
    from goldenmatch.identity.resolve import derive_record_id

    frame = to_frame(df)
    if "__row_id__" not in frame.columns:
        raise ValueError("backfill_block_index requires a __row_id__ column")
    native = frame.native if hasattr(frame, "native") else df
    pdf = native if isinstance(native, pl.DataFrame) else pl.from_arrow(native)

    keys_by_rid = compute_frame_block_keys(pdf, blocking)
    indexed = 0
    with store.bulk_writes():
        for row in pdf.iter_rows(named=True):
            rid = int(row["__row_id__"])
            keys = keys_by_rid.get(rid)
            if not keys:
                continue
            record_id, _ = derive_record_id(row, source, source_pk_col)
            entity_id = store.find_entity_by_record(record_id)
            store.index_record_block_keys(record_id, entity_id, keys)
            indexed += 1
    return indexed
