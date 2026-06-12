"""Clean top-level API for GoldenMatch.

Designed for discoverability by coding AIs and human developers.
Thin convenience layer over the existing pipeline modules.

Usage:
    import goldenmatch as gm

    result = gm.dedupe("data.csv", exact=["email"], fuzzy={"name": 0.85})
    result.golden.write_csv("output.csv")
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl

# Imported at module level so tests can patch goldenmatch._api.auto_configure_df.
# The lazy import guard inside each function is kept for cycle safety — this
# top-level import is a re-export alias that tests can mock.
try:
    from goldenmatch.core.autoconfig import auto_configure_df
except ImportError:  # pragma: no cover
    auto_configure_df = None  # type: ignore[assignment]

from goldenmatch.core.autoconfig_verify import PostflightReport

if TYPE_CHECKING:
    from goldenmatch.core.memory.corrections import CorrectionStats
    from goldenmatch.core.recall_certificate import RecallEstimate

logger = logging.getLogger(__name__)


def _attach_memory_to_postflight(
    postflight_report: PostflightReport | None,
    memory_stats: CorrectionStats | None,
) -> PostflightReport | None:
    """Attach memory_stats onto the postflight report so str(report) renders
    a 'Memory:' line. Creates an empty report when memory ran but no
    postflight ran (explicit-config path), so the rendering is always
    reachable via ``result.postflight_report``.

    Returns None only when both the report and memory_stats are absent.
    """
    if memory_stats is None:
        return postflight_report
    if postflight_report is None:
        postflight_report = PostflightReport()
    postflight_report.memory_stats = memory_stats
    return postflight_report


def _detect_llm_provider() -> str | None:
    """Auto-detect LLM provider from environment variables."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return None


@dataclass
class DedupeResult:
    """Result of a deduplication run.

    Attributes:
        golden: DataFrame of golden (canonical) records.
        clusters: Dict of cluster_id -> cluster info (members, pair_scores, confidence).
        dupes: DataFrame of duplicate records.
        unique: DataFrame of unique (non-duplicate) records.
        stats: Summary statistics (total_records, total_clusters, match_rate, etc.).
        scored_pairs: All scored pairs as canonical (min_id, max_id, score),
            sorted by id pair, max-score deduped. The full scored set -- includes
            pairs that auto-split later removed from clusters (a superset of the
            per-cluster pair_scores when oversized clusters split).
        config: The GoldenMatchConfig used for this run.
    """
    golden: pl.DataFrame | None = None
    clusters: dict[int, dict] = field(default_factory=dict)
    dupes: pl.DataFrame | None = None
    unique: pl.DataFrame | None = None
    stats: dict = field(default_factory=dict)
    scored_pairs: list[tuple[int, int, float]] = field(default_factory=list)
    config: Any = None
    postflight_report: PostflightReport | None = None
    # Note: memory_stats is also attached to postflight_report.memory_stats by
    # _attach_memory_to_postflight. The duplication is intentional — converting
    # this field to a property delegating to postflight_report would break the
    # pipeline's direct field assignment pattern. Single source of truth would
    # require refactoring _attach_memory_to_postflight; tracked as follow-up.
    memory_stats: CorrectionStats | None = None
    # Unsupervised recall estimate (capture-recapture over decorrelated
    # matchkey/pass systems). Populated only when dedupe_df(..., certify=True);
    # None otherwise. The audit-calibrated SAFE lower bound stays evaluate-only
    # (it needs a labelled sample). See goldenmatch.core.recall_certificate.
    recall_certificate: RecallEstimate | None = None

    def to_csv(self, path: str, which: str = "golden") -> Path:
        """Write results to CSV.

        Args:
            path: Output file path.
            which: Which result to write: "golden", "dupes", "unique", or "all".
        """
        p = Path(path)
        if which == "golden" and self.golden is not None:
            self.golden.write_csv(p)
        elif which == "dupes" and self.dupes is not None:
            self.dupes.write_csv(p)
        elif which == "unique" and self.unique is not None:
            self.unique.write_csv(p)
        elif which == "all":
            stem = p.stem
            parent = p.parent
            if self.golden is not None:
                self.golden.write_csv(parent / f"{stem}_golden.csv")
            if self.dupes is not None:
                self.dupes.write_csv(parent / f"{stem}_dupes.csv")
            if self.unique is not None:
                self.unique.write_csv(parent / f"{stem}_unique.csv")
        return p

    @property
    def match_rate(self) -> float:
        """Percentage of records that are duplicates."""
        return self.stats.get("match_rate", 0.0)

    @property
    def total_records(self) -> int:
        return self.stats.get("total_records", 0)

    @property
    def total_clusters(self) -> int:
        return self.stats.get("total_clusters", 0)

    def __repr__(self) -> str:
        return (
            f"DedupeResult(records={self.total_records}, "
            f"clusters={self.total_clusters}, "
            f"match_rate={self.match_rate:.1%})"
        )

    def _repr_html_(self) -> str:
        """Rich HTML display for Jupyter notebooks."""
        rows = [
            ("Total Records", str(self.total_records)),
            ("Clusters", str(self.total_clusters)),
            ("Match Rate", f"{self.match_rate:.1%}"),
            ("Duplicates", str(self.dupes.height) if self.dupes is not None else "0"),
            ("Unique", str(self.unique.height) if self.unique is not None else "0"),
        ]
        html = "<h3>GoldenMatch Dedupe Result</h3>"
        html += '<table style="border-collapse:collapse">'
        for label, val in rows:
            html += f'<tr><td style="padding:4px 12px;font-weight:bold">{label}</td>'
            html += f'<td style="padding:4px 12px">{val}</td></tr>'
        html += "</table>"
        if self.golden is not None and self.golden.height > 0:
            html += "<h4>Golden Records (first 10)</h4>"
            display_cols = [c for c in self.golden.columns if not c.startswith("__")][:6]
            sample = self.golden.select(display_cols).head(10)
            html += '<table style="border-collapse:collapse;border:1px solid #ddd">'
            html += "<tr>" + "".join(
                f'<th style="padding:4px 8px;border:1px solid #ddd;background:#f5f5f5">{c}</th>'
                for c in display_cols
            ) + "</tr>"
            for row in sample.to_dicts():
                html += "<tr>" + "".join(
                    f'<td style="padding:4px 8px;border:1px solid #ddd">{row.get(c, "")}</td>'
                    for c in display_cols
                ) + "</tr>"
            html += "</table>"
        return html


@dataclass
class MatchResult:
    """Result of a list-match run.

    Attributes:
        matched: DataFrame of matched target records with scores.
        unmatched: DataFrame of unmatched target records.
        stats: Summary statistics.
    """
    matched: pl.DataFrame | None = None
    unmatched: pl.DataFrame | None = None
    stats: dict = field(default_factory=dict)
    postflight_report: PostflightReport | None = None
    # See DedupeResult.memory_stats note — same intentional duplication.
    memory_stats: CorrectionStats | None = None

    def to_csv(self, path: str) -> Path:
        """Write matched results to CSV."""
        p = Path(path)
        if self.matched is not None:
            self.matched.write_csv(p)
        return p

    def __repr__(self) -> str:
        n_matched = self.matched.height if self.matched is not None else 0
        n_unmatched = self.unmatched.height if self.unmatched is not None else 0
        return f"MatchResult(matched={n_matched}, unmatched={n_unmatched})"

    def _repr_html_(self) -> str:
        """Rich HTML display for Jupyter notebooks."""
        n_matched = self.matched.height if self.matched is not None else 0
        n_unmatched = self.unmatched.height if self.unmatched is not None else 0
        html = "<h3>GoldenMatch Match Result</h3>"
        html += f"<p>Matched: {n_matched} | Unmatched: {n_unmatched}</p>"
        if self.matched is not None and self.matched.height > 0:
            display_cols = [c for c in self.matched.columns if not c.startswith("__")][:6]
            sample = self.matched.select(display_cols).head(10)
            html += '<table style="border-collapse:collapse;border:1px solid #ddd">'
            html += "<tr>" + "".join(
                f'<th style="padding:4px 8px;border:1px solid #ddd;background:#f5f5f5">{c}</th>'
                for c in display_cols
            ) + "</tr>"
            for row in sample.to_dicts():
                html += "<tr>" + "".join(
                    f'<td style="padding:4px 8px;border:1px solid #ddd">{row.get(c, "")}</td>'
                    for c in display_cols
                ) + "</tr>"
            html += "</table>"
        return html


def load_config(path: str) -> Any:
    """Load a GoldenMatch YAML config file.

    Args:
        path: Path to YAML config file.

    Returns:
        GoldenMatchConfig Pydantic model.
    """
    from goldenmatch.config.loader import load_config as _load
    return _load(path)


def dedupe(
    *files: str,
    config: str | Any | None = None,
    exact: list[str] | None = None,
    fuzzy: dict[str, float] | None = None,
    blocking: list[str] | None = None,
    threshold: float | None = None,
    llm_scorer: bool = False,
    backend: str | None = None,
) -> DedupeResult:
    """Deduplicate one or more files.

    Args:
        *files: Paths to CSV/Excel/Parquet files.
        config: Path to YAML config, or a GoldenMatchConfig object, or None for auto-config.
        exact: List of column names for exact matching (e.g., ["email", "phone"]).
        fuzzy: Dict of column name -> threshold for fuzzy matching (e.g., {"name": 0.85}).
        blocking: List of column names for blocking (e.g., ["zip"]).
        threshold: Override fuzzy match threshold for all fields.
        llm_scorer: Enable LLM scoring for borderline pairs (requires OPENAI_API_KEY).
        backend: Processing backend: None (default Polars), "ray", "duckdb".

    Returns:
        DedupeResult with golden records, clusters, dupes, unique, and stats.

    Examples:
        # Zero-config
        result = gm.dedupe("customers.csv")

        # Exact + fuzzy
        result = gm.dedupe("customers.csv", exact=["email"], fuzzy={"name": 0.85, "zip": 0.95})

        # With YAML config
        result = gm.dedupe("file1.csv", "file2.csv", config="match.yaml")

        # With LLM scorer
        result = gm.dedupe("products.csv", fuzzy={"title": 0.80}, llm_scorer=True)
    """
    from goldenmatch.core.pipeline import run_dedupe

    # Build config
    if isinstance(config, str):
        cfg = load_config(config)
    elif config is not None:
        cfg = config
    else:
        cfg = _build_config(exact, fuzzy, blocking, threshold, llm_scorer, backend)

    if backend and hasattr(cfg, "backend"):
        cfg.backend = backend

    # Build file specs
    file_specs = [(str(f), Path(f).stem) for f in files]

    # Run pipeline
    result = run_dedupe(file_specs, cfg)

    _mem = result.get("memory_stats")
    return DedupeResult(
        golden=result.get("golden"),
        clusters=result.get("clusters", {}),
        dupes=result.get("dupes"),
        unique=result.get("unique"),
        stats=_extract_stats(result),
        scored_pairs=result.get("scored_pairs", []),
        config=cfg,
        postflight_report=_attach_memory_to_postflight(
            result.get("postflight_report"), _mem
        ),
        memory_stats=_mem,
    )


def dedupe_df(
    df: pl.DataFrame,
    *,
    config: Any | None = None,
    exact: list[str] | None = None,
    fuzzy: dict[str, float] | None = None,
    blocking: list[str] | None = None,
    threshold: float | None = None,
    llm_scorer: bool = False,
    llm_auto: bool = False,
    backend: str | None = None,
    source_name: str = "dataframe",
    confidence_required: bool = True,
    allow_red_config: bool = False,
    exclude_columns: list[str] | None = None,
    planning_effort: str | None = None,
    fs_model_path: str | None = None,
    certify: bool = False,
) -> DedupeResult:
    """Deduplicate a Polars DataFrame directly (no file I/O).

    Same as dedupe() but accepts a DataFrame instead of file paths.
    Designed for programmatic use and as the entry point for SQL extensions.

    Args:
        df: Polars DataFrame to deduplicate.
        config: GoldenMatchConfig object, or None for auto-config from kwargs.
        exact: List of column names for exact matching.
        fuzzy: Dict of column name -> threshold for fuzzy matching.
        blocking: List of column names for blocking.
        threshold: Override fuzzy match threshold for all fields.
        llm_scorer: Enable LLM scoring for borderline pairs.
        backend: Processing backend: None (default), "ray".
        source_name: Source label for the DataFrame (default: "dataframe").
        fs_model_path: Persisted Fellegi-Sunter model file. When set, every
            probabilistic matchkey without its own ``model_path`` loads the
            model from here (skipping EM) if it exists, or trains and saves it
            there on first run (Splink-style train-once -> reuse).
        certify: When True, also compute an unsupervised recall estimate
            (capture-recapture over the config's decorrelated matchkey/pass
            systems) and attach it to ``DedupeResult.recall_certificate``.
            Off by default — it re-runs the pipeline once per system, so it
            roughly K-times the cost. The audit-calibrated SAFE lower bound is
            NOT computed here (it needs a labelled sample; use the ``evaluate``
            CLI / ``audit_calibrated_bound`` for that).

    Returns:
        DedupeResult with golden records, clusters, dupes, unique, and stats.

    Notes:
        Zero-config paths call ``auto_configure_df`` internally. If preflight
        finds an unrepairable issue, ``ConfigValidationError`` (from
        ``goldenmatch.core.autoconfig_verify``) propagates unchanged —
        callers that want a partial config can catch it and inspect
        ``err.report.findings``. The returned result's ``postflight_report``
        is populated when auto-config was used; ``None`` for hand-written
        configs.
    """
    import goldenmatch._api as _self_api
    from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
    from goldenmatch.core.pipeline import run_dedupe_df

    # Bind the kwarg-supplied exclude_columns to the runtime ContextVar
    # BEFORE any pipeline step that reads it (auto_configure_df,
    # GoldenFlow transforms). The pipeline downstream + the auto-config
    # exclusion resolver both read this var. config.exclude_columns is
    # OR'd in by the resolver itself; here we only propagate the kwarg
    # so it's visible even when the user passes a hand-written config.
    _kwarg_token = None
    if exclude_columns:
        _kwarg_token = _RUNTIME_EXCLUDE_COLUMNS.set(list(exclude_columns))

    try:
        _used_controller = False
        if isinstance(config, str):
            config = load_config(config)
        elif config is None:
            if exact or fuzzy:
                config = _build_config(exact, fuzzy, blocking, threshold, llm_scorer, backend)
            else:
                # Zero-config: call auto_configure_df *before* the pipeline so the
                # pipeline never re-invokes auto-config. This eliminates the
                # double-pipeline-run introduced by Task 5.1 (the controller loop
                # itself calls dedupe_df on samples; if the pipeline also ran
                # auto_configure_df we'd recurse). Task 5.2 fix.
                # Access via module attribute so tests can patch goldenmatch._api.auto_configure_df.
                _auto_config_provider = _detect_llm_provider() if llm_scorer else None
                from goldenmatch.core.bench import stage as _bench_stage
                with _bench_stage("auto_configure"):
                    config = _self_api.auto_configure_df(
                        df,
                        llm_provider=_auto_config_provider,
                        llm_auto=llm_auto,
                        _skip_finalize=True,
                        confidence_required=confidence_required,
                        allow_red_config=allow_red_config,
                        planning_effort=planning_effort,
                    )
                _used_controller = True

        # All branches above bind config to GoldenMatchConfig (load_config returns
        # GoldenMatchConfig; _build_config returns GoldenMatchConfig;
        # auto_configure_df returns GoldenMatchConfig). The Optional in the type
        # signature is the public input contract, not a runtime state here.
        assert config is not None, "dedupe_df: config resolution above must bind config"

        # Apply overrides uniformly regardless of config source
        if backend:
            config.backend = backend
        if llm_scorer:
            from goldenmatch.config.schemas import LLMScorerConfig
            config.llm_scorer = LLMScorerConfig(enabled=True)
        if llm_auto:
            config.llm_auto = llm_auto
        # Splink-style train-once: point probabilistic matchkeys at a persisted
        # model file (load-or-train-and-save). Only fills matchkeys that don't
        # already carry their own model_path. No-op when there are none.
        if fs_model_path:
            for mk in getattr(config, "matchkeys", None) or []:
                if getattr(mk, "type", None) == "probabilistic" and not mk.model_path:
                    mk.model_path = fs_model_path

        # Merge kwarg-supplied exclude_columns into config.exclude_columns
        # so downstream pipeline steps that read off config (rather than
        # the ContextVar) see them too. Idempotent + order-preserving.
        if exclude_columns:
            merged = list(dict.fromkeys(
                list(getattr(config, "exclude_columns", None) or [])
                + list(exclude_columns)
            ))
            try:
                config.exclude_columns = merged
            except Exception:
                # Defensive: if the config object can't accept the field
                # (e.g. user passed a non-Pydantic shim), the ContextVar
                # still carries the kwarg list so behavior is preserved.
                pass

        # If the user passed a hand-written config with exclude_columns
        # set, propagate to the ContextVar too so GoldenFlow + the
        # auto-config exclusion resolver see it on every call path.
        cfg_excl = getattr(config, "exclude_columns", None) or []
        if cfg_excl and _kwarg_token is None:
            _kwarg_token = _RUNTIME_EXCLUDE_COLUMNS.set(list(cfg_excl))

        result = run_dedupe_df(
            df, config, source_name=source_name,
            auto_config=False,
        )
    finally:
        if _kwarg_token is not None:
            _RUNTIME_EXCLUDE_COLUMNS.reset(_kwarg_token)

    _mem = result.get("memory_stats")
    pf = _attach_memory_to_postflight(result.get("postflight_report"), _mem)

    # Fix 1: Wire controller profile + history onto PostflightReport.
    # When the zero-config path ran the controller, stash sample_profile and
    # history so callers can inspect health verdicts, errors, and drift.
    # NOTE: full_vs_sample_drift is not set here because _skip_finalize=True
    # skips the controller's _finalize call. Task 6.1 will compute drift here
    # once pf.signals is a typed ComplexityProfile (currently PostflightSignals
    # TypedDict). For now, drift is left unset on this path.
    if _used_controller:
        from goldenmatch.core.autoconfig import (
            _LAST_AUTOCONFIG_EXCLUSIONS,
            _LAST_CONTROLLER_RUN,
        )
        _ctrl_state = _LAST_CONTROLLER_RUN.get()
        if _ctrl_state is not None:
            _sample_profile, _history = _ctrl_state
            if pf is None:
                pf = PostflightReport()
            pf.controller_profile = _sample_profile
            pf.controller_history = _history
        # #404: GoldenCheck auto-config exclusions surface in postflight.
        _exclusions = _LAST_AUTOCONFIG_EXCLUSIONS.get()
        if _exclusions:
            if pf is None:
                pf = PostflightReport()
            pf.autoconfig_exclusions = list(_exclusions)

    # Unsupervised recall certificate (opt-in). Runs each decorrelated
    # matchkey/pass system through the pipeline and applies the FP-aware
    # capture-recapture estimator. Reuses the resolved `config` so it does
    # NOT re-run auto-config; the per-system dedupe_df calls default
    # certify=False, so there is no recursion. Fail-open: a certify failure
    # never breaks the dedupe result it annotates.
    _recall_cert = None
    if certify:
        from goldenmatch.core.recall_certificate import certify_recall_df

        try:
            _recall_cert = certify_recall_df(df, config=config)
        except Exception:  # noqa: BLE001 - certify is additive; never break dedupe
            logger.warning("recall certify failed; returning result without certificate", exc_info=True)

    return DedupeResult(
        golden=result.get("golden"),
        clusters=result.get("clusters", {}),
        dupes=result.get("dupes"),
        unique=result.get("unique"),
        stats=_extract_stats(result),
        scored_pairs=result.get("scored_pairs", []),
        config=config,
        postflight_report=pf,
        memory_stats=_mem,
        recall_certificate=_recall_cert,
    )


def match_df(
    target: pl.DataFrame,
    reference: pl.DataFrame,
    *,
    config: Any | None = None,
    exact: list[str] | None = None,
    fuzzy: dict[str, float] | None = None,
    blocking: list[str] | None = None,
    threshold: float | None = None,
    backend: str | None = None,
    confidence_required: bool = True,
    allow_red_config: bool = False,
    exclude_columns: list[str] | None = None,
    planning_effort: str | None = None,
) -> MatchResult:
    """Match a target DataFrame against a reference DataFrame (no file I/O).

    Same as match() but accepts DataFrames instead of file paths.

    Args:
        target: Polars DataFrame of target records.
        reference: Polars DataFrame of reference records.
        config: GoldenMatchConfig object, or None for auto-config from kwargs.
        exact: List of column names for exact matching.
        fuzzy: Dict of column name -> threshold for fuzzy matching.
        blocking: List of column names for blocking.
        threshold: Override fuzzy match threshold.
        backend: Processing backend: None, "ray".

    Returns:
        MatchResult with matched and unmatched DataFrames.

    Notes:
        Zero-config paths call ``auto_configure_df`` internally. If preflight
        finds an unrepairable issue, ``ConfigValidationError`` (from
        ``goldenmatch.core.autoconfig_verify``) propagates unchanged —
        callers that want a partial config can catch it and inspect
        ``err.report.findings``. The returned result's ``postflight_report``
        is populated when auto-config was used; ``None`` for hand-written
        configs.
    """
    import goldenmatch._api as _self_api
    from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
    from goldenmatch.core.pipeline import run_match_df

    # Same exclude_columns plumbing as dedupe_df -- ContextVar before any
    # pipeline step so auto-config + GoldenFlow transforms both see it.
    _kwarg_token = None
    if exclude_columns:
        _kwarg_token = _RUNTIME_EXCLUDE_COLUMNS.set(list(exclude_columns))

    try:
        _used_controller = False
        if isinstance(config, str):
            config = load_config(config)
        elif config is None:
            if exact or fuzzy:
                config = _build_config(exact, fuzzy, blocking, threshold, backend=backend)
            else:
                # Zero-config: call auto_configure_df *before* the pipeline so the
                # pipeline never re-invokes auto-config. Eliminates double-pipeline-run
                # (Task 5.2 fix — mirrors dedupe_df zero-config refactor).
                # Pass reference= so auto-config sees both column sets and emits
                # matchkeys/blocking that apply uniformly across the join.
                # Access via module attribute so tests can patch goldenmatch._api.auto_configure_df.
                from goldenmatch.core.autoconfig import _match_mode_autoconfig
                # #858: match mode -- suppress the multi-source dedupe guard even
                # if `target` itself carries a user source-pattern column.
                with _match_mode_autoconfig():
                    config = _self_api.auto_configure_df(
                        target, reference=reference, _skip_finalize=True,
                        confidence_required=confidence_required,
                        allow_red_config=allow_red_config,
                        planning_effort=planning_effort,
                    )
                _used_controller = True

        assert config is not None, "match_df: config resolution above must bind config"

        if backend:
            config.backend = backend

        # Merge kwarg-supplied exclude_columns into config.exclude_columns
        # so downstream pipeline steps see them via config too.
        if exclude_columns:
            merged = list(dict.fromkeys(
                list(getattr(config, "exclude_columns", None) or [])
                + list(exclude_columns)
            ))
            try:
                config.exclude_columns = merged
            except Exception:
                pass

        # If user passed a hand-written config with exclude_columns set,
        # propagate to ContextVar so GoldenFlow + the auto-config exclusion
        # resolver see it on every call path.
        cfg_excl = getattr(config, "exclude_columns", None) or []
        if cfg_excl and _kwarg_token is None:
            _kwarg_token = _RUNTIME_EXCLUDE_COLUMNS.set(list(cfg_excl))

        result = run_match_df(target, reference, config, auto_config=False)
    finally:
        if _kwarg_token is not None:
            _RUNTIME_EXCLUDE_COLUMNS.reset(_kwarg_token)

    _mem = result.get("memory_stats")
    pf = _attach_memory_to_postflight(result.get("postflight_report"), _mem)

    # Fix 1: Wire controller profile + history onto PostflightReport (match path).
    # See dedupe_df for full commentary. Drift unset when _skip_finalize=True
    # (Task 6.1 deferred).
    if _used_controller:
        from goldenmatch.core.autoconfig import (
            _LAST_AUTOCONFIG_EXCLUSIONS,
            _LAST_CONTROLLER_RUN,
        )
        _ctrl_state = _LAST_CONTROLLER_RUN.get()
        if _ctrl_state is not None:
            _sample_profile, _history = _ctrl_state
            if pf is None:
                pf = PostflightReport()
            pf.controller_profile = _sample_profile
            pf.controller_history = _history
        # #404: GoldenCheck auto-config exclusions surface in postflight.
        _exclusions = _LAST_AUTOCONFIG_EXCLUSIONS.get()
        if _exclusions:
            if pf is None:
                pf = PostflightReport()
            pf.autoconfig_exclusions = list(_exclusions)

    return MatchResult(
        matched=result.get("matched"),
        unmatched=result.get("unmatched"),
        stats=_extract_stats(result),
        postflight_report=pf,
        memory_stats=_mem,
    )


def score_strings(
    value_a: str,
    value_b: str,
    scorer: str = "jaro_winkler",
) -> float:
    """Score two strings using a named similarity scorer.

    Maps to the SQL function: SELECT goldenmatch_score('John', 'Jon', 'jaro_winkler');

    Args:
        value_a: First string.
        value_b: Second string.
        scorer: Scoring algorithm: "jaro_winkler", "levenshtein", "exact",
                "token_sort", "soundex_match".

    Returns:
        Similarity score between 0.0 and 1.0.
    """
    from goldenmatch.core.scorer import score_field
    result = score_field(value_a, value_b, scorer)
    return result if result is not None else 0.0


def score_pair_df(
    record_a: dict,
    record_b: dict,
    *,
    fuzzy: dict[str, float] | None = None,
    exact: list[str] | None = None,
    scorer: str = "jaro_winkler",
) -> float:
    """Score a pair of records.

    Args:
        record_a: First record as dict.
        record_b: Second record as dict.
        fuzzy: Dict of field -> weight for fuzzy scoring.
        exact: List of fields for exact matching.
        scorer: Default scorer for fuzzy fields.

    Returns:
        Overall match score between 0.0 and 1.0.
    """
    from goldenmatch.config.schemas import MatchkeyField
    from goldenmatch.core.scorer import score_pair

    fields = []
    if exact:
        for col in exact:
            fields.append(MatchkeyField(field=col, scorer="exact", weight=1.0,
                                        transforms=["lowercase", "strip"]))
    if fuzzy:
        for col, weight in fuzzy.items():
            fields.append(MatchkeyField(field=col, scorer=scorer, weight=weight,
                                        transforms=["lowercase", "strip"]))

    if not fields:
        common = set(record_a.keys()) & set(record_b.keys())
        for col in sorted(common):
            fields.append(MatchkeyField(field=col, scorer=scorer, weight=1.0,
                                        transforms=["lowercase", "strip"]))

    return score_pair(record_a, record_b, fields)


def explain_pair_df(
    record_a: dict,
    record_b: dict,
    *,
    fuzzy: dict[str, float] | None = None,
    exact: list[str] | None = None,
    scorer: str = "jaro_winkler",
) -> str:
    """Generate a natural language explanation for a record pair.

    Args:
        record_a: First record as dict.
        record_b: Second record as dict.
        fuzzy: Dict of field -> weight for fuzzy scoring.
        exact: List of fields for exact matching.
        scorer: Default scorer for fuzzy fields.

    Returns:
        Human-readable explanation string.
    """
    from goldenmatch.config.schemas import MatchkeyField
    from goldenmatch.core.explain import explain_pair_nl
    from goldenmatch.core.scorer import score_field
    from goldenmatch.utils.transforms import apply_transforms

    fields = []
    if exact:
        for col in exact:
            fields.append(MatchkeyField(field=col, scorer="exact", weight=1.0,
                                        transforms=["lowercase", "strip"]))
    if fuzzy:
        for col, weight in fuzzy.items():
            fields.append(MatchkeyField(field=col, scorer=scorer, weight=weight,
                                        transforms=["lowercase", "strip"]))

    field_scores = []
    weighted_sum = 0.0
    weight_sum = 0.0
    for f in fields:
        val_a = apply_transforms(record_a.get(f.field), f.transforms)
        val_b = apply_transforms(record_b.get(f.field), f.transforms)
        fs = score_field(val_a, val_b, f.scorer)
        if fs is not None:
            field_scores.append({
                "field": f.field,
                "scorer": f.scorer,
                "score": fs,
                "value_a": str(val_a) if val_a is not None else "",
                "value_b": str(val_b) if val_b is not None else "",
            })
            weighted_sum += fs * f.weight
            weight_sum += f.weight

    overall = weighted_sum / weight_sum if weight_sum > 0 else 0.0

    return explain_pair_nl(record_a, record_b, field_scores, overall)


def match(
    target: str,
    reference: str,
    *,
    config: str | Any | None = None,
    exact: list[str] | None = None,
    fuzzy: dict[str, float] | None = None,
    blocking: list[str] | None = None,
    threshold: float | None = None,
    backend: str | None = None,
) -> MatchResult:
    """Match a target file against a reference file.

    Args:
        target: Path to target CSV/Excel/Parquet.
        reference: Path to reference CSV/Excel/Parquet.
        config: Path to YAML config, or a GoldenMatchConfig object.
        exact: List of column names for exact matching.
        fuzzy: Dict of column name -> threshold for fuzzy matching.
        blocking: List of column names for blocking.
        threshold: Override fuzzy match threshold.
        backend: Processing backend: None, "ray", "duckdb".

    Returns:
        MatchResult with matched and unmatched DataFrames.

    Examples:
        result = gm.match("new_customers.csv", "master.csv", fuzzy={"name": 0.85})
        result.matched.write_csv("matches.csv")
    """
    from goldenmatch.config.schemas import GoldenMatchConfig
    from goldenmatch.core.pipeline import run_match

    _auto_config = False

    if isinstance(config, str):
        cfg = load_config(config)
    elif config is not None:
        cfg = config
    elif exact or fuzzy:
        cfg = _build_config(exact, fuzzy, blocking, threshold, backend=backend)
    else:
        # Zero-config: defer to in-pipeline auto-config (mirrors match_df).
        # Without this, the file API previously fell through to _build_config
        # which emitted a stub matchkey on a non-existent ``__placeholder__``
        # column and crashed precompute_matchkey_transforms.
        cfg = GoldenMatchConfig()
        _auto_config = True

    if backend and hasattr(cfg, "backend"):
        cfg.backend = backend

    target_spec = (str(target), Path(target).stem)
    ref_specs = [(str(reference), Path(reference).stem)]

    result = run_match(target_spec, ref_specs, cfg, auto_config=_auto_config)

    _mem = result.get("memory_stats")
    return MatchResult(
        matched=result.get("matched"),
        unmatched=result.get("unmatched"),
        stats=_extract_stats(result),
        postflight_report=_attach_memory_to_postflight(
            result.get("postflight_report"), _mem
        ),
        memory_stats=_mem,
    )


def pprl_link(
    file_a: str,
    file_b: str,
    *,
    fields: list[str] | None = None,
    threshold: float | None = None,
    security_level: str = "high",
    protocol: str = "trusted_third_party",
    auto_config: bool = True,
) -> dict:
    """Privacy-preserving record linkage between two files.

    Args:
        file_a: Path to party A's CSV.
        file_b: Path to party B's CSV.
        fields: Field names to match on. If None and auto_config=True, auto-detected.
        threshold: Match threshold. If None and auto_config=True, auto-detected.
        security_level: "standard", "high", or "paranoid".
        protocol: "trusted_third_party" or "smc".
        auto_config: Auto-detect fields and threshold from data.

    Returns:
        Dict with clusters, match_count, total_comparisons.

    Examples:
        result = gm.pprl_link("hospital_a.csv", "hospital_b.csv", fields=["name", "dob", "zip"])
        print(f"Found {result['match_count']} matches across {len(result['clusters'])} clusters")
    """
    from goldenmatch.pprl.autoconfig import auto_configure_pprl
    from goldenmatch.pprl.protocol import PPRLConfig, run_pprl

    df_a = pl.read_csv(file_a, ignore_errors=True, encoding="utf8-lossy")
    df_b = pl.read_csv(file_b, ignore_errors=True, encoding="utf8-lossy")

    if fields is None and auto_config:
        auto_result = auto_configure_pprl(df_a, security_level=security_level)
        fields = auto_result.recommended_fields
        if threshold is None:
            threshold = auto_result.recommended_config.threshold

    if fields is None:
        raise ValueError("fields must be specified or auto_config must be True")
    if threshold is None:
        threshold = 0.85

    _LEVELS = {"standard": (2, 20, 512), "high": (2, 30, 1024), "paranoid": (3, 40, 2048)}
    ng, hf, bs = _LEVELS.get(security_level, (2, 30, 1024))

    config = PPRLConfig(
        fields=fields, threshold=threshold, security_level=security_level,
        ngram_size=ng, hash_functions=hf, bloom_filter_size=bs,
        protocol=protocol,
    )

    result = run_pprl(df_a, df_b, config)

    return {
        "clusters": result.clusters,
        "match_count": result.match_count,
        "total_comparisons": result.total_comparisons,
        "config": {
            "fields": fields,
            "threshold": threshold,
            "security_level": security_level,
        },
    }


def evaluate(
    *files: str,
    config: str | Any,
    ground_truth: str,
    col_a: str = "id_a",
    col_b: str = "id_b",
) -> dict:
    """Evaluate matching accuracy against ground truth.

    Args:
        *files: Input data files.
        config: Path to YAML config or GoldenMatchConfig object.
        ground_truth: Path to ground truth CSV with pair columns.
        col_a: Column name for ID A in ground truth.
        col_b: Column name for ID B in ground truth.

    Returns:
        Dict with precision, recall, f1, tp, fp, fn.

    Examples:
        metrics = gm.evaluate("data.csv", config="config.yaml", ground_truth="gt.csv")
        print(f"F1: {metrics['f1']:.1%}")
    """
    from goldenmatch.core.evaluate import evaluate_clusters, load_ground_truth_csv
    from goldenmatch.core.pipeline import run_dedupe

    if isinstance(config, str):
        cfg = load_config(config)
    else:
        cfg = config

    file_specs = [(str(f), Path(f).stem) for f in files]
    gt_pairs = load_ground_truth_csv(str(ground_truth), col_a, col_b)

    result = run_dedupe(file_specs, cfg)
    clusters = result.get("clusters", {})

    eval_result = evaluate_clusters(clusters, gt_pairs)
    return eval_result.summary()


# ── Internal helpers ──


def _build_config(
    exact: list[str] | None = None,
    fuzzy: dict[str, float] | None = None,
    blocking: list[str] | None = None,
    threshold: float | None = None,
    llm_scorer: bool = False,
    backend: str | None = None,
) -> Any:
    """Build a GoldenMatchConfig from simple kwargs."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        LLMScorerConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    matchkeys = []

    if exact:
        for col in exact:
            matchkeys.append(MatchkeyConfig(
                name=f"exact_{col}",
                type="exact",
                fields=[MatchkeyField(field=col, transforms=["lowercase", "strip"])],
            ))

    if fuzzy:
        fields = []
        for col, weight in fuzzy.items():
            fields.append(MatchkeyField(
                field=col,
                scorer="jaro_winkler",
                weight=weight,
                transforms=["lowercase", "strip"],
            ))
        t = threshold or 0.85
        matchkeys.append(MatchkeyConfig(
            name="fuzzy",
            type="weighted",
            threshold=t,
            fields=fields,
        ))

    if not matchkeys:
        # Auto-config: will be handled by pipeline's auto-suggest
        matchkeys.append(MatchkeyConfig(
            name="auto",
            type="exact",
            fields=[MatchkeyField(field="__placeholder__")],
        ))

    blocking_config = None
    if blocking:
        blocking_config = BlockingConfig(
            keys=[BlockingKeyConfig(fields=blocking, transforms=["lowercase"])],
        )
    elif fuzzy:
        # Auto-suggest blocking for fuzzy matchkeys
        blocking_config = BlockingConfig(keys=[], auto_suggest=True)

    llm_config = None
    if llm_scorer:
        llm_config = LLMScorerConfig(enabled=True)

    return GoldenMatchConfig(
        matchkeys=matchkeys,
        blocking=blocking_config,
        llm_scorer=llm_config,
        backend=backend,
    )


def _extract_stats(result: dict) -> dict:
    """Compute stats from pipeline result."""
    clusters = result.get("clusters", {})
    golden = result.get("golden")
    dupes = result.get("dupes")
    unique = result.get("unique")

    # total_records counts *input* rows, not output tables. Every source
    # record lives in exactly one of:
    #   - dupes:  rows that are members of a 2+ cluster
    #   - unique: rows that did not match anything
    # golden is a derived rollup (one canonical record per multi-member
    # cluster), not another row population. Including it here double-counted
    # every cluster and made total_records > df.height whenever any
    # duplicates were found.
    total_records = 0
    if dupes is not None:
        total_records += dupes.height
    if unique is not None:
        total_records += unique.height

    # Defensive: if a pipeline path ever produces a golden-only result with
    # dupes/unique elided, total_records would silently be 0 and match_rate
    # a meaningless 0/0. The standard pipeline always materializes dupes
    # and unique when it materializes golden, so ANY golden-present /
    # dupes-absent / unique-absent shape is a contract violation and deserves
    # the warning — including an empty golden, since that still means a
    # refactor started producing golden without dupes/unique.
    if golden is not None and dupes is None and unique is None:
        logger.warning(
            "Stats aggregation received golden (%d rows) with no dupes/unique "
            "tables — total_records will be 0. This shape is not produced by "
            "the standard pipeline; if you hit this, the pipeline output "
            "contract has changed and _extract_stats needs updating.",
            golden.height,
        )

    total_clusters = sum(1 for c in clusters.values() if c.get("size", 0) > 1)
    matched_records = sum(c.get("size", 0) for c in clusters.values() if c.get("size", 0) > 1)
    match_rate = matched_records / total_records if total_records > 0 else 0.0

    return {
        "total_records": total_records,
        "total_clusters": total_clusters,
        "matched_records": matched_records,
        "match_rate": match_rate,
    }


# ── Learning Memory API ──


def get_memory(path: str | None = None) -> Any:
    """Open (or create) a Learning Memory store.

    Args:
        path: Path to the SQLite DB file. Defaults to ``.goldenmatch/memory.db``.

    Returns:
        A ``MemoryStore`` instance. Caller is responsible for ``close()``.
    """
    from goldenmatch.core.memory.store import MemoryStore
    return MemoryStore(backend="sqlite", path=path or ".goldenmatch/memory.db")


def add_correction(
    id_a: int,
    id_b: int,
    decision: str,
    *,
    source: str = "api",
    reason: str | None = None,
    dataset: str | None = None,
    matchkey_name: str | None = None,
    path: str | None = None,
) -> None:
    """Add a correction to the Learning Memory store.

    Trust is derived from ``source``: 1.0 for ``steward``/``boost``/``unmerge``,
    0.5 otherwise. Default ``source="api"`` is treated as a programmatic
    (agent-tier) actor; pass ``source="steward"`` explicitly for human trust.

    Hashes are written empty — apply_corrections handles empty-hash entries
    via the row-ID-presence path.
    """
    import uuid
    from datetime import datetime

    from goldenmatch.core.memory.store import Correction, trust_for_source
    trust = trust_for_source(source)
    store = get_memory(path)
    try:
        store.add_correction(Correction(
            id=str(uuid.uuid4()),
            id_a=id_a, id_b=id_b,
            decision=decision, source=source, trust=trust,
            field_hash="", record_hash="",
            original_score=0.0,
            matchkey_name=matchkey_name,
            reason=reason,
            dataset=dataset,
            created_at=datetime.now(),
        ))
    finally:
        store.close()


def learn(
    matchkey_name: str | None = None,
    path: str | None = None,
) -> list:
    """Force a learning pass over stored corrections.

    Args:
        matchkey_name: Optional filter; only learn for this matchkey.
        path: Path to the memory DB file.

    Returns:
        List of ``LearnedAdjustment`` objects produced this pass.
    """
    from goldenmatch.core.memory.learner import MemoryLearner
    store = get_memory(path)
    try:
        learner = MemoryLearner(store)
        return learner.learn(matchkey_name=matchkey_name)
    finally:
        store.close()


def memory_stats(path: str | None = None) -> dict:
    """Return summary stats about the memory store.

    Returns a dict with ``count``, ``last_learn_time``, and ``adjustments``.
    """
    store = get_memory(path)
    try:
        return {
            "count": store.count_corrections(),
            "last_learn_time": store.last_learn_time(),
            "adjustments": [a.__dict__ for a in store.get_all_adjustments()],
        }
    finally:
        store.close()
