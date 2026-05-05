from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from goldenmatch.config.schemas import (
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
    RulesPayload,
)
from goldenmatch.core.lineage import build_lineage
from goldenmatch.core.pipeline import run_dedupe_df
from goldenmatch.web.registry import PreviewRegistry
from goldenmatch.web.runs import RunRef


def _build_config(rules: RulesPayload) -> GoldenMatchConfig:
    """Wrap RulesPayload's matchkey list into a single weighted MatchkeyConfig."""
    matchkey = MatchkeyConfig(
        name="preview",
        type="weighted",
        threshold=rules.threshold,
        fields=[MatchkeyField(**m.model_dump(exclude_none=True)) for m in rules.matchkeys],
    )
    return GoldenMatchConfig(matchkey=[matchkey])


def _clusters_csv(clusters: dict[int, dict]) -> str:
    """Produce the row_id,cluster_id CSV rows the inspector expects."""
    rows: list[tuple[int, int]] = []
    for cid, cinfo in clusters.items():
        for member in cinfo.get("members", []):
            rows.append((int(member), int(cid)))
    rows.sort()
    df = pl.DataFrame(
        {"row_id": [r[0] for r in rows], "cluster_id": [r[1] for r in rows]},
        schema={"row_id": pl.Int64, "cluster_id": pl.Int64},
    )
    buf = io.StringIO()
    df.write_csv(buf)
    return buf.getvalue()


def run_preview(
    *,
    project_root: Path,
    rules: RulesPayload,
    sample_n: int,
    seed: int,
    registry: PreviewRegistry,
) -> RunRef:
    """Sample source CSV, run dedupe in-process, register result.

    Returns a RunRef registered under a synthetic preview-<uuid8> run_name.
    """
    # v1: single source CSV at project_root/data.csv. Multi-source proportional
    # sampling is deferred (see spec Open Questions).
    src_path = project_root / "data.csv"
    if not src_path.exists():
        raise FileNotFoundError("source CSV (data.csv) not found in project root")

    df = pl.read_csv(src_path)
    if df.height > sample_n:
        df = df.sample(n=sample_n, seed=seed)

    config = _build_config(rules)
    result = run_dedupe_df(df, config, output_clusters=True)

    clusters: dict[int, dict] = result.get("clusters") or {}

    # run_dedupe_df does not return scored_pairs; derive from cluster pair_scores.
    scored_pairs: list[tuple[int, int, float]] = []
    for cinfo in clusters.values():
        for (a, b), score in cinfo.get("pair_scores", {}).items():
            scored_pairs.append((int(a), int(b), float(score)))

    # Re-attach __row_id__ so build_lineage can resolve pair indices. The pipeline
    # adds __row_id__ on its working copy; reconstruct the same column here.
    enriched = df.with_columns(pl.int_range(0, df.height, dtype=pl.Int64).alias("__row_id__"))
    lineage_records = build_lineage(
        scored_pairs=scored_pairs,
        df=enriched,
        matchkeys=config.get_matchkeys(),
        clusters=clusters,
    )

    run_name = f"preview-{uuid.uuid4().hex[:8]}"
    lineage = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_name": run_name,
        "total_pairs": len(lineage_records),
        "pairs": lineage_records,
    }

    buf = io.StringIO()
    df.write_csv(buf)
    source_csv = buf.getvalue()

    return registry.put(
        run_name=run_name,
        lineage=lineage,
        clusters_csv=_clusters_csv(clusters),
        source_csv=source_csv,
    )
