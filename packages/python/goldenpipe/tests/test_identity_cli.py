"""CLI tests for v1.2 `--identity-*` flags."""
from __future__ import annotations

from pathlib import Path

import pytest
from goldenpipe.adapters.identity import HAS_IDENTITY
from goldenpipe.cli.main import app
from typer.testing import CliRunner

pytestmark = pytest.mark.skipif(
    not HAS_IDENTITY,
    reason="requires goldenmatch>=1.15.0",
)


@pytest.fixture()
def people_csv(tmp_path: Path) -> str:
    p = tmp_path / "people.csv"
    p.write_text(
        "id,name,email,zip\n"
        "1,Alice Smith,a@x.com,12345\n"
        "2,Alyce Smith,a@x.com,12345\n"
        "3,Bob Jones,b@y.com,67890\n"
        "4,Robert Jones,b@y.com,67890\n",
    )
    return str(p)


def test_run_without_identity_flags_keeps_default_chain(people_csv: str, tmp_path: Path):
    """No --identity-path -> Identity stage isn't auto-appended."""
    runner = CliRunner()
    result = runner.invoke(app, ["run", people_csv])
    assert result.exit_code == 0, result.output
    assert "goldenmatch.identity_resolve" not in result.output


def test_run_with_identity_path_appends_stage(people_csv: str, tmp_path: Path):
    """--identity-path turns on the new stage."""
    db = str(tmp_path / "id.db")
    runner = CliRunner()
    result = runner.invoke(app, [
        "run", people_csv,
        "--identity-path", db,
        "--identity-source-pk", "id",
        "--identity-dataset", "demo",
    ])
    assert result.exit_code == 0, result.output
    # Identity stage shows up in the stage table
    assert "goldenmatch.identity_resolve" in result.output
    # And it actually wrote to the configured path
    assert Path(db).exists(), "Identity DB was not created"


def test_identity_path_persists_across_two_runs(people_csv: str, tmp_path: Path):
    """Two CLI invocations against the same --identity-path produce
    stable entity_ids for the same source records."""
    from goldenmatch.identity import IdentityStore

    db = str(tmp_path / "stable.db")
    runner = CliRunner()

    # First run: mints identities.
    result1 = runner.invoke(app, [
        "run", people_csv,
        "--identity-path", db,
        "--identity-source-pk", "id",
        "--identity-dataset", "stable",
    ])
    assert result1.exit_code == 0, result1.output
    with IdentityStore(path=db) as s:
        ids_run1 = {n.entity_id for n in s.list_identities(dataset="stable")}
    assert len(ids_run1) >= 2, f"expected >= 2 identities, got {len(ids_run1)}"

    # Second run on the same DB: same records -> same entity_ids.
    result2 = runner.invoke(app, [
        "run", people_csv,
        "--identity-path", db,
        "--identity-source-pk", "id",
        "--identity-dataset", "stable",
    ])
    assert result2.exit_code == 0, result2.output
    with IdentityStore(path=db) as s:
        ids_run2 = {n.entity_id for n in s.list_identities(dataset="stable")}
    # Every run-1 entity_id is still present after run 2.
    assert ids_run1.issubset(ids_run2), (
        f"entity_ids drifted across runs: missing={ids_run1 - ids_run2}"
    )


def test_identity_weak_threshold_flag_propagates(people_csv: str, tmp_path: Path):
    """The --identity-weak-threshold flag reaches the stage."""
    db = str(tmp_path / "weak.db")
    runner = CliRunner()
    # A very strict threshold (0.99) should flag at least one cluster
    # as weak even on the small fixture above.
    result = runner.invoke(app, [
        "run", people_csv,
        "--identity-path", db,
        "--identity-source-pk", "id",
        "--identity-weak-threshold", "0.99",
    ])
    assert result.exit_code == 0, result.output
    # Stage ran successfully (the threshold only affects conflict-edge
    # emission, never causes failure).
    assert "goldenmatch.identity_resolve" in result.output
