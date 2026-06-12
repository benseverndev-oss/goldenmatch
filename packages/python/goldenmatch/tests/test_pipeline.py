"""Tests for the pipeline orchestrator."""

from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenFieldRule,
    GoldenMatchConfig,
    GoldenRulesConfig,
    MatchkeyConfig,
    MatchkeyField,
    OutputConfig,
)
from goldenmatch.core.pipeline import _add_row_ids, run_dedupe, run_match


class TestAddRowIds:
    """#844: ``_add_row_ids`` must respect a pre-existing ``__row_id__``.

    The auto-config v0 sample pipeline ran ``_add_row_ids`` on a 100M input
    that already carried a global ``__row_id__`` (load-bearing for distributed
    scoring — re-synthesizing per partition collides across partitions). The
    unconditional ``with_row_index("__row_id__")`` raised polars
    ``DuplicateError``, failing every auto-config iteration -> RED config.
    """

    def test_reuses_existing_row_id(self):
        import polars as pl

        lf = pl.LazyFrame({"__row_id__": [10, 20, 30], "name": ["a", "b", "c"]})
        out = _add_row_ids(lf).collect()
        # Reused, not re-synthesized to 0..n-1 (would-be DuplicateError before).
        assert out["__row_id__"].to_list() == [10, 20, 30]
        assert out["__row_id__"].dtype == pl.Int64

    def test_reuses_existing_row_id_with_offset(self):
        import polars as pl

        lf = pl.LazyFrame({"__row_id__": [0, 1, 2], "name": ["a", "b", "c"]})
        out = _add_row_ids(lf, offset=100).collect()
        assert out["__row_id__"].to_list() == [100, 101, 102]

    def test_synthesizes_when_absent(self):
        import polars as pl

        lf = pl.LazyFrame({"name": ["a", "b", "c"]})
        out = _add_row_ids(lf).collect()
        assert out["__row_id__"].to_list() == [0, 1, 2]
        assert out["__row_id__"].dtype == pl.Int64


class TestRunDedupe:
    def test_exact_dedupe_single_file(self, sample_csv, tmp_path):
        cfg = GoldenMatchConfig(
            matchkeys=[
                MatchkeyConfig(
                    name="email_key",
                    fields=[MatchkeyField(column="email", transforms=["lowercase", "strip"])],
                    comparison="exact",
                )
            ],
            output=OutputConfig(format="csv", directory=str(tmp_path), run_name="test"),
            golden_rules=GoldenRulesConfig(
                default=GoldenFieldRule(strategy="most_complete"),
            ),
        )
        results = run_dedupe(
            files=[(sample_csv, "test_source")],
            config=cfg,
            output_golden=True,
            output_clusters=True,
            output_report=True,
        )
        assert "clusters" in results
        assert "golden" in results
        assert "report" in results
        assert results["report"]["total_records"] == 5
        multi = [c for c in results["clusters"].values() if c["size"] > 1]
        assert len(multi) >= 1

    def test_dedupe_across_files_only(self, sample_csv, sample_csv_b, tmp_path):
        cfg = GoldenMatchConfig(
            matchkeys=[
                MatchkeyConfig(
                    name="email_key",
                    fields=[MatchkeyField(column="email", transforms=["lowercase", "strip"])],
                    comparison="exact",
                )
            ],
            output=OutputConfig(format="csv", directory=str(tmp_path), run_name="test_across"),
            golden_rules=GoldenRulesConfig(
                default=GoldenFieldRule(strategy="most_complete"),
            ),
        )
        results = run_dedupe(
            files=[(sample_csv, "source_a"), (sample_csv_b, "source_b")],
            config=cfg,
            output_golden=True,
            output_clusters=True,
            output_report=True,
            across_files_only=True,
        )
        assert "clusters" in results
        assert results["report"]["total_records"] == 8

    def test_dedupe_output_files_written(self, sample_csv, tmp_path):
        cfg = GoldenMatchConfig(
            matchkeys=[
                MatchkeyConfig(
                    name="email_key",
                    fields=[MatchkeyField(column="email", transforms=["lowercase", "strip"])],
                    comparison="exact",
                )
            ],
            output=OutputConfig(format="csv", directory=str(tmp_path), run_name="out_test"),
            golden_rules=GoldenRulesConfig(
                default=GoldenFieldRule(strategy="most_complete"),
            ),
        )
        run_dedupe(
            files=[(sample_csv, "test_source")],
            config=cfg,
            output_golden=True,
            output_clusters=True,
            output_dupes=True,
            output_unique=True,
            output_report=True,
        )
        # Check that output files exist
        assert (tmp_path / "out_test_golden.csv").exists()
        assert (tmp_path / "out_test_clusters.csv").exists()

    def test_file_path_prep_cache_seed_includes_height(
        self, sample_csv, sample_csv_b, monkeypatch
    ):
        """run_dedupe must seed the prep cache with (id, height), not bare id().

        Regression for a CI flake (`test_dedupe_across_files_only` asserting
        `5 == 8`): the file path defaulted to a bare ``id(combined_lf)`` cache
        seed. ``combined_lf`` is GC-eligible the moment run_dedupe returns, so
        CPython recycles its id() slot for the next call. A 1-file 5-row dedupe
        followed by a same-schema 2-file 8-row dedupe could then collide on the
        recycled id and serve the stale 5-row prepared frame. Including height
        in the seed disambiguates same-schema/different-row inputs.
        """
        import goldenmatch.core.pipeline as pipeline_mod

        captured = {}
        real = pipeline_mod._run_dedupe_pipeline

        def _spy(combined_lf, *args, **kwargs):
            captured["seed"] = kwargs.get("_prep_cache_seed")
            return real(combined_lf, *args, **kwargs)

        monkeypatch.setattr(pipeline_mod, "_run_dedupe_pipeline", _spy)

        cfg = GoldenMatchConfig(
            matchkeys=[
                MatchkeyConfig(
                    name="email_key",
                    fields=[MatchkeyField(column="email", transforms=["lowercase", "strip"])],
                    comparison="exact",
                )
            ],
            golden_rules=GoldenRulesConfig(default=GoldenFieldRule(strategy="most_complete")),
        )
        run_dedupe(
            files=[(sample_csv, "source_a"), (sample_csv_b, "source_b")],
            config=cfg,
        )
        seed = captured["seed"]
        assert isinstance(seed, tuple) and len(seed) == 2, (
            f"expected an (id, height) prep-cache seed, got {seed!r}"
        )
        # sample_csv has 5 rows, sample_csv_b has 3 -> combined height is 8.
        assert seed[1] == 8, f"prep-cache seed height should be 8, got {seed[1]}"


class TestRunMatch:
    def test_exact_match(self, sample_csv, sample_csv_b, tmp_path):
        cfg = GoldenMatchConfig(
            matchkeys=[
                MatchkeyConfig(
                    name="email_key",
                    fields=[MatchkeyField(column="email", transforms=["lowercase", "strip"])],
                    comparison="exact",
                )
            ],
            output=OutputConfig(format="csv", directory=str(tmp_path), run_name="test_match"),
        )
        results = run_match(
            target_file=(sample_csv, "targets"),
            reference_files=[(sample_csv_b, "reference")],
            config=cfg,
            output_matched=True,
            output_unmatched=True,
            output_report=True,
        )
        assert "matched" in results
        assert "unmatched" in results
        assert "report" in results
        assert results["report"]["total_targets"] == 5

    def test_match_best_mode(self, sample_csv, sample_csv_b, tmp_path):
        cfg = GoldenMatchConfig(
            matchkeys=[
                MatchkeyConfig(
                    name="email_key",
                    fields=[MatchkeyField(column="email", transforms=["lowercase", "strip"])],
                    comparison="exact",
                )
            ],
            output=OutputConfig(format="csv", directory=str(tmp_path), run_name="test_best"),
        )
        results = run_match(
            target_file=(sample_csv, "targets"),
            reference_files=[(sample_csv_b, "reference")],
            config=cfg,
            output_matched=True,
            output_unmatched=True,
            output_report=True,
            match_mode="best",
        )
        # Each target should have at most one match in best mode
        if results["matched"] is not None and len(results["matched"]) > 0:
            target_ids = results["matched"]["__target_row_id__"].to_list()
            assert len(target_ids) == len(set(target_ids))


class TestAdaptiveBlockingPipeline:
    def test_dedupe_with_adaptive_blocking(self, sample_csv, tmp_path):
        """Config with strategy='adaptive', auto_suggest=True, and empty keys to trigger auto-suggest."""
        cfg = GoldenMatchConfig(
            matchkeys=[
                MatchkeyConfig(
                    name="name_zip",
                    fields=[
                        MatchkeyField(column="first_name", transforms=["lowercase"]),
                        MatchkeyField(column="last_name", transforms=["lowercase"]),
                        MatchkeyField(column="zip"),
                    ],
                    comparison="exact",
                ),
            ],
            blocking=BlockingConfig(
                keys=[],
                strategy="adaptive",
                auto_suggest=True,
                max_block_size=50,
            ),
            output=OutputConfig(format="csv", directory=str(tmp_path), run_name="adaptive_test"),
            golden_rules=GoldenRulesConfig(
                default=GoldenFieldRule(strategy="most_complete"),
            ),
        )
        results = run_dedupe(
            files=[(sample_csv, "test_source")],
            config=cfg,
            output_golden=True,
            output_clusters=True,
            output_report=True,
        )
        assert "clusters" in results
        assert "golden" in results
        assert results["report"]["total_records"] == 5

    def test_dedupe_auto_suggest_does_not_override_user_keys(self, sample_csv, tmp_path):
        """When user provides blocking keys, auto_suggest logs but does not override them."""
        cfg = GoldenMatchConfig(
            matchkeys=[
                MatchkeyConfig(
                    name="email_key",
                    fields=[MatchkeyField(column="email", transforms=["lowercase", "strip"])],
                    comparison="exact",
                ),
            ],
            blocking=BlockingConfig(
                keys=[BlockingKeyConfig(fields=["zip"])],
                strategy="static",
                auto_suggest=True,
            ),
            output=OutputConfig(format="csv", directory=str(tmp_path), run_name="no_override_test"),
            golden_rules=GoldenRulesConfig(
                default=GoldenFieldRule(strategy="most_complete"),
            ),
        )
        # Capture the original key
        original_keys = list(cfg.blocking.keys)
        results = run_dedupe(
            files=[(sample_csv, "test_source")],
            config=cfg,
            output_golden=True,
            output_report=True,
        )
        # Keys should remain unchanged
        assert cfg.blocking.keys == original_keys
        assert "clusters" in results

    def test_dedupe_without_auto_suggest_unchanged(self, sample_csv, tmp_path):
        """When auto_suggest is False, behavior is unchanged."""
        cfg = GoldenMatchConfig(
            matchkeys=[
                MatchkeyConfig(
                    name="email_key",
                    fields=[MatchkeyField(column="email", transforms=["lowercase", "strip"])],
                    comparison="exact",
                ),
            ],
            output=OutputConfig(format="csv", directory=str(tmp_path), run_name="no_auto_test"),
            golden_rules=GoldenRulesConfig(
                default=GoldenFieldRule(strategy="most_complete"),
            ),
        )
        results = run_dedupe(
            files=[(sample_csv, "test_source")],
            config=cfg,
            output_golden=True,
            output_report=True,
        )
        assert "clusters" in results
        assert results["report"]["total_records"] == 5
