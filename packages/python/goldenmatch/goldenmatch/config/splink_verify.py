"""Auto-verify a Splink -> GoldenMatch conversion against a locally-installed Splink.

The converter (``from_splink``) proves WHICH Splink constructs it maps. This
module proves the mapping PRESERVED BEHAVIOR: on a sample of the user's own
data it runs BOTH the original Splink settings (when ``splink`` is importable)
AND the converted GoldenMatch config, then reports pairwise cluster agreement
(precision / recall / F1 of GoldenMatch's dedupe clusters against Splink's).
That turns "trust the conversion warnings" into one measured number, without
the user wiring Splink up or handing over cluster output.

``splink`` is an OPTIONAL, heavy dependency with its own dep tree, so it is NOT
a goldenmatch requirement. ``verify_against_splink`` returns ``None`` (with an
info finding) when splink can't be imported, and fails soft (a warning finding,
``None`` returned) when the original settings can't be executed under the local
DuckDB engine -- e.g. a Spark-dialect settings export whose comparison SQL uses
Spark functions. Verification is best-effort by design: a missing agreement
number never blocks the conversion.

This module DELIBERATELY imports polars + the full dedupe pipeline (via the
reused ``splink_upgrade_measure`` helpers): unlike ``from_splink`` (which stays
polars-free), verification is an explicitly opt-in operation that runs the
pipeline, so ``from_splink`` must NOT import this module.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from goldenmatch._polars_lazy import pl
from goldenmatch.config.from_splink import ConversionReport
from goldenmatch.config.schemas import GoldenMatchConfig
from goldenmatch.config.splink_upgrade import _load_frame
from goldenmatch.config.splink_upgrade_measure import (
    _resolve_ids,
    _run_once,
    pair_set,
    pairwise_prf,
)
from goldenmatch.core.probabilistic import EMResult

# A conversion whose GoldenMatch clusters agree with Splink's at or above this
# pairwise F1 on the sample is reported as behaviourally faithful. 0.95 mirrors
# the "numerically-equivalent" bar the conformance vocabulary uses for a port
# that isn't byte-identical but preserves decisions.
_FAITHFUL_F1 = 0.95

# Reserved unique-id column injected into the Splink input frame so Splink's
# cluster output keys align 1:1 with GoldenMatch's resolved ids regardless of
# the settings' own unique_id_column_name.
_VERIFY_UID = "__gm_verify_uid__"

_DEFAULT_SAMPLE = 5000
_DEFAULT_MATCH_THRESHOLD = 0.5


@dataclass
class SplinkVerification:
    """Result of :func:`verify_against_splink`.

    ``agreement`` is the pairwise precision/recall/F1 of the converted
    GoldenMatch config's dedupe clusters vs the original Splink settings'
    clusters, over the ids present in both runs. High F1 == the conversion
    reproduces Splink's linking decisions on this data.
    """

    agreement: dict[str, float]
    n_records: int          # rows actually run through both engines
    n_shared_ids: int       # ids that clustered in BOTH runs (the metric base)
    gm_cluster_count: int
    splink_cluster_count: int
    gm_multi_clusters: int
    splink_multi_clusters: int
    match_threshold: float
    splink_version: str
    id_source: str

    @property
    def is_faithful(self) -> bool:
        return self.agreement.get("f1", 0.0) >= _FAITHFUL_F1


def _splink_available() -> bool:
    """True when ``splink`` can be imported (an OPTIONAL dependency)."""
    import importlib.util

    return importlib.util.find_spec("splink") is not None


def _multi_cluster_count(mapping: dict[str, str]) -> int:
    counts: dict[str, int] = {}
    for cid in mapping.values():
        counts[cid] = counts.get(cid, 0) + 1
    return sum(1 for n in counts.values() if n > 1)


def _run_splink(
    settings: dict, sample: pl.DataFrame, ids: list[str], threshold: float
) -> tuple[dict[str, str], str]:
    """Run the ORIGINAL Splink settings on ``sample`` -> (id -> cluster_id, version).

    Uses the DuckDB engine (the common Splink backend). A synthetic unique-id
    column keyed to ``ids`` is injected so the returned map shares GoldenMatch's
    id space exactly. ``link_type`` is forced to ``dedupe_only`` -- verification
    compares single-frame dedupe clustering. The splink version is returned
    alongside so the happy path never imports splink a second time. Any Splink
    exception propagates to the caller, which converts it into a soft-fail
    finding.
    """
    import splink as _splink  # pyright: ignore[reportMissingImports]  # optional heavy dep
    from splink import DuckDBAPI, Linker  # pyright: ignore[reportMissingImports]

    pdf = sample.to_pandas()
    pdf[_VERIFY_UID] = [str(x) for x in ids]

    # Copy the settings (never mutate the caller's dict) and pin the id column
    # + single-frame dedupe. sql_dialect stays as the export declared it so the
    # comparison SQL is interpreted as authored; a dialect the local DuckDB
    # engine can't run surfaces as an exception -> soft fail upstream.
    s = dict(settings)
    s["unique_id_column_name"] = _VERIFY_UID
    s["link_type"] = "dedupe_only"

    linker = Linker(pdf, s, DuckDBAPI())
    preds = linker.inference.predict()
    clustered = linker.clustering.cluster_pairwise_predictions_at_threshold(
        preds, threshold_match_probability=threshold
    )
    out = clustered.as_pandas_dataframe()
    mapping = {
        str(row[_VERIFY_UID]): f"s{row['cluster_id']}"
        for _, row in out[[_VERIFY_UID, "cluster_id"]].iterrows()
    }
    return mapping, getattr(_splink, "__version__", "unknown")


def _run_goldenmatch(
    sample: pl.DataFrame,
    gm_config: GoldenMatchConfig,
    em_model: EMResult | None,
    ids: list[str],
) -> dict[str, str]:
    """Run the converted GoldenMatch config on ``sample`` -> id -> cluster_id.

    When ``em_model`` is provided (a trained Splink import), it is injected via
    a temp ``model_path`` so ``dedupe_df`` uses the IMPORTED weights instead of
    silently retraining EM on the sample -- otherwise the agreement would
    measure a re-fit model, not the converted one. Mirrors the model-injection
    seam ``splink_upgrade_measure`` uses.
    """
    if em_model is None:
        mapping, _wall = _run_once(sample, gm_config, ids)
        return mapping

    config = gm_config.model_copy(deep=True)
    covered = set(em_model.match_weights)
    fully_covered = all(
        f.field in covered for f in config.matchkeys[0].fields if f.field
    )
    with tempfile.TemporaryDirectory() as tmp:
        if fully_covered:
            model_path = str(Path(tmp) / "em.json")
            em_model.save_json(model_path)
            config.matchkeys[0].model_path = model_path
        mapping, _wall = _run_once(sample, config, ids)
    return mapping


def verify_against_splink(
    settings: dict,
    data: pl.DataFrame | str | Path,
    gm_config: GoldenMatchConfig,
    *,
    em_model: EMResult | None = None,
    sample_size: int = _DEFAULT_SAMPLE,
    id_column: str | None = None,
    match_threshold: float = _DEFAULT_MATCH_THRESHOLD,
    report: ConversionReport | None = None,
) -> SplinkVerification | None:
    """Measure behavioural agreement between the conversion and real Splink.

    Runs the original ``settings`` (via a locally-installed Splink, DuckDB
    engine) AND the converted ``gm_config`` on a deterministic sample of
    ``data``, then reports pairwise cluster agreement.

    Returns ``None`` -- best-effort, never raising on the verify path itself --
    when: ``splink`` is not importable, the data is empty, or the original
    settings cannot be executed under the local engine (each recorded as a
    finding on ``report`` when one is supplied).

    Args:
        settings: the ORIGINAL Splink settings dict (what ``from_splink``
            converted from).
        data: a DataFrame or a ``.parquet`` / ``.csv`` path of the user's data.
        gm_config: the converted ``GoldenMatchConfig``.
        em_model: the imported ``EMResult`` for a trained Splink model, injected
            so GoldenMatch scores with the imported weights (not a re-fit).
        sample_size: rows to run through both engines (a seeded subsample above
            this cap; the whole frame at or below it).
        id_column: unique row-id column; defaults to
            ``unique_id``/``id``/``record_id`` then positional indices.
        match_threshold: Splink match-probability cut for its clustering (the
            GoldenMatch side clusters at its config's own link threshold).
        report: optional :class:`ConversionReport` to attach findings to.
    """

    def _note(kind: str, msg: str) -> None:
        # ConversionReport exposes info/warn/error; accept "warning" as an alias.
        if report is not None:
            method = "warn" if kind == "warning" else kind
            getattr(report, method)("verify", msg, None)

    if not _splink_available():
        _note(
            "info",
            "splink is not installed, so the conversion could not be verified "
            "against real Splink; `pip install splink` to enable the agreement "
            "check.",
        )
        return None

    frame = _load_frame(data)
    if frame.height == 0:
        _note("warning", "verify data is empty; agreement not measured.")
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
        splink_map, splink_version = _run_splink(
            settings, sample, ids, match_threshold
        )
    except Exception as exc:  # noqa: BLE001 -- verify is best-effort
        _note(
            "warning",
            "could not run the original Splink settings under the local DuckDB "
            f"engine ({type(exc).__name__}: {exc}); the conversion was written "
            "but not verified. This is expected for a Spark-dialect export whose "
            "comparison SQL uses Spark-only functions.",
        )
        return None

    gm_map = _run_goldenmatch(sample, gm_config, em_model, ids)

    shared = gm_map.keys() & splink_map.keys()
    gm_pairs, _c1 = pair_set({k: gm_map[k] for k in shared})
    sp_pairs, _c2 = pair_set({k: splink_map[k] for k in shared})
    agreement = pairwise_prf(gm_pairs, sp_pairs)

    result = SplinkVerification(
        agreement=agreement,
        n_records=len(ids),
        n_shared_ids=len(shared),
        gm_cluster_count=len(set(gm_map.values())),
        splink_cluster_count=len(set(splink_map.values())),
        gm_multi_clusters=_multi_cluster_count(gm_map),
        splink_multi_clusters=_multi_cluster_count(splink_map),
        match_threshold=match_threshold,
        splink_version=splink_version,
        id_source=id_source,
    )

    verdict = "faithful" if result.is_faithful else "DIVERGENT"
    _note(
        "info" if result.is_faithful else "warning",
        f"Splink agreement ({verdict}): pairwise F1={agreement['f1']:.3f} "
        f"(P={agreement['precision']:.3f}, R={agreement['recall']:.3f}) over "
        f"{len(shared)} records; GoldenMatch {result.gm_multi_clusters} vs "
        f"Splink {result.splink_multi_clusters} multi-record clusters at "
        f"match_threshold={match_threshold}.",
    )
    return result
