"""Live catalog write-back for the semantic-layer wedge (wedge B, persisted).

The `emit_*_from_crosswalk` functions PRODUCE the conformed entity declaration
(the `resolved_entity_id` join a semantic layer should group metrics by) as a YAML
string. `write_resolved_catalog` is the last mile: it emits that declaration for a
`ResolvedCrosswalk` and WRITES it to the catalog file the semantic layer reads —
so "resolve once, every metric inherits correct joins" lands as a file in the
dbt/MetricFlow, Cube, or OSI project, not just a returned string.

It dispatches to the right dialect emitter, refuses to clobber an existing file
unless `overwrite=True`, and returns the written YAML.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa

_DIALECTS = ("metricflow", "cube", "osi")


def _emit_for_dialect(
    crosswalk: Any, dialect: str, source_target: str, **emit_kwargs: Any
) -> str:
    """Emit a crosswalk's conformed entity declaration in the given dialect.

    Shared by `write_resolved_catalog` (which then writes) and
    `emit_semantic_model_from_store` (which builds the crosswalk from the store).
    """
    key = dialect.strip().lower()
    if key not in _DIALECTS:
        raise ValueError(
            f"unknown dialect {dialect!r}; expected one of {_DIALECTS}"
        )
    if key == "metricflow":
        from goldenmatch.semantic.metricflow import emit_from_crosswalk

        return emit_from_crosswalk(crosswalk, source_target, **emit_kwargs)
    if key == "cube":
        from goldenmatch.semantic.cube import emit_cube_from_crosswalk

        return emit_cube_from_crosswalk(crosswalk, source_cube=source_target, **emit_kwargs)
    from goldenmatch.semantic.osi import emit_osi_from_crosswalk  # osi

    return emit_osi_from_crosswalk(crosswalk, source_dataset=source_target, **emit_kwargs)


def write_resolved_catalog(
    crosswalk: Any,
    path: str | Path,
    *,
    dialect: str,
    source_target: str,
    overwrite: bool = False,
    **emit_kwargs: Any,
) -> str:
    """Emit a `ResolvedCrosswalk`'s conformed entity declaration and write it to `path`.

    Args:
        crosswalk: a `ResolvedCrosswalk` (from `build_resolved_crosswalk`).
        path: the catalog file to write (e.g. `models/customer_crosswalk.yml`).
        dialect: one of `"metricflow"`, `"cube"`, `"osi"` — selects the emitter.
        source_target: the source model / cube / dataset whose join points at the
            crosswalk (the `model` for MetricFlow, `source_cube` for Cube,
            `source_dataset` for OSI).
        overwrite: refuse to overwrite an existing file unless True.
        **emit_kwargs: forwarded to the dialect's `emit_*_from_crosswalk`
            (e.g. `measures=`, `grain=`, `resolved_field=`, `certificate=`).

    Returns:
        The YAML string that was written.
    """
    out = Path(path)
    if out.exists() and not overwrite:
        raise FileExistsError(
            f"write_resolved_catalog: {out} already exists; pass overwrite=True to replace it"
        )

    try:
        yaml_str = _emit_for_dialect(crosswalk, dialect, source_target, **emit_kwargs)
    except ValueError as exc:  # re-raise with the public function's name
        raise ValueError(f"write_resolved_catalog: {exc}") from None

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml_str, encoding="utf-8")
    return yaml_str


def emit_semantic_model_from_store(
    store: Any,
    *,
    source_name: str,
    source_pk_column: str,
    dialect: str = "metricflow",
    dataset: str | None = None,
    source_target: str | None = None,
    resolved_key: str = "resolved_entity_id",
    path: str | Path | None = None,
    overwrite: bool = False,
    **emit_kwargs: Any,
) -> str:
    """Emit the conformed entity declaration for a semantic layer **directly from
    the durable IdentityStore** (not a one-run `ResolvedCrosswalk`).

    `build_resolved_crosswalk` / `write_resolved_catalog` emit a catalog from a
    single dedupe run's crosswalk. This reads the *live* control plane instead:
    the store already holds the `entity_id` ↔ `{source, source_pk}` mapping, so the
    same conformed `resolved_entity_id` join declaration can be regenerated at any
    time — "keep the semantic layer's identity join live against the control
    plane." The emitted declaration is identical in shape to the crosswalk
    emitters' (they read provenance stats — record/entity counts, the resolved key
    — not the row data), populated from the store's identity summary for `dataset`.

    Args:
        store: an open `IdentityStore`.
        source_name: the logical source name the records were ingested under.
        source_pk_column: the column holding each record's source primary key
            (the `unique`/join column in the emitted declaration).
        dialect: `"metricflow"` | `"cube"` | `"osi"`.
        dataset: the identity-graph dataset scope to summarize (defaults to all).
        source_target: the source model / cube / dataset name the join points at
            (defaults to `source_name`).
        resolved_key: the conformed join column name (the control-plane id).
        path: when given, also write the emitted YAML to this catalog file
            (refuses to clobber unless `overwrite=True`), mirroring
            `write_resolved_catalog`.
        **emit_kwargs: forwarded to the dialect emitter (`measures`, `grain`, ...).

    Returns:
        The emitted (and, when `path` is set, written) catalog YAML.
    """
    from goldenmatch.identity import identity_summary_stats
    from goldenmatch.semantic.crosswalk import ResolvedCrosswalk

    summary = identity_summary_stats(store, dataset=dataset)
    # The emitters read only provenance stats off the crosswalk, never `.table`,
    # so a stats-only stand-in (empty table) reproduces the crosswalk emit exactly.
    xw = ResolvedCrosswalk(
        table=pa.table({"source": [], "source_pk": [], resolved_key: []}),
        source=source_name,
        source_pk_column=source_pk_column,
        resolved_key=resolved_key,
        n_records=summary.total_records,
        n_entities=summary.total_entities,
        store_path=getattr(store, "path", None),
    )
    target = source_target or source_name
    if path is not None:
        return write_resolved_catalog(
            xw, path, dialect=dialect, source_target=target, overwrite=overwrite, **emit_kwargs
        )
    return _emit_for_dialect(xw, dialect, target, **emit_kwargs)
