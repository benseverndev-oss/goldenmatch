"""Tests for coverage-based sync-claim enforcement."""

from __future__ import annotations

from pathlib import Path

from sync_claims.coverage_enforcement import function_spans


def test_function_spans_finds_top_level_and_nested_functions(tmp_path):
    (tmp_path / "m.py").write_text(
        """
def top_level():
    pass


class Widget:
    def method(self):
        pass

    async def async_method(self):
        pass
""".strip(),
        encoding="utf-8",
    )
    spans = function_spans(tmp_path)
    names = {name for name, _, _ in spans["m.py"]}
    assert names == {"top_level", "Widget.method", "Widget.async_method"}, names


def test_function_spans_line_ranges_are_correct(tmp_path):
    (tmp_path / "m.py").write_text(
        """
def two_liner():
    x = 1
    return x
""".strip(),
        encoding="utf-8",
    )
    spans = function_spans(tmp_path)
    ((name, start, end),) = spans["m.py"]
    assert name == "two_liner"
    assert start == 1
    assert end == 3


def test_function_spans_skips_unparseable_files(tmp_path):
    (tmp_path / "broken.py").write_text("def (:\n", encoding="utf-8")
    (tmp_path / "ok.py").write_text("def fine():\n    pass\n", encoding="utf-8")
    spans = function_spans(tmp_path)
    assert "broken.py" not in spans
    assert "ok.py" in spans


def test_function_spans_reads_bom_prefixed_files(tmp_path):
    """Two goldenmatch modules carry a UTF-8 BOM (see the shared_decisions
    detector's own history with this exact bug). Reading plain utf-8 raises
    on the first line and the file silently vanishes from the scan."""
    (tmp_path / "bom.py").write_bytes(b"\xef\xbb\xbfdef has_bom():\n    pass\n")
    spans = function_spans(tmp_path)
    assert "bom.py" in spans
    assert spans["bom.py"][0][0] == "has_bom"


import subprocess


def _run_shard(tmp_path: Path, shard_dir: str, test_file: str, data_file: str) -> None:
    """One CI shard: a real pytest-cov + pytest-xdist run producing a
    `.dat` file with dynamic test contexts.

    `dynamic_context = "test_function"` in a coverage config file is NOT
    used here on purpose -- pytest-cov refuses to start under xdist with
    that setting, raising `DistCovError` and pointing at
    https://github.com/pytest-dev/pytest-cov/issues/604, and it says to use
    `--cov-context` instead. Confirmed by hand during design: the config-file
    route fails outright; only the CLI flag works under `-n`.
    """
    env = dict(__import__("os").environ)
    env["COVERAGE_FILE"] = data_file
    result = subprocess.run(
        [
            "uv",
            "run",
            "--with",
            "pytest-cov",
            "--with",
            "pytest-xdist",
            "--with",
            "coverage",
            "pytest",
            test_file,
            "-n",
            "2",
            "--cov=.",
            "--cov-context=test",
            "--cov-report=",
            "-q",
        ],
        cwd=shard_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"shard run failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_coverage_enforced_survives_xdist_and_combine(tmp_path):
    """The Stage 1 proof. Two shards, two xdist workers each, matching real
    CI's shape. `mod_a.claimant` is called by BOTH shards' tests;
    `mod_b.target` only by shard 1; `mod_c.only_shard_2_calls_this` only by
    shard 2. Neither `target` nor `mod_b`/`mod_c` is ever named inside
    `claimant`'s own file -- this is a coverage claim, not a text one."""
    from sync_claims.coverage_enforcement import (
        coverage_enforced,
        function_contexts,
    )

    shard = tmp_path / "shard"
    shard.mkdir()
    (shard / "mod_a.py").write_text("def claimant():\n    return 1\n", encoding="utf-8")
    (shard / "mod_b.py").write_text("def target():\n    return 2\n", encoding="utf-8")
    (shard / "mod_c.py").write_text(
        "def only_shard_2_calls_this():\n    return 3\n", encoding="utf-8"
    )
    (shard / "test_shard1.py").write_text(
        "from mod_a import claimant\n"
        "from mod_b import target\n\n"
        "def test_calls_both():\n"
        "    assert claimant() == 1\n"
        "    assert target() == 2\n",
        encoding="utf-8",
    )
    (shard / "test_shard2.py").write_text(
        "from mod_a import claimant\n"
        "from mod_c import only_shard_2_calls_this\n\n"
        "def test_calls_claimant_and_c():\n"
        "    assert claimant() == 1\n"
        "    assert only_shard_2_calls_this() == 3\n",
        encoding="utf-8",
    )
    (shard / "pyproject.toml").write_text('[tool.coverage.run]\nsource = ["."]\n', encoding="utf-8")

    _run_shard(tmp_path, str(shard), "test_shard1.py", "shard1.dat")
    _run_shard(tmp_path, str(shard), "test_shard2.py", "shard2.dat")

    combine = subprocess.run(
        [
            "uv",
            "run",
            "--with",
            "coverage",
            "coverage",
            "combine",
            "shard1.dat",
            "shard2.dat",
        ],
        cwd=str(shard),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert combine.returncode == 0, combine.stderr
    combined = shard / ".coverage"
    assert combined.exists(), "coverage combine did not produce .coverage"

    spans = function_spans(shard)
    contexts = function_contexts(combined, shard, spans)

    claimant_key = ("mod_a.py", "claimant")
    target_key = ("mod_b.py", "target")
    c_key = ("mod_c.py", "only_shard_2_calls_this")

    assert coverage_enforced(claimant_key, target_key, contexts), (
        "claimant and target ARE both called by test_calls_both -- must be "
        f"reported enforced. contexts: {contexts}"
    )
    assert coverage_enforced(claimant_key, c_key, contexts), (
        "claimant and only_shard_2_calls_this ARE both called by "
        f"test_calls_claimant_and_c. contexts: {contexts}"
    )
    assert not coverage_enforced(target_key, c_key, contexts), (
        "target (shard1 only) and only_shard_2_calls_this (shard2 only) "
        f"share NO test -- must not be reported enforced. contexts: {contexts}"
    )
