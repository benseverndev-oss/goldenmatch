"""Tests for coverage-based sync-claim enforcement."""

from __future__ import annotations

from pathlib import Path

from sync_claims.coverage_enforcement import coverage_enforced, function_spans


def test_coverage_enforced_is_a_pure_intersection_check():
    """A fast, offline unit test for coverage_enforced() itself -- every
    other test exercising it goes through a real subprocess coverage run
    (~5s) or is skipped on Windows. This one needs neither."""
    shared = frozenset({"tests/t.py::test_a|run"})
    disjoint_a = frozenset({"tests/t.py::test_a|run"})
    disjoint_b = frozenset({"tests/t.py::test_b|run"})
    contexts = {
        ("m.py", "both_a"): shared,
        ("m.py", "both_b"): shared,
        ("m.py", "only_a"): disjoint_a,
        ("m.py", "only_b"): disjoint_b,
    }
    assert coverage_enforced(("m.py", "both_a"), ("m.py", "both_b"), contexts)
    assert not coverage_enforced(("m.py", "only_a"), ("m.py", "only_b"), contexts)
    assert not coverage_enforced(("m.py", "both_a"), ("m.py", "missing"), contexts)
    assert not coverage_enforced(("m.py", "missing1"), ("m.py", "missing2"), contexts)


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
    assert names == {
        "top_level",
        "Widget",
        "Widget.method",
        "Widget.async_method",
    }, names


def test_function_spans_class_span_covers_its_own_body_and_all_methods(tmp_path):
    """A claim can be attached to a class's own docstring, not just a
    function's (found triaging Stage 4b: VectorIndex, LintInput,
    CanonicalizationEval, FreshnessWithMaxAgeStrategy). The class needs its
    OWN span -- the whole body, from `class` to its last line -- so a claim
    naming the class as claimant or target has something to look up a
    coverage context against, and so any test touching ANY of its methods
    counts as coverage for the class overall, not just for that one method."""
    (tmp_path / "m.py").write_text(
        """
class Widget:
    def method_a(self):
        pass

    def method_b(self):
        pass
""".strip(),
        encoding="utf-8",
    )
    spans = function_spans(tmp_path)
    by_name = {name: (start, end) for name, start, end in spans["m.py"]}
    assert by_name["Widget"] == (1, 6), by_name["Widget"]
    assert by_name["Widget"][0] <= by_name["Widget.method_a"][0]
    assert by_name["Widget"][1] >= by_name["Widget.method_b"][1]


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


def test_function_spans_finds_functions_in_control_flow_blocks(tmp_path):
    """Regression test: _collect_spans must recurse into all node types,
    not just FunctionDef/AsyncFunctionDef/ClassDef. Functions defined inside
    try/except, if, for, while, with, etc. blocks must be discovered.
    Concrete case: goldenmatch/core/_native_loader.py uses try/except import
    fallbacks to discover optional features."""
    (tmp_path / "control_flow.py").write_text(
        """try:
    def fallback():
        pass
except ImportError:
    pass
if True:
    def conditional():
        pass
""",
        encoding="utf-8",
    )
    spans = function_spans(tmp_path)
    names = {name for name, _, _ in spans["control_flow.py"]}
    assert names == {"fallback", "conditional"}, names


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


def test_function_contexts_matches_paths_across_environments(tmp_path):
    """A CI-produced `.coverage`'s `measured_files()` are absolute paths
    shaped by wherever the run happened (e.g. a Linux CI checkout); `root`
    when the data is later read can be a completely different absolute
    location (e.g. a local dev machine's own checkout). Literal path
    containment (`.resolve().relative_to(root)`) matches nothing across that
    gap -- confirmed directly against a real downloaded CI artifact:
    `coverage_functions_with_data` read 0 despite `coverage_consulted` being
    True, the read succeeded, every file was simply invisible to that check.

    This pins the fix: two genuinely different absolute directory trees,
    sharing only a `goldenmatch/`-rooted tail, must still match via
    `coverage_paths.normalize()`'s substring-based canonical form."""
    from sync_claims.coverage_enforcement import coverage_enforced, function_contexts

    module_src = "def claimant():\n    return target()\n\n\ndef target():\n    return 1\n"
    test_src = (
        "from m import claimant, target\n\ndef test_it():\n    assert claimant() == target()\n"
    )

    # The "CI" checkout: coverage actually runs here.
    ci_root = tmp_path / "home_runner_work" / "goldenmatch" / "goldenmatch"
    ci_root.mkdir(parents=True)
    (ci_root / "m.py").write_text(module_src, encoding="utf-8")
    (ci_root / "test_it.py").write_text(test_src, encoding="utf-8")
    (ci_root / "pyproject.toml").write_text(
        '[tool.coverage.run]\nsource = ["."]\n', encoding="utf-8"
    )

    data_file = ci_root / "probe.dat"
    result = subprocess.run(
        [
            "uv",
            "run",
            "--with",
            "pytest-cov",
            "pytest",
            "test_it.py",
            "--cov=.",
            "--cov-context=test",
            "--cov-report=",
            "-q",
        ],
        cwd=str(ci_root),
        env={**__import__("os").environ, "COVERAGE_FILE": str(data_file)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    # A DIFFERENT, unrelated absolute directory -- simulating a local dev
    # checkout -- containing a COPY of the same module. Coverage never ran
    # here; only `root` for the span scan and the later lookup points at it.
    local_root = tmp_path / "d_show_case" / "goldenmatch" / "goldenmatch"
    local_root.mkdir(parents=True)
    (local_root / "m.py").write_text(module_src, encoding="utf-8")

    spans = function_spans(local_root)
    contexts = function_contexts(data_file, local_root, spans)

    assert contexts, (
        "no functions resolved any coverage data across the two different "
        "roots -- the cross-environment path match failed"
    )
    assert coverage_enforced(("m.py", "claimant"), ("m.py", "target"), contexts), (
        f"claimant and target share test_it's context; the CI-vs-local root "
        f"mismatch must not hide that. contexts: {contexts}"
    )
