"""Tests for unified column exclusions across GoldenFlow + GoldenMatch.

Spec: docs/superpowers/specs/2026-05-21-unified-column-exclusions-design.md
Plan: docs/superpowers/plans/2026-05-21-unified-column-exclusions.md
"""

from __future__ import annotations

import datetime

import polars as pl
import pytest


def _person_df_with_hash(n: int = 50) -> pl.DataFrame:
    """Person-shaped data with a record_hash + audit timestamp column.

    Auto-config + sentinel detectors would still pick name/last_name
    for matching; this fixture is for testing exclusion semantics, not
    detector logic.
    """
    return pl.DataFrame({
        "first_name": [f"name_{i}" for i in range(n)],
        "last_name": [f"smith_{i % 5}" for i in range(n)],
        "city": (["NYC", "LA", "SF", "Boston", "Seattle"] * (n // 5 + 1))[:n],
        # Will collide with auto-config detectors AND with explicit
        # exclude semantics, depending on which test exercises which.
        "record_hash": [f"{i:032x}" for i in range(n)],
        "created_at": [
            datetime.datetime(2026, 1, 1) + datetime.timedelta(seconds=i)
            for i in range(n)
        ],
    })


# ---------------------------------------------------------------------------
# Step 1: GoldenMatchConfig.exclude_columns field
# ---------------------------------------------------------------------------


def test_config_exclude_columns_field_defaults_empty():
    """New field defaults to empty list -- backward-compat with every
    existing config."""
    from goldenmatch.config.schemas import GoldenMatchConfig
    cfg = GoldenMatchConfig()
    assert cfg.exclude_columns == []


def test_config_exclude_columns_round_trips_yaml(tmp_path):
    """YAML round-trip: dump a config with exclude_columns set, reload,
    assert it survives."""
    import yaml
    from goldenmatch.config.loader import load_config
    from goldenmatch.config.schemas import GoldenMatchConfig

    cfg = GoldenMatchConfig(exclude_columns=["created_at", "external_id"])
    yaml_path = tmp_path / "test.yml"
    yaml_path.write_text(yaml.safe_dump(cfg.model_dump(exclude_none=True)))

    reloaded = load_config(str(yaml_path))
    assert reloaded.exclude_columns == ["created_at", "external_id"]


# ---------------------------------------------------------------------------
# Step 2: ContextVar + resolver combines every exclusion source
# ---------------------------------------------------------------------------


def test_resolver_combines_config_field_kwarg_context_and_env(monkeypatch):
    """`_resolve_effective_exclusion_overrides` ORs together: config.exclude_columns,
    _RUNTIME_EXCLUDE_COLUMNS, QualityConfig.autoconfig_force_exclude,
    and the env var. force_include from any source rescues."""
    from goldenmatch.config.schemas import GoldenMatchConfig, QualityConfig
    from goldenmatch.core.autoconfig import (
        _RUNTIME_EXCLUDE_COLUMNS,
        _resolve_effective_exclusion_overrides,
    )

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_FORCE_EXCLUDE", "env_col")
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_FORCE_INCLUDE", "rescued")

    cfg = GoldenMatchConfig(
        exclude_columns=["config_col"],
        quality=QualityConfig(
            autoconfig_force_exclude=["sub_col"],
            autoconfig_force_include=["another_rescue"],
        ),
    )
    token = _RUNTIME_EXCLUDE_COLUMNS.set(["runtime_col"])
    try:
        fe, fi = _resolve_effective_exclusion_overrides(config=cfg)
    finally:
        _RUNTIME_EXCLUDE_COLUMNS.reset(token)

    assert set(fe) == {"config_col", "runtime_col", "sub_col", "env_col"}
    assert set(fi) == {"another_rescue", "rescued"}


def test_resolver_no_config_falls_back_to_env_and_context(monkeypatch):
    """auto_configure_df calls the resolver with config=None. Resolver
    should still surface the kwarg ContextVar + env vars."""
    from goldenmatch.core.autoconfig import (
        _RUNTIME_EXCLUDE_COLUMNS,
        _resolve_effective_exclusion_overrides,
    )

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_FORCE_EXCLUDE", "env_col")
    monkeypatch.delenv("GOLDENMATCH_AUTOCONFIG_FORCE_INCLUDE", raising=False)

    token = _RUNTIME_EXCLUDE_COLUMNS.set(["runtime_col"])
    try:
        fe, fi = _resolve_effective_exclusion_overrides(config=None)
    finally:
        _RUNTIME_EXCLUDE_COLUMNS.reset(token)

    assert set(fe) == {"runtime_col", "env_col"}
    assert fi == []


# ---------------------------------------------------------------------------
# Step 3: dedupe_df / match_df kwarg
# ---------------------------------------------------------------------------


def test_dedupe_df_exclude_columns_kwarg_drops_from_matchkeys():
    """`dedupe_df(df, exclude_columns=[...])` -> committed config never
    references those columns in matchkeys or blocking."""
    import goldenmatch

    df = _person_df_with_hash(n=80)
    # Pass record_hash explicitly even though the system_hash detector
    # would also catch it -- this proves the kwarg works on its own.
    result = goldenmatch.dedupe_df(
        df, exclude_columns=["record_hash"], confidence_required=False,
    )
    cfg = result.config

    # Walk matchkeys + blocking and assert record_hash is absent.
    matchkey_cols: set[str] = set()
    for mk in (cfg.get_matchkeys() or []):
        for f in (getattr(mk, "fields", None) or []):
            if getattr(f, "field", None):
                matchkey_cols.add(f.field)
    blocking_cols: set[str] = set()
    if cfg.blocking and cfg.blocking.keys:
        for key in cfg.blocking.keys:
            for f in (getattr(key, "fields", None) or []):
                blocking_cols.add(f)

    assert "record_hash" not in matchkey_cols
    assert "record_hash" not in blocking_cols


def test_dedupe_df_kwarg_appears_in_postflight():
    """Kwarg-supplied exclusions surface in
    postflight.autoconfig_exclusions with the user_force_exclude tag."""
    import goldenmatch

    df = _person_df_with_hash(n=60)
    result = goldenmatch.dedupe_df(
        df, exclude_columns=["record_hash"], confidence_required=False,
    )
    pf = result.postflight_report
    assert pf is not None
    assert pf.autoconfig_exclusions
    matched = [ec for ec in pf.autoconfig_exclusions if ec.column == "record_hash"]
    assert matched, (
        f"record_hash must show up in postflight; got "
        f"{[ec.column for ec in pf.autoconfig_exclusions]}"
    )


def test_dedupe_df_kwarg_layered_with_config_field():
    """Both `config.exclude_columns` and the kwarg populated -> union."""
    import goldenmatch
    from goldenmatch.config.schemas import GoldenMatchConfig

    df = _person_df_with_hash(n=80)
    cfg = GoldenMatchConfig(exclude_columns=["created_at"])
    result = goldenmatch.dedupe_df(
        df, config=cfg, exclude_columns=["record_hash"],
        confidence_required=False,
    )
    # The merged config field carries both.
    assert set(result.config.exclude_columns) >= {"created_at", "record_hash"}


def test_force_include_beats_exclude_columns(monkeypatch):
    """`GOLDENMATCH_AUTOCONFIG_FORCE_INCLUDE=record_hash` rescues even
    when the user explicitly excluded it via the kwarg. Matches the
    'opt-in beats every opt-out' invariant from #404."""
    import goldenmatch

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_FORCE_INCLUDE", "record_hash")

    df = _person_df_with_hash(n=60)
    result = goldenmatch.dedupe_df(
        df, exclude_columns=["record_hash"], confidence_required=False,
    )
    pf = result.postflight_report
    if pf is not None and pf.autoconfig_exclusions:
        excluded_cols = {ec.column for ec in pf.autoconfig_exclusions}
        assert "record_hash" not in excluded_cols, (
            "force_include must beat exclude_columns kwarg; got "
            f"{excluded_cols}"
        )


# ---------------------------------------------------------------------------
# Step 4: GoldenFlow honors the exclusion set
# ---------------------------------------------------------------------------


def test_run_transform_skips_excluded_columns_via_context_var():
    """`_RUNTIME_EXCLUDE_COLUMNS` propagates into GoldenFlow: a column
    in the exclusion set passes through unchanged even when the
    underlying transform engine would touch it."""
    from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
    from goldenmatch.core.transform import run_transform

    df = pl.DataFrame({
        "name": ["Alice", "Bob"],
        "record_hash": ["ABC123def456", "XYZ789abc012"],
    })
    original_hashes = df["record_hash"].to_list()

    token = _RUNTIME_EXCLUDE_COLUMNS.set(["record_hash"])
    try:
        out, _fixes = run_transform(df, config=None)
    finally:
        _RUNTIME_EXCLUDE_COLUMNS.reset(token)

    assert out["record_hash"].to_list() == original_hashes, (
        "record_hash must survive GoldenFlow when in the exclusion set; "
        f"got {out['record_hash'].to_list()}"
    )
    # Column order preserved.
    assert out.columns == df.columns


def test_run_transform_without_excluded_columns_unchanged():
    """When no exclusion is set, GoldenFlow behaves as before. Smoke
    test against backward-compat."""
    from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
    from goldenmatch.core.transform import run_transform

    df = pl.DataFrame({"name": ["Alice", "Bob"]})

    # Reset to None to clear any leakage from earlier tests on the same worker.
    token = _RUNTIME_EXCLUDE_COLUMNS.set(None)
    try:
        out, _fixes = run_transform(df, config=None)
    finally:
        _RUNTIME_EXCLUDE_COLUMNS.reset(token)

    assert out.columns == df.columns
    assert out.height == df.height


# ---------------------------------------------------------------------------
# End-to-end: full dedupe_df run with exclude_columns + hand-written config
# ---------------------------------------------------------------------------


def test_e2e_hand_written_config_exclude_columns_propagates_to_transform():
    """User passes a hand-written config with exclude_columns set --
    GoldenFlow inside the pipeline must still skip those columns."""
    import goldenmatch
    from goldenmatch.config.schemas import GoldenMatchConfig

    df = pl.DataFrame({
        "first_name": ["Alice", "Bob"] * 10,
        "last_name": ["Smith", "Jones"] * 10,
        "record_hash": [f"HASH_{i:030d}" for i in range(20)],
    })
    original_hashes = df["record_hash"].to_list()

    cfg = GoldenMatchConfig(exclude_columns=["record_hash"])
    result = goldenmatch.dedupe_df(df, config=cfg, confidence_required=False)

    # The golden output is downstream of GoldenFlow -- assert hashes
    # survived. The actual columns present depend on the pipeline's
    # output shape, but record_hash must be byte-identical where present.
    if result.dupes is not None and "record_hash" in result.dupes.columns:
        assert set(result.dupes["record_hash"].to_list()) <= set(original_hashes)
    if result.unique is not None and "record_hash" in result.unique.columns:
        assert set(result.unique["record_hash"].to_list()) <= set(original_hashes)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
