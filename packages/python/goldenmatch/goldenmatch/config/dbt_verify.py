"""Auto-verify a dbt -> GoldenMatch conversion against the pipeline's own output.

Spec: docs/superpowers/specs/2026-07-26-dbt-to-goldenmatch-converter-design.md

``from_dbt`` proves WHICH dbt idioms it maps. This module proves the mapping
REPRODUCES BEHAVIOR: the hand-rolled dbt ER model already produces an output
table -- either a surrogate-key -> member mapping (window-dedup style) or a
canonical/golden table (master-data style). That existing output is the
LABEL-FREE ground truth. We read it into an ``id -> cluster_id`` map, run the
converted GoldenMatch config via ``dedupe_df`` on the SOURCE rows, and report
pairwise cluster agreement -- "reproduces N% of your existing clusters."

This is the exact engine-vs-engine primitive ``verify_against_splink`` uses
(``pair_set`` / ``pairwise_prf`` from ``splink_upgrade_measure``), with the dbt
output table as the reference instead of a live Splink run. No labels required
(dbt dedup, like Splink, is unsupervised). ``is_faithful`` at pairwise F1 >=
0.95, the same "numerically-equivalent" bar.

Verification is best-effort: it returns ``None`` (with a warning finding) when
the output table is empty or shares no ids with the source, rather than
reporting all-0.0 garbage. Like ``splink_verify``, this module imports polars +
the full pipeline, so ``from_dbt`` (polars-free) must NOT import it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from goldenmatch._polars_lazy import pl
from goldenmatch.config.from_splink import ConversionReport
from goldenmatch.config.schemas import GoldenMatchConfig
from goldenmatch.config.splink_upgrade import _load_frame
from goldenmatch.config.splink_upgrade_measure import (
    _POSITIONAL_ID_SOURCE,
    _resolve_ids,
    _run_once,
    pair_set,
    pairwise_prf,
)

# Same faithfulness bar as the Splink verify: a converted config whose clusters
# agree with the dbt output at or above this pairwise F1 is behaviourally
# faithful.
_FAITHFUL_F1 = 0.95
_DEFAULT_SAMPLE = 5000


@dataclass
class DbtVerification:
    """Result of :func:`verify_against_dbt`.

    ``agreement`` is the pairwise precision/recall/F1 of the converted
    GoldenMatch config's dedupe clusters vs the dbt pipeline's existing output
    clusters, over the ids present in both. High F1 == the converted config
    reproduces the hand-rolled pipeline's linking decisions.
    """

    agreement: dict[str, float]
    n_source_rows: int      # rows actually run through GoldenMatch
    n_shared_ids: int       # ids present in BOTH the GM run and the dbt output
    gm_cluster_count: int
    dbt_cluster_count: int
    gm_multi_clusters: int
    dbt_multi_clusters: int
    id_source: str

    @property
    def is_faithful(self) -> bool:
        return self.agreement.get("f1", 0.0) >= _FAITHFUL_F1


def _multi_cluster_count(mapping: dict[str, str]) -> int:
    counts: dict[str, int] = {}
    for cid in mapping.values():
        counts[cid] = counts.get(cid, 0) + 1
    return sum(1 for n in counts.values() if n > 1)


def _load_output_mapping(
    output: pl.DataFrame | str | Path,
    sample_ids: set[str],
    id_column: str | None,
    cluster_column: str | None,
) -> tuple[dict[str, str], int]:
    """Read the dbt output table into ``id -> cluster_id``, restricted to
    ``sample_ids``.

    ``id_column`` / ``cluster_column`` name the two columns; when unset they
    default to the first two columns (the ``splink_upgrade_measure`` reference
    convention). Returns ``(mapping, total_output_rows)`` -- the raw count lets
    the caller distinguish an EMPTY output from a zero-id-overlap join failure.
    """
    frame = _load_frame(output)
    if len(frame.columns) < 2:
        raise ValueError(
            "dbt output table needs at least two columns (id, cluster_id); got "
            + repr(frame.columns)
        )
    id_col = id_column or frame.columns[0]
    cluster_col = cluster_column or frame.columns[1]
    for name, role in ((id_col, "id"), (cluster_col, "cluster")):
        if name not in frame.columns:
            raise ValueError(
                f"dbt output {role} column '{name}' not in the output table "
                f"columns {frame.columns}"
            )
    mapping: dict[str, str] = {}
    for rid, cid in zip(frame[id_col].to_list(), frame[cluster_col].to_list()):
        key = str(rid)
        if key in sample_ids:
            mapping[key] = str(cid)
    return mapping, len(frame)


def verify_against_dbt(
    config: GoldenMatchConfig,
    source: pl.DataFrame | str | Path,
    output_table: pl.DataFrame | str | Path,
    *,
    id_column: str | None = None,
    output_id_column: str | None = None,
    output_cluster_column: str | None = None,
    sample_size: int = _DEFAULT_SAMPLE,
    report: ConversionReport | None = None,
) -> DbtVerification | None:
    """Measure how faithfully the converted config reproduces the dbt output.

    Runs the converted ``config`` via ``dedupe_df`` on a deterministic sample
    of ``source`` and compares its clusters against the dbt pipeline's existing
    ``output_table`` (an ``id, cluster_id`` mapping -- a surrogate-key->member
    table or a canonical/golden table), reporting pairwise cluster agreement.

    Returns ``None`` (best-effort, never raising on the verify path) when the
    source or output is empty, or shares no ids with the source sample -- each
    recorded as a finding on ``report`` when supplied.

    Args:
        config: the converted ``GoldenMatchConfig`` (from :func:`from_dbt`).
        source: the SOURCE rows the dbt ER model consumed (DataFrame or
            ``.parquet`` / ``.csv`` path).
        output_table: the dbt model's EXISTING output (id -> cluster_id).
        id_column: unique row-id column in ``source``; defaults to
            ``unique_id``/``id``/``record_id`` then positional indices.
        output_id_column / output_cluster_column: the id + cluster columns in
            ``output_table``; default to its first two columns.
        sample_size: rows of ``source`` to run through GoldenMatch (a seeded
            subsample above this cap; the whole frame at or below it).
        report: optional :class:`ConversionReport` to attach findings to.
    """

    def _note(kind: str, msg: str) -> None:
        if report is not None:
            method = "warn" if kind == "warning" else kind
            getattr(report, method)("verify", msg, None)

    frame = _load_frame(source)
    if frame.height == 0:
        _note("warning", "verify source is empty; agreement not measured.")
        return None

    sample = (
        frame.sample(n=sample_size, seed=0) if frame.height > sample_size else frame
    )
    if frame.height > sample_size:
        _note(
            "info",
            f"verifying on a seeded {sample_size}-row sample of {frame.height} "
            "rows (deterministic).",
        )

    ids, id_source = _resolve_ids(sample, id_column)

    try:
        dbt_map, n_output_rows = _load_output_mapping(
            output_table, set(ids), output_id_column, output_cluster_column
        )
    except ValueError as exc:
        _note("warning", f"could not read the dbt output table ({exc}); "
              "the conversion was written but not verified.")
        return None

    if n_output_rows == 0:
        _note("warning", "dbt output table is empty (0 rows); agreement not measured.")
        return None
    if not dbt_map:
        source_desc = (
            "positional row indices"
            if id_source == _POSITIONAL_ID_SOURCE
            else f"column '{id_source}'"
        )
        _note(
            "warning",
            f"the dbt output table ({n_output_rows} rows) shares no ids with the "
            f"source sample (ids came from {source_desc}) -- an id-join failure, "
            "so agreement is skipped rather than reported as 0.0; pass "
            "id_column= / output_id_column= naming the matching id columns.",
        )
        return None

    gm_map, _wall = _run_once(sample, config, ids)

    shared = gm_map.keys() & dbt_map.keys()
    gm_pairs, _c1 = pair_set({k: gm_map[k] for k in shared})
    dbt_pairs, _c2 = pair_set({k: dbt_map[k] for k in shared})
    agreement = pairwise_prf(gm_pairs, dbt_pairs)

    result = DbtVerification(
        agreement=agreement,
        n_source_rows=len(ids),
        n_shared_ids=len(shared),
        gm_cluster_count=len(set(gm_map.values())),
        dbt_cluster_count=len(set(dbt_map.values())),
        gm_multi_clusters=_multi_cluster_count(gm_map),
        dbt_multi_clusters=_multi_cluster_count(dbt_map),
        id_source=id_source,
    )

    verdict = "faithful" if result.is_faithful else "DIVERGENT"
    _note(
        "info" if result.is_faithful else "warning",
        f"dbt agreement ({verdict}): reproduces {agreement['f1'] * 100:.1f}% of "
        f"existing clusters (pairwise F1={agreement['f1']:.3f}, "
        f"P={agreement['precision']:.3f}, R={agreement['recall']:.3f}) over "
        f"{len(shared)} records; GoldenMatch {result.gm_multi_clusters} vs dbt "
        f"{result.dbt_multi_clusters} multi-record clusters.",
    )
    return result
