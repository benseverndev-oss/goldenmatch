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

_DIALECTS = ("metricflow", "cube", "osi")


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
    key = dialect.strip().lower()
    if key not in _DIALECTS:
        raise ValueError(
            f"write_resolved_catalog: unknown dialect {dialect!r}; expected one of {_DIALECTS}"
        )

    out = Path(path)
    if out.exists() and not overwrite:
        raise FileExistsError(
            f"write_resolved_catalog: {out} already exists; pass overwrite=True to replace it"
        )

    if key == "metricflow":
        from goldenmatch.semantic.metricflow import emit_from_crosswalk

        yaml_str = emit_from_crosswalk(crosswalk, source_target, **emit_kwargs)
    elif key == "cube":
        from goldenmatch.semantic.cube import emit_cube_from_crosswalk

        yaml_str = emit_cube_from_crosswalk(crosswalk, source_cube=source_target, **emit_kwargs)
    else:  # osi
        from goldenmatch.semantic.osi import emit_osi_from_crosswalk

        yaml_str = emit_osi_from_crosswalk(crosswalk, source_dataset=source_target, **emit_kwargs)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml_str, encoding="utf-8")
    return yaml_str
