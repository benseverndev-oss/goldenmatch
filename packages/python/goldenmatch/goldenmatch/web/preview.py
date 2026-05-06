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
    """Translate the workbench's flat matchkey list into a GoldenMatchConfig.

    The workbench presents each row as an independent matchkey — semantically
    these are OR'd: a pair matches if ANY matchkey qualifies. The engine
    evaluates separate MatchkeyConfigs with that OR semantic, so build one
    MatchkeyConfig per workbench row:

      - ``scorer == "exact"`` → MatchkeyConfig(type=exact, fields=[field])
        Pairs match when the (transformed) values are identical.
      - any fuzzy scorer → MatchkeyConfig(type=weighted, threshold=rules.threshold,
        fields=[field]). Single-field weighted is fine; the threshold gates
        whether the pair survives.

    Wrapping everything into one weighted matchkey would AND-average the
    field scores, which produces almost no matches when one column hits 1.0
    while another hits 0.4 (e.g. same email but different name).

    Two earlier bugs to keep in mind here:
      1. The schema field is ``matchkeys`` (plural). ``matchkey=`` is silently
         dropped because pydantic discards unknown kwargs.
      2. Weighted matchkeys require a blocking config; without one the
         engine generates zero comparisons. ``BlockingConfig(keys=[],
         auto_suggest=True)`` mirrors what ``goldenmatch.dedupe_df`` does for
         callers who haven't hand-tuned blocking.
    """
    from goldenmatch.config.schemas import BlockingConfig, StandardizationConfig

    matchkeys: list[MatchkeyConfig] = []
    for i, m in enumerate(rules.matchkeys):
        col = m.column or m.field or ""
        scorer = m.scorer or "exact"
        if scorer == "exact":
            matchkeys.append(
                MatchkeyConfig(
                    name=f"exact_{col or i}",
                    type="exact",
                    fields=[
                        MatchkeyField(
                            field=m.field,
                            column=m.column,
                            transforms=list(m.transforms or []),
                        )
                    ],
                )
            )
        else:
            matchkeys.append(
                MatchkeyConfig(
                    name=f"fuzzy_{col or i}",
                    type="weighted",
                    threshold=rules.threshold,
                    fields=[
                        MatchkeyField(
                            field=m.field,
                            column=m.column,
                            scorer=scorer,
                            weight=float(m.weight) if m.weight is not None else 1.0,
                            transforms=list(m.transforms or []),
                        )
                    ],
                )
            )

    standardization = (
        StandardizationConfig(rules=dict(rules.standardization))
        if rules.standardization
        else None
    )
    # Workbench default if the user hasn't pinned blocking: auto_suggest with
    # no static keys, mirroring goldenmatch.dedupe_df's zero-config path.
    blocking = rules.blocking or BlockingConfig(keys=[], auto_suggest=True)
    return GoldenMatchConfig(
        matchkeys=matchkeys,
        blocking=blocking,
        standardization=standardization,
    )


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

    # The engine accepts unknown columns silently and returns empty results —
    # for a workbench, that's the wrong UX (a typo looks like "no matches").
    # Validate up front and raise so the router maps to 400.
    referenced_columns = {m.column or m.field for m in rules.matchkeys if (m.column or m.field)}
    missing = sorted(referenced_columns - set(df.columns))
    if missing:
        raise ValueError(f"matchkey references unknown column(s): {missing}")

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
