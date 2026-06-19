"""Build records_ghsuite.csv from the curated concepts.jsonl via an injected search backend.

Mirrors build_real.py's structure but is split into two layers:

* This module: pure record-assembly core (``assemble_records``).  No I/O, no
  network, no ripgrep.  The search backend is injected so the core is fully
  unit-testable without any external calls.
* Task 4 (build_ghsuite_cli.py / backends): the ripgrep/gh search backends and
  the CLI entry point that wires them up and writes records_ghsuite.csv.

The CSV row schema (shared with the bench harness via FIELDNAMES) is:

    record_id, mention, entity_type, context, entity_id, failure_class, source
"""

from __future__ import annotations

from collections.abc import Callable

from dataset.concepts_loader import Concept  # pyright: ignore[reportMissingImports]

FIELDNAMES = [
    "record_id",
    "mention",
    "entity_type",
    "context",
    "entity_id",
    "failure_class",
    "source",
]


def assemble_records(
    concepts: list[Concept],
    search_fn: Callable[[str], tuple[bool, str | None]],
    start_id: int = 0,
) -> list[dict]:
    """Assemble bench row dicts from *concepts* using an injected *search_fn*.

    For each concept, iterates variants in order and:
    * Skips any surface already emitted for this concept (dedup within concept).
    * Calls ``search_fn(surface)`` -> ``(found, provenance)``.
    * Drops the variant when ``found`` is False.
    * Otherwise appends a row dict and increments the running record_id.

    Args:
        concepts:   List of ``Concept`` objects from ``concepts_loader``.
        search_fn:  Injected callable ``(surface: str) -> (found: bool, provenance: str | None)``.
                    Must be pure from this function's perspective (no side-effects observed here).
        start_id:   First ``record_id`` value (default 0).

    Returns:
        List of row dicts whose keys match ``FIELDNAMES``.  Pure -- no I/O.
    """
    rows: list[dict] = []
    rid = start_id

    for concept in concepts:
        seen: set[str] = set()
        for variant in concept.variants:
            surface = variant.surface
            if surface in seen:
                continue
            found, prov = search_fn(surface)
            if not found:
                continue
            rows.append(
                {
                    "record_id": rid,
                    "mention": surface,
                    "entity_type": concept.entity_type,
                    "context": concept.context,
                    "entity_id": concept.canonical_id,
                    "failure_class": variant.failure_class,
                    "source": prov,
                }
            )
            seen.add(surface)
            rid += 1

    return rows
