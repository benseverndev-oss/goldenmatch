"""GoldenMatch Identity Graph adapter -- v1.2.

Runs ``goldenmatch.identity.resolve_clusters`` after the dedupe stage to
populate a durable identity graph. Reuses cluster + scored_pairs artifacts
from DedupeStage. Idempotent: replaying the same ``run_id`` is a no-op.

Spec: ``docs/superpowers/specs/2026-05-13-goldenpipe-v1.2-identity-orchestration-design.md``
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import polars as pl

from goldenpipe.models.context import (
    Decision,
    PipeContext,
    StageResult,
    StageStatus,
)
from goldenpipe.models.stage import StageInfo

logger = logging.getLogger(__name__)

try:
    from goldenmatch.identity import IdentityStore, resolve_clusters

    HAS_IDENTITY = True
except ImportError:
    HAS_IDENTITY = False
    IdentityStore = None  # type: ignore[assignment,misc]
    resolve_clusters = None  # type: ignore[assignment]


_DEFAULT_PATH = ".goldenmatch/identity.db"


class IdentityResolveStage:
    info = StageInfo(
        name="goldenmatch.identity_resolve",
        produces=["identity_summary", "identity_store_path", "conflicts"],
        consumes=["df", "clusters"],
    )
    rollback = None

    def validate(self, ctx: PipeContext) -> None:
        if not HAS_IDENTITY:
            raise RuntimeError(
                "goldenmatch.identity not available. "
                "Identity Graph ships in goldenmatch>=1.15.0 -- "
                "upgrade with `pip install --upgrade goldenmatch`."
            )

    def run(self, ctx: PipeContext) -> StageResult:
        if not ctx.artifacts.get("clusters"):
            # decide_identity normally short-circuits us; this guard handles
            # callers who skip the decision logic and run the stage directly.
            return StageResult(
                status=StageStatus.SKIPPED,
                decision=Decision(
                    skip=["goldenmatch.identity_resolve"],
                    reason="no clusters from dedupe -- nothing to resolve",
                ),
            )

        stage_cfg = dict(ctx.stage_config or {})
        run_name = (
            stage_cfg.pop("run_name", None)
            or ctx.metadata.get("run_id")
            or uuid.uuid4().hex
        )
        db_path = stage_cfg.get("path", _DEFAULT_PATH)
        dataset = stage_cfg.get("dataset")
        source_pk_col = stage_cfg.get("source_pk_column")
        emit_singletons = stage_cfg.get("emit_singletons", True)
        weak_threshold = stage_cfg.get("weak_confidence_threshold", 0.6)
        backend = stage_cfg.get("backend", "sqlite")
        connection = stage_cfg.get("connection")

        # Resolve scored_pairs upstream if DedupeStage surfaced them; identity
        # still works without them (edges just won't include score info).
        scored_pairs = ctx.artifacts.get("scored_pairs", [])
        matchkey_name = ctx.artifacts.get("matchkey_used")

        # resolve_clusters keys on ``__row_id__`` + ``__source__`` on the
        # DataFrame, but DedupeStage doesn't surface the post-dedupe view --
        # those columns are set inside dedupe_df and not returned. Rebuild
        # them by enumeration; cluster ``members`` are positional row
        # indices in the same df, so 0..N-1 matches by construction.
        df = ctx.df
        if df is not None and "__row_id__" not in df.columns:
            df = df.with_columns(
                pl.int_range(0, df.height, dtype=pl.Int64).alias("__row_id__"),
            )
        if df is not None and "__source__" not in df.columns:
            src = ctx.metadata.get("source", "dataframe")
            stem = Path(str(src)).stem or "dataframe"
            df = df.with_columns(pl.lit(stem).alias("__source__"))

        store_kwargs: dict[str, Any] = {"backend": backend, "path": db_path}
        if connection is not None:
            store_kwargs["connection"] = connection

        try:
            with IdentityStore(**store_kwargs) as store:
                summary = resolve_clusters(
                    ctx.artifacts["clusters"],
                    df,
                    scored_pairs,
                    matchkey_name,
                    store,
                    run_name=run_name,
                    dataset=dataset,
                    source_pk_col=source_pk_col,
                    emit_singletons=emit_singletons,
                    weak_confidence_threshold=weak_threshold,
                )
        except Exception as e:
            logger.warning("Identity resolution failed: %s", e)
            return StageResult(status=StageStatus.FAILED, error=str(e))

        ctx.artifacts["identity_summary"] = summary.as_dict()
        ctx.artifacts["identity_store_path"] = db_path
        ctx.artifacts["conflicts"] = summary.conflicts_flagged
        logger.info(
            "Identity graph: %d created, %d absorbed, %d merged, %d conflicts",
            summary.created,
            summary.absorbed_records,
            summary.merged,
            summary.conflicts_flagged,
        )
        return StageResult(status=StageStatus.SUCCESS)


def decide_identity(ctx: PipeContext) -> Decision:
    """Skip identity_resolve when dedupe was skipped or produced no clusters.

    The pipeline driver calls this between stages. Returning ``skip=[]`` lets
    the stage run; populating ``skip`` excludes it.
    """
    clusters = ctx.artifacts.get("clusters")
    if not clusters:
        return Decision(
            skip=["goldenmatch.identity_resolve"],
            reason="no clusters from dedupe -- skipping identity resolution",
        )
    return Decision()
