"""#2988: a correct run on a small file must not read RED.

Two small-data artifacts made a 12-record file with 4 obvious duplicate groups
log "auto-config committed best-effort RED config ... output may be
low-precision" while producing exactly the right clusters:

- `cluster_giant`'s small-frame branch (`size_max > 0.1 * n_rows`) called any
  duplicate pair "giant" below 20 rows.
- The scoring profile only sees the fuzzy scorer's pairs, so a run whose
  matches all came from exact matchkeys read `scoring_nothing_above_threshold`
  although it had clustered records.
"""

import csv
import dataclasses

from goldenmatch.core.complexity_profile import (
    ClusterProfile,
    ComplexityProfile,
    HealthVerdict,
    ScoringProfile,
)


def test_small_frame_giant_needs_a_real_size():
    three_of_twelve = ClusterProfile(n_clusters=7, cluster_size_max=3, cluster_size_p50=2)
    assert three_of_twelve.red_reason(n_rows=12) is None
    # A genuinely giant cluster on a small frame still reads RED.
    forty_of_sixty = ClusterProfile(n_clusters=5, cluster_size_max=40, cluster_size_p50=2)
    assert forty_of_sixty.red_reason(n_rows=60) == "cluster_giant"


def _profile(scoring: ScoringProfile, cluster_max: int) -> ComplexityProfile:
    base = ComplexityProfile()
    return dataclasses.replace(
        base,
        scoring=scoring,
        cluster=ClusterProfile(n_clusters=3, cluster_size_max=cluster_max, cluster_size_p50=1),
    )


def test_scoring_nothing_admitted_is_overruled_by_a_multi_member_cluster():
    blind = ScoringProfile(n_pairs_scored=0, candidates_compared=7, mass_above_threshold=0.0)
    assert blind.red_reason() == "scoring_nothing_above_threshold"
    # Something WAS admitted (by an exact matchkey): not RED on scoring's account.
    assert _profile(blind, cluster_max=3).scoring_health() == HealthVerdict.GREEN
    # Nothing clustered either: the RED stands.
    assert _profile(blind, cluster_max=1).scoring_health() == HealthVerdict.RED


def test_other_scoring_reds_are_not_overruled():
    unimodal = ScoringProfile(
        n_pairs_scored=5000, candidates_compared=9000, mass_above_threshold=1.0,
        dip_statistic=0.0,
    )
    assert unimodal.red_reason() == "scoring_unimodal"
    assert _profile(unimodal, cluster_max=3).scoring_health() == HealthVerdict.RED


def test_correct_small_run_does_not_warn_red(tmp_path, caplog):
    import goldenmatch as gm

    rows = [
        ("1", "Maria Gonzalez", "maria.gonzalez@example.com", "555-201-3344", "14 Oak Street", "62704"),
        ("2", "Maria Gonzales", "mgonzalez@example.com", "(555) 201-3344", "14 Oak St", "62704"),
        ("3", "María González", "maria.gonzalez@example.com", "5552013344", "14 Oak St.", "62704"),
        ("4", "James O'Neil", "jim.oneil@example.com", "555-883-1200", "902 Pine Avenue", "84065"),
        ("5", "Jim O'Neill", "jim.oneil@example.com", "555.883.1200", "902 Pine Ave", "84065"),
        ("6", "Priya Raman", "priya.raman@example.com", "555-410-7788", "3 Birch Road", "49116"),
        ("7", "Priya Ramen", "p.raman@example.com", "555-410-7788", "3 Birch Rd", "49116"),
        ("8", "Thomas Becker", "tbecker@example.com", "555-667-0192", "77 Elm Court", "97024"),
        ("9", "Aisha Bello", "aisha.bello@example.com", "555-339-2471", "510 Cedar Lane", "29601"),
        ("10", "Chen Wei", "chen.wei@example.com", "555-722-9031", "8 Maple Drive", "95361"),
        ("11", "Wei Chen", "chen.wei@example.com", "555-722-9031", "8 Maple Dr", "95361"),
        ("12", "Thomas Baker", "thomas.baker@example.com", "555-120-4455", "41 Walnut Way", "97024"),
    ]
    path = tmp_path / "customers.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "email", "phone", "address", "zip"])
        w.writerows(rows)
    with caplog.at_level("WARNING"):
        result = gm.dedupe(str(path))
    groups = {tuple(sorted(c["members"])) for c in result.clusters.values() if c["size"] > 1}
    assert groups == {(0, 1, 2), (3, 4), (5, 6), (9, 10)}
    assert "best-effort RED config" not in caplog.text


def test_llm_scorer_decoration_needs_llm_auto():
    """#3014: an API key alone enabled per-pair LLM scoring. `llm_auto` is the
    documented opt-in, and the decoration now runs only with it."""
    from unittest.mock import patch

    import pyarrow as pa
    from goldenmatch.core.autoconfig import auto_configure_df
    from goldenmatch.core.autoconfig_controller import AutoConfigController

    df = pa.table({
        "name": ["Ann Lee", "Ann Leigh", "Bo Chan", "Bo Chen", "Cy Diaz", "Di Eve"],
        "email": ["ann@x.com", "ann@x.com", "bo@x.com", "bo@x.com", "cy@x.com", "di@x.com"],
    })
    calls = []
    real = AutoConfigController._maybe_decorate_with_llm_scorer

    def spy(self, config, profile):
        calls.append(True)
        return real(self, config, profile)

    with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-fake"}), patch.object(
        AutoConfigController, "_maybe_decorate_with_llm_scorer", spy
    ):
        auto_configure_df(df, llm_auto=False)
        assert calls == []
        auto_configure_df(df, llm_auto=True)
        assert calls == [True]
