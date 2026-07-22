"""TS-parity CLI commands: `score` and `info`.

- score: score similarity between two strings (wraps score_strings).
- info: package version + available scorers/strategies/blocking/transforms.
"""
from __future__ import annotations

import re

from goldenmatch.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


def test_score_default_scorer():
    result = runner.invoke(app, ["score", "John Smith", "Jon Smith"])
    assert result.exit_code == 0
    # `<scorer>: <0.xxxx>` — default scorer is jaro_winkler.
    assert re.search(r"jaro_winkler: \d\.\d{4}", result.stdout)


def test_score_named_scorer():
    result = runner.invoke(app, ["score", "apple", "aple", "--scorer", "levenshtein"])
    assert result.exit_code == 0
    assert result.stdout.startswith("levenshtein: ")
    # Score parses as a float in [0, 1].
    value = float(result.stdout.split(":", 1)[1].strip())
    assert 0.0 <= value <= 1.0


def test_score_exact_identical_is_one():
    result = runner.invoke(app, ["score", "abc", "abc", "-s", "exact"])
    assert result.exit_code == 0
    assert "1.0000" in result.stdout


def test_info_lists_surfaces():
    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0
    from goldenmatch import __version__

    assert f"GoldenMatch v{__version__}" in result.stdout
    for line in ("Scorers:", "Strategies:", "Blocking:", "Transforms:"):
        assert line in result.stdout
    # Sourced from the real allow-lists, so representative names appear.
    assert "jaro_winkler" in result.stdout
    assert "static" in result.stdout


def test_info_sources_from_valid_scorers():
    """The scorer line must be the actual VALID_SCORERS set (drift-free)."""
    from goldenmatch.config.schemas import VALID_SCORERS

    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0
    scorer_line = next(
        line for line in result.stdout.splitlines() if line.startswith("Scorers:")
    )
    listed = {s.strip() for s in scorer_line.split(":", 1)[1].split(",")}
    assert listed == set(VALID_SCORERS)
