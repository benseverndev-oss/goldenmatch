"""Real-producer integration tests — these prove the adapters against the ACTUAL
GoldenMatch / GoldenCheck / GoldenFlow / GoldenPipe output shapes.

They run in CI where the [match,check,flow,pipe] extras are installed (see the
goldenanalysis lane in .github/workflows/ci.yml) and SKIP on a bare local install.
A producer-side shape change breaks them HERE rather than silently. Do NOT run
these locally on the dev box (importing the suite packages risks the documented
polars/torch hangs + zombie-process starvation).
"""

from __future__ import annotations

import importlib.util

import goldenanalysis as ga
import polars as pl
import pytest
from goldenanalysis.adapters.check import CheckArtifactAdapter
from goldenanalysis.adapters.flow import FlowArtifactAdapter


def _requires(pkg: str) -> pytest.MarkDecorator:
    return pytest.mark.skipif(
        importlib.util.find_spec(pkg) is None, reason=f"needs goldenanalysis extra providing {pkg}"
    )


requires_goldenmatch = _requires("goldenmatch")
requires_goldencheck = _requires("goldencheck")
requires_goldenflow = _requires("goldenflow")
requires_goldenpipe = _requires("goldenpipe")


def _people_df() -> pl.DataFrame:
    # Small dedupe fixture; surnames spread across distinct soundex codes so
    # blocking makes several small blocks (no mega-block hang). 4 duplicate pairs.
    return pl.DataFrame(
        {
            "first_name": [
                "John", "Jon", "Mary", "Mari", "Robert", "Bob",
                "Susan", "Sue", "Peter", "Linda", "Karl", "Omar",
            ],
            "last_name": [
                "Smith", "Smith", "Jones", "Jones", "Brown", "Brown",
                "Davis", "Davis", "Wilson", "Taylor", "Klein", "Hassan",
            ],
            "email": [
                "j@x.com", "j@x.com", "m@x.com", "m@x.com", "r@x.com", "r@x.com",
                "s@x.com", "s@x.com", "p@x.com", "l@x.com", "k@x.com", "o@x.com",
            ],
        }
    )


@requires_goldenmatch
def test_analyze_match_over_real_dedupe_result() -> None:
    import goldenmatch
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    config = GoldenMatchConfig(
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["last_name"], transforms=["soundex"])],
        ),
        matchkeys=[
            MatchkeyConfig(
                name="identity",
                type="weighted",
                threshold=0.7,
                fields=[
                    MatchkeyField(field="first_name", scorer="jaro_winkler", weight=1.0),
                    MatchkeyField(field="last_name", scorer="jaro_winkler", weight=1.0),
                    MatchkeyField(field="email", scorer="jaro_winkler", weight=0.8),
                ],
            )
        ],
    )
    result = goldenmatch.dedupe_df(_people_df(), config=config)
    report = ga.analyze_match(result, dataset="people")

    keys = {m.key: m.value for m in report.metrics}
    assert keys["cluster.count"] >= 1
    assert keys["match.pair_count"] >= 0
    assert set(report.analyzers_run) == {"match.rates", "cluster.distribution"}
    # The report round-trips to markdown.
    assert "cluster.count" in report.to_markdown()


@requires_goldencheck
@requires_goldenflow
def test_quality_rollup_over_real_scan_and_transform() -> None:
    import goldencheck
    import goldenflow

    messy = pl.DataFrame(
        {
            "name": ["  Alice ", "BOB", None, "carol", "Dave  "],
            "email": ["a@x.com", "B@X.COM", "c@x.com", None, "d@x.com"],
        }
    )
    findings, profile = goldencheck.scan_dataframe(messy)
    transform = goldenflow.transform_df(messy)

    check_inp = CheckArtifactAdapter().from_scan(findings, profile, dataset="messy")
    flow_inp = FlowArtifactAdapter().load(transform, dataset="messy")
    # Merge both producers' artifacts into one input for quality.rollup.
    merged = check_inp.model_copy(update={"artifacts": {**check_inp.artifacts, **flow_inp.artifacts}})

    from goldenanalysis.analyzers.quality_rollup import QualityRollupAnalyzer

    metrics = {m.key for m in QualityRollupAnalyzer().run(merged).metrics}
    assert "quality.findings_total" in metrics
    assert "flow.rules_fired" in metrics


@requires_goldenpipe
def test_analyze_pipeline_over_real_pipe_result(tmp_path) -> None:
    import goldenpipe

    src = tmp_path / "people.csv"
    _people_df().write_csv(src)
    result = goldenpipe.run(str(src))
    report = ga.analyze_pipeline(result)
    # At least the producers whose artifacts are present should have run.
    assert len(report.analyzers_run) >= 1
