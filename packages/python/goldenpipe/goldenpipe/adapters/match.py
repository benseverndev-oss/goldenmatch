"""GoldenMatch adapter -- wraps dedupe_df()."""
from __future__ import annotations

import logging

from goldenpipe.models.context import PipeContext, StageResult, StageStatus
from goldenpipe.models.stage import StageInfo

logger = logging.getLogger(__name__)

try:
    from goldenmatch import dedupe_df as _dedupe
    HAS_MATCH = True
except ImportError:
    HAS_MATCH = False
    _dedupe = None


class DedupeStage:
    info = StageInfo(name="goldenmatch.dedupe", produces=["clusters", "golden"], consumes=["df"])
    rollback = None

    def validate(self, ctx: PipeContext) -> None:
        if not HAS_MATCH:
            raise RuntimeError("GoldenMatch not installed. Run: pip install goldenpipe[match]")

    def run(self, ctx: PipeContext) -> StageResult:
        column_contexts = ctx.artifacts.get("column_contexts")

        if column_contexts:
            config = _build_config_from_contexts(column_contexts, ctx.df)
            if config is not None:
                logger.info("Built match config from pipeline column contexts")
                result = _dedupe(ctx.df, config=config)
            else:
                logger.info("Column contexts insufficient for config; using GoldenMatch auto-configure")
                result = _dedupe(ctx.df)
        else:
            # No upstream context — let GoldenMatch auto-configure
            result = _dedupe(ctx.df)

        if hasattr(result, "clusters"):
            ctx.artifacts["clusters"] = result.clusters
        if hasattr(result, "golden"):
            ctx.artifacts["golden"] = result.golden
        if hasattr(result, "unique"):
            ctx.artifacts["unique"] = result.unique
        if hasattr(result, "dupes"):
            ctx.artifacts["dupes"] = result.dupes
        if hasattr(result, "stats"):
            ctx.artifacts["match_stats"] = result.stats
        return StageResult(status=StageStatus.SUCCESS)


def _build_config_from_contexts(contexts: list, df) -> object | None:
    """Build a GoldenMatchConfig from pipeline column contexts.

    Returns None if no usable matchkeys can be built (caller falls back to auto-configure).
    """
    try:
        from goldenmatch.config.schemas import (
            GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
            BlockingConfig, BlockingKeyConfig,
        )
    except ImportError:
        logger.warning(
            "goldenmatch.config.schemas not available — cannot build config from column contexts"
        )
        return None

    from goldenpipe.models.column_context import ColumnType

    name_cols = [c for c in contexts if c.inferred_type == ColumnType.NAME and c.is_identifier]
    email_cols = [c for c in contexts if c.inferred_type == ColumnType.EMAIL]

    matchkeys = []

    # Exact matchkeys for high-quality discriminators
    for col in email_cols:
        matchkeys.append(MatchkeyConfig(
            name=f"exact_{col.name}",
            type="exact",
            fields=[MatchkeyField(field=col.name, transforms=["lowercase", "strip"])],
        ))

    # Fuzzy matchkey on name columns (the core of person matching)
    if name_cols:
        fuzzy_fields = []
        for col in name_cols:
            fuzzy_fields.append(MatchkeyField(
                field=col.name,
                scorer="jaro_winkler",
                weight=1.0,
                transforms=["lowercase", "strip"],
            ))
        matchkeys.append(MatchkeyConfig(
            name="fuzzy_names",
            type="weighted",
            threshold=0.85,
            fields=fuzzy_fields,
        ))

    # Fallback: if no identifier columns found, use all string columns
    if not matchkeys:
        string_cols = [c for c in contexts if c.inferred_type in (ColumnType.STRING, ColumnType.NAME)]
        fallback_fields = []
        for col in string_cols[:3]:
            fallback_fields.append(MatchkeyField(
                field=col.name,
                scorer="jaro_winkler",
                weight=1.0,
                transforms=["lowercase", "strip"],
            ))
        if fallback_fields:
            matchkeys.append(MatchkeyConfig(
                name="fuzzy_fallback",
                type="weighted",
                threshold=0.85,
                fields=fallback_fields,
            ))

    # If we still have no matchkeys, give up and let caller fall back to auto-configure
    if not matchkeys:
        logger.warning(
            "Could not build matchkeys from %d column contexts. Types: %s",
            len(contexts), [c.inferred_type for c in contexts],
        )
        return None

    # Blocking: use last_name soundex for person data, or best name column
    blocking = None
    last_name_cols = [c for c in name_cols if "last" in c.name.lower()]
    if last_name_cols:
        blocking = BlockingConfig(
            strategy="multi_pass",
            keys=[BlockingKeyConfig(fields=[last_name_cols[0].name], transforms=["lowercase", "soundex"])],
            passes=[
                BlockingKeyConfig(fields=[last_name_cols[0].name], transforms=["lowercase", "soundex"]),
                BlockingKeyConfig(fields=[last_name_cols[0].name], transforms=["lowercase", "substring:0:3"]),
            ],
            max_block_size=500,
        )
    elif name_cols:
        blocking = BlockingConfig(
            keys=[BlockingKeyConfig(fields=[name_cols[0].name], transforms=["lowercase", "soundex"])],
            max_block_size=500,
        )

    # If we still have no blocking, let GoldenMatch auto-suggest
    if not blocking:
        blocking = BlockingConfig(keys=[], auto_suggest=True)

    return GoldenMatchConfig(
        matchkeys=matchkeys,
        blocking=blocking,
    )
