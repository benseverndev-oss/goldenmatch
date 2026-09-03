"""Tests for the sync-claim report."""

from __future__ import annotations

import io
import sys
from pathlib import Path

from sync_claims.report import DEFAULT_ROOT, DEFAULT_TESTS, inventory, main

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "scripts" / "fixtures" / "sync_enforcement"


def test_inventory_buckets_the_fixture():
    inv = inventory(FIXTURE / "src", FIXTURE / "tests")
    assert {c["symbol"] for c in inv["unenforced"]} == {
        "orphan_lane",
        "prose_lane",
        "arrow_lane",
    }
    assert {c["symbol"] for c in inv["unverified"]} == {"fast_lane"}
    assert {c["symbol"] for c in inv["unresolvable"]} == {"stray_lane"}


def test_claim_count_and_finding_count_are_separate(capsys):
    """Deleting a claim must not read as progress. Reporting only a finding
    count lets six words removed from a docstring look like a fix -- so this
    has to pin the printed report, not just the dict `main()` builds it
    from. A mutation that dropped the `{counts['claims']} claim(s);` prose
    fragment (but kept the dict intact) passed this test when it only
    inspected `inventory()`."""
    inv = inventory(FIXTURE / "src", FIXTURE / "tests")
    counts = inv["counts"]
    assert counts["claims"] >= counts["unenforced"]
    assert {
        "claims",
        "resolvable",
        "unenforced",
        "unverified",
        "unresolvable",
        "module_level",
    } <= set(counts)

    rc = main(["--root", str(FIXTURE / "src"), "--tests", str(FIXTURE / "tests")])
    out = capsys.readouterr().out
    assert rc == 0
    assert f"{counts['claims']} claim(s)" in out
    assert f"{counts['unenforced']} UNENFORCED" in out


def test_module_level_claims_are_reported_but_never_triaged():
    """A module has no single symbol a test can reference. An earlier
    version of this suite had no module-level claim in the fixture at all,
    so a mutation folding module claims into triage (`resolvable`,
    `unenforced`) passed every test unnoticed."""
    inv = inventory(FIXTURE / "src", FIXTURE / "tests")
    assert inv["counts"]["module_level"] == 1
    assert len(inv["module_level"]) == 1
    entry = inv["module_level"][0]
    assert entry["symbol"] == "<module>"
    assert entry["target"] == "slow_lane"
    assert "<module>" not in {c["symbol"] for c in inv["unenforced"]}
    assert "<module>" not in {c["symbol"] for c in inv["unverified"]}
    assert "<module>" not in {c["symbol"] for c in inv["unresolvable"]}


def test_the_report_names_the_matched_window(capsys):
    """A wrong target resolution must be visible, not silent. The first-match
    rule can pick the wrong symbol when a claim mentions several.

    "slow_lane" and "orphan_lane" alone are not enough to pin the
    `claim: {window}` print line: both strings also appear on the
    `--mirrors--> slow_lane` and symbol header lines that print regardless.
    `orphan_lane`'s docstring reads "...and nothing tests them together" --
    text that exists nowhere else in the fixture or the report, so it can
    only reach stdout through the window print itself."""
    rc = main(["--root", str(FIXTURE / "src"), "--tests", str(FIXTURE / "tests")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "nothing tests them together" in out


def test_the_report_states_its_scope(capsys):
    """Silence outside the scanned tree is not a clean bill, and the header
    has to say so -- module-level claims are reported but never triaged."""
    main(["--root", str(FIXTURE / "src"), "--tests", str(FIXTURE / "tests")])
    out = capsys.readouterr().out.lower()
    assert "scope" in out
    assert "module-level" in out
    # Substance, not just presence: a co-referenced claim must be described
    # as UNSAFE, never as verified or enforced. A mutation that flipped this
    # to "is safe and enforced" passed every earlier test in this file.
    assert "unverified is not safe" in out


def test_an_empty_tests_root_is_reported_not_presented_as_findings(capsys, tmp_path):
    """If the tests root is wrong every claim looks unenforced. That is a
    broken run, not 100% findings, and the report must say which."""
    rc = main(["--root", str(FIXTURE / "src"), "--tests", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "NO TEST FILES SCANNED" in out


def test_main_exits_zero_on_findings():
    """C0 is report-only. A finding is not a failure -- the gate is C3."""
    assert main(["--root", str(FIXTURE / "src"), "--tests", str(FIXTURE / "tests")]) == 0


def test_main_survives_non_utf8_stdout(monkeypatch):
    """main() is contracted to exit 0 whatever it finds. `arrow_lane`'s claim
    window carries a real non-ASCII character (an arrow) on purpose, and the
    fixed stdout below uses cp1252 with strict errors -- the codepage this
    broke on for real, unpatched by any PYTHONIOENCODING invocation-side
    workaround. Without the guarded `sys.stdout.reconfigure` in `main`, this
    raises UnicodeEncodeError partway through the findings loop: the process
    exits non-zero after printing only a few findings, which reads as a
    short complete report rather than the truncated one it actually is."""
    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="cp1252", errors="strict", newline="")
    monkeypatch.setattr(sys, "stdout", stream)
    rc = main(["--root", str(FIXTURE / "src"), "--tests", str(FIXTURE / "tests")])
    stream.flush()
    out = buf.getvalue().decode("cp1252")
    assert rc == 0
    assert "arrow_lane" in out


def test_the_default_roots_exist():
    """A default path that does not exist makes every CI run vacuously clean."""
    assert DEFAULT_ROOT.is_dir(), DEFAULT_ROOT
    assert DEFAULT_TESTS.is_dir(), DEFAULT_TESTS


def test_low_confidence_findings_are_reported_not_hidden():
    """Splitting the buckets must not lose claims.

    Every symbol-level claim lands in exactly one of: unenforced (high
    confidence), unenforced_low_confidence, unverified, or unresolvable.
    A split that quietly dropped the low-confidence half would shrink the
    reported number while the claims stayed exactly as unchecked.
    """
    inv = inventory(DEFAULT_ROOT, DEFAULT_TESTS)
    c = inv["counts"]
    assert "unenforced_low_confidence" in c
    total = c["unenforced"] + c["unenforced_low_confidence"] + c["unverified"] + c["unresolvable"]
    assert total == c["claims"] - c["module_level"], (
        f"buckets sum to {total} but there are "
        f"{c['claims'] - c['module_level']} symbol-level claims -- the "
        f"confidence split lost some"
    )
    assert c["unenforced_low_confidence"] > 0, (
        "no low-confidence findings at all: either the rule stopped firing or "
        "the corpus changed shape -- check before assuming this is good news"
    )


def test_the_report_says_low_confidence_findings_are_not_triaged(capsys):
    """A reader must not mistake the low-confidence bucket for a clean bill."""
    main(["--root", str(FIXTURE / "src"), "--tests", str(FIXTURE / "tests")])
    out = capsys.readouterr().out.lower()
    assert "low-confidence" in out or "low confidence" in out


def _run_real_coverage(src: Path, tests: Path) -> Path:
    """Run the given synthetic src/tests tree under real pytest-cov with
    dynamic contexts, return the resulting `.coverage` path. Used only by
    tests that need to prove the rescue happens against REAL coverage data,
    not a hand-built contexts dict."""
    import os
    import subprocess

    (src / "pyproject.toml").write_text('[tool.coverage.run]\nsource = ["."]\n', encoding="utf-8")
    data_file = src / "probe.dat"
    env = {**os.environ, "COVERAGE_FILE": str(data_file), "PYTHONPATH": str(src)}
    result = subprocess.run(
        [
            "uv",
            "run",
            "--with",
            "pytest-cov",
            "pytest",
            str(tests),
            "--cov=.",
            "--cov-context=test",
            "--cov-report=",
            "-q",
        ],
        cwd=str(src),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    return data_file


def test_inventory_without_coverage_db_is_unchanged(tmp_path):
    """The default call -- no coverage_db -- must produce EXACTLY today's
    output. This is the graceful-degradation contract: coverage is additive,
    never required."""
    inv_old = inventory(FIXTURE / "src", FIXTURE / "tests")
    inv_new = inventory(FIXTURE / "src", FIXTURE / "tests", coverage_db=None)
    assert inv_old == inv_new


def test_coverage_rescues_a_claim_the_text_check_misses(tmp_path):
    """A minimal reproduction of the _alias_score_matrix shape: `claimant`
    calls `wrapper`, `wrapper` and `target` are both referenced by ONE test
    -- but `claimant` and `target` never appear together in any test file's
    source. The text check alone must report it unenforced; adding coverage
    must rescue it, and the report must say the rescue came from coverage."""
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "m.py").write_text(
        '''
def claimant():
    """Byte-identical to ``target``."""
    return 1


def wrapper():
    return claimant()


def target():
    return 1
'''.strip(),
        encoding="utf-8",
    )
    (tests / "test_it.py").write_text(
        "from m import wrapper, target\n\n"
        "def test_wrapper_matches_target():\n"
        "    assert wrapper() == target()\n",
        encoding="utf-8",
    )

    text_only = inventory(src, tests)
    assert any(f["symbol"] == "claimant" for f in text_only["unenforced"]), (
        "the text check must NOT see this claim as enforced -- claimant and "
        "target never appear together in test_it.py's source"
    )

    subprocess_env = _run_real_coverage(src, tests)
    with_coverage = inventory(src, tests, coverage_db=subprocess_env)
    assert not any(f["symbol"] == "claimant" for f in with_coverage["unenforced"]), (
        f"claimant should be rescued by coverage; still unenforced: {with_coverage['unenforced']}"
    )
    rescued = [f for f in with_coverage["coverage_enforced"] if f["symbol"] == "claimant"]
    assert len(rescued) == 1
    assert with_coverage["counts"]["coverage_consulted"] is True
    assert with_coverage["counts"]["coverage_functions_with_data"] > 0


def test_coverage_rescues_a_method_level_claim(tmp_path):
    """claims.py's Claim.symbol is always a bare name (ast.walk visits
    methods too, and node.name for a method is just the method name).
    function_spans produces DOTTED names for methods. Without a bare-to-
    dotted resolution step, a method-level claimant's coverage lookup
    always misses, silently, no matter how well-tested the method is."""
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "m.py").write_text(
        '''
class Widget:
    def claimant(self):
        """Byte-identical to ``target``."""
        return 1

    def wrapper(self):
        return self.claimant()


def target():
    return 1
'''.strip(),
        encoding="utf-8",
    )
    (tests / "test_it.py").write_text(
        "from m import Widget, target\n\n"
        "def test_wrapper_matches_target():\n"
        "    w = Widget()\n"
        "    assert w.wrapper() == target()\n",
        encoding="utf-8",
    )

    text_only = inventory(src, tests)
    assert any(f["symbol"] == "claimant" for f in text_only["unenforced"]), (
        "text check must not see this enforced -- claimant never named in test source"
    )

    subprocess_env = _run_real_coverage(src, tests)
    with_coverage = inventory(src, tests, coverage_db=subprocess_env)
    assert not any(f["symbol"] == "claimant" for f in with_coverage["unenforced"]), (
        f"method-level claimant should be rescued by coverage; still unenforced: "
        f"{with_coverage['unenforced']}"
    )


def test_coverage_consulted_is_false_when_no_db_given():
    inv = inventory(FIXTURE / "src", FIXTURE / "tests")
    assert inv["counts"]["coverage_consulted"] is False
    assert inv["counts"]["coverage_functions_with_data"] == 0


def test_coverage_consulted_is_false_when_db_path_does_not_exist(tmp_path):
    """A missing file must degrade cleanly, not raise -- this is the CI
    scenario where the shard-producing jobs did not run on this PR."""
    inv = inventory(FIXTURE / "src", FIXTURE / "tests", coverage_db=tmp_path / "nonexistent")
    assert inv["counts"]["coverage_consulted"] is False
    assert inv["counts"]["coverage_functions_with_data"] == 0
    assert inv["coverage_enforced"] == []


def test_coverage_rescue_only_applies_to_high_confidence_claims(monkeypatch, tmp_path):
    """A LOW-confidence claim must never be coverage-rescued, even when its
    (possibly wrong) target genuinely shares a test context with the
    claimant -- coverage evidence against a wrong target proves nothing.
    Constructs the scenario directly (bypassing the confidence heuristic,
    which needs specific docstring shapes to fire) so this test is fast,
    deterministic, and does not depend on real subprocess coverage."""
    from sync_claims import report as report_mod
    from sync_claims.claims import Claim

    high = Claim(
        module="m.py",
        symbol="high_claim",
        kind="symbol",
        keyword="mirrors",
        window="high_claim mirrors high_target",
        target="high_target",
        lineno=1,
        confidence="high",
    )
    low = Claim(
        module="m.py",
        symbol="low_claim",
        kind="symbol",
        keyword="mirrors",
        window="low_claim mirrors low_target",
        target="low_target",
        lineno=2,
        confidence="low",
    )

    monkeypatch.setattr(report_mod, "claims", lambda root, symbols: [])
    monkeypatch.setattr(report_mod, "declared_symbols", lambda root: set())
    monkeypatch.setattr(report_mod, "test_reference_sets", lambda tests_root: {})
    monkeypatch.setattr(report_mod, "unenforced", lambda resolvable, reference_sets: [high, low])
    monkeypatch.setattr(
        report_mod,
        "function_spans",
        lambda root: {
            "m.py": [
                ("high_claim", 1, 1),
                ("high_target", 3, 3),
                ("low_claim", 5, 5),
                ("low_target", 7, 7),
            ]
        },
    )
    shared_ctx = frozenset({"tests/test_it.py::test_it|run"})
    monkeypatch.setattr(
        report_mod,
        "function_contexts",
        lambda coverage_db, root, spans: {
            ("m.py", "high_claim"): shared_ctx,
            ("m.py", "high_target"): shared_ctx,
            ("m.py", "low_claim"): shared_ctx,
            ("m.py", "low_target"): shared_ctx,
        },
    )

    fake_db = tmp_path / "fake.coverage"
    fake_db.write_text("", encoding="utf-8")

    inv = report_mod.inventory(Path("unused"), Path("unused"), coverage_db=fake_db)

    rescued_symbols = {c["symbol"] for c in inv["coverage_enforced"]}
    assert rescued_symbols == {"high_claim"}, (
        f"only the high-confidence claim should be rescued; got {rescued_symbols}"
    )
    assert any(c["symbol"] == "low_claim" for c in inv["unenforced_low_confidence"]), (
        "the low-confidence claim must remain reported, untouched by coverage"
    )


def test_inventory_survives_function_spans_raising(monkeypatch, tmp_path):
    """A broken `root` (not just a broken coverage file) must also degrade
    to text-only, not crash -- function_spans can fail independently of
    function_contexts, and the fallback must cover both."""
    from sync_claims import report as report_mod

    def _boom(root):
        raise OSError("simulated failure walking root")

    monkeypatch.setattr(report_mod, "function_spans", _boom)
    fake_db = tmp_path / "fake.coverage"
    fake_db.write_text("", encoding="utf-8")

    inv = report_mod.inventory(FIXTURE / "src", FIXTURE / "tests", coverage_db=fake_db)
    assert inv["counts"]["coverage_consulted"] is False
    assert inv["coverage_enforced"] == []


def test_the_real_alias_score_matrix_claim_resolves_via_coverage():
    """The Stage 2 exit criterion from the spec, literally: run a REAL,
    scoped coverage pass over core/scorer.py's own test file and confirm
    `_alias_score_matrix` -- reported unenforced by text alone -- resolves
    as coverage-enforced against real coverage data. Scoped to one test
    file so this runs in seconds, not the whole suite.

    `--cov=goldenmatch.core.scorer` (a DOTTED submodule path) is not used
    here on purpose. It reproduces `ImportError: cannot load module more
    than once per process` on `import numpy` during test collection --
    confirmed by isolating the trigger directly: `--cov=goldenmatch` (the
    bare top-level package) and `--cov=goldenmatch.core.blocker` (an
    unrelated dotted submodule) were both tried against the same fixture,
    and only the bare package name avoided the double-load. This was
    first found and (wrongly) diagnosed as Windows-specific; it reproduces
    identically on Linux CI (PR #2855's first real run, coverage.py
    7.13.5-equivalent, Python 3.12), so the fix is the invocation shape,
    not a platform skip. `--cov-fail-under=0` disables this package's own
    coverage floor for the probe -- irrelevant here, this run measures a
    handful of lines from one test file, not the real coverage gate."""
    import subprocess

    goldenmatch_src = DEFAULT_ROOT
    goldenmatch_tests = DEFAULT_TESTS
    text_only = inventory(goldenmatch_src, goldenmatch_tests)
    assert any(f["symbol"] == "_alias_score_matrix" for f in text_only["unenforced"]), (
        "expected _alias_score_matrix in the text-only unenforced set (a known finding)"
    )

    scratch = goldenmatch_src.parent  # packages/python/goldenmatch
    data_file = scratch / "coverage_alias_probe.dat"
    result = subprocess.run(
        [
            "uv",
            "run",
            "pytest",
            "tests/test_semantic_scorers.py",
            "--cov=goldenmatch",
            "--cov-context=test",
            "--cov-report=",
            "--cov-fail-under=0",
            "-q",
        ],
        cwd=str(scratch),
        env={**__import__("os").environ, "COVERAGE_FILE": str(data_file)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"probe run failed: {result.stdout}\n{result.stderr}"
    try:
        with_coverage = inventory(goldenmatch_src, goldenmatch_tests, coverage_db=data_file)
        assert not any(f["symbol"] == "_alias_score_matrix" for f in with_coverage["unenforced"]), (
            "_alias_score_matrix should now resolve as coverage-enforced"
        )
    finally:
        data_file.unlink(missing_ok=True)
