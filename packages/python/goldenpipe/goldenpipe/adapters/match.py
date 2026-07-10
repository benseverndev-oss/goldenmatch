"""GoldenMatch adapter -- wraps dedupe_df()."""
from __future__ import annotations

import logging

import polars as pl

from goldenpipe.models.context import PipeContext, StageResult, StageStatus
from goldenpipe.models.stage import StageInfo

logger = logging.getLogger(__name__)

try:
    from goldenmatch import dedupe_df as _dedupe
    HAS_MATCH = True
except ImportError:
    HAS_MATCH = False
    _dedupe = None


def _throughput_from_hint(spec: dict | None):
    """Build GoldenMatch's throughput arg from a brain hint (auto-config + hint,
    not an override). GoldenMatchConfig.throughput is a ThroughputConfig, which
    dedupe_df(throughput=) accepts directly."""
    from goldenmatch.config.schemas import ThroughputConfig
    return ThroughputConfig(enabled=True, **(spec or {}))


class DedupeStage:
    info = StageInfo(name="goldenmatch.dedupe", produces=["clusters", "golden"], consumes=["df"])
    rollback = None

    def validate(self, ctx: PipeContext) -> None:
        if not HAS_MATCH:
            raise RuntimeError("GoldenMatch not installed. Run: pip install goldenpipe[match]")

    def run(self, ctx: PipeContext) -> StageResult:
        # Cast all columns to string to prevent schema mismatch errors
        # when mixed-type columns (e.g. birth_year as i64 vs str) reach GoldenMatch
        ctx.df = ctx.df.cast({col: pl.Utf8 for col in ctx.df.columns})

        # Priority 0: brain scale-hint -> auto-config + hint (do NOT override
        # GoldenMatch's controller; it merges kwargs with its auto-config).
        stage_cfg = ctx.stage_config
        hints = stage_cfg.get("_dedupe_hints") if stage_cfg else None
        if hints:
            throughput = _throughput_from_hint(hints.get("throughput"))
            logger.info("Applying auto-config scale hint (throughput) from the brain")
            result = _dedupe(ctx.df, throughput=throughput)
        elif stage_cfg:
            # Priority 1: explicit stage config from YAML/PipelineConfig
            from goldenmatch.config.schemas import GoldenMatchConfig
            config = GoldenMatchConfig(**stage_cfg)
            logger.info("Using explicit GoldenMatch config from stage spec")
            result = _dedupe(ctx.df, config=config)
        else:
            # Priority 2: build config from upstream column contexts
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
                # Priority 3: let GoldenMatch auto-configure
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
        # Surface scored_pairs for downstream stages (v1.2 IdentityResolveStage).
        # Memory cost ~80 B/pair; cheap relative to clusters/df already held.
        # No-op for callers that don't consume it.
        if hasattr(result, "scored_pairs"):
            ctx.artifacts["scored_pairs"] = result.scored_pairs
        # Surface the first matchkey name so IdentityResolveStage can attach it
        # to evidence edges. The matchkey list is available on the config or
        # the result -- prefer the result for accuracy after auto-config.
        mks = getattr(result, "matchkeys", None) or (
            config.get_matchkeys() if "config" in locals() else None
        )
        if mks:
            ctx.artifacts["matchkey_used"] = mks[0].name
        # SP3: surface goldenmatch's golden-record provenance (survivorship audit) as an
        # advisory pipeline artifact. None (byte-identical) unless survivorship is active.
        from goldenpipe.compiler.e2e import surface_golden_provenance
        ctx.artifacts["golden_provenance"] = surface_golden_provenance(result, ctx.artifacts.get("clusters"))
        return StageResult(status=StageStatus.SUCCESS)


class FusedDedupeStage:
    """Opt-in Arrow-native fused match stage (memory/scale + composability).

    Runs goldenmatch's `match_fused` kernel (block+score+dedup+cluster in ONE
    FFI call) over the frame's columns as Arrow, with no intermediate
    `pl.DataFrame`/pairs-list materialization. MEASURED (1M-10M): wall-neutral
    vs the classic DedupeStage but ~2x lower peak RSS, byte-identical clusters --
    so this is the stage a GoldenPipe chain places when it wants cluster
    assignments at scale on a memory-constrained box, threaded as Arrow.

    CLUSTERS ONLY: it produces `clusters` + `cluster_assignments` (Arrow), NOT
    golden/unique/dupes (the fused kernel's declined scope -- golden records need
    the row payload, which stays the classic DedupeStage's job). Requires an
    EXPLICIT covered config (`match_fused_ready`): static single-key blocking +
    one weighted matchkey over covered scorers with a threshold. Transforms
    (lowercase/strip/substring/soundex/...) ARE covered -- derived host-side via
    the pipeline's own transform reference before the kernel runs. `validate()`
    raises a clear pointer to `goldenmatch.dedupe` for an uncovered config -- it
    never silently falls back, so a caller who asked for the fused stage always
    gets the fused path or an explicit error.
    """

    info = StageInfo(
        name="goldenmatch.dedupe_fused",
        produces=["clusters", "cluster_assignments"],
        consumes=["df"],
    )
    rollback = None

    def _config(self, ctx: PipeContext):
        stage_cfg = ctx.stage_config
        if not stage_cfg:
            return None
        from goldenmatch.config.schemas import GoldenMatchConfig

        return GoldenMatchConfig(**stage_cfg)

    def validate(self, ctx: PipeContext) -> None:
        if not HAS_MATCH:
            raise RuntimeError("GoldenMatch not installed. Run: pip install goldenpipe[match]")
        from goldenmatch.core.fused_match import _match_fused_symbol, match_fused_ready

        if _match_fused_symbol() is None:
            raise RuntimeError(
                "goldenmatch-native match_fused kernel unavailable. "
                "Install goldenmatch[native] or use the goldenmatch.dedupe stage."
            )
        config = self._config(ctx)
        if config is None:
            raise RuntimeError(
                "goldenmatch.dedupe_fused requires an explicit covered config in the "
                "stage spec (auto-built configs carry transforms the fused kernel does "
                "not cover). Supply one, or use the goldenmatch.dedupe stage."
            )
        if not match_fused_ready(config):
            raise RuntimeError(
                "config is not covered by the fused match kernel (needs static "
                "single-key blocking + one weighted matchkey over "
                "jaro_winkler/levenshtein/token_sort/exact with a threshold; "
                "transforms are fine). Use the goldenmatch.dedupe stage for this config."
            )

    def run(self, ctx: PipeContext) -> StageResult:
        from goldenmatch.core.fused_match import run_match_fused_arrow

        config = self._config(ctx)
        # Only the key + score columns cross as Arrow (one zero-copy view each);
        # no whole-frame or intermediate re-materialization.
        key_fields = list(config.blocking.keys[0].fields)
        score_fields = [f.field for f in config.get_matchkeys()[0].fields]
        needed = list(dict.fromkeys([*key_fields, *score_fields]))
        columns = {c: ctx.df[c].to_arrow() for c in needed}

        table = run_match_fused_arrow(columns, config, n_rows=ctx.df.height)
        if table is None:  # defensive: validate() already gated coverage
            raise RuntimeError("fused match declined a config validate() accepted")

        ctx.artifacts["cluster_assignments"] = table
        # Multi-member clusters as {cluster_id: [row_id, ...]} for downstream
        # consumers (e.g. IdentityResolveStage reads `clusters`).
        clusters: dict[int, list[int]] = {}
        for rid, cid in zip(
            table.column("__row_id__").to_pylist(),
            table.column("__cluster_id__").to_pylist(),
        ):
            clusters.setdefault(cid, []).append(rid)
        ctx.artifacts["clusters"] = {c: m for c, m in clusters.items() if len(m) >= 2}
        return StageResult(status=StageStatus.SUCCESS)


def _build_config_from_contexts(contexts: list, df) -> object | None:
    """Build a GoldenMatchConfig from pipeline column contexts.

    Returns None if no usable matchkeys can be built (caller falls back to auto-configure).
    """
    try:
        from goldenmatch.config.schemas import (
            BlockingConfig,
            BlockingKeyConfig,
            GoldenMatchConfig,
            MatchkeyConfig,
            MatchkeyField,
        )
    except ImportError:
        logger.warning(
            "goldenmatch.config.schemas not available — cannot build config from column contexts"
        )
        return None

    from goldenpipe.models.column_context import ColumnType

    name_cols = [c for c in contexts if c.inferred_type == ColumnType.NAME and c.is_identifier]
    email_cols = [c for c in contexts if c.inferred_type == ColumnType.EMAIL]
    geo_cols = [c for c in contexts if c.inferred_type == ColumnType.GEO]

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

    # Fallback: if no identifier columns found, use discriminative string columns.
    # Exclude low-cardinality columns (e.g. hospital_type with 5 values) — they inflate
    # fuzzy scores without providing meaningful discrimination.
    if not matchkeys:
        string_cols = [c for c in contexts if c.inferred_type in (ColumnType.STRING, ColumnType.NAME)]
        if df is not None:
            min_cardinality = max(10, int(df.height * 0.05))  # at least 5% unique values
            string_cols = [
                c for c in string_cols
                if df[c.name].drop_nulls().n_unique() >= min_cardinality
            ]
            if not string_cols:
                logger.warning(
                    "All string columns filtered by cardinality floor (%d) — "
                    "falling back to GoldenMatch auto-configure",
                    min_cardinality,
                )
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

    # Blocking: compound geo columns with name to prevent cross-region false positives
    blocking = None
    best_geo = None

    # Find best geo column for compound blocking.
    # Prefer low-cardinality geo (like state ~50 values) over high-cardinality (like city ~3000)
    # because low-cardinality geo provides broader geographic discrimination and avoids
    # same-city-name-different-state false positives.
    if geo_cols and df is not None:
        max_null_rate = 0.20
        geo_candidates = []
        for g in geo_cols:
            null_rate = df[g.name].null_count() / df.height if df.height > 0 else 1.0
            if null_rate <= max_null_rate:
                cardinality = df[g.name].drop_nulls().n_unique()
                geo_candidates.append((g.name, cardinality))
        if geo_candidates:
            # Pick lowest cardinality (broadest geo level, e.g. state over city)
            geo_candidates.sort(key=lambda x: x[1])
            best_geo = geo_candidates[0][0]

    def _make_blocking(primary_fields, recall_name, with_geo=False):
        """Build a BlockingConfig with consistent structure.

        Soundex is only applied to the name field (never to geo columns, where
        it produces meaningless hashes like "CA" -> "C000").
        """
        passes = [
            BlockingKeyConfig(fields=primary_fields, transforms=["lowercase", "strip"]),
        ]
        if with_geo:
            # Recall pass: geo + name substring (catches abbreviation variants)
            passes.append(
                BlockingKeyConfig(fields=primary_fields, transforms=["lowercase", "substring:0:3"]),
            )
        # Recall pass: name-only soundex (catches phonetic variants, relies on skip_oversized)
        passes.append(
            BlockingKeyConfig(fields=[recall_name], transforms=["lowercase", "soundex"]),
        )
        return BlockingConfig(
            strategy="multi_pass",
            keys=[passes[0]],
            passes=passes,
            max_block_size=500,
            skip_oversized=True,
        )

    last_name_cols = [c for c in name_cols if "last" in c.name.lower()]
    if last_name_cols:
        best_name = last_name_cols[0].name
        if best_geo:
            blocking = _make_blocking([best_geo, best_name], best_name, with_geo=True)
        else:
            blocking = _make_blocking([best_name], best_name)
    elif name_cols:
        best_name = name_cols[0].name
        if best_geo:
            blocking = _make_blocking([best_geo, best_name], best_name, with_geo=True)
        else:
            blocking = BlockingConfig(
                keys=[BlockingKeyConfig(fields=[best_name], transforms=["lowercase", "soundex"])],
                max_block_size=500,
                skip_oversized=True,
            )

    # Fallback: no name columns, but we have string columns in matchkeys + geo columns
    if not blocking and best_geo and matchkeys:
        fuzzy_mks = [mk for mk in matchkeys if mk.type == "weighted"]
        if fuzzy_mks and fuzzy_mks[0].fields:
            anchor = fuzzy_mks[0].fields[0].field
            blocking = _make_blocking([best_geo, anchor], anchor, with_geo=True)
            logger.info("Geo-compound blocking from string fallback: [%s, %s]", best_geo, anchor)

    # If we still have no blocking, let GoldenMatch auto-suggest
    if not blocking:
        blocking = BlockingConfig(keys=[], auto_suggest=True)

    return GoldenMatchConfig(
        matchkeys=matchkeys,
        blocking=blocking,
    )
