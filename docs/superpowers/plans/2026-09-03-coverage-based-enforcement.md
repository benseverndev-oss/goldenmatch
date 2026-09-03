# Coverage-Based Enforcement (Phase C, Stages 1-3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and wire in a second enforcement signal for the sync-claim detector -- coverage-based, not text-based -- that resolves the five confirmed false negatives C1 found, without narrowing anything the existing text-reference check already reports as enforced.

**Architecture:** `scripts/sync_claims/coverage_enforcement.py` (new) reads a combined `.coverage` SQLite file with per-test dynamic contexts and answers, per claim, whether any single test executed both the claimant and the target. `scripts/sync_claims/report.py`'s `inventory()` folds this in as `enforced = text_enforced OR coverage_enforced`. CI extends the two shard-producing jobs (`python_goldenmatch`, `python_goldenmatch_heavy`) with `--cov-context=test`, and `sync_claims` downloads the same shard artifacts those jobs already upload and combines them itself -- `coverage xml` (what the existing required-gate combine produces) drops context data entirely, so this needs the raw SQLite, not the existing artifact.

**Tech Stack:** Python 3.12/3.13, stdlib `ast`, `coverage.py`'s `CoverageData` API, `pytest-cov`, `pytest-xdist` (already a project dependency for the shard jobs; the new tests additionally use it via `uv run --with`, matching the exact ephemeral-install pattern this repo's own CI already uses for `pytest-split`).

**Spec:** `docs/superpowers/specs/2026-09-03-coverage-based-enforcement-design.md`

## Global Constraints

- **`enforced = text_enforced OR coverage_enforced`.** Coverage strictly widens what counts as enforced. Nothing the existing text-reference check reports as enforced may become unenforced because of this change.
- **Coverage-rescue applies only to HIGH-confidence findings** (the existing `confidence == "high"` bucket from #2850). Low-confidence findings have a target-resolution problem, a different axis; coverage evidence against a wrongly-resolved target proves nothing and is out of scope.
- **Function-level granularity, not file-level.** A claim is coverage-enforced only when one single test function's execution touched both the claimant's and the target's own line ranges.
- **Graceful degradation is mandatory, not optional.** When the coverage data is absent (the shard jobs didn't run on this PR), `sync_claims` must produce exactly today's text-only report and say so -- never hang, never silently report as if coverage found nothing.
- **The empty-string dynamic context (`''`) must be filtered out** wherever contexts are read. It marks lines executed at collection/import time with no active test, not "a test executed this" -- confirmed present in real `coverage.py` output during design verification.
- **No Claude attribution in any commit message, ever.** No `Co-Authored-By`, no `Claude-Session`, no footer.
- Report-only. No CI gate change, no ratchet. C3's arming decision is out of scope (Stage 5 of the spec, not this plan).

---

### Task 1: `coverage_enforcement.py` -- function spans and context reading

**Files:**
- Create: `scripts/sync_claims/coverage_enforcement.py`
- Test: `scripts/test_sync_claims_coverage_enforcement.py`

**Interfaces:**
- Consumes: nothing from other sync_claims modules.
- Produces:
  - `def function_spans(root: Path) -> dict[str, list[tuple[str, int, int]]]` -- module posix path (relative to `root`) -> list of `(qualified_name, lineno, end_lineno)` for every `FunctionDef`/`AsyncFunctionDef` in that module. `qualified_name` is dotted for nested functions/methods (e.g. `MyClass.my_method`), built by tracking the enclosing `ClassDef`/`FunctionDef` chain during the AST walk.
  - `def function_contexts(coverage_db: Path, root: Path, spans: dict[str, list[tuple[str, int, int]]]) -> dict[tuple[str, str], frozenset[str]]` -- `(module_path, qualified_name) -> {dynamic contexts that executed any line in that function}`, empty-string context excluded.
  - `def coverage_enforced(claimant_key: tuple[str, str], target_key: tuple[str, str], contexts: dict[tuple[str, str], frozenset[str]]) -> bool`

- [ ] **Step 1: Write the failing test for `function_spans`**

```python
"""Tests for coverage-based sync-claim enforcement."""

from __future__ import annotations

from pathlib import Path

from sync_claims.coverage_enforcement import (
    coverage_enforced,
    function_contexts,
    function_spans,
)


def test_function_spans_finds_top_level_and_nested_functions(tmp_path):
    (tmp_path / "m.py").write_text(
        '''
def top_level():
    pass


class Widget:
    def method(self):
        pass

    async def async_method(self):
        pass
'''.strip(),
        encoding="utf-8",
    )
    spans = function_spans(tmp_path)
    names = {name for name, _, _ in spans["m.py"]}
    assert names == {"top_level", "Widget.method", "Widget.async_method"}, names


def test_function_spans_line_ranges_are_correct(tmp_path):
    (tmp_path / "m.py").write_text(
        '''
def two_liner():
    x = 1
    return x
'''.strip(),
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
    (tmp_path / "bom.py").write_bytes(
        b"\xef\xbb\xbfdef has_bom():\n    pass\n"
    )
    spans = function_spans(tmp_path)
    assert "bom.py" in spans
    assert spans["bom.py"][0][0] == "has_bom"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /d/show_case/gm-rel3161
PYTHONPATH=scripts D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_sync_claims_coverage_enforcement.py -q --no-header -p no:cacheprovider
```

Expected: `ModuleNotFoundError: No module named 'sync_claims.coverage_enforcement'`.

- [ ] **Step 3: Write `function_spans`**

```python
"""Coverage-based enforcement: does any single test EXECUTE both a claim's
claimant and its target, whether or not either name appears in that test's
own source?

The text-reference check (`enforcement.py`) is not sound as a negative --
a test can compare two functions without naming either, by reaching them
through a caller. C1 confirmed this five times over, including one function
in this exact scope (`core/scorer.py:_alias_score_matrix`, reached through
`_fuzzy_score_matrix`) and a whole module
(`core/survivorship/native.py`, reached through `build_survivorship_native`).

This module answers the execution question instead: for a given claim, did
one single test function run code inside BOTH the claimant's own definition
and the target's own definition. Function-level granularity, not file-level
-- file-level would just move the "co-occurrence is not comparison" problem
from text to runtime rather than narrowing it.

WHAT THIS DOES NOT PROVE. Co-execution is not proof of comparison. A test
could run both functions without ever comparing their outputs, if both fire
inside one integration-shaped test for unrelated reasons. This is a real,
accepted residual gap -- narrower than the text-reference problem, not
eliminated. See docs/superpowers/specs/2026-09-03-coverage-based-enforcement-
design.md's Being Wrong section.
"""

from __future__ import annotations

import ast
from pathlib import Path

_EMPTY_CONTEXT = ""


def function_spans(root: Path) -> dict[str, list[tuple[str, int, int]]]:
    """Every function/method in every `.py` file under `root`, with its line
    range. Keys are module paths relative to `root`, posix-separated.

    Deliberately general -- every function, not a naming-convention subset.
    `parity_coverage.py:_py_function_spans` looks similar but answers a
    narrower, unrelated question (only names ending `_py`, Companion A's
    scope); that function is module-private and this one is not a call to
    it, it is the same AST technique applied to a different question.
    """
    out: dict[str, list[tuple[str, int, int]]] = {}
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="ignore"))
        except SyntaxError:
            continue
        rel = path.relative_to(root).as_posix()
        spans: list[tuple[str, int, int]] = []
        _collect_spans(tree, [], spans)
        if spans:
            out[rel] = spans
    return out


def _collect_spans(
    node: ast.AST, prefix: list[str], out: list[tuple[str, int, int]]
) -> None:
    """Walk `node`'s direct children, recursing into class/function bodies so
    nested functions and methods get dotted names (`Widget.method`) and their
    OWN enclosing scope's line range is not what gets recorded for them."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = ".".join([*prefix, child.name])
            out.append((name, child.lineno, child.end_lineno or child.lineno))
            _collect_spans(child, [*prefix, child.name], out)
        elif isinstance(child, ast.ClassDef):
            _collect_spans(child, [*prefix, child.name], out)
```

- [ ] **Step 4: Run the `function_spans` tests to verify they pass**

```bash
PYTHONPATH=scripts D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_sync_claims_coverage_enforcement.py -q --no-header -p no:cacheprovider -k function_spans
```

Expected: `4 passed`.

- [ ] **Step 5: Write the failing test for `function_contexts` and `coverage_enforced` -- a REAL two-shard, xdist coverage run, not a canned fixture**

This is Stage 1's proof from the spec: contexts must survive BOTH pytest-xdist's own worker-level merge AND an explicit `coverage combine` across two separate data files, exactly the two-layer merge the real CI shards go through. Proven by actually running it, via subprocess, not assumed from documentation -- `coverage.py` combine-preserves-contexts behavior was verified by hand during design and produced a real gotcha (see the comment in the test below), which is why this is a subprocess test and not a mocked one.

Append to `scripts/test_sync_claims_coverage_enforcement.py`:

```python
import subprocess
import sys


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
            sys.executable, "-m", "uv", "run",
            "--with", "pytest-cov", "--with", "pytest-xdist", "--with", "coverage",
            "pytest", test_file, "-n", "2",
            "--cov=.", "--cov-context=test", "--cov-report=", "-q",
        ],
        cwd=shard_dir, env=env, capture_output=True, text=True, timeout=120,
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
    (shard / "pyproject.toml").write_text(
        '[tool.coverage.run]\nsource = ["."]\n', encoding="utf-8"
    )

    _run_shard(tmp_path, str(shard), "test_shard1.py", "shard1.dat")
    _run_shard(tmp_path, str(shard), "test_shard2.py", "shard2.dat")

    combine = subprocess.run(
        [sys.executable, "-m", "uv", "run", "--with", "coverage",
         "coverage", "combine", "shard1.dat", "shard2.dat"],
        cwd=str(shard), capture_output=True, text=True, timeout=60,
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
```

- [ ] **Step 6: Run to verify it fails**

```bash
PYTHONPATH=scripts D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_sync_claims_coverage_enforcement.py -q --no-header -p no:cacheprovider -k xdist_and_combine
```

Expected: `ImportError` (`function_contexts`/`coverage_enforced` not defined yet).

- [ ] **Step 7: Write `function_contexts` and `coverage_enforced`**

Append to `scripts/sync_claims/coverage_enforcement.py`:

```python
def function_contexts(
    coverage_db: Path,
    root: Path,
    spans: dict[str, list[tuple[str, int, int]]],
) -> dict[tuple[str, str], frozenset[str]]:
    """(module_path, qualified_name) -> the dynamic test contexts that
    executed any line inside that function, read from a combined `.coverage`
    SQLite file with `dynamic_context = "test_function"` data in it.

    The empty-string context marks lines executed at import/collection time
    with no active test -- filtered out, or every function in a module would
    spuriously share that context with every other.
    """
    import coverage

    data = coverage.CoverageData(basename=str(coverage_db))
    data.read()

    line_contexts_by_file: dict[str, dict[int, set[str]]] = {}
    for measured in data.measured_files():
        rel = _relative_to_root(measured, root)
        if rel is None:
            continue
        line_contexts_by_file[rel] = data.contexts_by_lineno(measured)

    out: dict[tuple[str, str], frozenset[str]] = {}
    for module, functions in spans.items():
        line_contexts = line_contexts_by_file.get(module)
        if not line_contexts:
            continue
        for name, start, end in functions:
            ctxs: set[str] = set()
            for lineno in range(start, end + 1):
                ctxs.update(line_contexts.get(lineno, ()))
            ctxs.discard(_EMPTY_CONTEXT)
            if ctxs:
                out[(module, name)] = frozenset(ctxs)
    return out


def _relative_to_root(measured_path: str, root: Path) -> str | None:
    """`coverage.py` reports measured files with whatever path shape the run
    that produced them used (absolute, or relative to that run's CWD) -- not
    guaranteed to match `root`-relative posix paths. Returns None for a file
    outside `root` rather than raising, since a coverage run's `source`
    scope and this scan's `root` are configured independently and are not
    guaranteed identical."""
    try:
        return Path(measured_path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def coverage_enforced(
    claimant_key: tuple[str, str],
    target_key: tuple[str, str],
    contexts: dict[tuple[str, str], frozenset[str]],
) -> bool:
    """True when some single test's context appears for BOTH keys."""
    return bool(contexts.get(claimant_key, frozenset()) & contexts.get(target_key, frozenset()))
```

- [ ] **Step 8: Run all tests in the file to verify they pass**

```bash
PYTHONPATH=scripts D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_sync_claims_coverage_enforcement.py -q --no-header -p no:cacheprovider
```

Expected: `5 passed`. The xdist/combine test takes several seconds (real subprocess pytest runs) -- this is expected and acceptable for the one test that exists specifically to prove the mechanism, not a performance regression to chase.

- [ ] **Step 9: Sabotage-verify the empty-context filter and the path-matching guard**

```bash
cd /d/show_case/gm-rel3161
F=scripts/sync_claims/coverage_enforcement.py
cp "$F" /tmp/ce.bak && test -s /tmp/ce.bak && echo "backup ok"

echo "### A. stop filtering the empty context"
python - <<'EOF'
import pathlib
p = pathlib.Path("scripts/sync_claims/coverage_enforcement.py")
s = p.read_text(encoding="utf-8")
old = "            ctxs.discard(_EMPTY_CONTEXT)\n"
new = ""
assert s.count(old) == 1, "sabotage A did not apply"
p.write_text(s.replace(old, new), encoding="utf-8")
print("applied")
EOF
PYTHONPATH=scripts D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_sync_claims_coverage_enforcement.py -q --no-header -p no:cacheprovider -k xdist_and_combine
# Expected: FAILS -- target and c_key now share the empty context and are
# wrongly reported enforced
cp /tmp/ce.bak "$F"

echo "### B. drop the path-normalization guard (compare raw strings)"
python - <<'EOF'
import pathlib
p = pathlib.Path("scripts/sync_claims/coverage_enforcement.py")
s = p.read_text(encoding="utf-8")
old = '''    try:
        return Path(measured_path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None'''
new = "    return measured_path"
assert s.count(old) == 1, "sabotage B did not apply"
p.write_text(s.replace(old, new), encoding="utf-8")
print("applied")
EOF
PYTHONPATH=scripts D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_sync_claims_coverage_enforcement.py -q --no-header -p no:cacheprovider -k xdist_and_combine
# Expected: FAILS -- absolute measured-file paths no longer match the
# root-relative keys in `spans`, so contexts comes back empty and every
# assertion in the test fails
cp /tmp/ce.bak "$F"

echo "### restored:"
PYTHONPATH=scripts D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_sync_claims_coverage_enforcement.py -q --no-header -p no:cacheprovider
```

Both sabotages must print "applied" AND produce the named failure.

- [ ] **Step 10: Run ruff and commit**

```bash
D:/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check scripts/sync_claims/coverage_enforcement.py scripts/test_sync_claims_coverage_enforcement.py
git add scripts/sync_claims/coverage_enforcement.py scripts/test_sync_claims_coverage_enforcement.py
git commit -F - <<'EOF'
feat(sync-claims): coverage-based enforcement -- does a test EXECUTE both halves

The text-reference check is not sound as a negative: a test can compare
two functions without naming either, by reaching them through a caller.
C1 confirmed this five times, including core/scorer.py:_alias_score_matrix
(reached through _fuzzy_score_matrix) and three claims in
core/survivorship/native.py (reached through build_survivorship_native).

Answers a different question: did one single test function EXECUTE both
the claimant and the target, whether or not either name appears in that
test's source. Function-level granularity, not file-level.

Proven against a real two-shard, two-xdist-worker coverage run, not
assumed from documentation -- confirmed by hand during design that
`dynamic_context = "test_function"` in a coverage config file makes
pytest-cov refuse to start under xdist (DistCovError, pytest-cov#604);
only the `--cov-context` CLI flag works. Contexts correctly survive both
xdist's own worker-merge and an explicit `coverage combine` across shard
files, matching real CI's two-layer merge shape.

Sabotage-verified: dropping the empty-context filter wrongly enforces an
unrelated pair; dropping path normalization empties every result.
EOF
```

---

### Task 2: Fold coverage into `report.py`'s `inventory()`

**Files:**
- Modify: `scripts/sync_claims/report.py`
- Test: `scripts/test_sync_claims_report.py`

**Interfaces:**
- Consumes: `function_spans`, `function_contexts`, `coverage_enforced` from Task 1's `sync_claims.coverage_enforcement`.
- Produces: `inventory(root, tests_root, coverage_db=None) -> dict` with two new keys in the returned dict: `"coverage_enforced"` (list) and `coverage_consulted` (bool) inside `"counts"`.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test_sync_claims_report.py`:

```python
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
    return wrapper()


def wrapper():
    return target()


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
        f"claimant should be rescued by coverage; still unenforced: "
        f"{with_coverage['unenforced']}"
    )
    rescued = [f for f in with_coverage["coverage_enforced"] if f["symbol"] == "claimant"]
    assert len(rescued) == 1
    assert with_coverage["counts"]["coverage_consulted"] is True


def test_coverage_consulted_is_false_when_no_db_given():
    inv = inventory(FIXTURE / "src", FIXTURE / "tests")
    assert inv["counts"]["coverage_consulted"] is False


def test_coverage_consulted_is_false_when_db_path_does_not_exist(tmp_path):
    """A missing file must degrade cleanly, not raise -- this is the CI
    scenario where the shard-producing jobs did not run on this PR."""
    inv = inventory(
        FIXTURE / "src", FIXTURE / "tests", coverage_db=tmp_path / "nonexistent"
    )
    assert inv["counts"]["coverage_consulted"] is False
    assert inv["coverage_enforced"] == []


def test_the_real_alias_score_matrix_claim_resolves_via_coverage():
    """The Stage 2 exit criterion from the spec, literally: run a REAL,
    scoped coverage pass over core/scorer.py's own test file and confirm
    `_alias_score_matrix` -- reported unenforced by text alone -- resolves
    as coverage-enforced against real coverage data. Scoped to one test
    file so this runs in seconds, not the whole suite."""
    import subprocess
    import sys

    goldenmatch_src = DEFAULT_ROOT
    goldenmatch_tests = DEFAULT_TESTS
    text_only = inventory(goldenmatch_src, goldenmatch_tests)
    assert any(
        f["symbol"] == "_alias_score_matrix" for f in text_only["unenforced"]
    ), "expected _alias_score_matrix in the text-only unenforced set (a known finding)"

    scratch = goldenmatch_src.parent  # packages/python/goldenmatch
    data_file = scratch / "coverage_alias_probe.dat"
    result = subprocess.run(
        [
            sys.executable, "-m", "uv", "run", "--with", "pytest-cov",
            "pytest", "tests/test_semantic_scorers.py",
            "--cov=goldenmatch.core.scorer", "--cov-context=test", "--cov-report=", "-q",
        ],
        cwd=str(scratch),
        env={**__import__("os").environ, "COVERAGE_FILE": str(data_file)},
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"probe run failed: {result.stdout}\n{result.stderr}"
    try:
        with_coverage = inventory(goldenmatch_src, goldenmatch_tests, coverage_db=data_file)
        assert not any(
            f["symbol"] == "_alias_score_matrix" for f in with_coverage["unenforced"]
        ), "_alias_score_matrix should now resolve as coverage-enforced"
    finally:
        data_file.unlink(missing_ok=True)
```

Add the `_run_real_coverage` helper near the top of the test file (after the existing imports):

```python
def _run_real_coverage(src: Path, tests: Path) -> Path:
    """Run the given synthetic src/tests tree under real pytest-cov with
    dynamic contexts, return the resulting `.coverage` path. Used only by
    tests that need to prove the rescue happens against REAL coverage data,
    not a hand-built contexts dict."""
    import subprocess
    import sys

    (src / "pyproject.toml").write_text(
        '[tool.coverage.run]\nsource = ["."]\n', encoding="utf-8"
    )
    data_file = src / "probe.dat"
    result = subprocess.run(
        [sys.executable, "-m", "uv", "run", "--with", "pytest-cov",
         "pytest", str(tests), "--cov=.", "--cov-context=test", "--cov-report=", "-q"],
        cwd=str(src), env={**__import__("os").environ, "COVERAGE_FILE": str(data_file)},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    return data_file
```

- [ ] **Step 2: Run to verify failure**

```bash
PYTHONPATH=scripts D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_sync_claims_report.py -q --no-header -p no:cacheprovider -k "coverage"
```

Expected: `TypeError: inventory() got an unexpected keyword argument 'coverage_db'`.

- [ ] **Step 3: Modify `inventory()`**

In `scripts/sync_claims/report.py`, add the import and change the function:

```python
from sync_claims.coverage_enforcement import coverage_enforced, function_contexts, function_spans
```

Replace:

```python
def inventory(root: Path, tests_root: Path) -> dict:
    """Bucket every claim under `root` by enforcement state."""
    all_claims = claims(root, symbols=declared_symbols(root))
    symbol_claims = [c for c in all_claims if c.kind == "symbol"]
    module_claims = [c for c in all_claims if c.kind == "module"]
    resolvable = [c for c in symbol_claims if c.target is not None]
    unresolvable = [c for c in symbol_claims if c.target is None]

    reference_sets = test_reference_sets(tests_root)
    all_findings = unenforced(resolvable, reference_sets)
    # C1 triage measured that a LOW-confidence target is frequently a real
    # symbol the claim does not equate. Those stay reported, in their own
    # bucket, but are not the triage set and must not seed C3's ratchet floor.
    findings = [c for c in all_findings if c.confidence == "high"]
    low_confidence = [c for c in all_findings if c.confidence != "high"]
    finding_ids = {(c.module, c.symbol, c.lineno) for c in all_findings}
    unverified = [c for c in resolvable if (c.module, c.symbol, c.lineno) not in finding_ids]

    return {
        "counts": {
            "claims": len(all_claims),
            "resolvable": len(resolvable),
            "unenforced": len(findings),
            "unenforced_low_confidence": len(low_confidence),
            "unverified": len(unverified),
            "unresolvable": len(unresolvable),
            "module_level": len(module_claims),
            "test_files_scanned": len(reference_sets),
        },
        "unenforced": [_as_dict(c) for c in findings],
        "unenforced_low_confidence": [_as_dict(c) for c in low_confidence],
        "unverified": [_as_dict(c) for c in unverified],
        "unresolvable": [_as_dict(c) for c in unresolvable],
        "module_level": [_as_dict(c) for c in module_claims],
    }
```

with:

```python
def inventory(root: Path, tests_root: Path, coverage_db: Path | None = None) -> dict:
    """Bucket every claim under `root` by enforcement state.

    `coverage_db` is optional and additive. When given and readable, a
    HIGH-confidence claim the text check reports unenforced is re-checked
    against real test-execution data: if some single test executed both the
    claimant and the target, the claim moves from `unenforced` to
    `coverage_enforced` rather than staying a false negative. Absent, wrong
    path, or unreadable -> exactly today's text-only behavior, silently and
    correctly -- this must never raise or hang on missing coverage data, the
    CI scenario where the shard-producing jobs did not run on this PR.

    Coverage-rescue is scoped to HIGH-confidence findings only. A
    LOW-confidence claim's problem is that its resolved TARGET may be wrong
    (a different axis than enforcement); coverage evidence against a wrong
    target proves nothing about the claim the docstring actually makes.
    """
    all_claims = claims(root, symbols=declared_symbols(root))
    symbol_claims = [c for c in all_claims if c.kind == "symbol"]
    module_claims = [c for c in all_claims if c.kind == "module"]
    resolvable = [c for c in symbol_claims if c.target is not None]
    unresolvable = [c for c in symbol_claims if c.target is None]

    reference_sets = test_reference_sets(tests_root)
    all_findings = unenforced(resolvable, reference_sets)
    findings = [c for c in all_findings if c.confidence == "high"]
    low_confidence = [c for c in all_findings if c.confidence != "high"]

    coverage_consulted = False
    coverage_rescued: list[Claim] = []
    if coverage_db is not None and coverage_db.exists():
        try:
            spans = function_spans(root)
            contexts = function_contexts(coverage_db, root, spans)
            coverage_consulted = True
        except Exception:
            # A malformed or foreign .coverage file must degrade to
            # text-only, not crash a report-only job.
            contexts = {}
        for claim in findings:
            claimant_key = (claim.module, claim.symbol)
            target_module, _, target_name = _locate_target(root, claim.target)
            if target_module is None:
                continue
            target_key = (target_module, target_name)
            if coverage_enforced(claimant_key, target_key, contexts):
                coverage_rescued.append(claim)
        findings = [c for c in findings if c not in coverage_rescued]

    finding_ids = {(c.module, c.symbol, c.lineno) for c in all_findings}
    unverified = [c for c in resolvable if (c.module, c.symbol, c.lineno) not in finding_ids]

    return {
        "counts": {
            "claims": len(all_claims),
            "resolvable": len(resolvable),
            "unenforced": len(findings),
            "unenforced_low_confidence": len(low_confidence),
            "unverified": len(unverified),
            "coverage_enforced": len(coverage_rescued),
            "coverage_consulted": coverage_consulted,
            "unresolvable": len(unresolvable),
            "module_level": len(module_claims),
            "test_files_scanned": len(reference_sets),
        },
        "unenforced": [_as_dict(c) for c in findings],
        "unenforced_low_confidence": [_as_dict(c) for c in low_confidence],
        "unverified": [_as_dict(c) for c in unverified],
        "coverage_enforced": [_as_dict(c) for c in coverage_rescued],
        "unresolvable": [_as_dict(c) for c in unresolvable],
        "module_level": [_as_dict(c) for c in module_claims],
    }


def _locate_target(root: Path, target_name: str) -> tuple[str | None, None, str]:
    """The claim's `target` is a bare symbol NAME (claims.py resolves it
    from prose, not a module path), but `function_contexts` is keyed by
    (module, name) -- coverage cannot be checked without knowing which
    module the target lives in. Searches `function_spans(root)` for a
    function with this exact name; the FIRST module found wins.

    A real limitation, stated rather than hidden: if the target name exists
    in more than one module, this can pick the wrong one, and the ambiguity
    is silent. Scoped narrowly on purpose -- coverage-rescue only applies to
    HIGH-confidence claims, which are already the subset least likely to
    collide on a name (see KNOWN_AMBIGUOUS in the shared-decisions detector
    for what a real per-name collision problem looks like at scale). A
    future pass could resolve this properly by carrying the target's module
    through from claims.py instead of re-deriving it here; out of scope for
    this plan.
    """
    spans = function_spans(root)
    for module, functions in spans.items():
        for name, _, _ in functions:
            if name == target_name or name.rsplit(".", 1)[-1] == target_name:
                return module, None, name
    return None, None, target_name
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
PYTHONPATH=scripts D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_sync_claims_report.py -q --no-header -p no:cacheprovider
```

Expected: all pass, including the new coverage tests. The `_alias_score_matrix` test takes several seconds (a real, scoped subprocess pytest run) -- expected.

- [ ] **Step 5: Add the `--coverage-db` CLI flag**

In `scripts/sync_claims/report.py`'s `main()`, after the existing `--tests` argument:

```python
    parser.add_argument("--coverage-db", type=Path, default=None)
```

And change the `inventory(...)` call inside `main()` to:

```python
    inv = inventory(args.root, args.tests, coverage_db=args.coverage_db)
```

Add to the text-report printing (after the existing counts summary):

```python
    print(
        f"  coverage consulted: {counts['coverage_consulted']}"
        + (f" -- {counts['coverage_enforced']} claim(s) rescued from unenforced"
           if counts['coverage_consulted'] else "")
    )
```

- [ ] **Step 6: Run the full report test file once more, then ruff**

```bash
PYTHONPATH=scripts D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_sync_claims_report.py -q --no-header -p no:cacheprovider
D:/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check scripts/sync_claims/report.py scripts/test_sync_claims_report.py
```

Expected: all pass, ruff clean.

- [ ] **Step 7: Sabotage-verify the confidence-scoping guard**

```bash
cd /d/show_case/gm-rel3161
F=scripts/sync_claims/report.py
cp "$F" /tmp/rep.bak && test -s /tmp/rep.bak && echo "backup ok"
python - <<'EOF'
import pathlib
p = pathlib.Path("scripts/sync_claims/report.py")
s = p.read_text(encoding="utf-8")
old = "        for claim in findings:"
new = "        for claim in all_findings:"
assert s.count(old) == 1, "sabotage did not apply"
p.write_text(s.replace(old, new), encoding="utf-8")
print("applied")
EOF
PYTHONPATH=scripts D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_sync_claims_report.py -q --no-header -p no:cacheprovider 2>&1 | tail -5
# No existing test currently pins this scoping directly -- if this passes
# clean, that is itself a finding: add a low-confidence-claim fixture case
# to test_coverage_rescues_a_claim_the_text_check_misses proving a
# low-confidence claim is NEVER coverage-rescued even when its (wrong)
# target happens to share a test with the claimant, before moving on.
cp /tmp/rep.bak "$F"
```

If the sabotage does not fail an existing test, add the missing test now (a low-confidence claim, a wrong target, a test that happens to execute both) before proceeding -- do not skip this; an un-pinned confidence-scoping guard is exactly the class of defect this whole plan exists to prevent elsewhere.

- [ ] **Step 8: Commit**

```bash
git add scripts/sync_claims/report.py scripts/test_sync_claims_report.py
git commit -F - <<'EOF'
feat(sync-claims): fold coverage evidence into inventory() -- report-only

enforced = text_enforced OR coverage_enforced. A strict widening: nothing
the text check already reports enforced can become unenforced, and
coverage-rescue applies only to HIGH-confidence findings -- a
LOW-confidence claim's problem is a wrong target, a different axis, and
coverage evidence against a wrong target proves nothing.

coverage_db is optional and additive throughout. Absent, wrong path, or
unreadable degrades to exactly today's output -- proven by a test that
asserts inventory() with no coverage_db is byte-identical to the old
signature's output.

Proven against two real subprocess coverage runs, not synthetic
assertions: a minimal reproduction of the _alias_score_matrix shape
(claimant -> wrapper -> target, neither claimant nor target named
together in any test source), and the REAL claim itself, run scoped to
core/scorer.py's own test file so it stays fast. Both confirm the rescue
that motivated this whole plan.
EOF
```

---

### Task 3: Wire into CI

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `scripts/test_workflow_yaml.py`

**Interfaces:**
- Consumes: `sync_claims.report`'s new `--coverage-db` flag from Task 2; the existing `gm-cov-shard-*`/`gm-cov-heavy-*` artifacts `python_goldenmatch`/`python_goldenmatch_heavy` already upload.
- Produces: nothing consumed by a later task in this plan.

- [ ] **Step 1: Add `--cov-context=test` to the two shard-producing jobs**

In `.github/workflows/ci.yml`, inside `python_goldenmatch`'s main shard step (the one whose `env:` sets `COVERAGE_FILE: coverage_shard${{ matrix.shard }}.dat`), change:

```yaml
            --cov=goldenmatch --cov-report= \
```

to:

```yaml
            --cov=goldenmatch --cov-report= --cov-context=test \
```

Inside `python_goldenmatch_heavy`'s shard step, change the last line of its `run:` block:

```yaml
            --cov=goldenmatch --cov-report=
```

to:

```yaml
            --cov=goldenmatch --cov-report= --cov-context=test
```

Do NOT touch `python_goldenmatch`'s separate `pytest (serial + native, OOM-prone under -n auto)` step (`COVERAGE_FILE: coverage_shard${{ matrix.shard }}_serial.dat`) -- its data file is not one of the six files `python_goldenmatch_coverage`'s own `coverage combine` command lists, so it is out of the population this plan measures; touching it would be scope creep on a pre-existing, unrelated quirk.

- [ ] **Step 2: Replace the `sync_claims` job**

Replace the entire current `sync_claims:` job body with:

```yaml
  sync_claims:
    # Report-only audit for docstrings that claim "this code mirrors that
    # code" with no test enforcing the relationship (scripts/sync_claims/).
    # Not in ci-required and the report step has no continue-on-error to
    # suppress -- it always exits 0 by design. The gate is a later stage
    # (C3), after the C1 triage establishes a floor; this job only prints.
    #
    # Coverage-based enforcement (docs/superpowers/specs/2026-09-03-coverage-
    # based-enforcement-design.md): downloads the SAME gm-cov-* shard
    # artifacts python_goldenmatch_coverage combines, and combines them
    # itself -- with dynamic test contexts intact, which `coverage xml`
    # (what that job produces) drops entirely. Depends on the shard-producing
    # jobs directly, mirroring dead_code's existing always()+skip-tolerant
    # pattern below, so a PR that does not touch goldenmatch code degrades to
    # text-only enforcement rather than hanging on a job that never runs.
    needs: [changes, python_goldenmatch, python_goldenmatch_heavy]
    if: >-
      always() &&
      (needs.changes.outputs.sync_claims == 'true' || needs.changes.outputs.force_all == 'true') &&
      needs.python_goldenmatch.result != 'failure' &&
      needs.python_goldenmatch.result != 'cancelled' &&
      needs.python_goldenmatch_heavy.result != 'failure' &&
      needs.python_goldenmatch_heavy.result != 'cancelled'
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10  # v6
      - uses: astral-sh/setup-uv@caf0cab7a618c569241d31dcd442f54681755d39  # v3
      - run: uv sync --all-packages --no-install-package goldenmatch-native --no-install-package goldenflow-native
      - name: Detector self-tests
        # Listed by name, so a new test FILE must be added here or it never runs.
        env:
          PYTHONPATH: scripts
        run: uv run pytest scripts/test_sync_claims_claims.py scripts/test_sync_claims_enforcement.py scripts/test_sync_claims_report.py scripts/test_sync_claims_coverage_enforcement.py -q
      - name: Download coverage shards (best-effort)
        # Absent when python_goldenmatch/_heavy were both skipped (this PR
        # did not touch goldenmatch code) -- continue-on-error, not a
        # required step, so a skip here degrades rather than fails the job.
        continue-on-error: true
        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093  # v4
        with:
          pattern: gm-cov-*
          merge-multiple: true
          path: .
      - name: Combine coverage contexts (best-effort)
        # Same file list as python_goldenmatch_coverage's own combine, so
        # this measures the identical population the required gate's floors
        # already measure. continue-on-error: errors when shards are absent
        # (the download step above found nothing), which is the expected
        # degrade-to-text-only path, not a real failure.
        continue-on-error: true
        run: |
          uv run coverage combine --rcfile=packages/python/goldenmatch/pyproject.toml \
            coverage_shard1.dat coverage_shard2.dat coverage_shard3.dat \
            coverage_heavy_1.dat coverage_heavy_2.dat coverage_heavy_3.dat
      - name: Unenforced sync-claim report
        # Report-only by design: main() returns 0 whatever it finds. The gate
        # is stage C3, after the C1 triage establishes a floor.
        env:
          PYTHONPATH: scripts
        run: |
          if [ -f .coverage ]; then
            uv run python -m sync_claims.report --coverage-db .coverage
          else
            uv run python -m sync_claims.report
          fi
```

- [ ] **Step 3: Verify the workflow YAML is still valid**

```bash
cd /d/show_case/gm-rel3161
D:/show_case/goldenmatch/.venv/Scripts/python.exe scripts/check_workflow_yaml.py
```

Expected: no error (parses, no duplicate keys).

- [ ] **Step 4: Write the failing CI-wiring test**

Find the existing `_load_ci_workflow()` (or equivalently-named) helper in `scripts/test_workflow_yaml.py` -- built for C0's `test_sync_claims_job_is_reachable` -- and use it, do not write a second YAML loader. Append:

```python
def test_shard_jobs_carry_cov_context():
    """Without --cov-context=test on BOTH shard-producing jobs, the .coverage
    file sync_claims combines has no per-test data at all, and coverage-based
    enforcement silently finds nothing -- the exact failure mode this whole
    mechanism exists to avoid, one level up the chain."""
    spec = _load_ci_workflow()
    for job_name in ("python_goldenmatch", "python_goldenmatch_heavy"):
        steps = spec["jobs"][job_name]["steps"]
        cov_steps = [s for s in steps if "--cov=goldenmatch" in (s.get("run") or "")]
        assert cov_steps, f"{job_name} has no --cov=goldenmatch step to check"
        assert any("--cov-context=test" in s["run"] for s in cov_steps), (
            f"{job_name}'s coverage step is missing --cov-context=test"
        )


def test_sync_claims_depends_on_the_shard_jobs():
    spec = _load_ci_workflow()
    needs = spec["jobs"]["sync_claims"]["needs"]
    assert "python_goldenmatch" in needs
    assert "python_goldenmatch_heavy" in needs


def test_sync_claims_degrades_when_shard_jobs_are_skipped():
    """The `if:` must tolerate SKIPPED (not require success()) on both shard
    jobs, or sync_claims never runs at all on a PR that does not touch
    goldenmatch code -- exactly the scenario coverage-based enforcement must
    degrade through, not disappear under."""
    spec = _load_ci_workflow()
    job = spec["jobs"]["sync_claims"]
    condition = job["if"]
    assert "always()" in condition, (
        "the if: must start from always() or an implicit success() re-requires "
        "both shard jobs to have run, defeating graceful degradation"
    )
    for dep in ("python_goldenmatch", "python_goldenmatch_heavy"):
        assert f"needs.{dep}.result != 'failure'" in condition
        assert f"needs.{dep}.result != 'cancelled'" in condition


def test_sync_claims_downloads_coverage_shards_and_passes_the_flag():
    spec = _load_ci_workflow()
    steps = spec["jobs"]["sync_claims"]["steps"]
    download_steps = [s for s in steps if s.get("uses", "").startswith("actions/download-artifact")]
    assert download_steps, "sync_claims has no download-artifact step"
    assert download_steps[0].get("with", {}).get("pattern") == "gm-cov-*"
    report_steps = [s for s in steps if "sync_claims.report" in (s.get("run") or "")]
    assert report_steps, "sync_claims has no report step"
    assert "--coverage-db" in report_steps[0]["run"], (
        "the report step never passes --coverage-db even conditionally"
    )
```

- [ ] **Step 5: Run to verify pass (the wiring already exists from Steps 1-2)**

```bash
PYTHONPATH=scripts D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_workflow_yaml.py -q --no-header -p no:cacheprovider
```

Expected: all pass, including the four new tests.

- [ ] **Step 6: Sabotage-verify each new wiring test**

```bash
cd /d/show_case/gm-rel3161
cp .github/workflows/ci.yml /tmp/ci.bak && test -s /tmp/ci.bak && echo "backup ok"
run() { PYTHONPATH=scripts D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_workflow_yaml.py -q --no-header -p no:cacheprovider -k "shard_jobs_carry or depends_on_the_shard or degrades_when or downloads_coverage" 2>&1 | tail -6; }

echo "### A. drop --cov-context=test from python_goldenmatch's step"
sed -i.orig 's/--cov=goldenmatch --cov-report= --cov-context=test \\/--cov=goldenmatch --cov-report= \\/' .github/workflows/ci.yml
run
cp /tmp/ci.bak .github/workflows/ci.yml

echo "### B. drop sync_claims's needs on python_goldenmatch_heavy"
python - <<'EOF'
import pathlib
p = pathlib.Path(".github/workflows/ci.yml")
s = p.read_text(encoding="utf-8")
old = "needs: [changes, python_goldenmatch, python_goldenmatch_heavy]"
new = "needs: [changes, python_goldenmatch]"
assert s.count(old) == 1, "sabotage B did not apply"
p.write_text(s.replace(old, new), encoding="utf-8")
EOF
run
cp /tmp/ci.bak .github/workflows/ci.yml

echo "### C. require success() instead of always()"
python - <<'EOF'
import pathlib
p = pathlib.Path(".github/workflows/ci.yml")
s = p.read_text(encoding="utf-8")
old = """    if: >-
      always() &&
      (needs.changes.outputs.sync_claims == 'true' || needs.changes.outputs.force_all == 'true') &&
      needs.python_goldenmatch.result != 'failure' &&
      needs.python_goldenmatch.result != 'cancelled' &&
      needs.python_goldenmatch_heavy.result != 'failure' &&
      needs.python_goldenmatch_heavy.result != 'cancelled'"""
new = """    if: needs.changes.outputs.sync_claims == 'true' || needs.changes.outputs.force_all == 'true'"""
assert s.count(old) == 1, "sabotage C did not apply"
p.write_text(s.replace(old, new), encoding="utf-8")
EOF
run
cp /tmp/ci.bak .github/workflows/ci.yml

echo "### D. drop the --coverage-db flag from the report step"
python - <<'EOF'
import pathlib
p = pathlib.Path(".github/workflows/ci.yml")
s = p.read_text(encoding="utf-8")
old = "uv run python -m sync_claims.report --coverage-db .coverage"
new = "uv run python -m sync_claims.report"
assert s.count(old) == 1, "sabotage D did not apply"
p.write_text(s.replace(old, new), encoding="utf-8")
EOF
run
cp /tmp/ci.bak .github/workflows/ci.yml

echo "### restored:"
PYTHONPATH=scripts D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_workflow_yaml.py -q --no-header -p no:cacheprovider
```

Each of the four sabotages must fail exactly the test that names it, and the restore must return the file to a clean `git diff` against the committed state.

- [ ] **Step 7: Run the full filter and job-vs-filter gates**

```bash
D:/show_case/goldenmatch/.venv/Scripts/python.exe scripts/check_filter_coverage.py
```

Expected: `CI filter coverage OK` and `CI job-vs-filter coverage OK ... 0 new`. `sync_claims`'s own filter is unchanged by this task (still watches the goldenmatch tree, which is exactly what gates both the shard jobs and this job identically), so no filter edit is expected here -- if the gate reports a new gap, that is a sign something in this task's diff drifted from that assumption and needs investigating before continuing, not a gate to silence.

- [ ] **Step 8: Commit**

```bash
git add .github/workflows/ci.yml scripts/test_workflow_yaml.py
git commit -F - <<'EOF'
ci: wire coverage-based enforcement into the sync_claims job

python_goldenmatch and python_goldenmatch_heavy gain --cov-context=test
on their coverage-producing pytest invocations. sync_claims downloads
the same gm-cov-* shard artifacts python_goldenmatch_coverage already
combines (that job uploads nothing itself -- confirmed by reading it
rather than assuming), and combines them a second time itself, keeping
the raw .coverage file with contexts intact -- coverage xml, what the
required gate produces, drops context data entirely.

Depends on the shard jobs directly with always() + explicit
skip-tolerance, mirroring dead_code's existing pattern for the exact
same reason: a PR that does not touch goldenmatch code must degrade to
text-only enforcement, not hang waiting on jobs that never run.

Four sabotage-verified wiring tests, each failing the specific thing it
names: missing --cov-context=test, a missing needs: entry, success()
silently re-imposed instead of always(), and a dropped --coverage-db
flag. This is the exact failure shape the programme keeps finding --
PR #2839's silently-skipped jobs, the B3 ratchet's own test file outside
its filter -- so every new dependency here gets its own proof that
breaking it is visible.
EOF
```

---

## Self-Review

**Spec coverage.**

| spec requirement (Stages 1-3) | task |
| --- | --- |
| contexts survive xdist + combine, proven not assumed | Task 1, Steps 5-8 |
| general function-span scan, not `_py_function_spans` | Task 1, Step 3 |
| `_alias_score_matrix` resolves via real coverage data | Task 2, Step 1 (test), Step 4 |
| `enforced = text OR coverage`, strict widening | Task 2, Step 3 |
| coverage-rescue scoped to high-confidence only | Task 2, Step 3 + Step 7 sabotage |
| empty-string context filtered | Task 1, Step 3 + Step 9 sabotage |
| graceful degradation, visible in the report | Task 2, Steps 1/3; Task 3, Steps 2/6 |
| CI wiring, sabotage-verified | Task 3 |
| measure the real wall-clock cost | Not covered -- see below |

**Gap found and left open on purpose:** the spec's Being Wrong section requires measuring the actual CI wall-clock cost of `--cov-context=test` on this repo's real matrix before treating always-on as settled, and names a scheduled/nightly lane as the fallback if it is too expensive. That measurement can only happen once Task 3 has actually run in real CI -- there is no local proxy for the full 6-shard matrix's timing. This plan does not add a task for it because there is nothing to implement yet; it is the first thing to check once Task 3's PR has a real CI run, and if the delta is too large, downgrading `sync_claims`'s trigger from every-PR to scheduled is a follow-up change to this same job, not a new subsystem.

**Placeholder scan:** no TBD/TODO; `_locate_target`'s known name-collision limitation is stated explicitly with its cost and scope, not hidden.

**Type consistency:** `function_spans(root) -> dict[str, list[tuple[str, int, int]]]` (Task 1) is the exact type `inventory()` builds and passes to `function_contexts` (Task 2). `coverage_enforced(claimant_key, target_key, contexts)`'s signature in Task 1 matches its two call-shapes in Task 2 exactly (`(module, symbol)` tuples both places). `Claim` is unchanged by this plan -- `coverage_consulted`/`coverage_enforced` live only in `inventory()`'s returned dict, not on the dataclass, so nothing here touches Task 1/2/3 of the original C0 plan's still-shipping interfaces.
