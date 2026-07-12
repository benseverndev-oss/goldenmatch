"""Pipeline orchestrator for GoldenMatch dedupe and list-match workflows."""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from goldenmatch.core.cluster_pairscores import ClusterPairScores
    from goldenmatch.distributed.record_store import PreparedRecordStore

from goldenmatch._polars_lazy import pl
from goldenmatch.config.schemas import GoldenMatchConfig, GoldenRulesConfig
from goldenmatch.core.autofix import auto_fix_dataframe
from goldenmatch.core.bench import record_metric, record_metrics, stage
from goldenmatch.core.block_analyzer import analyze_blocking
from goldenmatch.core.blocker import build_blocks
from goldenmatch.core.ingest import apply_column_map, load_file, validate_columns
from goldenmatch.core.matchkey import compute_matchkeys, precompute_matchkey_transforms
from goldenmatch.core.scorer import (
    find_exact_matches,
    rerank_top_pairs,
    score_blocks_parallel,
)
from goldenmatch.core.standardize import apply_standardization
from goldenmatch.core.validate import ValidationRule, validate_dataframe


def _load_input_frames(config: Any) -> Any:  # pyright: ignore[reportUnusedFunction]
    """Route input loading to distributed or legacy loader.

    Phase 1 helper, opt-in via env flag. Not yet wired into the default
    pipeline — Phase 2+ work. Suppression on the unused-function lint is
    deliberate; removing this would lose the env-gated route.

    Distributed (Ray Dataset) when:
      - config.backend == "ray", AND
      - GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1

    Otherwise legacy ``core.ingest.load_files`` returning ``list[pl.LazyFrame]``.
    Phase 1 of the Splink-Spark roadmap — see
    docs/superpowers/specs/2026-05-19-ray-splink-spark-parity-roadmap.md.
    """
    import os
    use_distributed = (
        getattr(config, "backend", None) == "ray"
        and os.environ.get("GOLDENMATCH_ENABLE_DISTRIBUTED_RAY") == "1"
    )
    if use_distributed:
        from goldenmatch.distributed import read_csv_partitioned
        n = getattr(config, "n_partitions", None) or _default_distributed_partitions()
        return read_csv_partitioned(list(config.inputs), n_partitions=n)
    from goldenmatch.core.ingest import load_files
    return load_files([(p, "csv") for p in config.inputs])


def _default_distributed_partitions() -> int:
    """Default partition count for the distributed loader.

    4 partitions per core, clamped to [4, 256]. Phase 2 will compute this
    from runtime profile; Phase 1 uses a heuristic.
    """
    import os
    return min(256, max(4, (os.cpu_count() or 4) * 4))


def _unwrap_llm_pairs(
    result: list[tuple[int, int, float]] | tuple[list[tuple[int, int, float]], Any]
) -> list[tuple[int, int, float]]:
    """Narrow the LLM-scorer return type for the pipeline call site.

    Both ``llm_score_pairs`` and ``llm_cluster_pairs`` return a (pairs, stats)
    tuple when ``return_stats=True``; the pipeline never asks for that path,
    so the runtime is always a bare list. This helper makes that explicit for
    the type checker.
    """
    if isinstance(result, tuple):
        return result[0]
    return result


def _get_block_scorer(config: GoldenMatchConfig):
    """Return the block scoring function based on configured backend."""
    backend = getattr(config, "backend", None)
    if backend == "ray":
        from goldenmatch.backends.ray_backend import score_blocks_ray
        return score_blocks_ray
    if backend == "duckdb":
        # Routes block scoring through goldenmatch.backends.score_duckdb,
        # which accumulates pairs in a DuckDB table (in-memory by default;
        # set GOLDENMATCH_DUCKDB_SCORE_DB to an on-disk path, or use
        # "auto" for a tempfile, to spill to disk). Per-block rapidfuzz
        # cdist work is unchanged; only the pair accumulator moves out
        # of the Python list. Until this PR, config.backend="duckdb"
        # was silently a no-op for processing — only the source
        # connector branch existed.
        from goldenmatch.backends.score_duckdb import score_blocks_duckdb
        return score_blocks_duckdb
    if backend == "datafusion":
        # Experimental backend (spike: docs/superpowers/specs/
        # 2026-05-30-datafusion-backend-spike-design.md). Routes block
        # scoring through Apache DataFusion with native scorers wrapped
        # as vectorized Arrow-batch UDFs. Day-2 scope: single-field
        # weighted matchkey with one of {jaro_winkler, levenshtein,
        # token_sort}; raises NotImplementedError outside that scope
        # (callers must NOT catch + silently fall back -- the spike
        # depends on deterministic routing to produce interpretable
        # bench numbers). Requires goldenmatch[datafusion] + the
        # compiled goldenmatch._native module.
        from goldenmatch.backends.datafusion_backend import (
            score_blocks_datafusion,
        )
        return score_blocks_datafusion
    return score_blocks_parallel
from goldenmatch.core.cluster import (
    ClusterFrames,
    build_cluster_frames,
    build_clusters_columnar,
    cluster_frames_to_dict,
)

# ── Columnar pipeline fast-path (Arrow roadmap Phase A) ──────────────
# Routes the eligible single-fuzzy-matchkey dedupe shape through the
# columnar pair-stream path (score_blocks_columnar -> build_clusters_columnar)
# instead of the list path (score_blocks_parallel -> build_clusters). The
# 1M profile-hotspots run measured the columnar path ~38% faster (359s vs
# 575s) -- the win is the columnar scorer's direct-DataFrame emit over the
# pair stream, not the cluster build. Default OFF; eligibility is the narrow
# shape proven byte-identical to the list path by
# tests/test_columnar_pipeline_parity.py. Design: docs/columnar-pipeline-wiring.md.
_COLUMNAR_NON_DEFAULT_BACKENDS = frozenset({
    "ray", "duckdb", "duckdb-backend", "datafusion", "bucket", "chunked",
})
_COLUMNAR_SAFE_SCORERS = frozenset({
    "jaro_winkler", "levenshtein", "token_sort", "token_set", "ratio",
    "exact", "soundex_match", "dice", "jaccard", "ensemble",
})


def _columnar_pipeline_enabled() -> bool:
    """Phase A opt-in via ``GOLDENMATCH_COLUMNAR_PIPELINE`` (default OFF)."""
    return os.environ.get("GOLDENMATCH_COLUMNAR_PIPELINE", "0").strip().lower() in (
        "1", "true", "yes",
    )


def _is_columnar_eligible(
    config: GoldenMatchConfig, matchkeys: list, across_files_only: bool,
) -> bool:
    """True only for the narrow shape where the columnar fast-path is
    byte-identical to the list path AND free of the list-coupled optional
    steps (exact/probabilistic aggregation, postflight signals + threshold
    filter, rerank, LLM scorer/boost). See docs/columnar-pipeline-wiring.md."""
    if across_files_only or config.blocking is None:
        return False
    if getattr(config, "backend", None) in _COLUMNAR_NON_DEFAULT_BACKENDS:
        return False
    # Auto-config postflight consumes the pair LIST (signals + threshold filter).
    if getattr(config, "_preflight_report", None) is not None:
        return False
    if getattr(config, "llm_scorer", None) is not None:
        return False
    if getattr(config, "boost", None) is not None:
        return False
    if len(matchkeys) != 1:
        return False
    mk = matchkeys[0]
    if getattr(mk, "type", None) != "weighted" or getattr(mk, "rerank", False):
        return False
    for f in (getattr(mk, "fields", None) or []):
        if (getattr(f, "scorer", None) or "") not in _COLUMNAR_SAFE_SCORERS:
            return False
    return True

from goldenmatch.core.fused_routing import config_needs_artifacts
from goldenmatch.core.golden import (
    _polars_native_eligible,
    build_golden_records_batch,
    build_golden_records_df,
)
from goldenmatch.output.report import generate_dedupe_report, generate_match_report
from goldenmatch.output.writer import write_output

logger = logging.getLogger(__name__)


def _accumulate_block_candidate_pairs(
    block_df: pl.DataFrame,
    candidate_pairs: set[tuple[int, int]],
) -> None:
    """Add every within-block canonical ``(min, max)`` ``__row_id__`` pair to
    ``candidate_pairs`` (the blocking ceiling for the bench pair dump).

    The blocking ceiling is backend-independent — it is defined by the blocks
    that ``build_blocks`` produced, not by which scorer (per-block vectorized
    loop vs. the hash-bucket orchestration) actually compares them. Both the
    polars-direct and the ``backend="bucket"`` probabilistic paths feed their
    blocks through here so the candidate denominator is identical.

    Caps quadratic blow-up on a pathological huge block (panel data has small
    blocks; this is just a safety net) by skipping blocks over 20k members.
    """
    block_ids = block_df["__row_id__"].to_list()
    if len(block_ids) > 20000:
        logger.warning(
            "GOLDENMATCH_BENCH_DUMP_PAIRS: skipping candidate dump for block "
            "of size %d (> 20000 members) to avoid quadratic explosion",
            len(block_ids),
        )
        return
    for i in range(len(block_ids)):
        id_i = block_ids[i]
        for j in range(i + 1, len(block_ids)):
            id_j = block_ids[j]
            candidate_pairs.add((min(id_i, id_j), max(id_i, id_j)))


def _dump_bench_pairs(
    dump_dir: str,
    candidate_pairs: set[tuple[int, int]],
    emitted_pairs: set[tuple[int, int]],
) -> None:
    """Write candidate + emitted pair sets to parquet for the bench harness.

    Opt-in, env-gated companion to the ``GOLDENMATCH_BENCH_DUMP_PAIRS`` hook in
    the probabilistic dedupe branch. Pairs are canonical ``(min, max)`` tuples
    in internal ``__row_id__`` space (the harness remaps to record_id later).

    Two files land in ``dump_dir``:
      - ``candidate_pairs.parquet`` (cols ``a``, ``b``): all within-block
        candidate pairs across every probabilistic matchkey (the blocking
        ceiling).
      - ``emitted_pairs.parquet`` (cols ``a``, ``b``): the pairs the
        probabilistic scorer emitted above threshold.

    Each file is written atomically (``.tmp`` then ``os.replace``) so a reader
    never sees a partial parquet.
    """
    try:
        os.makedirs(dump_dir, exist_ok=True)
        for name, pairs in (
            ("candidate_pairs.parquet", candidate_pairs),
            ("emitted_pairs.parquet", emitted_pairs),
        ):
            a_col = [p[0] for p in pairs]
            b_col = [p[1] for p in pairs]
            frame = pl.DataFrame(
                {"a": a_col, "b": b_col},
                schema={"a": pl.Int64, "b": pl.Int64},
            )
            final_path = os.path.join(dump_dir, name)
            tmp_path = final_path + ".tmp"
            frame.write_parquet(tmp_path)
            os.replace(tmp_path, final_path)
    except Exception as exc:  # observability must never abort a dedupe run
        logger.warning(
            "GOLDENMATCH_BENCH_DUMP_PAIRS: failed to write pair dump to %s: %s",
            dump_dir, exc,
        )


def _extract_matchkey_columns(config: GoldenMatchConfig) -> list[str]:
    """Extract unique field names from all matchkeys in config."""
    cols = set()
    for mk in config.get_matchkeys():
        for f in mk.fields:
            cols.add(f.field)
    return sorted(cols)


def _propagate_autoconfig_markers(
    src: GoldenMatchConfig, dst: GoldenMatchConfig
) -> None:
    """Copy preflight-verification markers from an auto_configure_df result
    onto the user-facing ``dst`` config so postflight (later in the pipeline)
    knows auto-config was used and whether strict mode is on.

    These are underscore-private attrs, not Pydantic fields. ``dst.domain`` is
    not touched here — callers handle that explicitly because the assignment
    semantics differ (``domain`` is a Pydantic field, the markers are not).
    """
    if getattr(src, "_preflight_report", None) is not None:
        dst._preflight_report = src._preflight_report
    if getattr(src, "_strict_autoconfig", False):
        dst._strict_autoconfig = True


def _open_memory_store(config: GoldenMatchConfig):
    """Open the MemoryStore configured on `config`. Returns None on failure or
    when memory is disabled — pipeline must continue regardless."""
    if not config.memory or not config.memory.enabled:
        return None
    try:
        from goldenmatch.core.memory.store import MemoryStore
        return MemoryStore(
            backend=config.memory.backend,
            path=config.memory.path,
            connection=config.memory.connection,
            table_prefix=config.memory.table_prefix,
        )
    except Exception as e:
        logger.warning("Memory store init failed, continuing without memory: %s", e)
        return None


def _open_identity_store(config: GoldenMatchConfig):
    """Open the IdentityStore configured on ``config``. Returns None when
    identity graph is disabled or initialization fails (additive feature)."""
    if not config.identity or not config.identity.enabled:
        return None
    try:
        from goldenmatch.identity import IdentityStore
        return IdentityStore(
            backend=config.identity.backend,
            path=config.identity.path,
            connection=config.identity.connection,
        )
    except Exception as e:
        logger.warning("Identity store init failed, continuing without: %s", e)
        return None


def _resolve_identities(
    clusters: Any,
    df: pl.DataFrame,
    scored_pairs: list,
    matchkeys: list,
    config: GoldenMatchConfig,
    run_name: str,
    pair_score_view: Any = None,
    cluster_frames: ClusterFrames | None = None,
) -> dict | None:
    """Run identity resolution as a post-cluster step. Best-effort: failures
    log a warning and return None without affecting dedupe output.

    Polymorphic on ``clusters``:
    - ``dict[int, dict]`` -> in-memory resolver (default).
    - ``ray.data.Dataset`` -> distributed dispatch (Phase 6). Requires
      ``config.identity.backend == 'postgres'``.

    SP-C: when ``cluster_frames`` is supplied (frames-out path), ``clusters``
    is ``None`` and the in-memory resolver consumes the two-frame
    ``ClusterFrames`` directly via ``resolve_clusters(cluster_frames=...)`` --
    no dict is rebuilt for identity. ``pair_score_view`` is REQUIRED in that
    case (the frames carry no per-cluster pair_scores). The distributed/Ray
    branch is unreachable on the frames path (``clusters`` is ``None``, so
    ``is_ray_dataset`` is False) and stays byte-identical.
    """
    if not config.identity or not config.identity.enabled:
        return None

    # Phase 6: distributed dispatch when clusters is a Ray Dataset.
    try:
        from goldenmatch.distributed._utils import is_ray_dataset
    except Exception:
        is_ray_dataset = lambda _x: False  # noqa: E731

    if is_ray_dataset(clusters):
        if config.identity.backend != "postgres":
            logger.warning(
                "Distributed identity resolution requires backend='postgres'; "
                "got %r. Skipping identity.",
                config.identity.backend,
            )
            return None
        if not config.identity.connection:
            logger.warning(
                "Distributed identity resolution requires identity.connection "
                "(Postgres DSN). Skipping identity."
            )
            return None
        try:
            from goldenmatch.distributed.identity import (
                resolve_identities_distributed,
            )
            mk_name = matchkeys[0].name if matchkeys else None
            summary = resolve_identities_distributed(
                clusters, df, scored_pairs, mk_name,
                dsn=config.identity.connection,
                run_name=run_name,
                dataset=config.identity.dataset,
                source_pk_col=config.identity.source_pk_column,
            )
            return summary.as_dict()
        except Exception as e:
            logger.warning("Distributed identity resolution failed: %s", e)
            return None

    # In-memory path (legacy, unchanged).
    store = _open_identity_store(config)
    if store is None:
        return None
    try:
        from goldenmatch.identity import resolve_clusters
        mk_name = matchkeys[0].name if matchkeys else None
        # SP-C: on the frames-out path pass `cluster_frames=` (clusters is None)
        # so resolve_clusters iterates the frames directly; otherwise pass the
        # `clusters` dict positionally. resolve_clusters asserts exactly one of
        # the two is supplied, so the two are mutually exclusive here.
        summary = resolve_clusters(
            None if cluster_frames is not None else clusters,
            df, scored_pairs, mk_name, store,
            run_name=run_name,
            dataset=config.identity.dataset,
            source_pk_col=config.identity.source_pk_column,
            emit_singletons=config.identity.emit_singletons,
            weak_confidence_threshold=config.identity.weak_confidence_threshold,
            pair_score_view=pair_score_view,
            cluster_frames=cluster_frames,
        )
        return summary.as_dict()
    except Exception as e:
        logger.warning("Identity resolution failed: %s", e)
        return None
    finally:
        try:
            store.close()
        except Exception:
            pass


def _cast_user_cols_to_str(df: pl.DataFrame) -> pl.DataFrame:
    """Cast all non-internal columns to Utf8.

    Used by both file and DataFrame match entry points so that (a) per-file
    CSV inference cannot produce schema-incompatible columns across the
    target/reference vstack and (b) string transforms like lowercase/strip
    always have a string to consume in zero-config paths.
    """
    return df.cast({c: pl.Utf8 for c in df.columns if not c.startswith("__")})


def _apply_domain_extraction(
    combined_lf: pl.LazyFrame,
    config: GoldenMatchConfig,
) -> pl.LazyFrame:
    """Run domain feature extraction if configured.

    Materializes derived columns (``__title_key__``, ``__brand__``,
    ``__model__`` etc.) that auto-config may reference from matchkeys or
    blocking keys. Shared by ``_run_dedupe_pipeline`` and
    ``_run_match_pipeline``; without it the match pipeline crashes whenever
    auto-config detects a domain (CRASH cause for DBLP-ACM-shaped inputs).
    """
    domain_cfg = config.domain
    if not (domain_cfg and domain_cfg.enabled):
        return combined_lf

    from goldenmatch.core.domain import detect_domain, extract_features

    combined_df_tmp = combined_lf.collect()
    user_cols = [c for c in combined_df_tmp.columns if not c.startswith("__")]

    if domain_cfg.mode == "auto" or domain_cfg.mode is None:
        domain_profile = detect_domain(user_cols)
    else:
        from goldenmatch.core.domain import DomainProfile
        domain_profile = DomainProfile(
            name=domain_cfg.mode, confidence=1.0,
            text_columns=[
                c for c in user_cols
                if any(p in c.lower() for p in ("name", "title", "description", "product"))
            ],
        )

    if domain_profile.confidence <= 0.3:
        return combined_lf

    logger.info(
        "Domain detected: %s (confidence %.2f)",
        domain_profile.name, domain_profile.confidence,
    )
    combined_df_tmp, low_conf_ids = extract_features(
        combined_df_tmp, domain_profile, domain_cfg.confidence_threshold,
    )

    if domain_cfg.llm_validation and low_conf_ids:
        from goldenmatch.core.llm_extract import (
            apply_llm_extractions,
            llm_extract_features,
        )
        budget = None
        if domain_cfg.budget:
            from goldenmatch.core.llm_budget import BudgetTracker
            budget = BudgetTracker(domain_cfg.budget)
        text_col = (
            domain_profile.text_columns[0]
            if domain_profile.text_columns
            else user_cols[0]
        )
        extractions = llm_extract_features(
            combined_df_tmp, low_conf_ids, text_col,
            domain=domain_profile.name, budget_tracker=budget,
        )
        combined_df_tmp = apply_llm_extractions(
            combined_df_tmp, extractions, domain_profile.name,
        )
        logger.info(
            "LLM validated %d/%d low-confidence extractions",
            len(extractions), len(low_conf_ids),
        )

    return combined_df_tmp.lazy()


def _apply_memory_pre(memory_store: Any, config: GoldenMatchConfig, matchkeys: list) -> None:
    """Overlay learned threshold adjustments onto the matchkeys parameter.

    Mutates `matchkeys` in place — rebinding to a fresh list would shadow the
    parameter and the scoring loop would never see the overlay.
    """
    if memory_store is None or config.memory is None:
        return
    try:
        from goldenmatch.core.memory.learner import MemoryLearner
        learner = MemoryLearner(
            memory_store,
            threshold_min=config.memory.learning.threshold_min_corrections,
            weights_min=config.memory.learning.weights_min_corrections,
            dataset=config.memory.dataset,
        )
        if not learner.has_new_corrections():
            return
        adjustments = learner.learn()
        for adj in adjustments:
            if adj.threshold is None:
                continue
            for mk in matchkeys:
                if mk.threshold is None:
                    continue
                if (not adj.matchkey_name
                        or adj.matchkey_name == mk.name
                        or adj.matchkey_name == "_default"):
                    mk.threshold = adj.threshold
    except Exception as e:
        logger.warning("Memory learner overlay failed: %s", e)


def _apply_memory_post(
    memory_store: Any,
    config: GoldenMatchConfig,
    df: pl.DataFrame,
    all_pairs: list[tuple[int, int, float]],
):
    """Apply stored corrections to scored pairs. Returns (pairs, stats|None)."""
    if memory_store is None or config.memory is None:
        return all_pairs, None
    try:
        from goldenmatch.core.memory.corrections import apply_corrections
        # f.field is Optional[str] at schema level but is non-None for every
        # field that survives MatchkeyField validation; filter defensively.
        matchkey_field_names = sorted({
            f.field for mk in config.get_matchkeys() for f in mk.fields
            if f.field is not None
        })
        return apply_corrections(
            all_pairs, memory_store, df, matchkey_field_names,
            dataset=config.memory.dataset,
            reanchor=config.memory.reanchor,
        )
    except Exception as e:
        logger.warning("Memory apply_corrections failed: %s", e)
        from goldenmatch.core.memory.corrections import CorrectionStats
        return all_pairs, CorrectionStats(
            total_pairs=len(all_pairs), failed=True, error=str(e),
        )


def _derive_review_queue_path(config: GoldenMatchConfig) -> str | None:
    """Derive review-queue SQLite path as sibling of the memory store path.

    Returns None if memory is disabled or no path is configured.
    """
    if not config.memory or not config.memory.enabled:
        return None
    mem_path = getattr(config.memory, "path", None)
    if not mem_path:
        return None
    p = Path(mem_path)
    return str(p.with_name("review_queue.db"))


def _enqueue_stale_pairs(
    memory_stats: Any,
    all_pairs: list[tuple[int, int, float]],
    config: GoldenMatchConfig,
) -> None:
    """Push stale pairs onto a SQLite-backed ReviewQueue for steward triage.

    The queue is colocated with the memory store (sibling SQLite file) so the
    next `goldenmatch review` invocation surfaces these pairs across processes.
    """
    if memory_stats is None or not memory_stats.stale_pairs:
        return
    rq = None
    try:
        from goldenmatch.core.review_queue import ReviewQueue
        queue_path = _derive_review_queue_path(config)
        if queue_path is not None:
            rq = ReviewQueue(backend="sqlite", path=queue_path)
        else:
            rq = ReviewQueue()
        score_lookup = {(a, b): s for a, b, s in all_pairs}
        for (a, b) in memory_stats.stale_pairs:
            score = score_lookup.get((a, b), score_lookup.get((b, a), 0.0))
            rq.add(
                job_name="memory_stale", id_a=a, id_b=b, score=score,
                explanation="correction stale: re-decide",
            )
    except Exception as e:
        logger.warning("Failed to enqueue stale pairs: %s", e)
    finally:
        if rq is not None:
            try:
                rq.close()
            except Exception:
                pass


def _apply_postflight(
    df: pl.DataFrame,
    config: GoldenMatchConfig,
    pair_scores: list[tuple[int, int, float]],
) -> tuple[list[tuple[int, int, float]], object | None]:
    """Run postflight if auto-config was used; apply threshold adjustments
    unless strict.

    Returns ``(possibly-filtered pair_scores, postflight_report or None)``.
    Emits a logger warning + advisory if a threshold adjustment empties the
    pair list (so callers can see why no clusters formed).

    No-op (returns the input pair_scores and None) when the config carries
    no ``_preflight_report`` — i.e. the caller did not go through
    ``auto_configure_df``.
    """
    from goldenmatch.core.autoconfig_verify import (
        PreflightReport as _PfR,
    )
    from goldenmatch.core.autoconfig_verify import (
        postflight as _postflight,
    )

    _preflight = getattr(config, "_preflight_report", None)
    if not isinstance(_preflight, _PfR):
        return pair_scores, None

    report = _postflight(df, config, pair_scores=pair_scores)
    if not getattr(config, "_strict_autoconfig", False):
        for adj in report.adjustments:
            if adj.field == "threshold":
                prev_count = len(pair_scores)
                pair_scores = [p for p in pair_scores if p[2] >= adj.to_value]
                if prev_count > 0 and len(pair_scores) == 0:
                    msg = (
                        f"postflight threshold adjustment to {adj.to_value:.3f} "
                        f"dropped all {prev_count} scored pairs — no clusters "
                        f"will form. Consider strict=True or review the score "
                        f"distribution in postflight_report.signals['score_histogram']."
                    )
                    logger.warning(msg)
                    report.advisories.append(msg)
    return pair_scores, report


def _run_auto_suggest(df: pl.DataFrame, config: GoldenMatchConfig) -> None:
    """Run block analyzer auto-suggest if enabled in config.

    Logs the top 3 suggestions. If config.blocking.keys is empty,
    populates it from the top suggestion.
    """
    if not config.blocking or not config.blocking.auto_suggest:
        return

    matchkey_columns = _extract_matchkey_columns(config)
    if not matchkey_columns:
        return

    suggestions = analyze_blocking(df, matchkey_columns)
    if not suggestions:
        logger.info("Auto-suggest: no blocking suggestions found")
        return

    # Log top 3 suggestions
    for i, s in enumerate(suggestions[:3]):
        logger.info(
            "Auto-suggest #%d: %s (blocks=%d, max_size=%d, comparisons=%d, recall=%.2f, score=%.4f)",
            i + 1,
            s.description,
            s.group_count,
            s.max_group_size,
            s.total_comparisons,
            s.estimated_recall,
            s.score,
        )

    # If no user-configured keys, use the top suggestion
    if not config.blocking.keys:
        top = suggestions[0]
        from goldenmatch.config.schemas import BlockingKeyConfig

        new_keys = []
        for cand in top.keys:
            new_keys.append(BlockingKeyConfig(
                fields=cand["key_fields"],
                transforms=cand.get("transforms", []) if isinstance(cand.get("transforms", []), list) and all(isinstance(t, str) for t in cand.get("transforms", [])) else [],
            ))
        config.blocking.keys = new_keys
        logger.info("Auto-suggest: using top suggestion '%s' as blocking keys", top.description)


def _add_row_ids(lf: pl.LazyFrame, offset: int = 0) -> pl.LazyFrame:
    """Add __row_id__ column using with_row_index + offset.

    If the frame already carries ``__row_id__`` (a global id the caller wants
    respected — e.g. a distributed hash-shuffled input, where re-synthesizing
    ids per partition would collide across partitions; see #844), reuse it
    instead of calling ``with_row_index`` again. Re-adding a column that already
    exists raises polars ``DuplicateError`` — which is exactly what tripped the
    auto-config v0 sample pipeline on a ``__row_id__``-carrying 100M input. The
    offset still applies so reference/incremental callers keep their
    target/reference id spaces disjoint.
    """
    if "__row_id__" not in lf.collect_schema().names():
        lf = lf.with_row_index("__row_id__")
    if offset > 0:
        lf = lf.with_columns((pl.col("__row_id__") + offset).alias("__row_id__"))
    # Cast to Int64 for consistency
    lf = lf.with_columns(pl.col("__row_id__").cast(pl.Int64))
    return lf


def _get_required_columns(config: GoldenMatchConfig) -> list[str]:
    """Extract all *user* column names referenced in matchkeys and blocking config.

    Skips pipeline-generated synthetic columns (those wrapped in double-underscores,
    e.g. ``__title_key__``, ``__brand__``) because those are created by the domain-
    extraction step that runs *inside* the pipeline — before the scoring phase that
    actually needs them. Validating them upfront (on the raw DataFrame) would always
    fail when a config built by ``auto_configure_df`` references domain columns.
    """
    cols = set()
    for mk in config.get_matchkeys():
        for f in mk.fields:
            if f.columns:
                cols.update(
                    c for c in f.columns
                    if not (c.startswith("__") and c.endswith("__"))
                )
            elif f.field and f.field != "__record__":
                if not (f.field.startswith("__") and f.field.endswith("__")):
                    cols.add(f.field)
    if config.blocking:
        for key_config in config.blocking.keys:
            for field_name in key_config.fields:
                if not (field_name.startswith("__") and field_name.endswith("__")):
                    cols.add(field_name)
    return sorted(cols)


def run_dedupe(
    files: list[tuple],
    config: GoldenMatchConfig,
    output_golden: bool = False,
    output_clusters: bool = False,
    output_dupes: bool = False,
    output_unique: bool = False,
    output_report: bool = False,
    across_files_only: bool = False,
    llm_retrain: bool = False,
    llm_provider: str | None = None,
    llm_max_labels: int = 500,
) -> dict:
    """Run the dedupe pipeline.

    Args:
        files: List of (file_path, source_name) tuples.
        config: GoldenMatch configuration.
        output_golden: Whether to output golden records.
        output_clusters: Whether to output cluster info.
        output_dupes: Whether to output duplicate records.
        output_unique: Whether to output unique records.
        output_report: Whether to generate a report.
        across_files_only: If True, only match across different sources.

    Returns:
        Dict with keys: clusters, golden, unique, dupes, report.
    """
    matchkeys = config.get_matchkeys()

    # ── Step 1: INGEST ──
    frames = []
    offset = 0
    for file_spec in files:
        if len(file_spec) == 3:
            file_path, source_name, column_map = file_spec
        else:
            file_path, source_name = file_spec[0], file_spec[1]
            column_map = None
        # W5b-1: the shim boundary moved BELOW row-ids. The arrow lane's
        # ingest front (column_map / validate / __source__ / row-ids) runs
        # EAGERLY on the seam; conversion to polars-lazy happens once, after
        # the concat. Removal point: W5b-2+, as standardize/matchkeys/domain
        # go eager and the boundary keeps moving down.
        lf = load_file(file_path, return_frame=True)
        from goldenmatch.core.frame import ArrowFrame as _ArrowFrame

        if isinstance(lf, _ArrowFrame):
            frame = lf
            if column_map:
                # apply_column_map parity: same missing-source check + message.
                _missing = [src for src in column_map if src not in frame.columns]
                if _missing:
                    raise ValueError(
                        f"Column map references columns not in file: {_missing}. "
                        f"Available: {sorted(set(frame.columns))}"
                    )
                frame = frame.rename(column_map)
            required = _get_required_columns(config)
            # validate_columns parity: same message shape.
            _missing = [c for c in required if c not in frame.columns]
            if _missing:
                raise ValueError(
                    f"Missing required columns: {_missing}. "
                    f"Available columns: {list(frame.columns)}"
                )
            frame = frame.with_literal_column("__source__", source_name)
            frame = frame.ensure_row_ids(offset=offset)
            offset += frame.height
            frames.append(frame)
            continue
        if column_map:
            lf = apply_column_map(lf, column_map)
        required = _get_required_columns(config)
        validate_columns(lf, required)
        lf = lf.with_columns(pl.lit(source_name).alias("__source__"))
        lf = _add_row_ids(lf, offset=offset)
        collected = lf.collect()
        offset += len(collected)
        frames.append(collected.lazy())

    from goldenmatch.core.frame import ArrowFrame as _AF
    from goldenmatch.core.frame import concat_frames as _concat_frames

    _eager_done: frozenset[str] = frozenset()
    if frames and all(isinstance(f, _AF) for f in frames):
        _combined = _concat_frames(frames)
        # W5b-2: run standardize + exact matchkeys EAGERLY on the arrow frame
        # when nothing polars-bound sits between them (domain extraction and
        # semantic blocking both inject columns between the two stages --
        # decline the eager path when either is configured; the polars stages
        # then run as before). _run_dedupe_pipeline skips whatever is listed
        # in _eager_stages_done -- re-running standardize is NOT idempotent.
        # The prep block sits BETWEEN ingest and the standardize stage and
        # can MUTATE data (goldencheck quality + goldenflow transform are
        # DEFAULT-ON: they run unless mode == "disabled"; transform E.164s
        # phones, which is how the ordering bug surfaced). The eager path is
        # only sound when the pipeline's own prep gates all evaluate False --
        # mirrored VERBATIM from _run_dedupe_pipeline's stage conditions.
        _prep_will_run = (
            (config.quality is None or config.quality.mode != "disabled")
            or (config.transform is None or config.transform.mode != "disabled")
            or bool(config.validation and config.validation.auto_fix)
        )
        _eager_ok = (
            not _prep_will_run
            and config.semantic_blocking is None
            and not (config.domain and config.domain.enabled)
        )
        if _eager_ok:
            done: set[str] = set()
            if config.standardization and config.standardization.rules:
                for _col, _std_names in config.standardization.rules.items():
                    if _col not in _combined.columns:
                        continue
                    _combined = _combined.with_column(
                        _col, _combined.derive_standardized_column(_col, _std_names)
                    )
                done.add("standardize")
            _exact_mks = [mk for mk in matchkeys if mk.type == "exact"]
            if _exact_mks:
                for mk in _exact_mks:
                    _combined = _combined.with_column(
                        f"__mk_{mk.name}__",
                        _combined.derive_matchkey(
                            [(f.field, list(f.transforms or [])) for f in mk.fields]
                        ),
                    )
                done.add("compute_matchkeys")
            _eager_done = frozenset(done)
        # W5b-1 shim (single, post-eager-stages): everything below is still
        # polars-lazy; removal point is W5b-3 (prep-block integrations).
        combined_df = cast("pl.DataFrame", pl.from_arrow(_combined.native))
    else:
        combined_df = pl.concat([f.collect() for f in frames])
    combined_lf = combined_df.lazy()

    return _run_dedupe_pipeline(
        combined_lf, config, matchkeys,
        output_golden, output_clusters,
        output_dupes, output_unique, output_report,
        across_files_only, llm_retrain, llm_provider, llm_max_labels,
        # Seed the prep cache with (id, height) like the dedupe_df path. The
        # bare ``id(combined_lf)`` default is unsafe here: ``combined_lf`` is a
        # fresh object that's GC-eligible the moment this call returns, so
        # CPython readily recycles its ``id()`` slot for the NEXT run_dedupe
        # call. Two calls with the same schema + prep signature but different
        # row counts (e.g. a 1-file 5-row dedupe followed by a 2-file 8-row
        # across_files_only dedupe) would otherwise collide on the recycled id
        # and serve the stale prepared frame -- silently dropping the second
        # input's extra rows. Height disambiguates same-schema/different-rows.
        _prep_cache_seed=(id(combined_lf), combined_df.height),
        _eager_stages_done=_eager_done,
    )


def _prep_cache_signature(config: GoldenMatchConfig) -> str:
    """Stable string signature of the prep-step config slots.

    Covers GoldenCheck quality scan, GoldenFlow transform, and auto-fix.
    Used as part of the cache key in ``_run_dedupe_pipeline`` (Attack C
    of the map_elements perf spec). Other config slots (matchkeys,
    blocking, threshold) are NOT part of the signature — they don't
    influence prep output.
    """
    import json
    q = config.quality.model_dump() if config.quality is not None else None
    t = config.transform.model_dump() if config.transform is not None else None
    af = bool(config.validation and config.validation.auto_fix)
    return json.dumps(
        {"quality": q, "transform": t, "auto_fix": af},
        sort_keys=True,
        default=str,
    )


# Process-level LRU cache for the prep steps (quality + transform + auto-fix).
# Holds at most ``_PREP_CACHE_MAX`` entries; eviction is FIFO on insertion
# order. Single-threaded design; the controller's iteration loop drives the
# only realistic cache-hit scenario (5 dedupe_df calls on the same sample).
_PREP_CACHE: dict[tuple, pl.DataFrame] = {}
_PREP_CACHE_LRU: list[tuple] = []
_PREP_CACHE_MAX = 4


def _prep_cache_clear() -> None:  # pyright: ignore[reportUnusedFunction]
    """Reset the cache. Called by tests to isolate state.

    Pyright flags this as unused because its only callers live in
    ``tests/test_prep_cache.py`` which the pyright config excludes.
    """
    _PREP_CACHE.clear()
    _PREP_CACHE_LRU.clear()


# ── Semantic blocking (recall-lever) candidate-source union ──────────────────
# When ``config.semantic_blocking`` is set, three additional candidate sources
# are unioned onto the normal candidate set:
#   - acronym  -> a multi_pass block on the ``initialism`` transform
#   - alias    -> multi_pass blocks on the refdata canonicalization transforms
#                 (refdata_given_name_canonical / refdata_business_canonical)
#   - ann      -> embedding nearest-neighbor pairs via the ``ann_pairs`` strategy
# The acronym/alias blocks are scored with the SAME block scorer the main fuzzy
# loop uses; the ANN strategy returns pre-scored (a, b, cosine) pairs directly.
# All sources are appended to ``all_pairs`` BEFORE the ``dedup_pairs_max_score``
# seam, which canonicalizes (min, max) and keeps the max score -> purely
# additive (never drops an existing pair). Gated entirely behind
# ``config.semantic_blocking``; None (the default) does nothing.
_SEMANTIC_ALIAS_TRANSFORMS: dict[str, str] = {
    "given_names": "refdata_given_name_canonical",
    "business": "refdata_business_canonical",
}

# Internal column prefix for the pre-standardize snapshot of the name column the
# semantic-blocking sources key + score off of (see the capture in
# ``_run_dedupe_pipeline`` just before standardize). ``__``-prefixed so it never
# leaks into user-facing golden output.
_SEMANTIC_RAW_COL_PREFIX = "__raw__"


def _semantic_name_column(
    config: GoldenMatchConfig, matchkeys: list, columns: list[str],
) -> str | None:
    """Pick the ``name``-like column the semantic-blocking sources operate on.

    Preference order:
      1. A weighted/probabilistic matchkey field whose column name looks
         name-like (``name``/``company``/``business``/``org``/``title``).
      2. Any weighted/probabilistic matchkey field (first one).
      3. A blocking field that looks name-like, then the first blocking field.
      4. The first user (non-``__``) column.

    Returns None only when there is no user column at all.
    """
    name_like = ("name", "company", "business", "org", "title")

    def _looks_name_like(col: str) -> bool:
        low = col.lower()
        return any(tok in low for tok in name_like)

    # 1 + 2: matchkey fields (these are the columns the user is matching on).
    mk_fields: list[str] = []
    for mk in matchkeys:
        if getattr(mk, "type", None) in ("weighted", "probabilistic"):
            for f in getattr(mk, "fields", None) or []:
                col = getattr(f, "field", None)
                if col and not col.startswith("__") and col in columns:
                    mk_fields.append(col)
    for col in mk_fields:
        if _looks_name_like(col):
            return col
    if mk_fields:
        return mk_fields[0]

    # 3: blocking fields.
    block_fields: list[str] = []
    if config.blocking is not None:
        from goldenmatch.core.blocker import collect_blocking_fields
        block_fields = [
            c for c in collect_blocking_fields(config.blocking)
            if not c.startswith("__") and c in columns
        ]
    for col in block_fields:
        if _looks_name_like(col):
            return col
    if block_fields:
        return block_fields[0]

    # 4: first user column.
    user_cols = [c for c in columns if not c.startswith("__")]
    return user_cols[0] if user_cols else None


def _semantic_blocking_pairs(
    config: GoldenMatchConfig,
    combined_lf: pl.LazyFrame,
    collected_df: pl.DataFrame,
    matchkeys: list,
    matched_pairs: set[tuple[int, int]],
    across_files_only: bool,
    source_lookup: dict[int, str] | None,
) -> list[tuple[int, int, float]]:
    """Build + score the three semantic-blocking candidate sources.

    Returns the union of ``(a, b, score)`` pairs to extend ``all_pairs`` with.

    Each source is scored by its OWN CONFIRMING scorer, so a confirmed pair
    comes out at score 1.0 and merges at clustering regardless of how low its
    string similarity is:

    - acronym/initialism candidates -> ``initialism_match`` (1.0 when one name
      is the other's initialism, e.g. ``IBM`` <-> ``International Business
      Machines``; raw string similarity is ~0).
    - alias candidates -> ``alias_match`` (1.0 when both canonicalize to the
      same business/given-name alias, e.g. ``Acme Inc`` <-> ``Acme
      Incorporated``).
    - ANN candidates -> cosine, kept as the pre-scored value the index emits
      (gated by ``sb.ann_threshold`` so sub-threshold neighbors don't union in
      a low-score pair that over-merges at clustering).

    Reuses the pipeline's existing block scorer (``_get_block_scorer``). For
    each acronym/alias source we synthesize a single-field weighted matchkey on
    the name column whose field scorer IS the confirming scorer, at a 0.5
    threshold so a 1.0 confirmed pair is emitted (and a 0.0 non-confirmation is
    dropped). Best-effort per source: a failing source logs + is skipped
    (semantic blocking is purely additive, so a missing source only loses
    recall, never correctness).
    """
    sb = config.semantic_blocking
    if sb is None:
        return []

    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    from goldenmatch.core.blocker import build_blocks

    name_col = _semantic_name_column(config, matchkeys, list(collected_df.columns))
    if name_col is None:
        logger.warning("semantic_blocking: no usable name column found; skipping")
        return []

    # Register the semantic-blocking transforms at the POINT OF USE. `initialism`
    # (core.acronym) and the alias `refdata_*` transforms are registered only as an
    # import side-effect of their modules; the normal dedupe_df flow imports neither,
    # so without this a fresh process / xdist worker hits "Unknown transform" and the
    # source either silently fail-opens (no merge) or errors on lazy collect. Register
    # here so semantic_blocking is order-independent. acronym's register is idempotent
    # and core (always available); the refdata import (alias only) is guarded.
    from goldenmatch.core.acronym import register_transforms as _register_initialism

    _register_initialism()
    if "alias" in sb.keys:
        try:
            # Side-effect import (registers the refdata_* alias transforms). Use
            # importlib so neither ruff (F401) nor pyright (reportUnusedImport) flags
            # an "unused" import -- the value is intentionally discarded.
            import importlib

            importlib.import_module("goldenmatch.refdata")
        except Exception:
            logger.debug(
                "semantic_blocking: refdata pack unavailable; alias source will be skipped",
            )

    # The initialism/alias sources must key + score off the RAW (pre-standardize)
    # name so an all-caps acronym ("IBM") survives the `name_proper` title-casing
    # (which would otherwise leave "Ibm", killing `derive_initialism`'s
    # acronym-as-own-key step). `_run_dedupe_pipeline` snapshots it into
    # `__raw__<name_col>` before standardize; fall back to `name_col` if absent
    # (e.g. no standardization configured, or an upstream path that didn't
    # capture it) -- the fall back is the prior behavior, never worse.
    key_name_col = f"{_SEMANTIC_RAW_COL_PREFIX}{name_col}"
    if key_name_col not in collected_df.columns:
        key_name_col = name_col

    out: list[tuple[int, int, float]] = []
    block_scorer = _get_block_scorer(config)

    def _confirming_mk(name: str, scorer: str, field_col: str) -> MatchkeyConfig:
        # Single-field weighted matchkey on the given name column whose ONLY
        # field uses the confirming scorer. threshold=0.5 emits a confirmed
        # (1.0) pair and drops a non-confirmation (0.0); <=1.0 is required so
        # the confirmed pair is emitted at all.
        return MatchkeyConfig(
            name=name,
            type="weighted",
            fields=[MatchkeyField(field=field_col, scorer=scorer, weight=1.0)],
            threshold=0.5,
        )

    def _score_source(passes: list[BlockingKeyConfig], mk: MatchkeyConfig, label: str) -> None:
        if not passes:
            return
        try:
            cfg = BlockingConfig(strategy="multi_pass", passes=passes)
            blocks = build_blocks(combined_lf, cfg)
            out.extend(
                block_scorer(
                    blocks, mk, matched_pairs,
                    across_files_only=across_files_only,
                    source_lookup=source_lookup if across_files_only else None,
                )
            )
        except Exception:
            logger.warning(
                "semantic_blocking %s source failed; skipping", label, exc_info=True,
            )

    # ── acronym/initialism: scored by initialism_match (off the RAW name) ──
    if "initialism" in sb.keys:
        _score_source(
            [BlockingKeyConfig(fields=[key_name_col], transforms=["initialism"])],
            _confirming_mk("__sem_initialism", "initialism_match", key_name_col),
            "initialism",
        )

    # ── alias: scored by alias_match (off the RAW name; one pass per table) ──
    if "alias" in sb.keys:
        alias_passes = [
            BlockingKeyConfig(fields=[key_name_col], transforms=[transform])
            for table in sb.alias_tables
            if (transform := _SEMANTIC_ALIAS_TRANSFORMS.get(table)) is not None
        ]
        _score_source(
            alias_passes,
            _confirming_mk("__sem_alias", "alias_match", key_name_col),
            "alias",
        )

    # ── ann: direct-pair ANN blocking returns pre-scored (a, b, cosine) pairs ──
    # Kept as cosine; gated by sb.ann_threshold so a low-similarity neighbor
    # doesn't union in a sub-threshold pair that over-merges at clustering.
    if "ann" in sb.keys:
        try:
            ann_config = BlockingConfig(
                strategy="ann_pairs",
                ann_column=name_col,
                ann_model=sb.ann_model,
                ann_top_k=sb.ann_top_k,
            )
            ann_blocks = build_blocks(combined_lf, ann_config)
            for blk in ann_blocks:
                for a, b, s in blk.pre_scored_pairs or []:
                    if s < sb.ann_threshold:
                        continue
                    if (
                        across_files_only
                        and source_lookup is not None
                        and source_lookup.get(a) == source_lookup.get(b)
                    ):
                        continue
                    out.append((a, b, s))
        except Exception:
            logger.warning(
                "semantic_blocking ann source failed; skipping", exc_info=True,
            )

    logger.info(
        "semantic_blocking: unioned %d candidate pairs on column %r",
        len(out), name_col,
    )
    return out


def _try_fused_golden(
    multi_df: pl.DataFrame,
    golden_rules: GoldenRulesConfig,
    quality_scores: dict[tuple[int, str], float] | None,
    cluster_pair_scores: dict[int, dict[tuple[int, int], float]] | None,
    provenance: bool,
    wants_full_provenance: bool,
) -> pl.DataFrame | None:
    """Try the fused Arrow-native golden kernel on a cluster ``multi_df``.

    ``multi_df`` carries ``__row_id__`` + ``__cluster_id__`` + user columns; the
    kernel drops singletons/oversized itself. Returns the fused golden
    ``pl.DataFrame`` (one row per multi-member cluster, native dtypes) or
    ``None`` to decline, in which case the caller falls back to the classic
    builder unchanged. Declines when:

    - ``GOLDENMATCH_GOLDEN_FUSED`` is ``0``/``false``/``off`` (kill-switch),
    - ``wants_full_provenance`` (= ``config.output.lineage_provenance``): the
      fused path cannot reproduce the slow path's ``__survivorship_prov__``
      object (spec 2026-07-09 3), so decline rather than silently drop it,
    - ``run_golden_fused_arrow`` itself declines (uncovered config, fast-path-
      eligible config, or native kernel absent) or raises.
    """
    import os

    if os.environ.get("GOLDENMATCH_GOLDEN_FUSED", "").lower() in {"0", "false", "off"}:
        return None
    if wants_full_provenance:
        return None
    try:
        from goldenmatch.core.golden_fused import run_golden_fused_arrow

        result = run_golden_fused_arrow(
            multi_df,
            golden_rules,
            quality_scores=quality_scores,
            cluster_pair_scores=cluster_pair_scores,
            provenance=provenance,
        )
    except Exception:
        logger.debug("fused golden declined", exc_info=True)
        return None
    # provenance is False whenever we reach here (wants_full_provenance gates
    # it), so run_golden_fused_arrow returns a DataFrame or None -- never the
    # (df, records) provenance tuple. Guard defensively.
    if isinstance(result, tuple):
        result = result[0]
    if result is None:
        return None
    # Byte-identity: the classic SLOW golden path this replaces assembles its
    # frame from list[dict] records with an all-Utf8 schema_overrides, so its
    # user columns come back String. The fused kernel PRESERVES the source
    # dtype. On dedupe_df/run_dedupe_df the entry point already pre-casts every
    # user column to Utf8 (so this is a no-op there), but the file-based
    # run_dedupe/dedupe path infers native dtypes via scan_csv/scan_parquet, so
    # a golden `age` column would come back Int64 (fused) vs String (classic).
    # Cast the non-internal user columns to Utf8 to match the slow path exactly
    # (__cluster_id__ Int64 / __golden_confidence__ Float64 already agree). We
    # deliberately do NOT keep native dtypes -- that would change the slow
    # path's own behavior, out of scope for a transparent routing flag.
    return result.with_columns(
        [pl.col(c).cast(pl.Utf8) for c in result.columns if not c.startswith("__")]
    )


def _golden_records_to_df(golden_records: list[dict]) -> pl.DataFrame | None:
    """Assemble a golden ``pl.DataFrame`` from the ``list[dict]`` records the slow
    survivorship builder returns (``build_golden_records_batch``).

    Mirrors the shared slow-path rebuild in ``_run_dedupe_pipeline`` exactly: each
    record's ``{value}`` field-dicts collapse to a scalar, an all-``Utf8``
    ``schema_overrides`` prevents mixed-type inference errors, and
    ``__cluster_id__`` / ``__golden_confidence__`` keep their native dtypes.
    Returns ``None`` for an empty record list.
    """
    if not golden_records:
        return None
    golden_rows: list[dict] = []
    for rec in golden_records:
        row: dict = {"__cluster_id__": rec["__cluster_id__"]}
        row["__golden_confidence__"] = rec.get("__golden_confidence__", 0.0)
        for col, val_info in rec.items():
            if col in ("__cluster_id__", "__golden_confidence__"):
                continue
            if isinstance(val_info, dict) and "value" in val_info:
                row[col] = val_info["value"]
        golden_rows.append(row)
    all_keys: set[str] = set()
    for row in golden_rows:
        all_keys.update(row.keys())
    schema_overrides = {
        k: pl.Utf8 for k in all_keys
        if k not in ("__cluster_id__", "__golden_confidence__")
    }
    return pl.DataFrame(golden_rows, schema_overrides=schema_overrides)


def _golden_from_multi_df(
    multi_df: pl.DataFrame,
    golden_rules: GoldenRulesConfig,
    quality_scores: dict[tuple[int, str], float] | None,
    provenance_on: bool,
) -> tuple[pl.DataFrame | None, bool]:
    """Build golden for a pre-joined ``(__row_id__ + __cluster_id__)`` ``multi_df``,
    mirroring the classic dict-path golden demux so the output is byte-identical.

    Returns ``(golden_df, golden_fused_used)``. Decision, exactly as the pipeline's
    dict / frames golden branches:

    - FAST (``not provenance`` AND ``_polars_native_eligible``): the columnar
      ``build_golden_records_df`` -- no fused try (the fused kernel declines
      fast-path-eligible configs anyway).
    - SLOW: try ``_try_fused_golden`` (Stage E, default-on); on ``None`` fall back
      to ``build_golden_records_batch`` + the ``list[dict]`` -> frame rebuild.

    ``cluster_pair_scores`` is ``None`` (capacity mode sheds pair scores);
    ``confidence_majority`` is the only strategy that consumes them and is excluded
    upstream by ``config_needs_artifacts``, so ``None`` is byte-identical here.
    """
    fast_eligible = not provenance_on and _polars_native_eligible(
        golden_rules, quality_scores=quality_scores
    )
    if fast_eligible:
        return build_golden_records_df(multi_df, golden_rules), False

    fused_golden_df = _try_fused_golden(
        multi_df,
        golden_rules,
        quality_scores=quality_scores,
        cluster_pair_scores=None,
        provenance=provenance_on,
        wants_full_provenance=provenance_on,
    )
    if fused_golden_df is not None:
        return fused_golden_df, True

    golden_records = build_golden_records_batch(
        multi_df,
        golden_rules,
        quality_scores=quality_scores,
        provenance=provenance_on,
        cluster_pair_scores=None,
    )
    return _golden_records_to_df(golden_records), False


def _fused_needed_src_cols(config: GoldenMatchConfig) -> list[str]:
    """Source columns the fused match kernel needs as Arrow arrays: the blocking
    key field(s) (single-key or every multi-pass field) + the covered weighted
    matchkey's comparison fields. Deduped, source-order preserved (mirrors
    ``fused_match.run_match_fused_arrow``'s own ``src_cols`` derivation)."""
    fields: list[str] = []
    b = config.blocking
    if b is not None and getattr(b, "strategy", None) == "multi_pass":
        for p in getattr(b, "passes", None) or []:
            fields.extend(p.fields)
    elif b is not None:
        keys = getattr(b, "keys", None) or []
        if keys:
            fields.extend(keys[0].fields)
    mk = config.get_matchkeys()[0]
    fields.extend(f.field for f in mk.fields if f.field is not None)
    return list(dict.fromkeys(fields))


def _run_fused_match_short_circuit(
    collected_df: pl.DataFrame,
    config: GoldenMatchConfig,
    *,
    quarantine_df: pl.DataFrame | None,
    output_report: bool,
    across_files_only: bool,
) -> dict | None:
    """Fused-match whole-stage short-circuit (spec 2026-07-09 Stage F).

    Runs block + score + dedup + cluster in ONE ``match_fused`` FFI call, then
    routes the resulting connected components DIRECTLY to golden -- bypassing the
    classic ``exact -> fuzzy -> cluster -> 3-branch golden dispatch``. Returns a
    full result dict (the SAME keys ``_run_dedupe_pipeline`` returns) on success,
    or ``None`` to fall through to the classic path unchanged (kill-switch set, or
    the kernel declined the config / is absent).

    CAPACITY-SURVIVAL contract (spec §4.4): this fires only under est-RSS pressure
    (the controller's ``maybe_route_fused_match``), where the classic path would
    likely OOM. It sheds ``scored_pairs`` (``[]``) + per-cluster
    confidence/bottleneck/lineage, but the CLUSTER MEMBERSHIP + GOLDEN records are
    byte-identical to classic. ``match_fused_capacity_mode=True`` marks the shed so
    it is never silent (Stage G telemetry). The config-driven divergence gate
    (``config_needs_artifacts``) is re-checked by the caller before entry.

    DIVERGENCE GUARDS beyond ``config_needs_artifacts`` (transient / call-scoped
    signals that live on the pipeline call, NOT the persistent config, so they
    can't be folded into the single-source config gate):

    - ``across_files_only`` -- the classic exact/fuzzy stages drop within-source
      pairs; match_fused clusters all pairs, so an across-files run would
      DIVERGE. Decline.
    - ``_preflight_report`` present + NOT ``_strict_autoconfig`` -- the classic
      path runs ``_apply_postflight`` which may RAISE the effective threshold
      from a bimodal score histogram (needs the full scored-pair histogram the
      fused kernel never materializes, so it can't be replicated). Decline to
      stay byte-identical. NOTE this narrows auto-routing on the non-strict
      auto-config path; the fast-follow is for the controller (Stage D) to bake
      the postflight-adjusted threshold into the committed config before setting
      the flag, which would re-open this path.
    """
    if os.environ.get("GOLDENMATCH_MATCH_FUSED", "").lower() in {"0", "false", "off"}:
        return None
    if across_files_only:
        return None
    _preflight = getattr(config, "_preflight_report", None)
    if _preflight is not None and not getattr(config, "_strict_autoconfig", False):
        return None

    from goldenmatch.core.fused_match import (
        run_match_fused_arrow,
        run_match_fused_multipass_arrow,
    )

    n_rows = collected_df.height
    columns = {c: collected_df[c].to_arrow() for c in _fused_needed_src_cols(config)}
    fused_tbl = run_match_fused_arrow(
        columns, config, n_rows=n_rows
    ) or run_match_fused_multipass_arrow(columns, config, n_rows=n_rows)
    if fused_tbl is None:
        return None

    # (__row_id__, __cluster_id__), one row per input record. match_fused emits
    # every record incl singletons (own component). auto_split is excluded by
    # config_needs_artifacts, so oversized clusters are never SPLIT -- but the
    # classic path still FLAGS clusters over max_cluster_size as oversized and
    # excludes them from golden (while keeping them in dupes). Mirror that flag so
    # golden stays byte-identical.
    max_cluster_size = 100
    if config.golden_rules is not None:
        max_cluster_size = config.golden_rules.max_cluster_size
    fused_df = pl.from_arrow(fused_tbl)
    assert isinstance(fused_df, pl.DataFrame)  # from_arrow returns a DataFrame for a Table
    sizes = fused_df.group_by("__cluster_id__").agg(pl.len().alias("__size__"))
    # dupes: members of every multi-member cluster (oversized INCLUDED, mirroring
    # the classic size>1 dupe rule). golden: non-oversized multi-member only.
    dupe_ids = sizes.filter(pl.col("__size__") > 1)["__cluster_id__"].to_list()
    golden_ids = sizes.filter(
        (pl.col("__size__") > 1) & (pl.col("__size__") <= max_cluster_size)
    )["__cluster_id__"].to_list()
    oversized_ids = set(
        sizes.filter(pl.col("__size__") > max_cluster_size)["__cluster_id__"].to_list()
    )

    dupe_row_ids = fused_df.filter(pl.col("__cluster_id__").is_in(dupe_ids))[
        "__row_id__"
    ].to_list()
    golden_row_ids = fused_df.filter(pl.col("__cluster_id__").is_in(golden_ids))[
        "__row_id__"
    ].to_list()
    dupe_set = set(dupe_row_ids)
    all_ids = collected_df["__row_id__"].to_list()
    unique_row_ids = [r for r in all_ids if r not in dupe_set]
    dupes_df = collected_df.filter(pl.col("__row_id__").is_in(dupe_row_ids))
    unique_df = collected_df.filter(pl.col("__row_id__").is_in(unique_row_ids))

    # Clusters dict (capacity mode: pair_scores/confidence/bottleneck shed). Same
    # STRUCTURE as build_clusters so DedupeResult.clusters consumers don't break;
    # confidence sentinel mirrors cluster.py (1.0 singleton / 0.0 multi).
    grouped: dict[int, list[int]] = {}
    for rid, cid in zip(
        fused_df["__row_id__"].to_list(), fused_df["__cluster_id__"].to_list()
    ):
        grouped.setdefault(cid, []).append(rid)
    clusters: dict[int, dict] = {}
    for cid, members in grouped.items():
        size = len(members)
        clusters[cid] = {
            "members": sorted(members),
            "size": size,
            "oversized": cid in oversized_ids,
            "pair_scores": {},
            "confidence": 1.0 if size <= 1 else 0.0,
            "bottleneck_pair": None,
            "cluster_quality": "strong",
        }

    # Golden -- route the fused clusters through the SAME builder the classic path
    # picks (fast columnar vs slow batch), over the joined multi_df. Byte-identical
    # golden by construction (frames-path golden == dict-path golden == this).
    # golden_rules is non-None here: config_needs_artifacts (re-checked by the
    # caller before entry) returns True for a None golden_rules (auto_split
    # defaults True), so a None golden_rules never reaches the short-circuit.
    golden_rules = config.golden_rules
    assert golden_rules is not None  # noqa: S101 - invariant from config_needs_artifacts
    golden_df: pl.DataFrame | None = None
    golden_fused_used = False
    if golden_row_ids:
        # Quality-weighted survivorship, gated + scoped exactly as the classic
        # golden stage (over the full-column member frame, before slim; members =
        # non-oversized multi-member cluster rows).
        quality_scores: dict[tuple[int, str], float] | None = None
        if getattr(golden_rules, "quality_weighting", False):
            from goldenmatch.core.quality import compute_quality_scores

            quality_scores = (
                compute_quality_scores(
                    collected_df.filter(pl.col("__row_id__").is_in(golden_row_ids))
                )
                or None
            )
        # Build multi_df: member rows + slim internal columns + __cluster_id__
        # (mirrors the dict-path golden branch for byte-identity).
        multi_df = collected_df.filter(pl.col("__row_id__").is_in(golden_row_ids))
        if os.environ.get("GOLDENMATCH_GOLDEN_SLIM_MULTIDF", "1") != "0":
            _internal_prefixes = ("__xform_", "__mk_", "__block_key__", "__bucket__")
            multi_df = multi_df.select(
                [
                    c
                    for c in multi_df.columns
                    if not any(c.startswith(p) for p in _internal_prefixes)
                ]
            )
        multi_df = multi_df.join(fused_df, on="__row_id__", how="inner")
        golden_df, golden_fused_used = _golden_from_multi_df(
            multi_df, golden_rules, quality_scores, config.output.lineage_provenance
        )

    report = None
    if output_report:
        report = generate_dedupe_report(
            total_records=collected_df.height,
            total_clusters=len(clusters),
            cluster_sizes=[c["size"] for c in clusters.values()],
            oversized_clusters=len(oversized_ids),
            matchkeys_used=[mk.name for mk in config.get_matchkeys()],
        )

    return {
        "clusters": clusters,
        "golden": golden_df,
        "unique": unique_df,
        "dupes": dupes_df,
        "report": report,
        "quarantine": quarantine_df,
        "postflight_report": None,
        "memory_stats": None,
        "identity_summary": None,
        "scored_pairs": [],
        "llm_cost": None,
        "throughput_posture": None,
        "golden_fused_used": golden_fused_used,
        "match_fused_capacity_mode": True,
    }


def _run_dedupe_pipeline(
    combined_lf: pl.LazyFrame,
    config: GoldenMatchConfig,
    matchkeys: list,
    output_golden: bool = False,
    output_clusters: bool = False,
    output_dupes: bool = False,
    output_unique: bool = False,
    output_report: bool = False,
    across_files_only: bool = False,
    llm_retrain: bool = False,
    llm_provider: str | None = None,
    llm_max_labels: int = 500,
    auto_config: bool = False,
    auto_config_llm_provider: str | None = None,
    _eager_stages_done: frozenset[str] = frozenset(),
    _prep_cache_seed: tuple[int, int] | int | None = None,
    _prep_store: PreparedRecordStore | None = None,
) -> dict:
    """Shared dedupe pipeline logic (post-ingest).

    This function contains all pipeline steps from auto-fix/validation through
    output. Both run_dedupe() and run_dedupe_df() delegate to this function.

    ``_prep_cache_seed``: optional stable identity for the prep-cache key —
    typically ``(id(df), df.height)`` of the caller's input DataFrame. The
    height component guards against CPython recycling an ``id()`` slot and
    serving a stale prep across logically distinct inputs of the same schema.
    Defaults to ``id(combined_lf)`` when None; the seeded form is required for
    the controller's iteration loop to hit the cache because each iteration
    wraps the same caller-side ``df`` in a fresh LazyFrame.
    """
    memory_store = _open_memory_store(config)

    # ── Attack C cache lookup (map_elements spec Tier 2): quality + transform
    # + auto-fix are deterministic in (input_lf, config.quality, config.transform,
    # config.validation). The auto-config controller calls dedupe_df ~5x on
    # the same sample per `auto_configure_df` call; iteration only mutates
    # matchkeys/blocking/threshold, so these prep steps produce identical
    # output every time.
    #
    # Cache key: (seed, tuple(columns), prep_config_signature) where seed is
    # `(id(df), df.height)` from the caller (dedupe_df) or `id(combined_lf)`
    # for the file path. The columns tuple + height are cheap fingerprints
    # that defend against Python reusing `id()` slots after GC: without them,
    # a NEW frame landing at a previously-cached frame's memory address would
    # silently get the stale entry. Column names defend against a different
    # schema; height defends against same-schema-different-rows (e.g. an empty
    # input vs a populated one) — the `test_dedupe_df_empty` flake.
    prep_cache_key = (
        _prep_cache_seed if _prep_cache_seed is not None else id(combined_lf),
        tuple(combined_lf.collect_schema().names()),
        _prep_cache_signature(config),
    )
    cached_prep = _PREP_CACHE.get(prep_cache_key)
    if cached_prep is not None:
        combined_lf = cached_prep.lazy()
        logger.debug("prep cache HIT (id=%s)", prep_cache_key[0])
    else:
        # NEW (Phase 2): try the disk-backed prepared-record store before
        # re-prepping. In-memory _PREP_CACHE was already consulted above;
        # disk store covers cross-call + cross-process cases that the
        # per-process LRU can't. Same signature -> same prepared records.
        disk_signature = _prep_cache_signature(config)
        if _prep_store is not None:
            from goldenmatch.distributed.record_store import load_prepared_records
            cached_disk = load_prepared_records(_prep_store, signature=disk_signature)
            if cached_disk is not None:
                combined_lf = cached_disk.lazy()
                # Seed in-memory cache so subsequent in-process iterations
                # skip the disk read (RAM > DuckDB+Arrow latency).
                # Guard against _PREP_CACHE_MAX == 0 (tests use this to
                # disable the in-memory cache) -- the existing eviction
                # logic would IndexError on pop() from an empty LRU list
                # when ``0 >= 0`` is true.
                if _PREP_CACHE_MAX > 0:
                    if len(_PREP_CACHE_LRU) >= _PREP_CACHE_MAX:
                        evicted = _PREP_CACHE_LRU.pop(0)
                        _PREP_CACHE.pop(evicted, None)
                    _PREP_CACHE[prep_cache_key] = cached_disk
                    _PREP_CACHE_LRU.append(prep_cache_key)
                logger.debug("prep store DISK-HIT (signature=%s)", disk_signature)
            else:
                cached_disk = None  # explicit; falls through to prep steps
        else:
            cached_disk = None

        if cached_disk is None:
            # ── Step 1.3.5: FIRST INGEST MATERIALIZATION ──
            # Pull the initial `combined_lf.collect()` out of whichever prep
            # stage runs first so its cost isn't misattributed. v20 QIS
            # instrumentation (2026-05-29) showed pipeline_prep_quality_scan
            # = 117.4s of which only ~5s was actual goldencheck work; the
            # other ~112s was THIS collect materializing the 10M-row ingest
            # LazyFrame. The cost is fundamental Polars work the rest of
            # the prep block needs anyway -- it just shouldn't read as
            # "quality scan time" in bench reports. Subsequent .collect()s
            # in the prep block (transform / auto_fix / cache_populate)
            # see a lazy-wrapped eager df and unwrap cheaply.
            with stage("pipeline_initial_collect"):
                combined_df_tmp = combined_lf.collect()
                combined_lf = combined_df_tmp.lazy()

            # ── Step 1.4: GOLDENCHECK QUALITY SCAN (if available) ──
            if config.quality is None or config.quality.mode != "disabled":
                from goldenmatch.core.quality import run_quality_check
                with stage("pipeline_prep_quality_scan"):
                    combined_df_tmp = combined_lf.collect()
                    combined_df_tmp, gc_fixes = run_quality_check(combined_df_tmp, config.quality)
                    if gc_fixes:
                        logger.info("GoldenCheck: %d fixes applied", len(gc_fixes))
                    combined_lf = combined_df_tmp.lazy()

            # ── Step 1.4b: GOLDENFLOW TRANSFORM (if available) ──
            # Runs after GoldenCheck (validates) and before autofix (remaining cleanup).
            # Not in _run_match_pipeline -- add there if match pipeline gains a quality step.
            if config.transform is None or config.transform.mode != "disabled":
                from goldenmatch.core.transform import run_transform
                with stage("pipeline_prep_transform"):
                    combined_df_tmp = combined_lf.collect()
                    combined_df_tmp, gf_fixes = run_transform(combined_df_tmp, config.transform)
                    if gf_fixes:
                        logger.info("GoldenFlow: %d transforms applied", len(gf_fixes))
                    combined_lf = combined_df_tmp.lazy()

            # ── Step 1.5a: AUTO-FIX + VALIDATION ──
            if config.validation and config.validation.auto_fix:
                with stage("pipeline_prep_auto_fix"):
                    combined_df_tmp = combined_lf.collect()
                    combined_df_tmp, fix_log = auto_fix_dataframe(combined_df_tmp)
                    logger.info("Auto-fix applied: %d fix type(s)", len(fix_log))
                    combined_lf = combined_df_tmp.lazy()

            # Populate in-memory cache (LRU eviction). We materialize as an
            # eager DataFrame so subsequent hits don't re-evaluate a long lazy
            # plan. Guard _PREP_CACHE_MAX > 0 so tests that monkey-patch it to
            # 0 don't trigger IndexError on pop() from an empty LRU list.
            with stage("pipeline_prep_cache_populate"):
                prepped_df = combined_lf.collect()
            if _PREP_CACHE_MAX > 0:
                if len(_PREP_CACHE_LRU) >= _PREP_CACHE_MAX:
                    evicted = _PREP_CACHE_LRU.pop(0)
                    _PREP_CACHE.pop(evicted, None)
                _PREP_CACHE[prep_cache_key] = prepped_df
                _PREP_CACHE_LRU.append(prep_cache_key)
            combined_lf = prepped_df.lazy()

            # NEW (Phase 2): also write to disk store, if provided.
            if _prep_store is not None:
                from goldenmatch.distributed.record_store import materialize_prepared_records
                with stage("pipeline_prep_disk_store_write"):
                    materialize_prepared_records(
                        _prep_store, prepped_df, signature=disk_signature,
                    )
                logger.debug("prep store DISK-WRITE (signature=%s)", disk_signature)

    # ── Step 1.5b: AUTO-CONFIG ON CLEANED DATA (if zero-config) ──
    if auto_config:
        from goldenmatch.core.autoconfig import auto_configure_df
        combined_df_tmp = combined_lf.collect()
        with stage("auto_configure"):
            auto_cfg = auto_configure_df(
                combined_df_tmp,
                llm_provider=auto_config_llm_provider,
                llm_auto=config.llm_auto,
            )
        config.matchkeys = auto_cfg.matchkeys
        config.match_settings = auto_cfg.match_settings
        config.blocking = auto_cfg.blocking
        config.golden_rules = auto_cfg.golden_rules
        config.llm_scorer = auto_cfg.llm_scorer
        config.memory = auto_cfg.memory
        # Propagate domain config so pipeline's domain-extraction step runs
        # when auto_configure_df (via preflight Check 1) decided it should.
        if auto_cfg.domain is not None:
            config.domain = auto_cfg.domain
        _propagate_autoconfig_markers(auto_cfg, config)
        matchkeys = config.get_matchkeys()
        logger.info("Auto-configured from cleaned data: %d matchkeys", len(matchkeys))
        combined_lf = combined_df_tmp.lazy()

    if config.validation and config.validation.rules:
        with stage("pipeline_prep_validation_rules"):
            rules = [
                ValidationRule(
                    column=rc.column,
                    rule_type=rc.rule_type,
                    params=rc.params,
                    action=rc.action,
                )
                for rc in config.validation.rules
            ]
            combined_df_tmp = combined_lf.collect()
            valid_df, quarantine_df, _val_report = validate_dataframe(combined_df_tmp, rules)
            logger.info("Validation: %d quarantined rows", quarantine_df.height)
            combined_lf = valid_df.lazy()
    else:
        quarantine_df = None

    # RSS attribution sentinel: a near-no-op stage that captures ru_maxrss right
    # before standardize fires. Lets us tell whether peak growth happens INSIDE
    # apply_standardization (sentinel low, standardize high) or in the prep work
    # above (sentinel high, standardize delta 0).
    with stage("pipeline_pre_standardize_sentinel"):
        pass

    # ── Step 1.5b: STANDARDIZE ──
    # When GOLDENMATCH_PREP_STAGED_COLLECT=1, FORCE an intermediate collect
    # after each prep step so the bench can attribute combined_lf_collect's
    # 80s wall to standardize vs domain_extraction vs compute_matchkeys.
    # Default off (the forced materializations defeat Polars fusion and
    # only exist to localize the wall hotspot for the next perf PR).
    _staged_collect = os.environ.get("GOLDENMATCH_PREP_STAGED_COLLECT") == "1"

    def _force_collect_if_staged(lf: pl.LazyFrame, label: str) -> pl.LazyFrame:
        if not _staged_collect:
            return lf
        with stage(f"prep_force_collect_{label}"):
            return lf.collect().lazy()

    # ── Semantic-blocking raw-name capture (recall lever) ──
    # The semantic-blocking sources (initialism / alias) derive their block keys
    # and confirming scores from the name column. Standardize (below) title-cases
    # that column (`name_proper` -> "IBM" becomes "Ibm"), which destroys the
    # all-caps acronym signal `derive_initialism` keys off of. Snapshot the RAW
    # (pre-standardize) name value into an internal `__raw__<col>` column so
    # `_semantic_blocking_pairs` can key + score off the un-standardized text.
    # Gated entirely behind `config.semantic_blocking`; byte-identical when None.
    if config.semantic_blocking is not None:
        _sem_name_col = _semantic_name_column(
            config, matchkeys, list(combined_lf.collect_schema().names()),
        )
        if _sem_name_col is not None:
            _raw_col = f"{_SEMANTIC_RAW_COL_PREFIX}{_sem_name_col}"
            combined_lf = combined_lf.with_columns(
                pl.col(_sem_name_col).alias(_raw_col)
            )

    if (
        config.standardization
        and config.standardization.rules
        and "standardize" not in _eager_stages_done
    ):
        with stage("standardize"):
            combined_lf = apply_standardization(combined_lf, config.standardization.rules)
        combined_lf = _force_collect_if_staged(combined_lf, "standardize")

    # ── Step 1.5c: DOMAIN FEATURE EXTRACTION ──
    with stage("domain_extraction"):
        combined_lf = _apply_domain_extraction(combined_lf, config)
    combined_lf = _force_collect_if_staged(combined_lf, "domain_extraction")

    # ── Learning Memory: pre-scoring learner overlay ──
    with stage("memory_pre_overlay"):
        _apply_memory_pre(memory_store, config, matchkeys)

    # ── Step 2: TRANSFORM ──
    if "compute_matchkeys" not in _eager_stages_done:
        with stage("compute_matchkeys"):
            combined_lf = compute_matchkeys(combined_lf, matchkeys)
        combined_lf = _force_collect_if_staged(combined_lf, "compute_matchkeys")
    else:
        # Eager arrow path already derived the __mk_*__ columns; keep the
        # telemetry surface identical (compute_matchkeys emits this).
        from goldenmatch.core.matchkey import _emit_matchkey_profile

        _emit_matchkey_profile(combined_lf, matchkeys)

    # ── Step 2.5: AUTO-SUGGEST blocking keys ──
    # Hoist matchkey transforms onto the materialized df once — eliminates
    # one .select() per (block × matchkey field) during scoring (folds into
    # the existing collect; no extra materialization). See spec
    # docs/superpowers/specs/2026-05-04-hoist-matchkey-transforms.md.
    #
    # NEW (2026-05-18): two distinct stages so the heartbeat-stream can
    # tell us whether the LazyFrame collect or the precompute step is
    # the long pole at 5M scale. Prior runner runs had a ~5 min black
    # hole between auto_configure_df returning and any fuzzy stage
    # entering -- this instrumentation closes it.
    with stage("combined_lf_collect"):
        _collected_pre_mk = combined_lf.collect()
    with stage("precompute_matchkey_transforms"):
        collected_df = precompute_matchkey_transforms(_collected_pre_mk, matchkeys)
    combined_lf = collected_df.lazy()
    with stage("auto_suggest_blocking"):
        _run_auto_suggest(collected_df, config)

    # ── Fused-match whole-stage short-circuit (spec 2026-07-09 Stage F) ──
    # When the controller flagged this run for fused match (est-RSS pressure via
    # ExecutionPlan.use_fused_match -> config._use_fused_match) AND the
    # config-driven divergence gate is clear, run block+score+dedup+cluster in ONE
    # match_fused FFI call and route the components DIRECTLY to golden, bypassing
    # the classic exact->fuzzy->cluster->golden dispatch below. config_needs_
    # artifacts is the authoritative single-source re-check (the same helper the
    # controller consulted); the caller-intent conditions are already folded into
    # the flag via the _api.py hint (Task D.3), so they are not re-checked here
    # (out of pipeline scope). On decline (kill-switch / uncovered / native
    # absent) the helper returns None and the classic path runs unchanged.
    if getattr(config, "_use_fused_match", False) and not config_needs_artifacts(config):
        _fused_result = _run_fused_match_short_circuit(
            collected_df,
            config,
            quarantine_df=quarantine_df,
            output_report=output_report,
            across_files_only=across_files_only,
        )
        if _fused_result is not None:
            if memory_store is not None:
                memory_store.close()
            return _fused_result

    # ── Step 3: BLOCK + COMPARE (cascading: exact first, then fuzzy) ──
    all_pairs: list[tuple[int, int, float]] = []
    matched_pairs: set[tuple[int, int]] = set()

    # Arrow roadmap Phase A: when GOLDENMATCH_COLUMNAR_PIPELINE=1 and the config
    # is eligible (single weighted matchkey, no exact/probabilistic, no
    # auto-config postflight, default backend), the fuzzy scoring + cluster steps
    # below route through the columnar pair-stream path. Default OFF -> the list
    # path runs unchanged. See docs/columnar-pipeline-wiring.md.
    _use_columnar = _columnar_pipeline_enabled() and _is_columnar_eligible(
        config, matchkeys, across_files_only,
    )
    _columnar_pairs_df: pl.DataFrame | None = None

    # Top-line metric: every downstream pair-count ratio depends on N.
    record_metric("record_count", collected_df.height)
    record_metrics({
        "matchkey_count": len(matchkeys),
        "exact_matchkey_count": sum(1 for mk in matchkeys if mk.type == "exact"),
        "fuzzy_matchkey_count": sum(1 for mk in matchkeys if mk.type == "weighted"),
        "probabilistic_matchkey_count": sum(
            1 for mk in matchkeys if mk.type == "probabilistic"
        ),
    })

    # Build source lookup for across_files_only filtering
    source_lookup = {}
    if across_files_only:
        for row in collected_df.select("__row_id__", "__source__").to_dicts():
            source_lookup[row["__row_id__"]] = row["__source__"]

    # Phase 1: Exact matchkeys (fast)
    exact_pair_count = 0
    with stage("exact_matching"):
        for mk in matchkeys:
            if mk.type == "exact":
                # Fast path: no NE, no across_files_only. Skip the
                # find_exact_matches list[tuple[int,int,1.0]] materialization
                # (3-4 GB of CPython tuple overhead at 36.5M exact pairs in
                # the QIS 10M-bucket-realistic shape). Pull the row-id pairs
                # as zero-copy int64 numpy arrays, build matched_pairs +
                # all_pairs in one pass via vectorized minimum/maximum +
                # bulk list-of-tuples construction.
                if not mk.negative_evidence and not across_files_only:
                    import numpy as _np

                    from goldenmatch.core.scorer import _find_exact_match_ids
                    ids_a_np, ids_b_np = _find_exact_match_ids(combined_lf, mk)
                    n_pairs = int(ids_a_np.size)
                    if n_pairs > 0:
                        mins_np = _np.minimum(ids_a_np, ids_b_np)
                        maxs_np = _np.maximum(ids_a_np, ids_b_np)
                        mins_list = mins_np.tolist()
                        maxs_list = maxs_np.tolist()
                        del mins_np, maxs_np  # free 580 MB at 36.5M pairs
                        # Build matched_pairs from the canonical pairs
                        # (set.update consumes the zip without materializing
                        # an intermediate list).
                        matched_pairs.update(zip(mins_list, maxs_list))
                        # Extend all_pairs with the (a, b, 1.0) tuples in one
                        # shot. The list[tuple] construction is unavoidable
                        # while all_pairs stays a Python list, but at least
                        # only happens once (no intermediate from
                        # find_exact_matches' old per-element comprehension).
                        ids_a_list = ids_a_np.tolist()
                        ids_b_list = ids_b_np.tolist()
                        del ids_a_np, ids_b_np
                        all_pairs.extend(
                            (a, b, 1.0) for a, b in zip(ids_a_list, ids_b_list)
                        )
                    exact_pair_count += n_pairs
                else:
                    # Slow path: NE on exact matchkey (v1.12 Path Y) OR
                    # across_files_only filter. Both need per-pair iteration
                    # at the tuple level today; keep the legacy list[tuple]
                    # shape so the existing filters drop in unchanged.
                    pairs = find_exact_matches(combined_lf, mk)
                    if mk.negative_evidence:
                        # v1.12 Path Y: filter pairs by NE penalty
                        from goldenmatch.core.scorer import (
                            _apply_negative_evidence_to_exact_pairs,
                        )
                        pairs = _apply_negative_evidence_to_exact_pairs(
                            pairs, mk, collected_df
                        )
                    if across_files_only:
                        pairs = [
                            (a, b, s) for a, b, s in pairs
                            if source_lookup.get(a) != source_lookup.get(b)
                        ]
                    all_pairs.extend(pairs)
                    exact_pair_count += len(pairs)
                    for a, b, _s in pairs:
                        matched_pairs.add((min(a, b), max(a, b)))

    record_metric("exact_pair_count", exact_pair_count)
    logger.info("Exact matching found %d pairs", exact_pair_count)

    # Phase 2: Fuzzy matchkeys (parallel block scoring)
    fuzzy_pair_count = 0
    total_blocks = 0
    block_size_samples: list[int] = []
    # matched_pairs is the cross-pass exclude set. A weighted pass's additions
    # are consumed ONLY by a LATER scoring pass (another weighted matchkey, or
    # any probabilistic matchkey). When nothing later consumes them, the
    # per-pair min/max/set.add build is pure waste (~100s at 1M / 131M pairs on
    # the default list path) -- pass track_matched=False to skip it. The exact
    # loop's prior writes are this pass's EXCLUDE (read at entry), unaffected.
    _weighted_mks = [m for m in matchkeys if m.type == "weighted"]
    _last_weighted_mk = _weighted_mks[-1] if _weighted_mks else None
    _has_probabilistic_pass = any(m.type == "probabilistic" for m in matchkeys)
    with stage("fuzzy_scoring"):
        for mk in matchkeys:
            if mk.type == "weighted":
                if config.blocking is None:
                    continue
                # True iff a later scoring pass will read matched_pairs.
                _mp_consumed_after = (
                    mk is not _last_weighted_mk
                ) or _has_probabilistic_pass
                # Bucket backend: skip build_blocks entirely. The bucket
                # scorer derives block-key + bucket assignment from
                # `collected_df` in a single eager pass and partitions
                # twice (bucket, then key) -- no per-block LazyFrame
                # construction at all. Designed for 5M+ tiny-block
                # workloads where the historical build_blocks +
                # per-block .collect() chain explodes Polars arena
                # memory on Linux runners (7 consecutive 5M bench runs
                # hung at 62.99 GB RSS plateau before reaching real
                # scoring).
                if config.backend == "bucket":
                    from goldenmatch.backends.score_buckets import score_buckets
                    pairs = score_buckets(
                        collected_df,
                        config.blocking,
                        mk,
                        matched_pairs,
                        n_buckets=config.n_buckets,
                        across_files_only=across_files_only,
                        source_lookup=source_lookup if across_files_only else None,
                    )
                    all_pairs.extend(pairs)
                    fuzzy_pair_count += len(pairs)
                    continue  # skip the legacy build_blocks path below
                with stage("fuzzy_build_blocks"):
                    blocks = build_blocks(combined_lf, config.blocking)
                # Component 2 v2: materialize blocks to hash-bucketed Parquet
                # (default off). Phase 2 wiring: replaces Phase 1's
                # NotImplementedError stub. Workers load one bucket Parquet
                # and recover per-block grouping in-worker via partition_by.
                if (
                    config.prepared_record_store
                    and config.partitioned_block_scoring
                    and _prep_store is not None
                ):
                    from goldenmatch.distributed.record_store import (
                        materialize_bucketed_blocks,
                    )
                    # Component 2 v2: build the (row_id, block_key)
                    # assignment table fully vectorized in Polars. v1's
                    # dict-comprehension at 5M / 1.67M blocks was the
                    # bottleneck v2 exists to avoid; this path stays in
                    # Arrow/Rust the whole way.
                    #
                    # Multi-pass blocking semantics: a row that appears in
                    # blocks A then B then C ends up with block_key = "C".
                    # The .unique(subset=["__row_id__"], keep="last")
                    # enforces last-write-wins by deduplicating ON the
                    # row_id with the trailing block_key. After unique(),
                    # the row -> block_key map is single-valued, so the
                    # downstream inner join in materialize_bucketed_blocks
                    # has no ambiguity.
                    assignment_parts = []
                    for blk in blocks:
                        lf = blk.df if isinstance(blk.df, pl.LazyFrame) else blk.df.lazy()
                        assignment_parts.append(
                            lf.select("__row_id__").with_columns(
                                pl.lit(blk.block_key).alias("__block_key__"),
                            )
                        )
                    assignments_df = (
                        pl.concat(assignment_parts)
                        .unique(subset=["__row_id__"], keep="last")
                        .collect()
                    )

                    n_buckets = config.n_buckets or max((os.cpu_count() or 1) * 4, 64)
                    n_buckets = min(n_buckets, 1024)
                    with stage("partition_blocks_to_buckets"):
                        materialize_bucketed_blocks(
                            _prep_store,
                            combined_lf.collect(),
                            block_assignments=assignments_df,
                            n_buckets=n_buckets,
                            signature=_prep_cache_signature(config),
                        )
                total_blocks += len(blocks)
                # Block-size sampling for the histogram metric.
                # Despite the historical "cheap O(1) plan operation"
                # comment, at 1.67M filter-LazyFrames over a 5M parent
                # the `.select(pl.len()).collect().item()` per block
                # accumulates Polars arena memory at ~3 GB/min --
                # observed via heartbeat instrumentation on bench runs
                # 25998537828, 26000789629, 26002766443, 26004842882,
                # 26006853280 (all OOM-killed before scoring started
                # at 60+ GB RSS by t~20 min).
                #
                # Skip the sampling loop at scale: P50/P95/P99/max are
                # diagnostic metrics, not load-bearing for scoring.
                # Profile readers should treat the absence of these
                # keys as "skipped at scale (>10K blocks)", not as
                # zero-sized blocks. Small-N workloads still get the
                # full histogram cheaply.
                _BLOCK_SAMPLE_SKIP_THRESHOLD = 10_000
                if len(blocks) <= _BLOCK_SAMPLE_SKIP_THRESHOLD:
                    for blk in blocks:
                        try:
                            df_blk = blk.df
                            if isinstance(df_blk, pl.LazyFrame):
                                size = int(df_blk.select(pl.len()).collect().item())
                            else:
                                size = int(df_blk.height)
                            block_size_samples.append(size)
                        except Exception:  # pragma: no cover -- defensive
                            continue
                else:
                    logger.info(
                        "Skipping block-size sampling at scale: %d blocks "
                        "> %d threshold (block_size_p* metrics will be absent)",
                        len(blocks), _BLOCK_SAMPLE_SKIP_THRESHOLD,
                    )
                block_scorer = _get_block_scorer(config)

                # Component 3: when all three gating flags are on AND we have a live
                # _prep_store, hand the backend the store_path + signature so the Ray
                # key-mode dispatch path can fire. Backend ignores these kwargs in
                # df-mode and the non-Ray scorers.
                key_mode_kwargs: dict[str, str] = {}
                if (
                    config.backend == "ray"
                    and config.prepared_record_store
                    and config.partitioned_block_scoring
                    and _prep_store is not None
                ):
                    key_mode_kwargs["store_path"] = str(_prep_store.path)
                    key_mode_kwargs["signature"] = _prep_cache_signature(config)
                    # Windows (and some Linux file systems) hold an exclusive
                    # write-lock on the DuckDB file for the driver process. Ray
                    # workers in separate processes cannot open the same file
                    # read-only while that lock is held. Release the driver
                    # connection here -- all writes are already flushed to disk
                    # by the time we reach the scoring stage -- so workers can
                    # open the file concurrently.
                    _prep_store.release_connection()

                with stage("fuzzy_score_blocks"):
                    if _use_columnar:
                        # Phase A: the columnar scorer emits the pair stream as a
                        # DataFrame; build_clusters_columnar consumes it at the
                        # cluster step (no list materialization). Eligibility
                        # guarantees a single weighted matchkey, so this runs once.
                        # Any error falls back to the list scorer so the opt-in
                        # gate can't break an otherwise-eligible run.
                        try:
                            from goldenmatch.core.scorer import score_blocks_columnar
                            # Eligibility guarantees a single weighted matchkey,
                            # so matched_pairs is never consumed by a later pass
                            # -- skip building it (the profiled ~104s of per-pair
                            # min/max/set.add at 1M / 131M pairs).
                            _columnar_pairs_df = score_blocks_columnar(
                                blocks, mk, matched_pairs, track_matched=False,
                            )
                            fuzzy_pair_count += _columnar_pairs_df.height
                            continue
                        except Exception:
                            logger.warning(
                                "columnar fast-path scoring failed; falling back "
                                "to the list path",
                                exc_info=True,
                            )
                            _use_columnar = False
                            _columnar_pairs_df = None
                    # The **key_mode_kwargs unpack feeds str values
                    # (store_path, signature) to score_blocks_ray; the
                    # parallel/duckdb scorers never see them since the
                    # dict is empty unless backend=="ray". Pyright can't
                    # narrow the dynamic dispatch and flags every union
                    # arm against the str values -- intentional dynamic
                    # dispatch, suppress.
                    # Only the parallel scorer accepts track_matched; the
                    # ray/duckdb/datafusion scorers would reject the kwarg, so
                    # pass it solely when no later pass consumes matched_pairs
                    # AND we're on the parallel path (the False-skip win).
                    # dict[str, object] so the bool value sits alongside the
                    # str key_mode_kwargs (store_path/signature).
                    _scorer_kwargs: dict[str, object] = {}
                    _scorer_kwargs.update(key_mode_kwargs)
                    if block_scorer is score_blocks_parallel and not _mp_consumed_after:
                        _scorer_kwargs["track_matched"] = False
                    pairs = block_scorer(
                        blocks, mk, matched_pairs,
                        across_files_only=across_files_only,
                        source_lookup=source_lookup if across_files_only else None,
                        **_scorer_kwargs,  # pyright: ignore[reportArgumentType]
                    )
                all_pairs.extend(pairs)
                fuzzy_pair_count += len(pairs)

    record_metrics({
        "fuzzy_pair_count": fuzzy_pair_count,
        "block_count": total_blocks,
    })
    if block_size_samples:
        block_size_samples.sort()
        n = len(block_size_samples)
        record_metrics({
            "block_size_p50": block_size_samples[max(0, n // 2 - 1)],
            "block_size_p95": block_size_samples[max(0, int(n * 0.95) - 1)],
            "block_size_p99": block_size_samples[max(0, int(n * 0.99) - 1)],
            "block_size_max": block_size_samples[-1],
        })

    # Phase 2b: Probabilistic matchkeys (Fellegi-Sunter with EM)
    #
    # Opt-in bench hook: when GOLDENMATCH_BENCH_DUMP_PAIRS names a directory,
    # accumulate the within-block candidate set (the blocking ceiling) and the
    # emitted set (above-threshold pairs) across all probabilistic matchkeys,
    # then dump both as parquet AFTER the loop. Works for BOTH the polars-direct
    # per-block scorer and the backend="bucket" hash orchestration — the
    # candidate ceiling is the same blocking-defined within-block set either way.
    # The env read + two empty-set inits below are unconditional (a dict lookup +
    # two empty sets, negligible); all ACCUMULATION and I/O is guarded by
    # `if _bench_dump_dir:`, so the unset path does no per-pair/per-block work
    # and writes nothing.
    _bench_dump_dir = os.environ.get("GOLDENMATCH_BENCH_DUMP_PAIRS")
    _bench_candidate_pairs: set[tuple[int, int]] = set()
    _bench_emitted_pairs: set[tuple[int, int]] = set()
    for mk in matchkeys:
        if mk.type == "probabilistic":
            if config.blocking is None:
                continue
            from goldenmatch.core.blocker import collect_blocking_fields
            from goldenmatch.core.probabilistic import load_or_train_em, probabilistic_block_scorer
            # Build blocks first, then train EM on within-block pairs
            blocks = build_blocks(combined_lf, config.blocking)
            # Collect from keys AND passes (multi_pass puts keys in `.passes`).
            blocking_fields = collect_blocking_fields(config.blocking) if config.blocking else []
            # Reuses mk.model_path when set (Splink-style train-once), else trains.
            em_result = load_or_train_em(
                collected_df, mk,
                blocks=blocks,
                blocking_fields=blocking_fields,
            )
            logger.info(
                "F-S EM: converged=%s, iterations=%d, match_rate=%.4f",
                em_result.converged, em_result.iterations, em_result.proportion_matched,
            )
            # Bucket backend: score via the hash-bucketed parallel orchestration
            # (the same path weighted matchkeys use, which inherits the Ray /
            # DataFusion distribution wiring) instead of the sequential per-block
            # loop. Same em_result, so clusters are identical to polars-direct
            # (parity asserted in scripts/bench_fs_and_stages.py). EM still
            # samples within-block pairs above; at true scale pair train-once via
            # mk.model_path so EM is skipped on reuse.
            if config.backend == "bucket":
                from goldenmatch.backends.score_buckets import score_buckets
                pairs = score_buckets(
                    collected_df,
                    config.blocking,
                    mk,
                    matched_pairs,
                    n_buckets=config.n_buckets,
                    across_files_only=across_files_only,
                    source_lookup=source_lookup if across_files_only else None,
                    em_result=em_result,
                )
                all_pairs.extend(pairs)
                fuzzy_pair_count += len(pairs)
                for a, b, _s in pairs:
                    matched_pairs.add((min(a, b), max(a, b)))
                if _bench_dump_dir:
                    # Candidate ceiling: enumerate within-block pairs from the
                    # SAME blocks score_buckets consumes (blocking is backend-
                    # independent). score_buckets already applied across-files
                    # filtering, so `pairs` is the emitted set directly.
                    for block in blocks:
                        block_df = (
                            block.df.collect()
                            if isinstance(block.df, pl.LazyFrame)
                            else block.df
                        )
                        _accumulate_block_candidate_pairs(
                            block_df, _bench_candidate_pairs
                        )
                    for a, b, _s in pairs:
                        _bench_emitted_pairs.add((min(a, b), max(a, b)))
                continue
            # Vectorized NxN-matrix block scorer: one rapidfuzz cdist per field
            # + numpy level/weight/normalize, replacing the per-pair Python
            # loop. This makes full-block scoring cheap enough that large
            # blocks no longer have to be skipped for performance — the
            # dominant FS recall lever. Falls back to the scalar path for
            # model-backed scorers or GOLDENMATCH_FS_VECTORIZED=0.
            if _bench_dump_dir:
                # Bench path stays per-block so candidate/emitted pair accounting
                # is exact (the batched path doesn't expose per-block candidates).
                block_scorer = probabilistic_block_scorer(mk, em_result)
                for block in blocks:
                    block_df = block.df.collect() if isinstance(block.df, pl.LazyFrame) else block.df
                    _accumulate_block_candidate_pairs(
                        block_df, _bench_candidate_pairs
                    )
                    pairs = block_scorer(block_df, matched_pairs)
                    if across_files_only:
                        pairs = [
                            (a, b, s) for a, b, s in pairs
                            if source_lookup.get(a) != source_lookup.get(b)
                        ]
                    all_pairs.extend(pairs)
                    for a, b, _s in pairs:
                        matched_pairs.add((min(a, b), max(a, b)))
                    for a, b, _s in pairs:
                        _bench_emitted_pairs.add((min(a, b), max(a, b)))
            else:
                # Coalesce small blocks into batched per-field matrices to
                # amortize the per-call FFI/marshal overhead that dominates FS
                # scoring on the many tiny blocks multi-pass blocking produces.
                # Within-block cells are identical to per-block scoring, so the
                # emitted pair set is unchanged.
                from goldenmatch.core.probabilistic import (
                    score_probabilistic_blocks_batched,
                )
                pairs = score_probabilistic_blocks_batched(
                    blocks, mk, em_result, matched_pairs
                )
                if across_files_only:
                    pairs = [
                        (a, b, s) for a, b, s in pairs
                        if source_lookup.get(a) != source_lookup.get(b)
                    ]
                all_pairs.extend(pairs)
                for a, b, _s in pairs:
                    matched_pairs.add((min(a, b), max(a, b)))

    if _bench_dump_dir:
        _dump_bench_pairs(
            _bench_dump_dir, _bench_candidate_pairs, _bench_emitted_pairs
        )

    # ── Step 3.2b: SEMANTIC BLOCKING (recall-lever, opt-in) ──
    # Union three additional candidate sources (acronym/initialism, alias,
    # ANN nearest-neighbor) onto the candidate set, scored by the SAME block
    # scorer the fuzzy loop uses. Purely additive (dedup_pairs_max_score keeps
    # the max score per canonical pair). Gated entirely behind
    # config.semantic_blocking; None (the default) does nothing -> byte-identical.
    if config.semantic_blocking is not None:
        if _use_columnar:
            # The columnar fast-path consumes the pair stream as a DataFrame
            # (_columnar_pairs_df) and never touches all_pairs, so a union into
            # all_pairs would silently no-op. Refuse rather than drop candidates.
            raise NotImplementedError(
                "semantic_blocking is incompatible with COLUMNAR_PIPELINE"
            )
        with stage("semantic_blocking"):
            _semantic_pairs = _semantic_blocking_pairs(
                config, combined_lf, collected_df, matchkeys,
                matched_pairs, across_files_only,
                source_lookup if across_files_only else None,
            )
        all_pairs.extend(_semantic_pairs)

    # ── Step 3.3: CROSS-ENCODER RERANKING (optional) ──
    for mk in matchkeys:
        if mk.type == "weighted" and mk.rerank:
            all_pairs = rerank_top_pairs(all_pairs, collected_df, mk)
            break  # rerank once with the first rerank-enabled matchkey

    # ── Step 3.4: LLM SCORER (optional) ──
    # Measured pair-scorer cost surfaced on DedupeResult.llm_cost; None unless
    # the score-mode LLM branch below runs. Cluster-mode cost is left None
    # (deliberate follow-up — the measured bench lane is the pairwise scorer).
    llm_budget_summary: dict | None = None
    if config.llm_scorer and config.llm_scorer.enabled and all_pairs:
        if config.llm_scorer.mode == "cluster":
            # llm_cluster_pairs also accepts return_budget; left unwired here on
            # purpose (cluster-mode cost stays None for this task).
            from goldenmatch.core.llm_cluster import llm_cluster_pairs
            all_pairs = _unwrap_llm_pairs(
                llm_cluster_pairs(all_pairs, collected_df, config=config.llm_scorer)
            )
        else:
            from goldenmatch.core.llm_scorer import llm_score_pairs
            # return_budget=True always returns the (pairs, budget) tuple, but
            # llm_score_pairs' return type is a union (not overloaded on the bool),
            # so cast to the 2-tuple shape to narrow _scored + llm_budget_summary.
            _scored, llm_budget_summary = cast(
                "tuple[list[tuple[int, int, float]], dict | None]",
                llm_score_pairs(
                    all_pairs, collected_df, config=config.llm_scorer, return_budget=True
                ),
            )
            all_pairs = _unwrap_llm_pairs(_scored)
        # Filter to scored matches only
        all_pairs = [(a, b, s) for a, b, s in all_pairs if s > 0.5]

    # ── Step 3.5: LLM BOOST (optional) ──
    if config.llm_boost and all_pairs:
        try:
            from goldenmatch.core.boost import boost_accuracy
            matchable_cols = [
                c for c in collected_df.columns if not c.startswith("__")
            ]
            all_pairs = boost_accuracy(
                all_pairs, collected_df, matchable_cols,
                provider=llm_provider,
                api_key=None,  # auto-detect from env/settings
                model_name=None,  # auto-detect
                max_labels=llm_max_labels,
                retrain=llm_retrain,
            )
        except ImportError as e:
            logger.warning("LLM boost unavailable: %s", e)

    # ── Learning Memory: post-scoring corrections overlay ──
    all_pairs, memory_stats = _apply_memory_post(
        memory_store, config, collected_df, all_pairs
    )

    # ── Step 3.6: POSTFLIGHT (auto-config only) ──
    # Postflight verification. Signals are computed from the unadjusted pair
    # list; threshold adjustments (if any, non-strict only) are then applied
    # to all_pairs before clustering. Ordering rationale: signals reflect
    # pre-adjustment observations; downstream clustering reflects the adjusted
    # threshold.
    all_pairs, postflight_report = _apply_postflight(
        collected_df, config, all_pairs
    )

    # ── Step 3.7: THROUGHPUT TIER (#1083) ──
    # When auto-config set _throughput_plan with verify_mode="sketch_distance",
    # the normal weighted-matchkey loop above produced zero pairs (throughput
    # configs carry no weighted matchkey). Run a self-contained sketch-block +
    # verify step here and replace all_pairs with the confirmed pairs.
    # This is a STRICT NO-OP when _throughput_plan is absent or verify_mode is
    # "full" — the branch does not touch any other pipeline variable.
    _throughput_posture: dict | None = None
    _tp_plan = getattr(config, "_throughput_plan", None)
    if _tp_plan is not None and getattr(_tp_plan, "verify_mode", "full") == "sketch_distance":
        import numpy as _np_tp
        import polars as _pl_tp

        from goldenmatch.core import throughput_verify as _tv

        # all_ids is built in Step 4 below but we need it now for the remap.
        # Derive it early; Step 4 will re-derive from the same collected_df.
        _tp_all_ids = collected_df["__row_id__"].to_list()

        _tp_blocking = config.blocking
        _tp_strategy = getattr(_tp_blocking, "strategy", "lsh") if _tp_blocking else "lsh"
        _tp_posture_built = False

        # Unified posture inputs — initialised up front so the build block below is
        # statically bound regardless of which branch runs. Each branch overwrites
        # these with measured values before setting _tp_posture_built=True; they are
        # only consumed when _tp_posture_built is True.
        _tp_pos_pairs: set = set()
        _tp_scored_pos: list = []
        _tp_metric = "jaccard"
        _tp_sim = 0.8
        _tp_eff_bands = 0
        _tp_eff_rows = 0

        if _tp_strategy == "simhash":
            _tp_sc = _tp_blocking.simhash if _tp_blocking else None
            if _tp_sc is None:
                # Malformed: simhash strategy without a SimHashKeyConfig. Fall back
                # to the LSH path below rather than crash on a None config.
                _tp_strategy = "lsh"
            else:
                _tp_col = _tp_sc.column or collected_df.columns[0]
                _tp_texts = (
                    collected_df[_tp_col].cast(_pl_tp.Utf8).fill_null("").to_list()
                )
                try:
                    from goldenmatch.core.embedder import get_embedder as _get_embedder
                    # _tp_sc.model is Optional; an unbuildable embedder (e.g. model
                    # None) raises and is caught below, falling the tier back to LSH.
                    _tp_emb = _np_tp.asarray(
                        _get_embedder(_tp_sc.model).embed_column(  # pyright: ignore[reportArgumentType]
                            _tp_texts, cache_key="throughput"
                        ),
                        dtype=_np_tp.float64,
                    )
                    from goldenmatch.core.simhash_blocker import (
                        SimHashLSHBlocker as _SimHashLSHBlocker,
                    )
                    _tp_blocker = _SimHashLSHBlocker(
                        num_planes=_tp_sc.num_planes,
                        num_bands=_tp_plan.sketch_bands,
                        seed=_tp_sc.seed,
                    )
                    _tp_pos_pairs = _tp_blocker.candidate_pairs(_tp_emb)
                    _tp_sim = _tp_plan.sketch_similarity or 0.85
                    _tp_scored_pos = _tv.score_sketch_pairs(
                        _tp_pos_pairs,
                        metric="cosine",
                        threshold=_tp_sim,
                        embeddings=_tp_emb,
                    )
                    _tp_metric = "cosine"
                    # Effective bands/rows for posture; prefer plan fields (set by
                    # apply_throughput_overlay) over the blocker/config defaults.
                    _tp_eff_bands = (
                        _tp_plan.sketch_bands
                        if _tp_plan.sketch_bands is not None
                        else _tp_blocker.num_bands
                    )
                    _tp_eff_rows = (
                        _tp_plan.sketch_rows
                        if _tp_plan.sketch_rows is not None
                        else _tp_sc.num_planes // _tp_eff_bands
                    )
                    _tp_posture_built = True
                except Exception as _tp_exc:
                    logger.warning(
                        "throughput simhash branch failed (%s); falling back to lsh",
                        _tp_exc, exc_info=False,
                    )
                    _tp_strategy = "lsh"

        if _tp_strategy != "simhash" or not _tp_posture_built:
            # LSH (MinHash) path — also the fallback from a simhash failure.
            _tp_lsh_cfg = getattr(_tp_blocking, "lsh", None)
            if _tp_lsh_cfg is not None:
                _tp_col = _tp_lsh_cfg.column
                _tp_mode = _tp_lsh_cfg.mode
                _tp_k = _tp_lsh_cfg.k
                _tp_num_perms = _tp_lsh_cfg.num_perms
                _tp_lsh_seed = _tp_lsh_cfg.seed
            else:
                # Fallback: pick first string-ish column
                _tp_str_cols = [
                    c for c, dt in collected_df.schema.items()
                    if dt in (_pl_tp.Utf8, _pl_tp.String) and not c.startswith("__")
                ]
                _tp_col = _tp_str_cols[0] if _tp_str_cols else collected_df.columns[0]
                _tp_mode = "char"
                _tp_k = 3
                _tp_num_perms = 128
                _tp_lsh_seed = 0

            _tp_texts = (
                collected_df[_tp_col].cast(_pl_tp.Utf8).fill_null("").to_list()
            )
            from goldenmatch.core.lsh_blocker import MinHashLSHBlocker as _MinHashLSHBlocker
            _tp_n_bands = _tp_plan.sketch_bands or 20
            _tp_blocker = _MinHashLSHBlocker(
                mode=_tp_mode,
                k=_tp_k,
                num_perms=_tp_num_perms,
                num_bands=_tp_n_bands,
                seed=_tp_lsh_seed,
            )
            _tp_pos_pairs = _tp_blocker.candidate_pairs(_tp_texts)
            _tp_sim = _tp_plan.sketch_similarity or 0.8
            _tp_scored_pos = _tv.score_sketch_pairs(
                _tp_pos_pairs,
                metric="jaccard",
                threshold=_tp_sim,
                texts=_tp_texts,
                mode=_tp_mode,
                k=_tp_k,
                num_perms=_tp_num_perms,
                seed=_tp_lsh_seed,
            )
            _tp_metric = "jaccard"
            # Effective bands/rows for posture; prefer plan fields over defaults.
            _tp_eff_bands = (
                _tp_plan.sketch_bands
                if _tp_plan.sketch_bands is not None
                else _tp_n_bands
            )
            _tp_eff_rows = (
                _tp_plan.sketch_rows
                if _tp_plan.sketch_rows is not None
                else _tp_num_perms // _tp_eff_bands
            )
            _tp_posture_built = True

        if _tp_posture_built:
            # Remap positional indices (into texts/emb) to __row_id__ values.
            # For single-source dedupe_df (offset=0) these are identical, but the
            # remap is the correct contract for all callers.
            all_pairs = [
                (_tp_all_ids[a], _tp_all_ids[b], s)
                for (a, b, s) in _tp_scored_pos
            ]
            _tp_cfg = getattr(config, "throughput", None)
            _tp_recall_target = (
                _tp_cfg.recall_target if _tp_cfg is not None else 0.95
            )
            _tp_posture_obj = _tv.build_posture(
                metric=_tp_metric,
                recall_target=_tp_recall_target,
                similarity=_tp_sim,
                bands=_tp_eff_bands,
                rows=_tp_eff_rows,
                n_rows=collected_df.height,
                candidate_pairs=len(_tp_pos_pairs),
                verified_pairs=len(all_pairs),
                semantic_fell_back=False,
            )
            _throughput_posture = _tp_posture_obj.to_dict()
            logger.info(
                "throughput tier: %d candidate pairs -> %d verified (metric=%s, "
                "similarity=%.3f, bands=%d, expected_recall=%.3f)",
                len(_tp_pos_pairs), len(all_pairs), _tp_metric, _tp_sim,
                _tp_eff_bands, _tp_posture_obj.expected_recall,
            )

    # ── Step 4: CLUSTER ──
    all_ids = collected_df["__row_id__"].to_list()
    max_cluster_size = 100
    weak_threshold = 0.3
    auto_split = True
    split_edge_budget = None
    if config.golden_rules:
        if hasattr(config.golden_rules, "max_cluster_size"):
            max_cluster_size = config.golden_rules.max_cluster_size
        if hasattr(config.golden_rules, "weak_cluster_threshold"):
            weak_threshold = config.golden_rules.weak_cluster_threshold
        if hasattr(config.golden_rules, "auto_split"):
            auto_split = config.golden_rules.auto_split
        split_edge_budget = getattr(config.golden_rules, "split_edge_budget", None)

    record_metric(
        "scored_pair_count",
        _columnar_pairs_df.height
        if (_use_columnar and _columnar_pairs_df is not None)
        else len(all_pairs),
    )
    # SP-B/SP-C: frames-out path. Build the two-frame ClusterFrames
    # representation and consume it DIRECTLY for GOLDEN + STATS + DUPES + REPORT
    # below (no dict). The legacy `clusters` dict is rebuilt LAZILY via
    # `_clusters_dict()` -- at most once, and each remaining dict consumer calls
    # it at its OWN consumption site (adaptive refiner, output_clusters rows,
    # lineage, golden_provenance, results["clusters"]). Identity consumes the
    # frames directly (`cluster_frames=` + a ClusterPairScores view), so on the
    # hot path (identity ON, output OFF) the FIRST and ONLY `_clusters_dict()`
    # call is results["clusters"], AFTER the identity stage: cluster->golden->
    # identity runs fully dict-free (the SP-C RSS win). Mutually exclusive with
    # the columnar pair-stream branch (frames-out only fires on the non-columnar
    # list build site).
    cluster_frames: ClusterFrames | None = None
    # `clusters` is bound to the real dict on the columnar branch; on the
    # frames-out branch it stays {} until the lazy rebuild at OUTPUT time
    # (the {} is never read -- every earlier dict consumer is guarded by
    # `cluster_frames is None`). Explicit init keeps pyright from seeing it as
    # possibly-unbound.
    clusters: dict[int, dict] = {}
    with stage("cluster"):
        if _use_columnar and _columnar_pairs_df is not None:
            # Phase A columnar path: same clusters as build_clusters on the
            # equivalent list (parity: tests/test_columnar_pipeline_parity.py).
            clusters = build_clusters_columnar(
                _columnar_pairs_df, all_ids=all_ids,
                max_cluster_size=max_cluster_size,
                weak_cluster_threshold=weak_threshold,
                auto_split=auto_split,
                split_edge_budget=split_edge_budget,
            )
        else:
            cluster_frames = build_cluster_frames(
                all_pairs, all_ids,
                max_cluster_size=max_cluster_size,
                weak_cluster_threshold=weak_threshold,
                auto_split=auto_split,
                split_edge_budget=split_edge_budget,
            )
            # Do NOT rebuild the dict eagerly. stats + dupes (always-run hot path)
            # are computed directly from the frame aggregates below; the dict is
            # rebuilt LAZILY, only when a remaining dict consumer actually needs
            # it, via `_clusters_dict()`. `clusters` stays the empty-dict init on
            # this branch until the lazy rebuild at OUTPUT time.
    # SP-B lazy dict rebuild. On the frames-out branch `clusters` is unbound;
    # the remaining dict consumers (adaptive refiner, lineage, identity,
    # results["clusters"], output_clusters report/rows) go through this helper,
    # which builds the dict from the frames AT MOST ONCE and caches it. The hot
    # path (stats + dupes) never calls it -- those read the metadata/assignments
    # frames directly -- so golden + stats + dupes stay dict-free.
    _clusters_cache: list[dict[int, dict]] = []
    _pair_score_view_cache: list[ClusterPairScores | None] = []

    def _pair_score_view() -> ClusterPairScores | None:
        # The per-cluster pair scores for the frames-out path, sourced from the
        # raw scored pairs + final assignments. Built AT MOST ONCE and cached.
        # None on the dict/columnar paths (their `clusters` dict already carries
        # real pair_scores). Used to (a) restore pair_scores on the rebuilt
        # results["clusters"] dict and (b) feed the confidence_majority golden
        # slow path -- both of which the frames-out cluster dict leaves empty.
        if cluster_frames is None:
            return None
        if not _pair_score_view_cache:
            from goldenmatch.core.cluster_pairscores import ClusterPairScores
            _pair_score_view_cache.append(
                ClusterPairScores.from_frames(cluster_frames.assignments, all_pairs)
            )
        return _pair_score_view_cache[0]

    def _clusters_dict() -> dict[int, dict]:
        if cluster_frames is None:
            # Gate-OFF / columnar paths bound `clusters` eagerly above.
            return clusters
        if not _clusters_cache:
            d = cluster_frames_to_dict(cluster_frames)
            # cluster_frames_to_dict leaves pair_scores={} on every cluster;
            # restore the real per-pair scores from the view so the returned
            # dict matches the legacy path's contract (unmerge, lineage, and
            # callers that read clusters[cid]["pair_scores"] all depend on it).
            psv = _pair_score_view()
            if psv is not None:
                for cid, edges in psv.iter_clusters():
                    info = d.get(cid)
                    if info is not None:
                        info["pair_scores"] = {(a, b): s for (a, b, s) in edges}
            _clusters_cache.append(d)
        return _clusters_cache[0]

    def _golden_member_row_ids() -> list[int]:
        """``__row_id__`` of members of multi-member, non-oversized clusters --
        the only rows survivorship builds a golden record for (singletons and
        oversized clusters never are). Read from whichever cluster representation
        is populated, WITHOUT materializing the full cluster dict on the
        frames-out path."""
        if cluster_frames is not None:
            keep = cluster_frames.metadata.filter(
                (pl.col("size") > 1) & (~pl.col("oversized"))
            )["cluster_id"].to_list()
            return cluster_frames.assignments.filter(
                pl.col("cluster_id").is_in(keep)
            )["member_id"].to_list()
        return [
            mid
            for info in clusters.values()
            if isinstance(info, dict)
            and info.get("size", 0) > 1
            and not info.get("oversized")
            for mid in info.get("members", [])
        ]

    if cluster_frames is not None:
        # Stats from frame aggregates (no dict materialization). Matches the
        # dict path's len(clusters) / size>1 / oversized counts exactly.
        record_metrics({
            "cluster_count": cluster_frames.metadata.height,
            "multi_member_cluster_count": cluster_frames.metadata.filter(
                pl.col("size") > 1
            ).height,
            "oversized_cluster_count": cluster_frames.metadata.filter(
                pl.col("oversized")
            ).height,
        })
    else:
        record_metrics({
            "cluster_count": len(clusters),
            "multi_member_cluster_count": sum(
                1 for c in clusters.values() if c.get("size", 0) > 1
            ),
            "oversized_cluster_count": sum(
                1 for c in clusters.values() if c.get("oversized")
            ),
        })

    # ── Step 5: GOLDEN ──
    golden_records: list[dict] = []
    # Set up the golden_df slot here so the fast path inside the stage("golden")
    # block can write to it directly without the slow path's `golden_df = None`
    # re-init clobbering it after the `with` exits.
    golden_df: pl.DataFrame | None = None
    # Fused-golden routing marker (spec 2026-07-09, Stage G telemetry): flips
    # True when the Arrow-native golden kernel produced the golden frame.
    golden_fused_used = False
    golden_rules = config.golden_rules or GoldenRulesConfig(default_strategy="most_complete")

    # Throughput tier (#1151): corpus dedup consumes the clusters / dup mapping,
    # not canonical golden records — that's the whole point of the tier. Golden
    # survivorship's per-cluster build is an O(N) polars iter_rows that wedges the
    # 100k+ corpus ceiling (a 100k FineWeb run was faulthandler-killed at 150s in
    # this stage). When the throughput plan engaged, skip the golden/survivorship
    # stage (and its quality scan + adaptive refinement) entirely: golden_df stays
    # None and clusters/dupes/unique carry the result. Same posture as the #1134
    # quality-scan skip already applied to this tier. `_throughput_posture` is a
    # non-None dict only after the throughput scoring branch actually ran.
    _skip_golden = _throughput_posture is not None

    # v1.18: post-cluster golden-rules refinement. When the user opted
    # in via `golden_rules.adaptive=True`, refine per-field strategies
    # using cluster shape + column profiles. Refinement is a NEW config
    # (immutable mutation); the original golden_rules is unchanged.
    if not _skip_golden and golden_rules.adaptive:
        try:
            from goldenmatch.core.golden_rules_refiner import refine_golden_rules

            # `profiles` is the ColumnProfile list built in Step 1; reuse
            # if it's in scope (it is for the dedupe pipeline).
            _profiles_for_refiner = locals().get("profiles") or []
            # v1.18.1 (#intelligence-2): thread MemoryStore + dataset
            # into the refiner so the tuner can consult past corrections.
            _dataset_for_refiner = (
                config.memory.dataset if config.memory and config.memory.dataset else "default"
            )
            golden_rules = refine_golden_rules(
                base_rules=golden_rules,
                clusters=_clusters_dict(),
                prepared_df=collected_df,
                column_profiles=_profiles_for_refiner,
                memory_store=memory_store,
                dataset=_dataset_for_refiner,
            )
        except Exception as exc:
            # Refinement failure is non-fatal -- fall back to base rules.
            logger.warning(
                "Adaptive golden-rules refinement failed: %s. "
                "Falling back to base rules.", exc,
            )

    # Quality-weighted survivorship (GoldenRulesConfig.quality_weighting): when
    # enabled AND goldencheck is installed, compute per-cell quality weights so
    # the golden record prefers higher-quality values (canonical spelling over a
    # typo, a real date over a 2099 one) when cluster members disagree.
    # Fail-open + SPARSE: None when the data is clean or goldencheck is absent,
    # which preserves the fast survivorship path (zero behaviour/perf change
    # unless there are real quality issues). The field defaulted True but was a
    # documented no-op until now.
    quality_scores = None
    if not _skip_golden and getattr(golden_rules, "quality_weighting", False):
        # Scope the goldencheck.cell_quality scan (full-frame O(N) value counts +
        # O(distinct^2) fuzzy variant detection per string column) to just the
        # rows that will actually get a golden record -- members of multi-member,
        # non-oversized clusters -- instead of the entire collected frame. On a
        # typical dedupe that is a fraction of N with far fewer distinct values,
        # so the scan is much cheaper, and singleton rows never consume a quality
        # weight anyway. When nothing multi-member clustered, the scan is skipped
        # entirely. Variant detection is now judged relative to the cluster
        # members (the values survivorship actually compares), which is the
        # relevant universe; scoped so cell_quality is not paid over the whole
        # frame on every default dedupe.
        _member_ids = _golden_member_row_ids()
        if _member_ids:
            from goldenmatch.core.quality import compute_quality_scores
            with stage("golden_quality_scores"):
                quality_scores = compute_quality_scores(
                    collected_df.filter(pl.col("__row_id__").is_in(_member_ids))
                )
            if quality_scores:
                logger.info(
                    "GoldenCheck quality weighting: %d penalized cell(s)",
                    len(quality_scores),
                )

    # Golden-record construction was the hidden N²-shaped stage that the
    # bench harness surfaced at 11K rows (36% of wall before this rewrite):
    # the prior loop called `collected_df.filter(__row_id__.is_in(member_ids))`
    # once per cluster, scanning all N rows × K clusters = N·K work. The
    # equivalent vectorized shape is:
    #   1. collect all member_ids of every eligible cluster once,
    #   2. filter collected_df to just those rows (single O(N) pass),
    #   3. attach a `__cluster_id__` column via a tiny row_id → cluster_id
    #      replace_strict (linear in the result frame),
    #   4. partition by cluster_id → dict of small DataFrames,
    #   5. call build_golden_record on each pre-filtered partition.
    # Total: O(N + sum-of-cluster-sizes) which is O(N), independent of K.
    #
    # SP-B frames-out: when build_cluster_frames ran (gate ON), build golden
    # directly from the ClusterFrames via the Task-1 helper, which DEMUXes into
    # the same golden_df / golden_records slots the dict path uses (fast ->
    # golden_df set + golden_records=[]; slow -> golden_records set, golden_df
    # rebuilt by the shared block below). quality_scores is always None in this
    # pipeline (matching the dict path's _polars_native_eligible(..., None)
    # gate); provenance mirrors config.output.lineage_provenance.
    if _skip_golden:
        with stage("golden"):
            # Throughput tier: no survivorship (see _skip_golden above). golden_df
            # stays None; the DedupeResult exposes clusters/dupes/unique instead.
            logger.info(
                "throughput tier: skipping golden-record survivorship (#1151)"
            )
    elif cluster_frames is not None:
        with stage("golden"):
            from goldenmatch.core.golden import build_golden_records_from_frames
            _provenance_on = config.output.lineage_provenance
            # Mirror the dict path's slim projection (below): drop internal
            # __xform_*__ / __mk_*__ / __block_key__ / __bucket__ columns that
            # survivorship never reads, BEFORE the join, so golden records carry
            # the same columns as the dict path (byte-identical golden). Keep
            # __row_id__ (the from-frames join needs it). Same env opt-out.
            _golden_source = collected_df
            if os.environ.get("GOLDENMATCH_GOLDEN_SLIM_MULTIDF", "1") != "0":
                _internal_prefixes = (
                    "__xform_", "__mk_", "__block_key__", "__bucket__",
                )
                from goldenmatch.core.frame import to_frame as _tf_golden

                _golden_source = _tf_golden(collected_df).select([
                    c for c in collected_df.columns
                    if not any(c.startswith(p) for p in _internal_prefixes)
                ]).native
            # Source per-cluster pair scores from the view so the slow builder's
            # confidence_majority survivorship weights by edge confidence instead
            # of degrading to count-majority (the frames-out cluster dict carries
            # pair_scores={}). The fast builder ignores it; only built once here
            # if the slow path needs it.
            _frames_fast_eligible = (
                not _provenance_on
                and _polars_native_eligible(golden_rules, quality_scores=quality_scores)
            )
            _frames_pair_scores: dict[int, dict[tuple[int, int], float]] | None = None
            if not _frames_fast_eligible:
                _psv = _pair_score_view()
                if _psv is not None:
                    _frames_pair_scores = {
                        cid: {(a, b): s for (a, b, s) in edges}
                        for cid, edges in _psv.iter_clusters()
                    }
            # Fused-golden routing (spec 2026-07-09, default-on): try the Arrow-
            # native kernel on the SAME multi_df the classic from-frames builder
            # assembles internally (via _multi_df_from_frames), so a non-None
            # result is byte-identical to the slow path it replaces. Attempt only
            # when the slow path would run -- the fused kernel declines fast-path-
            # eligible configs anyway, and gating avoids an extra join on the hot
            # fast path. On None, fall back unchanged.
            _fused_golden_df = None
            if not _frames_fast_eligible:
                from goldenmatch.core.golden import _multi_df_from_frames
                _fused_multi_df = _multi_df_from_frames(_golden_source, cluster_frames)
                _fused_golden_df = _try_fused_golden(
                    _fused_multi_df,
                    golden_rules,
                    quality_scores=quality_scores,
                    cluster_pair_scores=_frames_pair_scores,
                    provenance=_provenance_on,
                    wants_full_provenance=_provenance_on,
                )
            if _fused_golden_df is not None:
                golden_df, golden_records = _fused_golden_df, []
                golden_fused_used = True
            else:
                golden_df, golden_records = build_golden_records_from_frames(
                    _golden_source,
                    cluster_frames,
                    golden_rules,
                    quality_scores=quality_scores,
                    provenance=_provenance_on,
                    cluster_pair_scores=_frames_pair_scores,
                )
    else:
        with stage("golden"):
            with stage("golden_eligible_filter"):
                eligible: list[tuple[int, dict[str, Any]]] = [
                    (cid, info) for cid, info in clusters.items()
                    if info["size"] > 1 and not info["oversized"]
                ]
            if eligible:
                with stage("golden_row_to_cluster_dict"):
                    # row_id → cluster_id mapping. Members are int row IDs; one
                    # row belongs to at most one cluster, so the map is
                    # unambiguous.
                    row_to_cluster: dict[int, int] = {}
                    for cid, info in eligible:
                        for mid in info["members"]:
                            row_to_cluster[mid] = cid
                    member_ids_all = list(row_to_cluster.keys())

                with stage("golden_multi_df_filter"):
                    multi_df = collected_df.filter(
                        pl.col("__row_id__").is_in(member_ids_all)
                    )

                # Slim projection (PR #595). v32 attribution localized the
                # +9 GB peak jump entirely to golden_attach_cluster_id's
                # with_columns COW -- multi_df carries every column from
                # collected_df including __xform_*__ / __mk_*__ / __block_key__
                # / __bucket__ internals that survivorship never reads. Drop
                # them BEFORE the with_columns so the COW runs over a smaller
                # frame.
                #
                # v33 measured: -2.6 GB peak vs v32 baseline, F1 invariant,
                # wall flat. Default ON; opt out via
                # GOLDENMATCH_GOLDEN_SLIM_MULTIDF=0 if any downstream golden
                # path ever needs an internal column.
                if os.environ.get("GOLDENMATCH_GOLDEN_SLIM_MULTIDF", "1") != "0":
                    with stage("golden_slim_multidf"):
                        _internal_prefixes = (
                            "__xform_", "__mk_", "__block_key__", "__bucket__",
                        )
                        multi_df = multi_df.select([
                            c for c in multi_df.columns
                            if not any(c.startswith(p) for p in _internal_prefixes)
                        ])

                with stage("golden_attach_cluster_id"):
                    # Attach __cluster_id__ via replace_strict. The old keys/new
                    # vals lists are tiny (1 entry per member); the join itself
                    # is linear in `multi_df.height`.
                    multi_df = multi_df.with_columns(
                        pl.col("__row_id__").replace_strict(
                            list(row_to_cluster.keys()),
                            list(row_to_cluster.values()),
                            return_dtype=pl.Int64,
                        ).alias("__cluster_id__")
                    )
                # Batch builder: sorts by __cluster_id__ once and pre-extracts
                # each user column to a Python list ONCE. At 5M / 1.67M
                # multi-member clusters the previous partition_by(as_dict=True)
                # + per-cluster build_golden_record loop allocated 1.67M tiny
                # eager DataFrames AND called cluster_df[col].to_list() ~6.7M
                # times. New path: 4 to_list() calls + Python list slicing.
                # Measured: golden stage 307s -> ~30s at 5M.
                # provenance=True (opt-in) enriches each field with source_row_id
                # for the lineage sidecar; the golden_df builder below ignores
                # the extra key, so the same records feed both paths (no double
                # build).
                #
                # Fast path (provenance=False + uniform strategy + no
                # quality_scores + no field_rules + no cluster_overrides): bypass
                # the list[dict] intermediate entirely. At 10M / 2M multi-member
                # clusters that intermediate allocates ~14 GB of CPython dict
                # overhead; build_golden_records_df does the whole compute
                # columnar in Polars at ~0.8 GB. Provenance + non-fast strategies
                # still go through the list[dict] path so the existing semantics
                # (per-field source_row_id, custom merge_field rules) stay intact.
                _provenance_on = config.output.lineage_provenance
                _fast_eligible = (
                    not _provenance_on
                    and _polars_native_eligible(golden_rules, quality_scores=quality_scores)
                )
                if _fast_eligible:
                    with stage("golden_build_records_df_fast"):
                        golden_df = build_golden_records_df(
                            multi_df, golden_rules
                        )
                    # Leave golden_records empty: the provenance branch below
                    # (gated on `if config.output.lineage_provenance and
                    # golden_records`) is a no-op when provenance is off, so no
                    # metadata is lost.
                    golden_records = []
                else:
                    with stage("golden_build_records_batch_slow"):
                        # #678: thread per-cluster pair_scores so the
                        # confidence_majority strategy actually weights by
                        # edge confidence instead of silently degrading to
                        # count-majority. The legacy `clusters` dict (this
                        # non-frames, non-columnar default path) carries real
                        # per-cluster pair_scores keyed by __row_id__; the
                        # builder remaps those to positional member indices.
                        # The frames-out / columnar paths carry pair_scores={}
                        # by design, so this lookup yields empty dicts there
                        # (documented limitation; confidence_majority on those
                        # paths still falls back to count-majority).
                        cluster_pair_scores = {
                            cid: info.get("pair_scores", {})
                            for cid, info in clusters.items()
                        }
                        # Fused-golden routing (spec 2026-07-09, default-on): try
                        # the Arrow-native kernel on the live multi_df; a non-None
                        # result is byte-identical to build_golden_records_batch
                        # for the covered config surface. On None, fall back.
                        _fused_golden_df = _try_fused_golden(
                            multi_df,
                            golden_rules,
                            quality_scores=quality_scores,
                            cluster_pair_scores=cluster_pair_scores,
                            provenance=_provenance_on,
                            wants_full_provenance=_provenance_on,
                        )
                        if _fused_golden_df is not None:
                            golden_df = _fused_golden_df
                            golden_records = []
                            golden_fused_used = True
                        else:
                            golden_records = build_golden_records_batch(
                                multi_df, golden_rules,
                                quality_scores=quality_scores,
                                provenance=_provenance_on,
                                cluster_pair_scores=cluster_pair_scores,
                            )

    # Build golden DataFrame (slow path: walks the list[dict] returned by
    # build_golden_records_batch. The fast path above already populated
    # golden_df directly and left golden_records empty, so this branch is
    # a no-op on the fast path.)
    if golden_records:
        # Build explicit schema to prevent mixed-type inference errors: golden
        # records from different clusters may have different value types for the
        # same column (e.g. "0" str vs 0 int). Shared with the fused-match
        # short-circuit via _golden_records_to_df.
        golden_df = _golden_records_to_df(golden_records)

    # Classify records
    if cluster_frames is not None:
        # SP-B: dupe row ids from the frame aggregates -- members of every
        # size>1 cluster. OVERSIZED-INCLUDED to match the dict path (which
        # filters size>1 only; oversized clusters' members are still dupes).
        dupe_row_ids = set(
            cluster_frames.assignments.join(
                cluster_frames.metadata.filter(pl.col("size") > 1).select(
                    "cluster_id"
                ),
                on="cluster_id",
            )["member_id"].to_list()
        )
    else:
        multi_cluster_ids = [
            cid for cid, cinfo in clusters.items() if cinfo["size"] > 1
        ]
        dupe_row_ids = set()
        for cid in multi_cluster_ids:
            dupe_row_ids.update(clusters[cid]["members"])
    unique_row_ids = set(all_ids) - dupe_row_ids

    dupes_df = collected_df.filter(pl.col("__row_id__").is_in(list(dupe_row_ids)))
    unique_df = collected_df.filter(pl.col("__row_id__").is_in(list(unique_row_ids)))

    # ── Step 6: REPORT ──
    report = None
    if output_report:
        if cluster_frames is not None:
            # SP-B: report stats from the metadata frame -- no dict build.
            cluster_sizes = cluster_frames.metadata["size"].to_list()
            oversized_count = cluster_frames.metadata.filter(
                pl.col("oversized")
            ).height
            total_clusters = cluster_frames.metadata.height
        else:
            cluster_sizes = [c["size"] for c in clusters.values()]
            oversized_count = sum(
                1 for c in clusters.values() if c["oversized"]
            )
            total_clusters = len(clusters)
        report = generate_dedupe_report(
            total_records=len(collected_df),
            total_clusters=total_clusters,
            cluster_sizes=cluster_sizes,
            oversized_clusters=oversized_count,
            matchkeys_used=[mk.name for mk in matchkeys],
        )

    # ── Step 7: OUTPUT ──
    # SP-C: do NOT rebuild the dict eagerly here. The remaining dict consumers
    # (output_clusters rows, lineage, golden_provenance, results["clusters"])
    # each call `_clusters_dict()` at their OWN consumption site, so the rebuild
    # is deferred until one actually runs. On the frames-out hot path (identity
    # ON, output flags OFF) the FIRST `_clusters_dict()` call is now the
    # `results["clusters"]` assignment -- AFTER the `stage("identity_resolve")`
    # block -- so cluster->golden->identity runs with NO dict materialized
    # (identity reads `cluster_frames`, not the dict). With output flags ON the
    # dict builds at the first opt-in consumer (output_clusters / lineage). On
    # gate-OFF / columnar, `_clusters_dict()` returns the already-bound real
    # `clusters` dict cheaply -- byte-identical, no extra work.
    run_name = config.output.run_name or datetime.now().strftime("%Y%m%d_%H%M%S")
    fmt = config.output.format or "csv"
    directory = config.output.directory or config.output.path or "."

    if output_golden and golden_df is not None:
        write_output(golden_df, directory, run_name, "golden", fmt)

    if output_clusters:
        # Build clusters DataFrame
        cluster_rows = []
        for cid, cinfo in _clusters_dict().items():
            for member_id in cinfo["members"]:
                cluster_rows.append({
                    "__cluster_id__": cid,
                    "__row_id__": member_id,
                    "__cluster_size__": cinfo["size"],
                    "__oversized__": cinfo["oversized"],
                })
        if cluster_rows:
            clusters_df = pl.DataFrame(cluster_rows)
            write_output(clusters_df, directory, run_name, "clusters", fmt)

    if output_dupes and len(dupes_df) > 0:
        write_output(dupes_df, directory, run_name, "dupes", fmt)

    if output_unique and len(unique_df) > 0:
        write_output(unique_df, directory, run_name, "unique", fmt)

    # ── Step 7.5: LINEAGE (always save when outputting) ──
    if output_golden or output_clusters or output_dupes:
        try:
            from goldenmatch.core.lineage import build_lineage, save_lineage
            lineage = build_lineage(
                all_pairs, collected_df, matchkeys, _clusters_dict()
            )
            golden_provenance = None
            if config.output.lineage_provenance and golden_records:
                from goldenmatch.core.golden import golden_records_to_provenance
                golden_provenance = golden_records_to_provenance(
                    golden_records, _clusters_dict(), golden_rules,
                )
            save_lineage(
                lineage, directory, run_name, golden_provenance=golden_provenance,
            )
        except Exception as e:
            logger.warning("Lineage generation failed: %s", e)

    # ── Step 7.6: IDENTITY GRAPH (optional) ──
    # SP-C: feed identity the `cluster_frames` directly (NOT a rebuilt dict) so
    # the cluster->golden->identity stage never calls `_clusters_dict()`. The
    # ClusterPairScores view is built from the frames (assignments frame + RAW
    # all_pairs) via `from_frames(assignments, all_pairs)` -- it buckets the raw
    # input pairs (input-order last-wins) against the final cluster membership.
    # On the columnar branch cluster_frames is None and `_pair_score_view()`
    # returns None, so identity falls back to the dict's pair_scores.
    pair_score_view: ClusterPairScores | None = _pair_score_view()
    with stage("identity_resolve"):
        identity_summary = _resolve_identities(
            clusters if cluster_frames is None else None,
            collected_df, all_pairs, matchkeys, config, run_name,
            pair_score_view=pair_score_view,
            cluster_frames=cluster_frames,
        )

    # SP3: source scored_pairs from the pre-cluster stream, decoupled from cluster
    # pair_scores. Normalized canonical (min,max) + max-score deduped + sorted via
    # dedup_pairs_max_score. Documented behavior change: this is the FULL scored set
    # (a superset of the post-split cluster pair_scores when oversized clusters
    # split). Both pipeline paths normalize identically.
    from goldenmatch.core.pairs import dedup_pairs_max_score
    if _use_columnar and _columnar_pairs_df is not None:
        from goldenmatch.core.scorer import pairs_df_to_list
        scored_pairs = dedup_pairs_max_score(pairs_df_to_list(_columnar_pairs_df))
    else:
        scored_pairs = dedup_pairs_max_score(all_pairs)

    results = {
        "clusters": _clusters_dict(),
        "golden": golden_df,
        "unique": unique_df,
        "dupes": dupes_df,
        "report": report,
        "quarantine": quarantine_df,
        "postflight_report": postflight_report,
        "memory_stats": memory_stats,
        "identity_summary": identity_summary,
        "scored_pairs": scored_pairs,
        "llm_cost": llm_budget_summary,
        "throughput_posture": _throughput_posture,
        "golden_fused_used": golden_fused_used,
        # Classic path never sheds artifacts; the fused-match short-circuit sets
        # this True on its own early-return (spec 2026-07-09 Stage F/G).
        "match_fused_capacity_mode": False,
    }

    try:
        _enqueue_stale_pairs(memory_stats, all_pairs, config)
    finally:
        if memory_store is not None:
            memory_store.close()
    return results


def run_dedupe_df(
    df: pl.DataFrame,
    config: GoldenMatchConfig,
    source_name: str = "dataframe",
    output_golden: bool = False,
    output_clusters: bool = False,
    output_dupes: bool = False,
    output_unique: bool = False,
    output_report: bool = False,
    auto_config: bool = False,
    auto_config_llm_provider: str | None = None,
    _prep_store: PreparedRecordStore | None = None,
) -> dict:
    """Run dedupe pipeline on a DataFrame directly (no file I/O).

    ``_prep_store``: caller-provided PreparedRecordStore. When supplied
    (Phase 3: controller path), this function does NOT open its own store
    and does NOT close the provided one — lifecycle belongs to the caller.
    When None and ``config.prepared_record_store=True``, opens a
    per-call store (Phase 2 stopgap for non-controller paths).
    """
    # Attack C cache seed: stash the caller's df id BEFORE the .cast() below
    # creates a new DataFrame. The seed is the stable identity the controller's
    # iteration loop reuses across 5 dedupe_df calls on the same `sample`.
    # Without it, each iteration's freshly-wrapped LazyFrame had a different
    # id() and the cache never hit.
    #
    # `df.height` (O(1) on an eager frame) is folded into the seed so a
    # recycled id() slot can't serve a stale prep across logically distinct
    # inputs. The schema-name fingerprint in the key alone does NOT defend
    # against this: an empty df and a populated df of the same schema share
    # column names AND can land on the same id() slot after GC, producing a
    # stale cache HIT (the source of the `test_dedupe_df_empty` `assert 3 == 0`
    # flake under `pytest -n auto`). Height distinguishes them.
    cache_seed = (id(df), df.height)
    # Cast all columns to string to prevent schema mismatch errors when
    # mixed-type columns (e.g. birth_year inferred as i64 in some rows,
    # str in others) reach blocking/scoring operations.
    df = df.cast({col: pl.Utf8 for col in df.columns if not col.startswith("__")})
    matchkeys = [] if auto_config else config.get_matchkeys()
    lf = df.lazy()
    if not auto_config:
        required = _get_required_columns(config)
        validate_columns(lf, required)
    lf = lf.with_columns(pl.lit(source_name).alias("__source__"))
    lf = _add_row_ids(lf, offset=0)
    combined_lf = lf.collect().lazy()

    # Phase 2 stopgap: when prepared_record_store=True and no caller-provided
    # _prep_store (Phase 3's controller will supply one), open our own store
    # from env vars. cleanup=not persist so ephemeral runs don't leave files
    # behind, but PERSIST=1 enables stable cross-call reuse.
    # Phase 3 reconciliation: when _prep_store is already provided by the
    # controller, skip opening a second store (own_store=False keeps the
    # finally block from closing the caller's store).
    own_store = False
    _prep_store_ctx: PreparedRecordStore | None = _prep_store
    if _prep_store is None and getattr(config, "prepared_record_store", False):
        from goldenmatch.distributed.record_store import PreparedRecordStore as _PRS
        base_dir_env = os.environ.get("GOLDENMATCH_PREPARED_RECORD_STORE_DIR")
        persist = os.environ.get(
            "GOLDENMATCH_PREPARED_RECORD_STORE_PERSIST", "0"
        ).lower() in ("1", "true", "yes")
        if base_dir_env is not None:
            store_path = Path(base_dir_env) / "goldenmatch_prepared.duckdb"
            _prep_store_ctx = _PRS(path=store_path, cleanup=not persist)
        else:
            _prep_store_ctx = _PRS(cleanup=not persist)
        own_store = True
    try:
        return _run_dedupe_pipeline(combined_lf, config, matchkeys,
                                    output_golden, output_clusters,
                                    output_dupes, output_unique, output_report,
                                    auto_config=auto_config,
                                    auto_config_llm_provider=auto_config_llm_provider,
                                    _prep_cache_seed=cache_seed,
                                    _prep_store=_prep_store_ctx)
    finally:
        if own_store and _prep_store_ctx is not None:
            _prep_store_ctx.close()


def run_match(
    target_file: tuple,
    reference_files: list[tuple],
    config: GoldenMatchConfig,
    output_matched: bool = False,
    output_unmatched: bool = False,
    output_scores: bool = False,
    output_report: bool = False,
    match_mode: str = "best",
    auto_config: bool = False,
    auto_config_llm_provider: str | None = None,
) -> dict:
    """Run the list-match pipeline.

    Args:
        target_file: (file_path, source_name) for the target file.
        reference_files: List of (file_path, source_name) for reference files.
        config: GoldenMatch configuration.
        output_matched: Whether to output matched records.
        output_unmatched: Whether to output unmatched records.
        output_scores: Whether to output score details.
        output_report: Whether to generate a report.
        match_mode: "best" (top score per target) or "all" (all matches).

    Returns:
        Dict with keys: matched, unmatched, report.
    """
    matchkeys = [] if auto_config else config.get_matchkeys()

    # ── Step 1: Load target ──
    if len(target_file) == 3:
        target_path, target_source, target_col_map = target_file
    else:
        target_path, target_source = target_file[0], target_file[1]
        target_col_map = None
    target_lf = load_file(target_path)
    if target_col_map:
        target_lf = apply_column_map(target_lf, target_col_map)
    target_lf = target_lf.with_columns(pl.lit(target_source).alias("__source__"))
    target_lf = _add_row_ids(target_lf, offset=0)
    target_df = target_lf.collect()
    # Cast user columns to string before concat: target and reference files
    # may have schema-incompatible types for the same column (e.g. DBLP
    # ``id`` is string while ACM ``id`` is Int64), and CSV inference produces
    # numeric columns that string transforms cannot consume downstream.
    target_df = _cast_user_cols_to_str(target_df)
    target_ids = set(target_df["__row_id__"].to_list())
    offset = len(target_df)

    # ── Step 2: Load references ──
    ref_frames = []
    ref_sources = set()
    for ref_spec in reference_files:
        if len(ref_spec) == 3:
            ref_path, ref_source, ref_col_map = ref_spec
        else:
            ref_path, ref_source = ref_spec[0], ref_spec[1]
            ref_col_map = None
        ref_lf = load_file(ref_path)
        if ref_col_map:
            ref_lf = apply_column_map(ref_lf, ref_col_map)
        ref_lf = ref_lf.with_columns(pl.lit(ref_source).alias("__source__"))
        ref_lf = _add_row_ids(ref_lf, offset=offset)
        ref_df = _cast_user_cols_to_str(ref_lf.collect())
        offset += len(ref_df)
        ref_frames.append(ref_df)
        ref_sources.add(ref_source)

    # Concat all (frames are already Utf8-cast on user columns above so the
    # vstack can never fail on cross-file schema mismatches).
    combined_df = pl.concat([target_df] + ref_frames)
    combined_lf = combined_df.lazy()

    return _run_match_pipeline(
        combined_lf, config, matchkeys, target_ids,
        output_matched, output_unmatched, output_scores,
        output_report, match_mode,
        auto_config=auto_config,
        auto_config_llm_provider=auto_config_llm_provider,
    )


def _run_match_pipeline(
    combined_lf: pl.LazyFrame,
    config: GoldenMatchConfig,
    matchkeys: list,
    target_ids: set,
    output_matched: bool = False,
    output_unmatched: bool = False,
    output_scores: bool = False,
    output_report: bool = False,
    match_mode: str = "best",
    auto_config: bool = False,
    auto_config_llm_provider: str | None = None,
) -> dict:
    """Shared match pipeline logic (post-ingest).

    This function contains all pipeline steps from auto-fix/validation through
    output. Both run_match() and run_match_df() delegate to this function.
    """
    memory_store = _open_memory_store(config)
    # ── Step 2.5a: AUTO-FIX + VALIDATION ──
    if config.validation and config.validation.auto_fix:
        combined_df_tmp = combined_lf.collect()
        combined_df_tmp, fix_log = auto_fix_dataframe(combined_df_tmp)
        logger.info("Auto-fix applied: %d fix type(s)", len(fix_log))
        combined_lf = combined_df_tmp.lazy()

    # ── Step 2.5a': AUTO-CONFIG ON CLEANED DATA (if zero-config) ──
    if auto_config:
        from goldenmatch.core.autoconfig import (
            _match_mode_autoconfig,
            auto_configure_df,
        )
        combined_df_tmp = combined_lf.collect()
        # #858: this is match mode -- the 2-value __source__ here is the
        # target/reference split, and cross-source linking is the goal, not
        # over-merge. Suppress the multi-source dedupe guard.
        with _match_mode_autoconfig():
            auto_cfg = auto_configure_df(
                combined_df_tmp,
                llm_provider=auto_config_llm_provider,
                llm_auto=config.llm_auto,
            )
        config.matchkeys = auto_cfg.matchkeys
        config.match_settings = auto_cfg.match_settings
        config.blocking = auto_cfg.blocking
        config.golden_rules = auto_cfg.golden_rules
        config.llm_scorer = auto_cfg.llm_scorer
        config.memory = auto_cfg.memory
        if auto_cfg.domain is not None:
            config.domain = auto_cfg.domain
        _propagate_autoconfig_markers(auto_cfg, config)
        matchkeys = config.get_matchkeys()
        logger.info("Auto-configured from cleaned data: %d matchkeys", len(matchkeys))
        combined_lf = combined_df_tmp.lazy()

    if config.validation and config.validation.rules:
        rules = [
            ValidationRule(
                column=rc.column,
                rule_type=rc.rule_type,
                params=rc.params,
                action=rc.action,
            )
            for rc in config.validation.rules
        ]
        combined_df_tmp = combined_lf.collect()
        valid_df, quarantine_df_match, _val_report = validate_dataframe(combined_df_tmp, rules)
        logger.info("Validation: %d quarantined rows", quarantine_df_match.height)
        combined_lf = valid_df.lazy()
    else:
        quarantine_df_match = None

    # ── Step 2.5b: STANDARDIZE ──
    if config.standardization and config.standardization.rules:
        combined_lf = apply_standardization(combined_lf, config.standardization.rules)

    # ── Step 2.5c: DOMAIN FEATURE EXTRACTION ──
    # Mirrors the dedupe pipeline. Auto-config can emit matchkeys that
    # reference domain-extracted columns (e.g. ``__title_key__``); without
    # this step the precompute_matchkey_transforms call below crashes with
    # ColumnNotFoundError.
    combined_lf = _apply_domain_extraction(combined_lf, config)

    # ── Learning Memory: pre-scoring learner overlay ──
    _apply_memory_pre(memory_store, config, matchkeys)

    # ── Step 3: Compute matchkeys ──
    combined_lf = compute_matchkeys(combined_lf, matchkeys)
    # Hoist matchkey transforms — eliminates one .select() per (block ×
    # matchkey field) during scoring. See spec
    # docs/superpowers/specs/2026-05-04-hoist-matchkey-transforms.md.
    combined_df = precompute_matchkey_transforms(combined_lf.collect(), matchkeys)
    combined_lf = combined_df.lazy()

    # ── Step 3.5: AUTO-SUGGEST blocking keys ──
    _run_auto_suggest(combined_df, config)

    # Build source lookup
    source_lookup = {}
    for row in combined_df.select("__row_id__", "__source__").to_dicts():
        source_lookup[row["__row_id__"]] = row["__source__"]

    # ── Step 4: Find matches (cascading: exact first, then fuzzy) ──
    all_pairs: list[tuple[int, int, float]] = []
    matched_pairs: set[tuple[int, int]] = set()

    # Phase 1: Exact matchkeys (fast)
    for mk in matchkeys:
        if mk.type == "exact":
            pairs = find_exact_matches(combined_lf, mk)
            if mk.negative_evidence:
                # v1.12 Path Y: filter pairs by NE penalty
                from goldenmatch.core.scorer import _apply_negative_evidence_to_exact_pairs
                pairs = _apply_negative_evidence_to_exact_pairs(
                    pairs, mk, combined_df
                )
            # Filter to cross target/ref pairs only
            pairs = [
                (a, b, s) for a, b, s in pairs
                if (a in target_ids) != (b in target_ids)
            ]
            all_pairs.extend(pairs)
            for a, b, _s in pairs:
                matched_pairs.add((min(a, b), max(a, b)))

    # Phase 2: Fuzzy matchkeys (parallel block scoring)
    for mk in matchkeys:
        if mk.type == "weighted":
            if config.blocking is None:
                continue
            blocks = build_blocks(combined_lf, config.blocking)
            block_scorer = _get_block_scorer(config)
            pairs = block_scorer(
                blocks, mk, matched_pairs,
                target_ids=target_ids,
            )
            all_pairs.extend(pairs)

    # Phase 2b: Probabilistic matchkeys (Fellegi-Sunter with EM)
    for mk in matchkeys:
        if mk.type == "probabilistic":
            if config.blocking is None:
                continue
            from goldenmatch.core.blocker import collect_blocking_fields
            from goldenmatch.core.probabilistic import (
                load_or_train_em,
                score_probabilistic_blocks_batched,
            )
            blocks = build_blocks(combined_lf, config.blocking)
            # Collect from keys AND passes (multi_pass puts keys in `.passes`).
            blocking_fields = collect_blocking_fields(config.blocking) if config.blocking else []
            # Reuses mk.model_path when set (Splink-style train-once), else trains.
            em_result = load_or_train_em(
                combined_df, mk,
                blocks=blocks,
                blocking_fields=blocking_fields,
            )
            # Blocks scored in parallel across cores (GIL-releasing FS kernels).
            # Does not mutate matched_pairs; fold the returned pairs in below.
            pairs = score_probabilistic_blocks_batched(
                blocks, mk, em_result, matched_pairs,
            )
            all_pairs.extend(pairs)
            for a, b, _s in pairs:
                matched_pairs.add((min(a, b), max(a, b)))

    # ── Step 4.5: CROSS-ENCODER RERANKING (optional) ──
    for mk in matchkeys:
        if mk.type == "weighted" and mk.rerank:
            all_pairs = rerank_top_pairs(all_pairs, combined_df, mk)
            break

    # ── Step 4.6: LLM SCORER (optional) ──
    if config.llm_scorer and config.llm_scorer.enabled and all_pairs:
        if config.llm_scorer.mode == "cluster":
            from goldenmatch.core.llm_cluster import llm_cluster_pairs
            all_pairs = _unwrap_llm_pairs(
                llm_cluster_pairs(all_pairs, combined_df, config=config.llm_scorer)
            )
        else:
            from goldenmatch.core.llm_scorer import llm_score_pairs
            all_pairs = _unwrap_llm_pairs(
                llm_score_pairs(all_pairs, combined_df, config=config.llm_scorer)
            )
        all_pairs = [(a, b, s) for a, b, s in all_pairs if s > 0.5]

    # ── Learning Memory: post-scoring corrections overlay ──
    all_pairs, memory_stats = _apply_memory_post(
        memory_store, config, combined_df, all_pairs
    )

    # ── Step 4.7: POSTFLIGHT (auto-config only) ──
    # Postflight verification. Signals are computed from the unadjusted pair
    # list; threshold adjustments (if any, non-strict only) are then applied
    # to all_pairs before downstream grouping. Ordering: signals reflect
    # pre-adjustment observations; grouping reflects the adjusted threshold.
    all_pairs, postflight_report = _apply_postflight(
        combined_df, config, all_pairs
    )

    # ── Step 5: Normalize pairs so target ID is always first ──
    normalized: list[tuple[int, int, float]] = []
    for a, b, score in all_pairs:
        if a in target_ids:
            normalized.append((a, b, score))
        else:
            normalized.append((b, a, score))

    # ── Step 6: Group by target, apply match_mode ──
    target_matches: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for target_id, ref_id, score in normalized:
        target_matches[target_id].append((ref_id, score))

    if match_mode == "best":
        # Keep only highest score per target
        for tid in target_matches:
            matches = target_matches[tid]
            best = max(matches, key=lambda x: x[1])
            target_matches[tid] = [best]

    # ── Step 7: Build output ──
    matched_rows = []
    all_scores = []
    for target_id, matches in target_matches.items():
        target_row = combined_df.filter(pl.col("__row_id__") == target_id).to_dicts()[0]
        for ref_id, score in matches:
            ref_row = combined_df.filter(pl.col("__row_id__") == ref_id).to_dicts()[0]
            row = {"__target_row_id__": target_id, "__ref_row_id__": ref_id, "__match_score__": score}
            # Add target fields with target_ prefix
            for col, val in target_row.items():
                if not col.startswith("__"):
                    row[f"target_{col}"] = val
            # Add ref fields with ref_ prefix
            for col, val in ref_row.items():
                if not col.startswith("__"):
                    row[f"ref_{col}"] = val
            matched_rows.append(row)
            all_scores.append(score)

    matched_df = pl.DataFrame(matched_rows) if matched_rows else None

    # Unmatched targets
    matched_target_ids = set(target_matches.keys())
    unmatched_ids = target_ids - matched_target_ids
    unmatched_df = combined_df.filter(pl.col("__row_id__").is_in(list(unmatched_ids)))

    # Report
    report = None
    if output_report:
        report = generate_match_report(
            total_targets=len(target_ids),
            matched=len(matched_target_ids),
            unmatched=len(unmatched_ids),
            scores=all_scores,
        )

    # Write outputs
    run_name = config.output.run_name or datetime.now().strftime("%Y%m%d_%H%M%S")
    fmt = config.output.format or "csv"
    directory = config.output.directory or config.output.path or "."

    if output_matched and matched_df is not None:
        write_output(matched_df, directory, run_name, "matched", fmt)

    if output_unmatched and len(unmatched_df) > 0:
        write_output(unmatched_df, directory, run_name, "unmatched", fmt)

    if output_scores and matched_df is not None:
        write_output(matched_df, directory, run_name, "scores", fmt)

    try:
        _enqueue_stale_pairs(memory_stats, all_pairs, config)
    finally:
        if memory_store is not None:
            memory_store.close()

    return {
        "matched": matched_df,
        "unmatched": unmatched_df,
        "report": report,
        "quarantine": quarantine_df_match,
        "postflight_report": postflight_report,
        "memory_stats": memory_stats,
    }


def run_match_df(
    target_df: pl.DataFrame,
    reference_df: pl.DataFrame,
    config: GoldenMatchConfig,
    target_name: str = "target",
    reference_name: str = "reference",
    auto_config: bool = False,
    auto_config_llm_provider: str | None = None,
) -> dict:
    """Run match pipeline on DataFrames directly (no file I/O)."""
    # Cast all columns to string to keep schema consistent with dedupe_df's
    # pre-pipeline behaviour — prevents mixed-type errors in zero-config paths.
    target_df = target_df.cast(
        {col: pl.Utf8 for col in target_df.columns if not col.startswith("__")}
    )
    reference_df = reference_df.cast(
        {col: pl.Utf8 for col in reference_df.columns if not col.startswith("__")}
    )
    matchkeys = [] if auto_config else config.get_matchkeys()
    if not auto_config:
        required = _get_required_columns(config)
        validate_columns(target_df.lazy(), required)
        validate_columns(reference_df.lazy(), required)

    target_lf = target_df.lazy()
    target_lf = target_lf.with_columns(pl.lit(target_name).alias("__source__"))
    target_lf = _add_row_ids(target_lf, offset=0)
    target_collected = target_lf.collect()
    target_ids = set(target_collected["__row_id__"].to_list())

    ref_lf = reference_df.lazy()
    ref_lf = ref_lf.with_columns(pl.lit(reference_name).alias("__source__"))
    ref_lf = _add_row_ids(ref_lf, offset=len(target_collected))
    ref_collected = ref_lf.collect()

    combined_lf = pl.concat([target_collected, ref_collected]).lazy()

    return _run_match_pipeline(
        combined_lf, config, matchkeys, target_ids,
        auto_config=auto_config,
        auto_config_llm_provider=auto_config_llm_provider,
    )


def _score_partition_with_config(  # pyright: ignore[reportUnusedFunction]
    df: pl.DataFrame,
    config: GoldenMatchConfig,
) -> list[tuple[int, int, float]]:
    """Narrow scoring-only kernel for distributed per-partition execution.

    Bypasses the controller, clustering, golden, identity, and postflight.
    The driver auto-configures once on a sample (Phase 2) and ships the
    committed config to each worker; workers just run the cheap scoring
    kernel on their partition and return scored pairs.

    Used by ``distributed.scoring.score_blocks_distributed``. NOT a public
    API -- callers that need a full pipeline (clustering, golden, etc.)
    must go through ``dedupe_df``/``run_dedupe_df``.

    Skipped steps (vs full ``_run_dedupe_pipeline``):
      * auto-config (driver already ran it)
      * memory store (per-partition memory makes no sense)
      * identity resolution (driver-side post-cluster step)
      * build_clusters / build_golden_record (driver merges pairs first)
      * postflight (driver-side report)

    Kept: standardize, domain extraction, compute_matchkeys, precompute
    matchkey transforms, find_exact_matches, score_buckets OR
    build_blocks + find_fuzzy_matches. These are required so the partition
    produces pairs at the same key shape the driver expects.

    Returns list of (id_a, id_b, score) canonicalized as (min, max, score)
    by the downstream ``dedup_pairs_distributed`` stage; this kernel emits
    pairs as-is.
    """
    from goldenmatch.core.autofix import auto_fix_dataframe
    from goldenmatch.core.matchkey import (
        compute_matchkeys,
        precompute_matchkey_transforms,
    )
    from goldenmatch.core.scorer import find_exact_matches
    from goldenmatch.core.standardize import apply_standardization

    matchkeys = config.get_matchkeys()
    if not matchkeys or df.height < 2:
        return []

    # Ensure internal bookkeeping columns exist. Ray-dataset Arrow batches
    # arrive without them; the full pipeline adds them in run_dedupe_df
    # before calling _run_dedupe_pipeline, so the kernel mirrors that here.
    if "__source__" not in df.columns:
        df = df.with_columns(pl.lit("partition").alias("__source__"))
    if "__row_id__" not in df.columns:
        df = df.with_row_index("__row_id__").with_columns(
            pl.col("__row_id__").cast(pl.Int64),
        )

    # Prep: cheap auto-fix on the partition (cleanup, no controller).
    if config.validation and config.validation.auto_fix:
        df, _ = auto_fix_dataframe(df)

    combined_lf = df.lazy()

    # Standardize (if configured by driver).
    if config.standardization and config.standardization.rules:
        combined_lf = apply_standardization(
            combined_lf, config.standardization.rules,
        )

    # Domain extraction (if configured by driver).
    combined_lf = _apply_domain_extraction(combined_lf, config)

    # Compute matchkey columns + precompute transforms (same shape as the
    # full pipeline so scoring primitives find the columns they expect).
    combined_lf = compute_matchkeys(combined_lf, matchkeys)
    collected_df = precompute_matchkey_transforms(
        combined_lf.collect(), matchkeys,
    )
    combined_lf = collected_df.lazy()

    all_pairs: list[tuple[int, int, float]] = []
    matched_pairs: set[tuple[int, int]] = set()

    # Phase 1: Exact matchkeys.
    for mk in matchkeys:
        if mk.type != "exact":
            continue
        pairs = find_exact_matches(combined_lf, mk)
        if mk.negative_evidence:
            from goldenmatch.core.scorer import (
                _apply_negative_evidence_to_exact_pairs,
            )
            pairs = _apply_negative_evidence_to_exact_pairs(
                pairs, mk, collected_df,
            )
        all_pairs.extend(pairs)
        for a, b, _s in pairs:
            matched_pairs.add((min(a, b), max(a, b)))

    # Phase 2: Fuzzy matchkeys. Bucket backend is the only sensible
    # choice inside a distributed worker (small partition, no per-block
    # LazyFrame churn). The driver's score_blocks_distributed already
    # forces backend='bucket' before calling us; we still honor it
    # explicitly in case a caller skips that step.
    if config.blocking is not None:
        for mk in matchkeys:
            if mk.type != "weighted":
                continue
            if config.backend == "bucket":
                from goldenmatch.backends.score_buckets import score_buckets
                pairs = score_buckets(
                    collected_df,
                    config.blocking,
                    mk,
                    matched_pairs,
                    n_buckets=config.n_buckets,
                    across_files_only=False,
                    source_lookup=None,
                )
                all_pairs.extend(pairs)
                continue
            # Fallback: legacy build_blocks + parallel scorer. Not used by
            # the distributed path today, but keeps the kernel usable for
            # callers that hand us a non-bucket config.
            blocks = build_blocks(combined_lf, config.blocking)
            block_scorer = _get_block_scorer(config)
            pairs = block_scorer(
                blocks, mk, matched_pairs,
                across_files_only=False,
                source_lookup=None,
            )
            all_pairs.extend(pairs)

    return all_pairs
