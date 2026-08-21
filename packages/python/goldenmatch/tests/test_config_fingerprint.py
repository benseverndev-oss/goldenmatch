"""Config fingerprint / lineage identity (config_fingerprint, config_id).

A config's fingerprint is a deterministic hash of its matching semantics, so a
run can record "which config produced this" and two configs can be diffed by
id. Per-run (``output.run_name``) and versioning (``schema_version``) noise must
not move the id; a real matching-semantic change must.
"""
from __future__ import annotations

from goldenmatch.config.schemas import (
    CONFIG_SCHEMA_VERSION,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
    OutputConfig,
)


def _cfg(field: str = "npi") -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="mk1", type="exact", fields=[MatchkeyField(field=field)])]
    )


def test_fingerprint_is_deterministic_and_prefixed() -> None:
    a, b = _cfg(), _cfg()
    assert a.config_fingerprint() == b.config_fingerprint()
    assert a.config_fingerprint().startswith("sha256:")
    assert len(a.config_fingerprint()) == len("sha256:") + 64


def test_config_id_is_short_form() -> None:
    c = _cfg()
    assert c.config_id == c.config_fingerprint()[: len("sha256:") + 8]
    assert len(c.config_id) == len("sha256:") + 8


def test_schema_version_defaults_and_is_excluded_from_fingerprint() -> None:
    c = _cfg()
    assert c.schema_version == CONFIG_SCHEMA_VERSION
    bumped = _cfg()
    bumped.schema_version = CONFIG_SCHEMA_VERSION + 100
    # A semantics-preserving schema bump must not change the id.
    assert bumped.config_fingerprint() == c.config_fingerprint()


def test_run_name_is_excluded_from_fingerprint() -> None:
    a = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="mk1", type="exact", fields=[MatchkeyField(field="npi")])],
        output=OutputConfig(run_name="run_A"),
    )
    b = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="mk1", type="exact", fields=[MatchkeyField(field="npi")])],
        output=OutputConfig(run_name="run_B"),
    )
    assert a.config_fingerprint() == b.config_fingerprint()


def test_matching_semantic_change_changes_fingerprint() -> None:
    assert _cfg("npi").config_fingerprint() != _cfg("email").config_fingerprint()
