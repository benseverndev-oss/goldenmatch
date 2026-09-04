"""MatchEngine — shared foundation for TUI and preview mode.

Wraps the existing pipeline modules into a clean API with sample
extraction, scored-pairs caching, and threshold re-clustering.
No Textual dependency — pure Python + Polars.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from goldenmatch.core.frame import to_frame as _to_frame


@dataclass
class EngineStats:
    total_records: int
    total_clusters: int
    singleton_count: int
    match_rate: float
    cluster_sizes: list[int]
    avg_cluster_size: float
    max_cluster_size: int
    oversized_count: int
    hit_rate: float | None = None
    avg_score: float | None = None
    llm_cost: dict | None = None


@dataclass
class EngineResult:
    clusters: dict[int, dict]
    # `Any`, not `pl.DataFrame`: since the pipeline delegation these arrive as
    # pa.Table on the arrow lane (the default install has no polars at all).
    # `from __future__ import annotations` keeps these unevaluated, so the old
    # names never actually forced an import -- but they told the reader the
    # wrong thing, which is how the polars assumptions spread.
    golden: Any | None
    unique: Any | None
    dupes: Any | None
    quarantine: Any | None
    matched: Any | None
    unmatched: Any | None
    scored_pairs: list[tuple[int, int, float]]
    stats: EngineStats
    # Trained Fellegi-Sunter models keyed by probabilistic matchkey name, for
    # FS-native explainability (match-weight waterfall). None when no
    # probabilistic matchkey ran.
    em_results: dict[str, Any] | None = None


@dataclass
class ControllerTelemetry:
    """v1.7-v1.12 AutoConfigController output, captured by ``auto_configure``.

    Loosely typed (Any) to dodge import cycles — the consumer
    (``tabs/controller_tab.py``) uses ``getattr`` on the inner sub-profiles.
    Populated only when the user actually triggered auto-config from the TUI;
    None on every cold start and after manual ConfigTab edits.
    """
    profile: Any = None          # ComplexityProfile
    history: Any = None          # RunHistory
    committed_config: Any = None # GoldenMatchConfig the controller committed
    source: str = "auto_configure"
    recorded_at: str | None = None
    column_priors: dict[str, Any] = field(default_factory=dict)


class MatchEngine:
    """Wraps the pipeline into a clean API for the TUI and preview mode."""

    def __init__(self, files: list[Path | str]):
        self._files = [Path(f) for f in files]
        self._data: Any | None = None
        self._profile: dict | None = None
        self._last_result: EngineResult | None = None
        self._last_telemetry: ControllerTelemetry | None = None
        self._load()

    def _load(self) -> None:
        from goldenmatch.core.io_arrow import read_files_arrow
        from goldenmatch.core.profiler import profile_dataframe

        # Arrow ingest. This used `pl.concat` + `with_row_index`, which made
        # every command that builds a MatchEngine (`demo`, `lineage`) raise
        # ImportError on a default install, where polars is absent.
        combined = read_files_arrow(
            [(f, f.stem) for f in self._files],
            source_column="__source__",
            row_id_column="__row_id__",
        )
        self._data = combined
        # Profile without internal columns. Column NAMES come from the seam:
        # `pa.Table.columns` returns arrays, not names -- reading it like the
        # polars attribute is a silent wrong answer rather than an error.
        _f = _to_frame(combined)
        profile_cols = [c for c in _f.columns if not c.startswith("__")]
        self._profile = profile_dataframe(_f.select(profile_cols).native)

    @classmethod
    def from_dataframe(cls, df: Any) -> MatchEngine:
        """Build an engine over an in-memory frame (no file loading).

        For callers like config-suggestion review (``core/suggest``) that
        already hold the data and only need ``_run_pipeline``/``run_full``.
        Mirrors every instance field ``__init__`` assigns so it can't break
        silently if ``__init__`` evolves -- ``_data`` is the passed frame,
        the rest take ``__init__``'s post-load defaults.
        Note: ``profile`` is None until a file-constructor run populates it;
        callers that need it should use the file constructor instead.
        """
        engine = object.__new__(cls)
        engine._files = []
        engine._data = df
        engine._profile = None  # not populated by from_dataframe
        engine._last_result = None
        engine._last_telemetry = None
        return engine

    @property
    def data(self) -> Any:
        """The loaded frame. A ``pa.Table`` from the file constructor; whatever
        the caller passed via :meth:`from_dataframe` otherwise. Read it through
        ``core.frame.to_frame`` rather than assuming polars attributes."""
        return self._data

    @property
    def profile(self) -> dict | None:
        return self._profile

    @property
    def columns(self) -> list[str]:
        # Seam, not `.columns`: on a pa.Table that attribute returns ARRAYS, so
        # the old expression would compare column data against a string prefix
        # and silently return nothing.
        return [c for c in _to_frame(self._data).columns if not c.startswith("__")]

    @property
    def row_count(self) -> int:
        return _to_frame(self._data).height

    def get_sample(self, n: int) -> Any:
        if n >= self.row_count:
            return self._data
        return _to_frame(self._data).head(n).native

    def _compute_stats(self, clusters: dict[int, dict], total_records: int) -> EngineStats:
        """Compute statistics from cluster results."""
        multi = [cid for cid, c in clusters.items() if c["size"] > 1]
        singletons = len(clusters) - len(multi)
        cluster_sizes = [clusters[cid]["size"] for cid in multi]
        oversized_count = sum(1 for cid in multi if clusters[cid]["oversized"])
        avg_size = sum(cluster_sizes) / len(cluster_sizes) if cluster_sizes else 0.0
        max_size = max(cluster_sizes) if cluster_sizes else 0
        matched_records = sum(cluster_sizes)
        match_rate = matched_records / total_records if total_records > 0 else 0.0

        return EngineStats(
            total_records=total_records,
            total_clusters=len(multi),
            singleton_count=singletons,
            match_rate=match_rate,
            cluster_sizes=cluster_sizes,
            avg_cluster_size=avg_size,
            max_cluster_size=max_size,
            oversized_count=oversized_count,
        )

    def _run_pipeline(self, df: Any, config) -> EngineResult:
        """Delegate to the real dedupe pipeline and adapt its result.

        This used to be a ~200-line REIMPLEMENTATION of ``run_dedupe`` -- its own
        docstring claimed the two stayed in step -- and that copy is why `demo`
        and `lineage` raised ImportError on a default install: it drove the
        polars LazyFrame API (``df.lazy()`` / ``.collect()``) while the shipped
        pipeline had moved to an eager arrow path behind ``_eager_stages_done``.
        A second implementation inherits none of the first's fixes, so it was
        deleted rather than ported -- this method just calls the real pipeline
        now, below, which is what makes that whole class of drift impossible
        going forward.

        ``run_dedupe_df`` takes ``pl.DataFrame | pa.Table | Frame`` through the
        frame seam, so both lanes work. The FS models the TUI's match-weight
        waterfall needs come back through the ``_em_results`` out-param, which is
        the one thing this delegation needed that the pipeline did not already
        expose.
        """
        from goldenmatch.core.pairs import materialize_scored_pairs
        from goldenmatch.core.pipeline import run_dedupe_df

        em_results: dict[str, Any] = {}
        result = run_dedupe_df(
            df,
            config,
            output_golden=True,
            output_clusters=True,
            output_dupes=True,
            output_unique=True,
            _em_results=em_results,
        )

        clusters = result.get("clusters") or {}
        # Via the helper, not `result["scored_pairs"]`: on the FS path that key
        # is None and an Arrow table is the backing (#2417).
        scored_pairs = materialize_scored_pairs(result) or []

        stats = self._compute_stats(clusters, _to_frame(df).height)
        llm_cost = result.get("llm_cost")
        if llm_cost:
            stats.llm_cost = llm_cost

        return EngineResult(
            clusters=clusters,
            golden=result.get("golden"),
            unique=result.get("unique"),
            dupes=result.get("dupes"),
            quarantine=result.get("quarantine"),
            matched=None,
            unmatched=None,
            scored_pairs=scored_pairs,
            stats=stats,
            em_results=em_results or None,
        )


    def auto_configure(self, domain: str | None = None) -> tuple[Any, ControllerTelemetry]:
        """Run AutoConfigController on the loaded data, capture telemetry.

        Returns ``(committed_config, telemetry)``. The committed config is the
        same shape ``ConfigTab.set_config`` accepts, so callers can apply it
        directly. ``telemetry`` is stashed on the engine for later inspection
        by the Controller tab.

        Mirrors the web router's ``_LAST_CONTROLLER_RUN`` capture (see
        ``web/routers/autoconfig.py``) so we surface the same stop_reason /
        decisions / NE the workbench shows.
        """
        # Lazy imports: ``auto_configure_df`` pulls in the policy + indicator
        # graph (heavy). Keeping the import here means ``MatchEngine.__init__``
        # stays cheap for TUI sessions that never trigger Ctrl+A.
        from datetime import UTC, datetime

        from goldenmatch.config.schemas import DomainConfig
        from goldenmatch.core.autoconfig import (
            _LAST_CONTROLLER_RUN,
            auto_configure_df,
        )

        # Profile/extract priors before the controller wipes the ContextVar
        # state — these come from the eager indicator pass and aren't on
        # ComplexityProfile.data.column_priors until the controller publishes.
        domain_cfg = DomainConfig(enabled=True, mode=domain) if domain else None
        config = auto_configure_df(
            self._data,
            allow_remote_assets=False,
            domain_config=domain_cfg,
        )
        ctrl_state = _LAST_CONTROLLER_RUN.get()
        profile = ctrl_state[0] if ctrl_state else None
        history = ctrl_state[1] if ctrl_state else None

        # Lift column_priors off DataProfile so the tab can render without
        # walking the frozen dataclass tree at render time.
        priors: dict[str, Any] = {}
        if profile is not None:
            data_profile = getattr(profile, "data", None)
            cp = getattr(data_profile, "column_priors", None) or {}
            for col, p in cp.items():
                priors[col] = {
                    "identity_score": float(getattr(p, "identity_score", 0.0)),
                    "corruption_score": float(getattr(p, "corruption_score", 0.0)),
                }

        telemetry = ControllerTelemetry(
            profile=profile,
            history=history,
            committed_config=config,
            source="auto_configure",
            recorded_at=datetime.now(UTC).isoformat(),
            column_priors=priors,
        )
        self._last_telemetry = telemetry
        return config, telemetry

    @property
    def last_telemetry(self) -> ControllerTelemetry | None:
        """Most recent controller telemetry from ``auto_configure``, if any."""
        return self._last_telemetry

    def run_sample(self, config, sample_size: int = 1000) -> EngineResult:
        """Run the pipeline on a sample of the data."""
        sample_df = self.get_sample(sample_size)
        result = self._run_pipeline(sample_df, config)
        self._last_result = result
        return result

    def run_full(self, config) -> EngineResult:
        """Run the pipeline on the full dataset."""
        result = self._run_pipeline(self._data, config)
        self._last_result = result
        return result

    def unmerge_record(self, record_id: int, threshold: float = 0.0) -> EngineResult | None:
        """Remove a record from its cluster and return updated results."""
        if self._last_result is None:
            return None

        from goldenmatch.core.cluster import unmerge_record

        clusters = unmerge_record(
            record_id, self._last_result.clusters, threshold,
            scored_pairs=self._last_result.scored_pairs,
        )
        stats = self._compute_stats(clusters, _to_frame(self._data).height)

        self._last_result = EngineResult(
            clusters=clusters,
            golden=self._last_result.golden,
            unique=self._last_result.unique,
            dupes=self._last_result.dupes,
            quarantine=self._last_result.quarantine,
            matched=self._last_result.matched,
            unmatched=self._last_result.unmatched,
            scored_pairs=self._last_result.scored_pairs,
            stats=stats,
            em_results=self._last_result.em_results,
        )
        return self._last_result

    def unmerge_cluster(self, cluster_id: int) -> EngineResult | None:
        """Shatter a cluster into singletons and return updated results."""
        if self._last_result is None:
            return None

        from goldenmatch.core.cluster import unmerge_cluster

        clusters = unmerge_cluster(cluster_id, self._last_result.clusters)
        stats = self._compute_stats(clusters, _to_frame(self._data).height)

        self._last_result = EngineResult(
            clusters=clusters,
            golden=self._last_result.golden,
            unique=self._last_result.unique,
            dupes=self._last_result.dupes,
            quarantine=self._last_result.quarantine,
            matched=self._last_result.matched,
            unmatched=self._last_result.unmatched,
            scored_pairs=self._last_result.scored_pairs,
            stats=stats,
            em_results=self._last_result.em_results,
        )
        return self._last_result

    def match_one(self, record: dict, config) -> list[tuple[int, float]]:
        """Match a single record against the loaded dataset.

        Uses brute-force scoring against the full dataset. For ANN-accelerated
        matching, use core.match_one directly with a pre-built ANNBlocker.
        """
        from goldenmatch.core.match_one import match_one

        matchkeys = config.get_matchkeys()
        results = []
        for mk in matchkeys:
            if mk.type == "weighted":
                matches = match_one(record, self._data, mk)
                results.extend(matches)
        # Deduplicate by row_id, keep highest score
        best: dict[int, float] = {}
        for row_id, score in results:
            if row_id not in best or score > best[row_id]:
                best[row_id] = score
        return sorted(best.items(), key=lambda x: x[1], reverse=True)

    def recluster_at_threshold(self, threshold: float) -> EngineStats:
        """Re-cluster cached scored pairs at a new threshold. No re-scoring."""
        if self._last_result is None:
            raise RuntimeError("No previous run exists. Call run_sample or run_full first.")

        from goldenmatch.core.cluster import build_clusters

        filtered_pairs = [
            (a, b, s) for a, b, s in self._last_result.scored_pairs if s >= threshold
        ]

        # Gather all IDs from the last result's clusters
        all_ids = []
        for cluster_info in self._last_result.clusters.values():
            all_ids.extend(cluster_info["members"])
        all_ids = sorted(set(all_ids))

        clusters = build_clusters(filtered_pairs, all_ids)
        return self._compute_stats(clusters, len(all_ids))
