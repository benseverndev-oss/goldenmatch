import pytest
from datetime import timedelta
from goldenmatch.core.complexity_profile import (
    ComplexityProfile, DataProfile, BlockingProfile, ScoringProfile,
    ClusterProfile, MatchkeyProfile, DomainProfile, ProfileMeta, HealthVerdict,
)
from goldenmatch.core.autoconfig_history import (
    RunHistory, HistoryEntry, PolicyDecision, ErrorRecord,
)


def _profile(*, scoring: ScoringProfile | None = None,
             blocking: BlockingProfile | None = None,
             n_rows: int = 100) -> ComplexityProfile:
    return ComplexityProfile(
        data=DataProfile(n_rows=n_rows, n_cols=4,
                         column_types={"a": "text", "b": "id-like", "c": "text", "d": "date"}),
        blocking=blocking or BlockingProfile(
            keys_used=[["a"]], n_blocks=10, total_comparisons=500,
            reduction_ratio=0.95, block_sizes_p50=10, block_sizes_p95=15,
            block_sizes_p99=20, block_sizes_max=25,
            singleton_block_count=0, oversized_block_count=0,
        ),
        scoring=scoring or ScoringProfile(
            n_pairs_scored=500, score_histogram=[0]*15 + [100]*5,
            dip_statistic=0.05, mass_above_threshold=0.4, mass_in_borderline=0.05,
        ),
        cluster=ClusterProfile(
            n_clusters=20, cluster_size_p50=2, cluster_size_p99=5,
            cluster_size_max=8, transitivity_rate=0.95,
            edge_confidence_p50=0.85, edge_confidence_min=0.7,
        ),
    )


def _entry(iteration: int, config, profile, decision=None, error=None) -> HistoryEntry:
    return HistoryEntry(
        iteration=iteration, config=config, profile=profile,
        decision=decision, error=error, wall_clock_ms=10,
    )


def test_empty_history_has_no_oscillation():
    h = RunHistory()
    assert not h.is_oscillating()
    assert h.iteration == 0
    assert h.decisions == []
    assert h.errors == []


def test_iteration_property_counts_entries():
    h = RunHistory()
    h.entries.append(_entry(0, "a", _profile()))
    h.entries.append(_entry(1, "b", _profile()))
    assert h.iteration == 2


def test_decisions_property_filters_none():
    h = RunHistory()
    h.entries.append(_entry(0, "a", _profile(),
                            decision=PolicyDecision(rule_name="r1", rationale="x", config_diff={})))
    h.entries.append(_entry(1, "b", _profile(), decision=None))
    h.entries.append(_entry(2, "c", _profile(),
                            decision=PolicyDecision(rule_name="r2", rationale="y", config_diff={})))
    assert [d.rule_name for d in h.decisions] == ["r1", "r2"]


def test_errors_property_filters_none():
    h = RunHistory()
    err = ErrorRecord(exception_type="ValueError", traceback_summary="...")
    h.entries.append(_entry(0, "a", _profile(), error=err))
    h.entries.append(_entry(1, "b", _profile(), error=None))
    assert [e.exception_type for e in h.errors] == ["ValueError"]


def test_cheapest_healthy_returns_none_when_all_red():
    """Legacy test retained for documentation: v1.8 cheapest_healthy returned
    None for all-RED. As of v1.9 it is a deprecated alias for pick_committed(),
    which returns the best RED entry instead. This test now asserts the new
    behavior (non-None) and that a DeprecationWarning is emitted."""
    import warnings
    h = RunHistory()
    red = _profile(scoring=ScoringProfile(
        n_pairs_scored=0, score_histogram=[0]*20,
        mass_above_threshold=0.0, dip_statistic=0.001,
    ))
    h.entries.append(_entry(0, "x", red))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = h.cheapest_healthy()
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)
    # v1.9 behavior: returns the RED entry rather than None
    assert result is not None
    assert result.config == "x"


def test_cheapest_healthy_picks_highest_separation():
    h = RunHistory()
    weak = _profile(scoring=ScoringProfile(
        n_pairs_scored=500, score_histogram=[0]*15 + [100]*5,
        dip_statistic=0.05, mass_above_threshold=0.3, mass_in_borderline=0.25,
    ))
    strong = _profile(scoring=ScoringProfile(
        n_pairs_scored=500, score_histogram=[0]*15 + [100]*5,
        dip_statistic=0.05, mass_above_threshold=0.5, mass_in_borderline=0.05,
    ))
    h.entries.append(_entry(0, "weak", weak))
    h.entries.append(_entry(1, "strong", strong))
    best = h.pick_committed()
    assert best is not None
    assert best.config == "strong"


def test_cheapest_healthy_prefers_green_over_yellow():
    h = RunHistory()
    # Yellow profile (oversized cluster) but high separation
    yellow_cluster = ClusterProfile(
        n_clusters=10, cluster_size_p50=2, cluster_size_p99=5,
        cluster_size_max=8, transitivity_rate=0.95,
        oversized_cluster_count=1,  # makes cluster YELLOW
    )
    yellow_profile = ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=4,
                         column_types={"a": "text", "b": "id-like", "c": "text", "d": "date"}),
        blocking=BlockingProfile(
            keys_used=[["a"]], n_blocks=10, total_comparisons=500,
            reduction_ratio=0.95, block_sizes_p50=10, block_sizes_p95=15,
            block_sizes_p99=20, block_sizes_max=25,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=500, score_histogram=[0]*15 + [100]*5,
            dip_statistic=0.05, mass_above_threshold=0.7, mass_in_borderline=0.01,
        ),
        cluster=yellow_cluster,
    )
    green = _profile(scoring=ScoringProfile(
        n_pairs_scored=500, score_histogram=[0]*15 + [100]*5,
        dip_statistic=0.05, mass_above_threshold=0.4, mass_in_borderline=0.05,
    ))
    h.entries.append(_entry(0, "yellow", yellow_profile))
    h.entries.append(_entry(1, "green", green))
    # Even though yellow has higher mass_above_threshold, GREEN beats YELLOW in lex key.
    assert h.pick_committed().config == "green"


def test_cheapest_healthy_breaks_tie_on_iteration():
    """With identical health and separation, prefer earlier iteration (cheaper)."""
    h = RunHistory()
    same_profile = _profile()
    h.entries.append(_entry(0, "first", same_profile))
    h.entries.append(_entry(1, "second", same_profile))
    assert h.pick_committed().config == "first"


def test_oscillation_detected_after_two_repeats_in_window():
    h = RunHistory()
    cfg_hashes = ["a", "b", "a", "b"]
    for i, c in enumerate(cfg_hashes):
        h.entries.append(_entry(
            i, c, _profile(),
            decision=PolicyDecision(rule_name=f"r_{c}", rationale="x", config_diff={}),
        ))
    assert h.is_oscillating()


def test_oscillation_not_detected_with_distinct_configs():
    h = RunHistory()
    for i, c in enumerate(["a", "b", "c", "d"]):
        h.entries.append(_entry(
            i, c, _profile(),
            decision=PolicyDecision(rule_name=f"r_{c}", rationale="x", config_diff={}),
        ))
    assert not h.is_oscillating()


def test_oscillation_requires_window_of_4():
    h = RunHistory()
    h.entries.append(_entry(0, "a", _profile(),
                            decision=PolicyDecision(rule_name="r", rationale="x", config_diff={})))
    h.entries.append(_entry(1, "a", _profile(),
                            decision=PolicyDecision(rule_name="r", rationale="x", config_diff={})))
    # Only 2 entries; need window of 4
    assert not h.is_oscillating()


def test_profile_distance_to_prev_is_zero_for_identical():
    h = RunHistory()
    p = _profile()
    h.entries.append(_entry(0, "a", p))
    h.entries.append(_entry(1, "b", p))
    assert h.profile_distance_to_prev() == pytest.approx(0.0)


def test_profile_distance_to_prev_is_inf_for_short_history():
    h = RunHistory()
    h.entries.append(_entry(0, "a", _profile()))
    assert h.profile_distance_to_prev() == float("inf")


def test_profile_distance_to_prev_nonzero_for_different():
    h = RunHistory()
    a = _profile(blocking=BlockingProfile(
        keys_used=[["a"]], n_blocks=2, total_comparisons=50,
        reduction_ratio=0.5, block_sizes_p99=50,
    ))
    b = _profile(blocking=BlockingProfile(
        keys_used=[["a"]], n_blocks=20, total_comparisons=10,
        reduction_ratio=0.99, block_sizes_p99=5,
    ))
    h.entries.append(_entry(0, "a", a))
    h.entries.append(_entry(1, "b", b))
    assert h.profile_distance_to_prev() > 0.0


def test_prior_runs_default_empty_list():
    h = RunHistory()
    assert h.prior_runs == []
    # Not frozen — caller can mutate, but v1 leaves it empty
    h.prior_runs.append("v2-hook")
    assert h.prior_runs == ["v2-hook"]


# ============================================================
# pick_committed (added 2026-05-08)
# ============================================================

def _make_red_history_entry(iteration, mass_above, mass_borderline,
                             dip_statistic=0.05):
    """Helper: produce a HistoryEntry whose profile rolls up to RED via
    ScoringProfile (mass_above==0 OR dip<0.01 forces scoring RED)."""
    from goldenmatch.core.autoconfig_history import HistoryEntry
    from goldenmatch.core.complexity_profile import (
        ComplexityProfile, DataProfile, BlockingProfile, ScoringProfile,
        ClusterProfile, MatchkeyProfile, FieldStats,
    )
    return HistoryEntry(
        iteration=iteration,
        config=f"cfg_{iteration}",
        profile=ComplexityProfile(
            data=DataProfile(
                n_rows=100, n_cols=4,
                column_types={"a": "text", "b": "id-like",
                              "c": "text", "d": "date"},
            ),
            blocking=BlockingProfile(
                keys_used=[["a"]], n_blocks=10, total_comparisons=500,
                reduction_ratio=0.95, block_sizes_p99=20,
            ),
            scoring=ScoringProfile(
                n_pairs_scored=0, candidates_compared=500,
                mass_above_threshold=mass_above,
                mass_in_borderline=mass_borderline,
                dip_statistic=dip_statistic,
            ),
            cluster=ClusterProfile(transitivity_rate=0.95),
            matchkey=MatchkeyProfile(per_field={"a": FieldStats(0.5, 0.0, 10)}),
        ),
        decision=None, error=None, wall_clock_ms=10,
    )


def test_pick_committed_returns_red_when_no_green_or_yellow():
    """The headline new behavior: pick_committed returns the best RED entry
    when all entries are RED. cheapest_healthy() would return None here."""
    from goldenmatch.core.autoconfig_history import RunHistory
    from goldenmatch.core.complexity_profile import HealthVerdict
    h = RunHistory()
    h.entries.append(_make_red_history_entry(0, 0.0, 0.4))
    h.entries.append(_make_red_history_entry(1, 0.0, 0.1))
    assert h.entries[0].profile.health() == HealthVerdict.RED
    assert h.entries[1].profile.health() == HealthVerdict.RED
    best = h.pick_committed()
    assert best is not None
    assert best.iteration == 1
    assert best.config == "cfg_1"


def test_pick_committed_excludes_errored_entries():
    """Entries with error != None are filtered out before lex-key ranking."""
    from goldenmatch.core.autoconfig_history import (
        RunHistory, HistoryEntry, ErrorRecord,
    )
    from goldenmatch.core.complexity_profile import ComplexityProfile, DataProfile
    h = RunHistory()
    h.entries.append(HistoryEntry(
        iteration=0, config="errored",
        profile=ComplexityProfile(data=DataProfile(n_rows=0)),
        decision=None,
        error=ErrorRecord(exception_type="RuntimeError", traceback_summary="..."),
        wall_clock_ms=10,
    ))
    h.entries.append(_make_red_history_entry(1, 0.5, 0.1))
    h.entries[1] = h.entries[1].__class__(
        iteration=1, config="real_red",
        profile=h.entries[1].profile,
        decision=None, error=None, wall_clock_ms=10,
    )
    best = h.pick_committed()
    assert best is not None
    assert best.config == "real_red"


def test_pick_committed_returns_none_when_all_errored():
    """All entries errored -> pick_committed returns None.
    Controller falls back to v0 in this case."""
    from goldenmatch.core.autoconfig_history import (
        RunHistory, HistoryEntry, ErrorRecord,
    )
    from goldenmatch.core.complexity_profile import ComplexityProfile, DataProfile
    h = RunHistory()
    h.entries.append(HistoryEntry(
        iteration=0, config="x",
        profile=ComplexityProfile(data=DataProfile(n_rows=0)),
        decision=None,
        error=ErrorRecord(exception_type="RuntimeError", traceback_summary=""),
        wall_clock_ms=10,
    ))
    h.entries.append(HistoryEntry(
        iteration=1, config="y",
        profile=ComplexityProfile(data=DataProfile(n_rows=0)),
        decision=None,
        error=ErrorRecord(exception_type="ValueError", traceback_summary=""),
        wall_clock_ms=10,
    ))
    assert h.pick_committed() is None


def test_pick_committed_lex_key_orders_red_by_mass_separation():
    """Within RED tier, the entry with highest (mass_above - mass_borderline)
    wins. Use dip<0.01 to force RED while still varying mass values."""
    from goldenmatch.core.autoconfig_history import RunHistory
    h = RunHistory()
    h.entries.append(_make_red_history_entry(0, 0.4, 0.3, dip_statistic=0.001))  # sep=0.1
    h.entries.append(_make_red_history_entry(1, 0.6, 0.1, dip_statistic=0.001))  # sep=0.5
    h.entries.append(_make_red_history_entry(2, 0.5, 0.4, dip_statistic=0.001))  # sep=0.1
    best = h.pick_committed()
    assert best is not None
    assert best.iteration == 1


def test_pick_committed_empty_history_returns_none():
    """No entries -> None. Edge case at the start of run() before any iter."""
    from goldenmatch.core.autoconfig_history import RunHistory
    assert RunHistory().pick_committed() is None


def test_cheapest_healthy_emits_deprecation_warning_and_delegates():
    """cheapest_healthy() now emits DeprecationWarning and delegates to
    pick_committed(). Behavior change: returns RED entries that v1.8
    callers expected to be None."""
    import pytest
    from goldenmatch.core.autoconfig_history import RunHistory
    h = RunHistory()
    h.entries.append(_make_red_history_entry(0, 0.0, 0.0))
    with pytest.warns(DeprecationWarning, match=r"pick_committed"):
        result = h.cheapest_healthy()
    assert result is not None
    assert result.config == "cfg_0"


def test_cheapest_healthy_warning_message_calls_out_behavior_change():
    """The DeprecationWarning text mentions the behavior change explicitly."""
    import warnings
    from goldenmatch.core.autoconfig_history import RunHistory
    h = RunHistory()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        h.cheapest_healthy()
    assert len(caught) >= 1
    msg = str(caught[0].message)
    assert "pick_committed" in msg
    assert "RED" in msg or "behavior" in msg.lower()


def test_runhistory_stop_reason_default_is_none():
    """Default stop_reason is None; controller sets it at each break point."""
    from goldenmatch.core.autoconfig_history import RunHistory
    h = RunHistory()
    assert h.stop_reason is None


def test_runhistory_stop_reason_can_be_set():
    """stop_reason is mutable (the controller writes to it)."""
    from goldenmatch.core.autoconfig_history import RunHistory
    from goldenmatch.core.complexity_profile import StopReason
    h = RunHistory()
    h.stop_reason = StopReason.BUDGET_ITERATIONS
    assert h.stop_reason == StopReason.BUDGET_ITERATIONS


# ============================================================
# precision_collapse_floor (v1.9 amendment, 2026-05-08)
# ============================================================

def _make_red_entry_with_mass(iteration: int, mass_above: float, mass_borderline: float) -> "HistoryEntry":
    """Build a HistoryEntry that is definitely RED (via dip_statistic < 0.005
    with n_pairs_scored > 0) with the given mass values.

    Used for precision_collapse_floor tests where we need RED entries with
    varying mass_above values (including > 0.9 for the collapse pathology).
    """
    from goldenmatch.core.complexity_profile import (
        ComplexityProfile, DataProfile, BlockingProfile, ScoringProfile,
        ClusterProfile, MatchkeyProfile, FieldStats,
    )
    return HistoryEntry(
        iteration=iteration,
        config=f"cfg_{iteration}",
        profile=ComplexityProfile(
            data=DataProfile(
                n_rows=100, n_cols=4,
                column_types={"a": "text", "b": "id-like", "c": "text", "d": "date"},
            ),
            blocking=BlockingProfile(
                keys_used=[["a"]], n_blocks=10, total_comparisons=500,
                reduction_ratio=0.95, block_sizes_p99=20,
            ),
            scoring=ScoringProfile(
                n_pairs_scored=100,          # > 0 so dip_statistic check fires
                candidates_compared=500,
                mass_above_threshold=mass_above,
                mass_in_borderline=mass_borderline,
                dip_statistic=0.001,         # < 0.005 → RED
            ),
            cluster=ClusterProfile(transitivity_rate=0.95),
            matchkey=MatchkeyProfile(per_field={"a": FieldStats(0.5, 0.0, 10)}),
        ),
        decision=None, error=None, wall_clock_ms=10,
    )


def test_pick_committed_precision_floor_demotes_collapsed_red():
    """RED entry with mass_above > 0.9 (precision collapse) is demoted to
    rank=3. A non-collapsed RED entry (lower mass_separation) wins."""
    from goldenmatch.core.autoconfig_history import RunHistory
    h = RunHistory()
    h.entries.append(_make_red_entry_with_mass(0, mass_above=0.95, mass_borderline=0.10))
    h.entries.append(_make_red_entry_with_mass(1, mass_above=0.40, mass_borderline=0.10))
    assert h.entries[0].profile.health().name == "RED"
    assert h.entries[1].profile.health().name == "RED"
    best = h.pick_committed(precision_collapse_floor=0.9)
    assert best is not None
    assert best.iteration == 1


def test_pick_committed_precision_floor_default_is_off():
    """Default behavior preserves v1.9 lex-key ranking (no demotion)."""
    from goldenmatch.core.autoconfig_history import RunHistory
    h = RunHistory()
    h.entries.append(_make_red_entry_with_mass(0, mass_above=0.95, mass_borderline=0.10))
    h.entries.append(_make_red_entry_with_mass(1, mass_above=0.40, mass_borderline=0.10))
    best = h.pick_committed()
    assert best is not None
    assert best.iteration == 0   # higher mass_separation wins (0.95-0.10=0.85 vs 0.40-0.10=0.30)


def test_pick_committed_floor_rejects_out_of_range():
    """precision_collapse_floor must be in [0, 1]; values outside raise."""
    from goldenmatch.core.autoconfig_history import RunHistory
    h = RunHistory()
    h.entries.append(_make_red_history_entry(0, mass_above=0.5, mass_borderline=0.0))
    with pytest.raises(ValueError, match=r"precision_collapse_floor"):
        h.pick_committed(precision_collapse_floor=1.5)
    with pytest.raises(ValueError, match=r"precision_collapse_floor"):
        h.pick_committed(precision_collapse_floor=-0.1)
    # Boundary values are valid
    h.pick_committed(precision_collapse_floor=0.0)   # no raise
    h.pick_committed(precision_collapse_floor=1.0)   # no raise
