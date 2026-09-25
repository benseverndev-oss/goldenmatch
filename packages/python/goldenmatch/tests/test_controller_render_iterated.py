"""The controller panel and tab render an iterated, RED run.

Their decision-trace paths used to be covered incidentally: tests auto-configured
small sample files, which read RED on two small-data artifacts (#2988), so the
controller iterated and recorded rule decisions. With those artifacts fixed the
samples commit GREEN at v0 and record none. These tests build that history
directly, so the RED/iterated rendering is covered on purpose.
"""

import io

import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.autoconfig_history import HistoryEntry, PolicyDecision, RunHistory
from goldenmatch.core.complexity_profile import (
    ClusterProfile,
    ComplexityProfile,
    ScoringProfile,
    StopReason,
)


def _iterated_red_run():
    config = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="fuzzy_name",
                type="weighted",
                threshold=0.85,
                fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
            )
        ],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["name"])]),
    )
    # Nothing admitted and nothing clustered: RED that the #2988 override leaves alone.
    profile = ComplexityProfile(
        scoring=ScoringProfile(n_pairs_scored=0, candidates_compared=40, mass_above_threshold=0.0),
        cluster=ClusterProfile(n_clusters=20, cluster_size_max=1, cluster_size_p50=1),
    )
    entries = [
        HistoryEntry(
            iteration=i,
            config=config,
            profile=profile,
            decision=PolicyDecision(
                rule_name=f"rule_step_{i}",
                rationale=f"iteration {i}: nothing reached the threshold, lowering it",
                config_diff={"matchkeys[0].threshold": 0.85 - 0.05 * i},
            ),
            error=None,
            wall_clock_ms=10 + i,
        )
        for i in range(3)
    ]
    history = RunHistory(entries=entries, stop_reason=StopReason.BUDGET_ITERATIONS)
    return profile, history, config


def test_cli_panel_renders_red_iterated_run_with_decision_trace():
    from goldenmatch.cli._controller_render import render_controller_panel
    from rich.console import Console

    profile, history, config = _iterated_red_run()
    assert profile.health().value == "red"
    buf = io.StringIO()
    Console(file=buf, width=200, color_system=None).print(
        render_controller_panel(profile=profile, history=history, committed_config=config, verbose=True)
    )
    out = buf.getvalue()
    assert "health · red" in out
    for i in range(3):
        assert f"rule_step_{i}" in out


@pytest.mark.asyncio
async def test_tui_tab_lists_the_decisions_of_an_iterated_run(tmp_path):
    from goldenmatch.tui.app import GoldenMatchApp
    from goldenmatch.tui.engine import ControllerTelemetry
    from goldenmatch.tui.tabs.controller_tab import ControllerTab
    from textual.widgets import DataTable

    csv = tmp_path / "people.csv"
    csv.write_text("name,email\nAnn Lee,ann@x.com\nBo Chan,bo@x.com\n", encoding="utf-8")
    profile, history, config = _iterated_red_run()
    app = GoldenMatchApp(files=[str(csv)])
    async with app.run_test() as pilot:
        await pilot.pause()
        tab = app.query_one(ControllerTab)
        tab.update_telemetry(
            ControllerTelemetry(profile=profile, history=history, committed_config=config)
        )
        await pilot.pause()
        table = app.query_one("#decisions-table", DataTable)
        assert table.display is True
        assert table.row_count == 3
