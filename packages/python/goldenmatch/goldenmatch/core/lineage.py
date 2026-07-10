"""Lineage persistence -- save per-pair match explanations to a sidecar file.

Every merge decision gets a traceable explanation: which fields matched,
what scores they got, and why the pair was accepted. Enables post-hoc
auditing and "why did these merge?" queries without re-running the pipeline.

Supports streaming output for large runs (no in-memory cap).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from goldenmatch._polars_lazy import pl
from goldenmatch.config.schemas import MatchkeyConfig
from goldenmatch.core._logging import sanitize_for_log
from goldenmatch.core._paths import safe_path

logger = logging.getLogger(__name__)


def build_lineage(
    scored_pairs: list[tuple[int, int, float]],
    df: pl.DataFrame,
    matchkeys: list[MatchkeyConfig],
    clusters: dict[int, dict],
    max_pairs: int = 10000,
    natural_language: bool = False,
    em_results: dict | None = None,
) -> list[dict]:
    """Build lineage records for scored pairs.

    Args:
        scored_pairs: All scored pairs from the pipeline.
        df: Full DataFrame with record data.
        matchkeys: Matchkey configs used for scoring.
        clusters: Cluster results with membership info.
        max_pairs: Cap on lineage records (0 or None = no cap).
        natural_language: Whether to include NL explanations.
        em_results: Trained Fellegi-Sunter models keyed by probabilistic
            matchkey name. When present, each record gains an ``fs_waterfall``
            field (per-comparison log2(m/u) bits, prior, posterior).

    Returns:
        List of lineage dicts, one per scored pair.
    """
    from goldenmatch.core.explainer import explain_pair

    rows = df.to_dicts()
    row_ids = df["__row_id__"].to_list()
    id_to_idx = {rid: i for i, rid in enumerate(row_ids)}

    # Map row_id to cluster_id
    row_to_cluster: dict[int, int] = {}
    for cid, cinfo in clusters.items():
        for mid in cinfo["members"]:
            row_to_cluster[mid] = cid

    # Find the first weighted or probabilistic matchkey for explanations
    fields = []
    threshold = 0.80
    for mk in matchkeys:
        if mk.type in ("weighted", "probabilistic"):
            fields = mk.fields
            threshold = mk.threshold or 0.80
            break

    # FS waterfall source: first probabilistic matchkey with a trained model.
    fs_mk = None
    if em_results:
        fs_mk = next(
            (mk for mk in matchkeys
             if mk.type == "probabilistic" and mk.name in em_results),
            None,
        )

    # Determine pair limit
    effective_max = max_pairs if max_pairs else len(scored_pairs)

    lineage = []
    for a, b, score in scored_pairs[:effective_max]:
        idx_a = id_to_idx.get(a)
        idx_b = id_to_idx.get(b)
        if idx_a is None or idx_b is None:
            continue

        row_a = rows[idx_a]
        row_b = rows[idx_b]

        # Get field-level explanation
        field_details = []
        if fields:
            exp = explain_pair(row_a, row_b, fields, threshold)
            for f in exp.fields:
                field_details.append({
                    "field": f.field_name,
                    "scorer": f.scorer,
                    "value_a": f.value_a,
                    "value_b": f.value_b,
                    "score": round(f.score, 4),
                    "weight": f.weight,
                    "diff_type": f.diff_type,
                })

        record = {
            "row_id_a": a,
            "row_id_b": b,
            "score": round(score, 4),
            "cluster_id": row_to_cluster.get(a),
            "fields": field_details,
        }

        # Attach the Fellegi-Sunter match-weight waterfall when available.
        if fs_mk is not None:
            wf = _fs_waterfall_dict(row_a, row_b, fs_mk, em_results[fs_mk.name])
            if wf is not None:
                record["fs_waterfall"] = wf

        # Add natural language explanation
        if natural_language and field_details:
            from goldenmatch.core.explain import explain_pair_nl
            record["explanation"] = explain_pair_nl(row_a, row_b, field_details, score)

        lineage.append(record)

    return lineage


def golden_provenance_for_run(data_df, clusters, rules) -> list | None:
    """Build golden ClusterProvenance for a finished run from the source frame +
    clusters dict (the inputs the standalone lineage/explain surfaces have).
    Fail-open: returns None on any error. Returns None when there are no
    multi-member clusters. Returns None for non-survivorship configs so plain
    runs stay byte-identical."""
    try:
        import polars as pl

        from goldenmatch.core.golden import (
            _survivorship_active,
            build_golden_records_batch,
            golden_records_to_provenance,
        )
        if not _survivorship_active(rules):
            return None
        member_rows = [
            {"__row_id__": rid, "__cluster_id__": cid}
            for cid, cinfo in clusters.items()
            if cinfo.get("size", len(cinfo.get("members", []))) > 1
            for rid in cinfo.get("members", [])
        ]
        if not member_rows:
            return None
        multi_df = pl.DataFrame(member_rows).join(data_df, on="__row_id__", how="inner")
        rows = build_golden_records_batch(multi_df, rules, provenance=True)
        return golden_records_to_provenance(rows, clusters, rules)
    except Exception:
        logger.warning("lineage: golden provenance unavailable; skipping")
        return None


def _serialize_provenance(provenance: list) -> list[dict]:
    """Serialize ClusterProvenance dataclasses to dicts."""
    from dataclasses import asdict
    return [asdict(p) for p in provenance]


def _safe_cluster_audit(cp) -> str:
    """Fail-open render of a cluster's survivorship audit trail (group +
    condition + validation lines). '' when nothing survivorship-specific."""
    try:
        return render_cluster_provenance_nl(cp)
    except Exception:
        logger.warning("lineage: survivorship audit render failed; omitting")
        return ""


def _serialize_golden_records(provenance: list) -> list[dict]:
    records = _serialize_provenance(provenance)
    for cp, rec in zip(provenance, records):
        audit = _safe_cluster_audit(cp)
        if audit:
            rec["audit"] = audit
        for grp in rec.get("groups", []):
            if not grp.get("filled"):
                grp.pop("filled", None)
    return records


def _fs_waterfall_dict(row_a: dict, row_b: dict, mk, em_result) -> dict | None:
    """Compute + serialize the FS match-weight waterfall for a pair (or None)."""
    from goldenmatch.core.probabilistic import explain_pair_fs

    try:
        wf = explain_pair_fs(row_a, row_b, mk, em_result)
    except Exception:  # noqa: BLE001 — lineage is best-effort, never fail a run
        return None
    return {
        "prior_bits": round(wf.prior_bits, 4),
        "total_weight_bits": round(wf.total_weight_bits, 4),
        "final_bits": round(wf.final_bits, 4),
        "posterior": round(wf.posterior, 6),
        "proportion_matched": round(wf.proportion_matched, 6),
        "fields": [
            {
                "field": c.field,
                "scorer": c.scorer,
                "level": c.level,
                "n_levels": c.n_levels,
                "m": round(c.m, 6) if c.m == c.m else None,
                "u": round(c.u, 6) if c.u == c.u else None,
                "weight_bits": round(c.weight_bits, 4),
            }
            for c in wf.fields
        ],
    }


def save_lineage(
    lineage: list[dict],
    output_dir: str | Path,
    run_name: str,
    golden_provenance: list | None = None,
) -> Path:
    """Save lineage to a JSON sidecar file.

    Args:
        lineage: List of lineage dicts from build_lineage.
        output_dir: Directory to save the file.
        run_name: Run identifier for the filename.

    Returns:
        Path to the saved lineage file.
    """
    output_dir = safe_path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_name = Path(run_name).name  # strip any directory components

    path = output_dir / f"{run_name}_lineage.json"
    data = {
        "generated_at": datetime.now().isoformat(),
        "run_name": run_name,
        "total_pairs": len(lineage),
        "pairs": lineage,
    }
    if golden_provenance:
        data["golden_records"] = _serialize_golden_records(golden_provenance)
    path.write_text(json.dumps(data, default=str, indent=2), encoding="utf-8")
    logger.info("Saved lineage for %d pairs to %s", len(lineage), sanitize_for_log(path))
    return path


def save_lineage_streaming(
    scored_pairs: list[tuple[int, int, float]],
    df: pl.DataFrame,
    matchkeys: list[MatchkeyConfig],
    clusters: dict[int, dict],
    output_dir: str | Path,
    run_name: str,
    natural_language: bool = False,
    golden_provenance: list | None = None,
) -> Path:
    """Save lineage with streaming -- writes pairs incrementally to disk.

    No in-memory cap. Handles arbitrarily large pair lists.

    Args:
        scored_pairs: All scored pairs.
        df: Full DataFrame.
        matchkeys: Matchkey configs.
        clusters: Cluster results.
        output_dir: Output directory.
        run_name: Run identifier.
        natural_language: Include NL explanations.

    Returns:
        Path to the saved lineage file.
    """
    from goldenmatch.core.explainer import explain_pair

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{run_name}_lineage.json"

    rows = df.to_dicts()
    row_ids = df["__row_id__"].to_list()
    id_to_idx = {rid: i for i, rid in enumerate(row_ids)}

    row_to_cluster: dict[int, int] = {}
    for cid, cinfo in clusters.items():
        for mid in cinfo["members"]:
            row_to_cluster[mid] = cid

    fields = []
    threshold = 0.80
    for mk in matchkeys:
        if mk.type in ("weighted", "probabilistic"):
            fields = mk.fields
            threshold = mk.threshold or 0.80
            break

    # Stream write
    with open(path, "w", encoding="utf-8") as f:
        f.write('{\n')
        f.write(f'  "generated_at": "{datetime.now().isoformat()}",\n')
        f.write(f'  "run_name": "{run_name}",\n')
        f.write(f'  "total_pairs": {len(scored_pairs)},\n')
        f.write('  "pairs": [\n')

        written = 0
        for i, (a, b, score) in enumerate(scored_pairs):
            idx_a = id_to_idx.get(a)
            idx_b = id_to_idx.get(b)
            if idx_a is None or idx_b is None:
                continue

            row_a = rows[idx_a]
            row_b = rows[idx_b]

            field_details = []
            if fields:
                exp = explain_pair(row_a, row_b, fields, threshold)
                for fld in exp.fields:
                    field_details.append({
                        "field": fld.field_name,
                        "scorer": fld.scorer,
                        "value_a": fld.value_a,
                        "value_b": fld.value_b,
                        "score": round(fld.score, 4),
                        "weight": fld.weight,
                        "diff_type": fld.diff_type,
                    })

            record = {
                "row_id_a": a,
                "row_id_b": b,
                "score": round(score, 4),
                "cluster_id": row_to_cluster.get(a),
                "fields": field_details,
            }

            if natural_language and field_details:
                from goldenmatch.core.explain import explain_pair_nl
                record["explanation"] = explain_pair_nl(row_a, row_b, field_details, score)

            if written > 0:
                f.write(",\n")
            f.write("    " + json.dumps(record, default=str))
            written += 1

        f.write('\n  ]')
        if golden_provenance:
            f.write(',\n  "golden_records": ')
            f.write(json.dumps(_serialize_golden_records(golden_provenance), default=str, indent=2))
        f.write('\n}\n')

    logger.info("Streamed lineage for %d pairs to %s", written, path)
    return path


def load_lineage(path: str | Path) -> dict:
    """Load lineage from a JSON sidecar file."""
    path = Path(path)
    if not path.exists():
        return {"error": f"Lineage file not found: {path}"}
    return json.loads(path.read_text(encoding="utf-8"))


# ── Survivorship provenance NL rendering ──────────────────────────────────


def render_group_provenance_line(gp) -> str:
    """One audit line for a lock-step field group. Spec 4.1.

    When allow_fill back-filled any columns, appends one line per filled column:
    "{group_name}: {col} back-filled from record {rid}"
    """
    cols = ", ".join(gp.columns)
    line = (f"{cols} promoted together from record {gp.winner_row_id} "
            f"via {gp.strategy} (group '{gp.name}')")
    fills = [
        f"{gp.name}: {col} back-filled from record {rid}"
        for col, rid in (getattr(gp, "filled", {}) or {}).items()
    ]
    return "\n".join([line, *fills]) if fills else line


def render_field_condition_line(field: str, fp) -> str | None:
    """One audit line for a conditioned/validated field, or None if neither applies.
    Spec 4.1."""
    parts = []
    if getattr(fp, "condition", None):
        parts.append(f"{field} used {fp.strategy} because {fp.condition}")
    if getattr(fp, "dropped_invalid", 0) and getattr(fp, "validator", None):
        suffix = f" ({fp.dropped_invalid} candidate(s) dropped by {fp.validator})"
        if parts:
            parts[0] = parts[0] + suffix
        else:
            # Suffix-only (validator dropped candidates but no when: fired):
            # "{field}: {N} candidate(s) dropped by {validator}"
            parts.append(f"{field}: {fp.dropped_invalid} candidate(s) dropped by {fp.validator}")
    return parts[0] if parts else None


def render_cluster_provenance_nl(cp) -> str:
    """Render a ClusterProvenance's survivorship audit trail (group + condition
    lines) as newline-joined text. Returns '' when there is nothing survivorship-
    specific to report. Spec 4.1."""
    lines = []
    for gp in getattr(cp, "groups", []) or []:
        lines.append(render_group_provenance_line(gp))
    for field, fp in (getattr(cp, "fields", {}) or {}).items():
        line = render_field_condition_line(field, fp)
        if line:
            lines.append(line)
    return "\n".join(lines)
