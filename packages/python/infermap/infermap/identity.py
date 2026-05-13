"""InferMap -> Identity Graph bridge.

Helper that writes InferMap's schema-mapping output into the GoldenMatch
Identity Graph as ``IdentityAlias`` rows. When InferMap discovers that
``crm.cust_id`` maps to ``customer_id`` (the canonical entity-id field
on the target schema), each source record's ``crm.cust_id`` value becomes
an alias that resolves to that record's identity.

This is **per-record** alias writing -- InferMap tells us *which column
holds the id of this kind*, we record one row per (record, alias-kind).
Schema-level "this column maps to that column" aliasing without a record
context is intentionally **not** modeled -- the alias table is keyed on
the alias *value*, not the column name.

Sized down on purpose: this module imports goldenmatch.identity lazily so
infermap users without goldenmatch installed keep working. The
``write_aliases_from_mapping`` function is the only public surface.

See issue #206 for the design discussion.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from infermap.types import MapResult

logger = logging.getLogger(__name__)


@dataclass
class AliasWriteResult:
    """Summary of one ``write_aliases_from_mapping`` invocation."""

    aliases_written: int = 0
    records_processed: int = 0
    mappings_used: int = 0
    skipped_low_confidence: int = 0
    skipped_no_value: int = 0
    skipped_no_entity: int = 0
    skipped_no_kind: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "aliases_written": self.aliases_written,
            "records_processed": self.records_processed,
            "mappings_used": self.mappings_used,
            "skipped_low_confidence": self.skipped_low_confidence,
            "skipped_no_value": self.skipped_no_value,
            "skipped_no_entity": self.skipped_no_entity,
            "skipped_no_kind": self.skipped_no_kind,
        }


def _is_alias_kind(target_field: str, alias_kinds: frozenset[str]) -> str | None:
    """Return the alias ``kind`` for a target field name, or None.

    Strict match against ``alias_kinds`` plus a tiny set of common
    aliases (e.g. ``customer_id`` -> ``customer_id``). Case-insensitive.
    """
    norm = target_field.lower().strip()
    if norm in alias_kinds:
        return norm
    return None


def write_aliases_from_mapping(
    mapping: MapResult,
    records: Iterable[dict[str, Any]],
    store: Any,
    entity_id_resolver: Callable[[dict[str, Any]], str | None],
    *,
    source_name: str,
    alias_kinds: frozenset[str] = frozenset({
        "customer_id", "user_id", "account_id", "external_id", "email",
        "phone", "ssn", "tax_id", "ein", "vin", "isbn", "doi",
    }),
    min_confidence: float = 0.85,
    dataset: str | None = None,
) -> AliasWriteResult:
    """Write IdentityAlias rows for each record where InferMap mapped a
    source column to a known alias-kind target column.

    Parameters
    ----------
    mapping:
        InferMap's ``MapResult`` -- typically the result of
        ``infermap.map(source_schema, target_schema)``.
    records:
        Iterable of dicts. Each dict represents one source record;
        keys are source field names.
    store:
        A ``goldenmatch.identity.IdentityStore`` instance. Imported lazily;
        this function will not import goldenmatch if the caller doesn't
        pass one.
    entity_id_resolver:
        ``record -> entity_id | None`` function. Typically pulls the
        record's primary-key value and calls
        ``store.find_entity_by_record(record_id)``. Returning None for a
        record skips alias writing for that row.
    source_name:
        The source name (e.g. ``"crm"``). Used to namespace the alias
        value (``f"{source_name}:{value}"``) so two sources with the
        same id space don't collide.
    alias_kinds:
        Target field names that count as alias-kinds. The default set
        covers the common identifier types; pass an extended set when
        your target schema has domain-specific ids
        (e.g. ``"npi"`` for healthcare).
    min_confidence:
        Drop any mapping below this confidence threshold. 0.85 default
        matches the threshold InferMap considers a "strong" match.
    dataset:
        Optional dataset name flowed onto each ``IdentityAlias`` row.

    Returns
    -------
    AliasWriteResult
        Counters describing what got written and why anything was
        skipped.
    """
    try:
        from goldenmatch.identity import IdentityAlias
    except ImportError as e:
        raise ImportError(
            "write_aliases_from_mapping requires goldenmatch>=1.15.0 "
            "to be installed (provides IdentityAlias).",
        ) from e

    # Pre-compute the usable (source_col, target_kind) tuples from the
    # mapping. Drops low-confidence and non-alias-kind mappings upfront so
    # we don't reread per record.
    usable: list[tuple[str, str]] = []
    for m in mapping.mappings:
        if m.confidence < min_confidence:
            continue
        kind = _is_alias_kind(m.target, alias_kinds)
        if kind is None:
            continue
        usable.append((m.source, kind))

    skipped_low = sum(
        1 for m in mapping.mappings if m.confidence < min_confidence
    )
    summary = AliasWriteResult(
        mappings_used=len(usable),
        skipped_low_confidence=skipped_low,
    )
    if not usable:
        logger.info(
            "No usable alias-kind mappings (>= %.2f confidence). "
            "Available mappings: %s",
            min_confidence,
            [m.target for m in mapping.mappings],
        )
        return summary

    for record in records:
        summary.records_processed += 1
        entity_id = entity_id_resolver(record)
        if entity_id is None:
            summary.skipped_no_entity += 1
            continue

        for source_col, kind in usable:
            value = record.get(source_col)
            if value is None or value == "":
                summary.skipped_no_value += 1
                continue
            alias_value = f"{source_name}:{value}"
            try:
                store.add_alias(
                    IdentityAlias(
                        alias=alias_value,
                        entity_id=entity_id,
                        kind=kind,
                        dataset=dataset,
                    ),
                )
                summary.aliases_written += 1
            except Exception as e:  # noqa: BLE001
                # Don't blow up the whole batch on one bad row -- log and
                # continue. Identity is additive; partial writes are fine.
                logger.warning(
                    "Failed to write alias %s (kind=%s) for entity %s: %s",
                    alias_value, kind, entity_id, e,
                )

    logger.info(
        "InferMap -> Identity: wrote %d aliases across %d records (%d mappings used)",
        summary.aliases_written,
        summary.records_processed,
        summary.mappings_used,
    )
    return summary


__all__ = ["AliasWriteResult", "write_aliases_from_mapping"]
