import polars as pl
import pytest
from goldenmatch.tui.engine import EngineResult, EngineStats


class TestEngineStats:
    def test_create(self):
        stats = EngineStats(
            total_records=1000,
            total_clusters=50,
            singleton_count=900,
            match_rate=0.05,
            cluster_sizes=[2, 3, 2, 5],
            avg_cluster_size=3.0,
            max_cluster_size=5,
            oversized_count=0,
        )
        assert stats.total_records == 1000
        assert stats.hit_rate is None

    def test_match_mode_stats(self):
        stats = EngineStats(
            total_records=500,
            total_clusters=0,
            singleton_count=0,
            match_rate=0.0,
            cluster_sizes=[],
            avg_cluster_size=0.0,
            max_cluster_size=0,
            oversized_count=0,
            hit_rate=0.7,
            avg_score=0.88,
        )
        assert stats.hit_rate == 0.7


class TestEngineResult:
    def test_create_dedupe(self):
        result = EngineResult(
            clusters={1: {"members": [0, 1], "size": 2, "oversized": False, "pair_scores": {}}},
            golden=None,
            unique=None,
            dupes=None,
            quarantine=None,
            matched=None,
            unmatched=None,
            scored_pairs=[(0, 1, 0.95)],
            stats=EngineStats(
                total_records=5, total_clusters=1, singleton_count=3,
                match_rate=0.2, cluster_sizes=[2], avg_cluster_size=2.0,
                max_cluster_size=2, oversized_count=0,
            ),
        )
        assert len(result.scored_pairs) == 1


from goldenmatch.tui.engine import MatchEngine


class TestMatchEngineLoad:
    def test_load_single_file(self, sample_csv):
        engine = MatchEngine([sample_csv])
        assert engine.row_count == 5
        assert "email" in engine.columns
        assert engine.profile is not None
        assert engine.profile["total_rows"] == 5

    def test_load_multiple_files(self, sample_csv, sample_csv_b):
        engine = MatchEngine([sample_csv, sample_csv_b])
        assert engine.row_count == 8

    def test_load_nonexistent_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            MatchEngine([tmp_path / "missing.csv"])

    def test_columns_property(self, sample_csv):
        engine = MatchEngine([sample_csv])
        cols = engine.columns
        assert "first_name" in cols
        assert "last_name" in cols
        # Internal columns should not appear
        assert "__source__" not in cols
        assert "__row_id__" not in cols

    def test_sample_extraction(self, sample_csv):
        engine = MatchEngine([sample_csv])
        sample = engine.get_sample(3)
        assert isinstance(sample, pl.DataFrame)
        assert sample.height == 3


from goldenmatch.config.schemas import (
    GoldenFieldRule,
    GoldenMatchConfig,
    GoldenRulesConfig,
    MatchkeyConfig,
    MatchkeyField,
    OutputConfig,
)


@pytest.fixture
def exact_email_config(tmp_path):
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="email_key",
                fields=[MatchkeyField(column="email", transforms=["lowercase"])],
                comparison="exact",
            )
        ],
        output=OutputConfig(format="csv", directory=str(tmp_path), run_name="test"),
        golden_rules=GoldenRulesConfig(
            default=GoldenFieldRule(strategy="most_complete"),
        ),
    )


class TestMatchEngineRunSample:
    def test_run_sample_dedupe(self, sample_csv, exact_email_config):
        engine = MatchEngine([sample_csv])
        result = engine.run_sample(exact_email_config, sample_size=5)
        assert isinstance(result, EngineResult)
        assert result.stats.total_records == 5
        assert result.stats.total_clusters >= 1
        assert len(result.scored_pairs) >= 1

    def test_run_sample_small(self, sample_csv, exact_email_config):
        engine = MatchEngine([sample_csv])
        result = engine.run_sample(exact_email_config, sample_size=3)
        assert result.stats.total_records == 3

    def test_scored_pairs_cached(self, sample_csv, exact_email_config):
        engine = MatchEngine([sample_csv])
        result = engine.run_sample(exact_email_config)
        assert engine._last_result is not None
        assert engine._last_result.scored_pairs == result.scored_pairs

    def test_golden_records_created(self, sample_csv, exact_email_config):
        engine = MatchEngine([sample_csv])
        result = engine.run_sample(exact_email_config, sample_size=5)
        # sample_csv has john@example.com twice, so golden should exist
        if result.stats.total_clusters > 0:
            assert result.golden is not None


class TestMatchEngineRecluster:
    def test_recluster_at_threshold(self, sample_csv, exact_email_config):
        engine = MatchEngine([sample_csv])
        engine.run_sample(exact_email_config)
        stats = engine.recluster_at_threshold(1.0)
        assert isinstance(stats, EngineStats)
        assert stats.total_records > 0

    def test_recluster_without_run_raises(self, sample_csv):
        engine = MatchEngine([sample_csv])
        with pytest.raises(RuntimeError):
            engine.recluster_at_threshold(0.8)


class TestMatchEngineRunFull:
    def test_run_full(self, sample_csv, exact_email_config):
        engine = MatchEngine([sample_csv])
        result = engine.run_full(exact_email_config)
        assert result.stats.total_records == 5


class TestEngineDomainExtraction:
    """Regression for #1300.

    ``MatchEngine._run_pipeline`` must materialize domain-derived columns (e.g.
    ``__title_key__``) that the config references from a matchkey, the same way
    ``core.pipeline._run_dedupe_pipeline`` does. Without the domain-extraction
    step, any MatchEngine run on a bibliographic/product config -- including the
    healer's ``review_config(verify=True)`` baseline + per-candidate re-runs --
    crashed with ``ColumnNotFoundError: __title_key__``.
    """

    @staticmethod
    def _biblio_config(tmp_path):
        from goldenmatch.config.schemas import DomainConfig

        return GoldenMatchConfig(
            domain=DomainConfig(enabled=True, mode="bibliographic", llm_validation=False),
            matchkeys=[
                MatchkeyConfig(
                    name="title_key",
                    fields=[MatchkeyField(column="__title_key__")],
                    comparison="exact",
                )
            ],
            output=OutputConfig(format="csv", directory=str(tmp_path), run_name="biblio"),
            golden_rules=GoldenRulesConfig(
                default=GoldenFieldRule(strategy="most_complete"),
            ),
        )

    @staticmethod
    def _biblio_df():
        return pl.DataFrame(
            {
                "title": [
                    "A Survey of Entity Resolution",
                    "A Survey of Entity Resolution",
                    "Deep Learning for Record Linkage",
                    "Scalable Blocking Techniques",
                ],
                "authors": ["Smith, J", "J. Smith", "Lee, K", "Brown, A"],
                "venue": ["VLDB", "VLDB", "SIGMOD", "ICDE"],
                "year": ["2020", "2020", "2021", "2019"],
            }
        )

    def test_run_pipeline_materializes_domain_derived_matchkey_column(self, tmp_path):
        from goldenmatch.tui.engine import MatchEngine

        # _run_pipeline expects __row_id__ on the frame (callers add it upstream;
        # the healer does df.with_row_index("__row_id__") before calling in).
        df = self._biblio_df().with_row_index("__row_id__").with_columns(
            pl.col("__row_id__").cast(pl.Int64)
        )
        config = self._biblio_config(tmp_path)
        engine = MatchEngine.from_dataframe(df)

        # Pre-fix this raised ColumnNotFoundError("__title_key__") because the
        # domain-extraction step that creates __title_key__ never ran.
        result = engine._run_pipeline(df, config)

        assert result is not None
        # The two identical-title rows share a __title_key__ -> exact match.
        assert result.clusters, "expected the duplicate-title rows to cluster"
