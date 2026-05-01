"""Smoke tests for the CLI — verifies subcommand wiring and help text."""
from __future__ import annotations

from click.testing import CliRunner

from infermap_bench.cli import main
from infermap_bench.manifest import CaseRef, CaseSource


def _ref(id_: str, category: str = "valentine", difficulty: str = "easy",
         tags: list[str] | None = None) -> CaseRef:
    return CaseRef(
        id=id_,
        path="cases/test",
        category=category,
        subcategory="test",
        source=CaseSource(name="x", url="x", license="MIT", attribution="x"),
        tags=tags or [],
        expected_difficulty=difficulty,
        field_counts={"source": 1, "target": 1},
    )


class TestTopLevel:
    def test_shows_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "infermap" in result.output.lower()

    def test_no_args_shows_help_or_usage(self):
        runner = CliRunner()
        result = runner.invoke(main, [])
        # Click groups with no args show help with exit 0 or 2 depending on version
        assert result.exit_code in (0, 2)


class TestSubcommandHelp:
    def test_run_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["run", "--help"])
        assert result.exit_code == 0
        assert "--output" in result.output
        assert "--self-test" in result.output

    def test_compare_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["compare", "--help"])
        assert result.exit_code == 0
        assert "--baseline" in result.output
        assert "--current" in result.output

    def test_report_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["report", "--help"])
        assert result.exit_code == 0

    def test_migrate_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["migrate", "--help"])
        assert result.exit_code == 0
        assert "--from" in result.output
        assert "--to" in result.output

    def test_rebuild_manifest_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["rebuild-manifest", "--help"])
        assert result.exit_code == 0

    def test_regenerate_synthetic_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["regenerate-synthetic", "--help"])
        assert result.exit_code == 0


class TestMigrateSubcommand:
    def test_noop_migration(self):
        from infermap_bench import MANIFEST_VERSION
        runner = CliRunner()
        result = runner.invoke(
            main, ["migrate", "--from", str(MANIFEST_VERSION), "--to", str(MANIFEST_VERSION)]
        )
        assert result.exit_code == 0
        assert "no migration needed" in result.output.lower()

    def test_version_mismatch_fails(self):
        runner = CliRunner()
        result = runner.invoke(main, ["migrate", "--from", "1", "--to", "5"])
        assert result.exit_code != 0


class TestFilterHelper:
    def test_category_filter(self):
        from infermap_bench.cli import _matches_filter
        assert _matches_filter(_ref("a", category="valentine"), "category:valentine")
        assert not _matches_filter(_ref("a", category="valentine"), "category:synthetic")

    def test_difficulty_filter(self):
        from infermap_bench.cli import _matches_filter
        assert _matches_filter(_ref("a", difficulty="hard"), "difficulty:hard")
        assert not _matches_filter(_ref("a", difficulty="easy"), "difficulty:hard")

    def test_tag_filter(self):
        from infermap_bench.cli import _matches_filter
        assert _matches_filter(_ref("a", tags=["alias_dominant"]), "tag:alias_dominant")
        assert not _matches_filter(_ref("a", tags=["other"]), "tag:alias_dominant")

    def test_prefix_filter(self):
        from infermap_bench.cli import _matches_filter
        assert _matches_filter(_ref("valentine/magellan/foo"), "valentine/")
        assert not _matches_filter(_ref("synthetic/customer/easy/0"), "valentine/")

    def test_unknown_filter_key(self):
        from infermap_bench.cli import _matches_filter
        assert not _matches_filter(_ref("a"), "nosuchkey:x")


class TestRepoRootResolution:
    def test_repo_root_points_at_repo(self):
        from infermap_bench.cli import REPO_ROOT
        assert (REPO_ROOT / "pyproject.toml").exists()
        assert (REPO_ROOT / "benchmark").exists()
