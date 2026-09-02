"""Assert CI path filters cover the surfaces they claim to gate (#1846).

A path-filtered job can only catch what its filter matches. When a filter
lists fewer paths than the job actually gates, the job goes SILENT on exactly
the changes it exists to catch -- and the failure surfaces later, on unrelated
PRs that happen to touch a listed path, which reads as "that PR broke it".

That happened: `quality_gate` pins f1 and f1_probabilistic in its scorecard but
did not list `core/probabilistic.py`. It was skipped on #1829 / #1834 / #1836 /
#1840 -- every recent PR able to move those numbers -- and historical_50k
f1_probabilistic fell 0.83 -> 0.33 on the native path with nothing to catch it.

`workflow_lint` only checks that the YAML parses. This checks that it MEANS
something. Same spirit as #435 (benchmark_runner had the identical hole).

Both checks above are CURATED: REQUIRED/FORBIDDEN are populated one incident
at a time, by a human noticing a specific gate pins a specific number. Neither
inspects what a job actually RUNS, so neither could have caught #2839: `dead_code`
and `goldenmatch_sweep_coverage` were gated on `python_goldenmatch` (watches
`packages/python/goldenmatch/**`) while the detector they run lives entirely
under `scripts/`. A detector-only PR went green having never executed the
detector or its own test suite -- exactly the #1846 class, one level up, and
this time the curated table had no entry to catch it because nobody had
written one yet.

`check_job_filter_coverage()` below is the generic version: for every job in
ci.yml, read what filters its own `if:` gates on, extract the repo-relative
paths its `run:` steps actually execute or read, and assert every such path
is covered by at least one of those filters. It is RATCHETED like
`KNOWN_DEAD` in scripts/test_no_new_dead_code.py and `KNOWN_POLARS_BOUND` in
scripts/test_cli_polars_free_sweep.py: `KNOWN_JOB_FILTER_GAPS` is a floor of
pre-existing gaps to work down, never a bucket to top up. It fails on any NEW
(job, path) pair outside that floor, and on any floor entry that is no longer
reproduced (fix the filter, then shrink the floor -- don't let it go stale).

Run: python scripts/check_filter_coverage.py
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import yaml

FILTERS = Path(__file__).resolve().parent.parent / ".github" / "filters.yml"
CI_WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
REPO_ROOT = Path(__file__).resolve().parent.parent

GM = "packages/python/goldenmatch/goldenmatch"

# filter name -> paths that MUST trigger it, with why. Each entry is a real
# regression or a real near-miss, not a hypothetical.
REQUIRED: dict[str, list[tuple[str, str]]] = {
    "quality_gate": [
        (f"{GM}/core/probabilistic.py", "scorecard pins f1_probabilistic (#1834, #1836)"),
        (f"{GM}/core/fused_match.py", "FS scoring path (#1834)"),
        (f"{GM}/backends/score_buckets.py", "native-on planner routes here (#1829)"),
        (f"{GM}/core/learned_blocking.py", "blocking; sibling of blocker.py (#1840, #1841)"),
        (f"{GM}/core/autoconfig.py", "the config decisions the scorecard pins"),
        (f"{GM}/core/blocker.py", "blocking fields/cost signals"),
        (f"{GM}/core/scorer.py", "scorecard pins f1"),
        (f"{GM}/core/cluster.py", "clusters decide the measured pairs"),
        (f"{GM}/core/pipeline.py", "routing picks WHICH scorer runs"),
        ("packages/rust/extensions/native/src/lib.rs", "baseline is blessed native-on"),
        ("scripts/autoconfig_quality/datasets.py", "the harness itself"),
        (".github/filters.yml", "self-test: filter edits must re-run the gate"),
    ],
    "benchmark_runner": [
        (f"{GM}/core/probabilistic.py", "#435: the library being benchmarked"),
        (f"{GM}/core/scorer.py", "#435"),
        (f"{GM}/core/pipeline.py", "#435"),
    ],
    # A rename, not a missing path, is what broke this one: #2494 moved the tier
    # from goldenmatch/sail/ to goldenmatch/spark/ and the filter kept watching
    # the old directory -- which by then held only the back-compat shim. Both
    # Spark lanes went silent on every change to the tier they gate. Listing the
    # real source dir here means the NEXT rename fails workflow_lint loudly.
    "spark": [
        (f"{GM}/spark/clustering.py", "#2494: the tier's own source, post-rename"),
        (f"{GM}/spark/scorers.py", "#2494"),
        (f"{GM}/spark/session.py", "#2494"),
        (f"{GM}/sail/__init__.py", "the deprecated alias still ships"),
        (
            "packages/python/goldenmatch/pyproject.toml",
            "both lanes install via the [spark]/[sail] extras defined here",
        ),
        (
            "packages/jvm/goldenmatch-spark/src/dev/goldensuite/spark/GoldenScoreUdf.java",
            "J0: the JVM scorer jar is built + self-tested in the spark_connect lane",
        ),
    ],
    # The doc-generation gates had the same hole in two places at once, and both
    # were invisible because a SECOND job happened to cover the same artifacts:
    #   docs_regen     -- ci.yml calls it "the single authoritative doc-drift gate"
    #                     while its filter omitted scripts/config_matrix/**, which
    #                     RENDERS six of the nine artifacts it regenerates. Only the
    #                     `config_matrix` job (which the same comment proposes to
    #                     delete) kept it honest.
    #   docs_staleness -- gated on `docs`, which matches no packages/python/**/*.py
    #                     path, so its flag rule was skipped on exactly the diffs it
    #                     exists to catch and could fire only where docs were
    #                     already updated.
    "docs_regen": [
        ("scripts/config_matrix/render.py", "renders every config-matrix.mdx block"),
        ("scripts/config_matrix/manifest.py", "renders docs/agent-manifest.json"),
        ("scripts/config_matrix/registry.py", "declares what each package renders"),
        ("scripts/config_matrix/roster.py", "DOCUMENTED drives every generator's package list"),
        ("scripts/gen_api_surface.py", "renders the api-surface capability matrix"),
        ("scripts/agent_codemap.py", "renders docs/agent-codemap.json"),
        ("packages/rust/extensions/native/src/lib.rs", "the <PREFIX>_* env scan reads Rust"),
        ("packages/typescript/goldenmatch/package.json", "api-surface TS version column"),
        ("parity/goldenmatch.yaml", "MCP tool counts in suite-matrix + api-surface"),
        (".github/filters.yml", "self-test: filter edits must re-run the gate"),
        # Absorbed when the 6-leg `config_matrix` job was merged into docs_regen.
        # A glob-aware diff of the two filters showed these were the only entries
        # not already covered; losing them would have silently narrowed the gate.
        ("llms.txt", "the suite tool total, rewritten by check_llms_counts --write"),
        ("scripts/test_config_matrix.py", "gate unit tests now run in this job"),
        ("scripts/test_roster.py", "gate unit tests now run in this job"),
        ("scripts/test_regen_docs.py", "gate unit tests now run in this job"),
        ("scripts/test_llms_counts.py", "gate unit tests now run in this job"),
        ("scripts/check_thesis_conformance.py", "the thesis auditor now runs in this job"),
    ],
    "docs_staleness": [
        (f"{GM}/core/pipeline.py", "the flag rule scans non-test packages/python/**/*.py"),
        (f"{GM}/core/autoconfig.py", "same: a new GOLDENMATCH_* knob lands in ordinary source"),
        ("docs-site/goldenmatch/tuning.mdx", "the canonical prose flag reference"),
        ("scripts/config_matrix/registry.py", "declares env_prefix + prose_flag_page"),
        (".github/filters.yml", "self-test: filter edits must re-run the gate"),
    ],
    # The umbrella doc gates (check_docs_consistency / _links / _sections) assert
    # against the published-package roster, which is derived from the publish-*.yml
    # callers -- so adding a publisher without a doc surface must re-run them.
    "docs": [
        ("docs-site/docs.json", "nav integrity + orphan detection"),
        ("packages/python/goldenmatch/CHANGELOG.md", "changelog<->version lockstep"),
        ("packages/python/goldenmatch/goldenmatch/llms.txt", "agent-surface pointers"),
        ("scripts/check_docs_consistency.py", "self-test"),
        (
            "scripts/config_matrix/roster.py",
            "the canonical roster both docs gates derive their package list from",
        ),
    ],
}

# Paths that must NOT trigger these filters -- guards against "fix" by
# over-matching, which turns a gate into a tax on every PR.
FORBIDDEN: dict[str, list[str]] = {
    "quality_gate": [
        "README.md",
        "docs/design/foo.md",
        "packages/typescript/goldenmatch/src/cli.ts",
    ],
}


def _matches(path: str, patterns: list[str]) -> str | None:
    """Approximate dorny/paths-filter: globs are unanchored, ** spans dirs."""
    for p in patterns:
        if fnmatch.fnmatch(path, p) or fnmatch.fnmatch(path, p.replace("**", "*")):
            return p
    return None


def _covered(path: str, patterns: list[str]) -> str | None:
    """Same approximation as `_matches`, extended for one real distinction
    dorny/paths-filter itself makes: it only ever evaluates CHANGED FILES, never
    bare directories. A `run:` step that does `cd some/dir` or
    `pytest some/dir` names a directory as shorthand for "everything under
    it" -- that string can never appear as a real changed-file path, so
    matching it literally against a `some/dir/**` pattern is asking a question
    dorny would never ask, and fails for a reason that has nothing to do with
    coverage (validated against `goldenflow_nopolars`, which lists
    'packages/python/goldenflow/tests/nopolars/**' in filters.yml and passes
    `pytest packages/python/goldenflow/tests/nopolars` in ci.yml -- genuinely
    covered, but the bare-string match above says no). For a path that is a
    directory on disk, additionally ask whether a file directly inside it
    would match -- that mirrors what dorny would actually see the day a file
    in that directory changes.
    """
    hit = _matches(path, patterns)
    if hit is not None:
        return hit
    if (REPO_ROOT / path).is_dir():
        return _matches(f"{path.rstrip('/')}/__probe__", patterns)
    return None


# Generic job-vs-filter coverage check (companion to REQUIRED/FORBIDDEN above,
# see the module docstring). Only tokens that look like a path AND exist on
# disk are treated as evidence -- a `--fail-under=0` or a bare "coverage.xml"
# generated at CI time and absent from a checkout is noise, not a path the job
# depends on.
_PATH_TOKEN_RE = re.compile(r"[^\s\"'=,;()`]+")
_PATH_SHAPE_RE = re.compile(r"^[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)+$")


def _strip_shell_comment(line: str) -> str:
    """Drop a trailing `# ...` shell comment. A `#` at the start of the line
    (after leading whitespace) or preceded by whitespace opens a comment; a
    `#` glued to other text (e.g. inside a string) does not. Imperfect for a
    `#` inside a quoted string, but this only widens what gets IGNORED, never
    what gets flagged -- consistent with "skip anything inside a comment"."""
    lstripped = line.lstrip()
    if lstripped.startswith("#"):
        return line[: len(line) - len(lstripped)]
    m = re.search(r"(?<=\s)#", line)
    return line[: m.start()] if m else line


def _extract_run_paths(run_text: str) -> set[str]:
    """Conservative repo-relative path extraction from a `run:` shell script:
    tokenize on whitespace/quotes/`=`/punctuation, keep tokens shaped like a
    path (>=1 `/`, no shell metacharacters), keep only ones that exist on disk
    relative to the repo root."""
    found: set[str] = set()
    for raw_line in run_text.splitlines():
        line = _strip_shell_comment(raw_line)
        for tok in _PATH_TOKEN_RE.findall(line):
            tok = tok.strip("'\"`,;()")
            if not _PATH_SHAPE_RE.match(tok):
                continue
            if (REPO_ROOT / tok).exists():
                found.add(tok)
    return found


def _output_to_filter_map(ci_spec: dict) -> dict[str, str]:
    """changes.outputs.<name> -> the paths-filter key it actually reads.
    Usually name == key, but not always: `web_ui_e2e` job output is wired to
    `steps.filter.outputs.web`. Outputs sourced from a different step
    (`python_pkgs` from `steps.set_python`, `force_all` from `steps.flags`)
    aren't paths-filter-backed at all and are left out of the map -- a job
    gating on one of those has no filters.yml pattern to check coverage
    against, so it's out of scope for this check, not a violation.
    """
    outputs = ci_spec["jobs"]["changes"].get("outputs", {})
    mapping: dict[str, str] = {}
    for out_name, expr in outputs.items():
        m = re.search(r"steps\.filter\.outputs\.(\w+)", str(expr))
        if m:
            mapping[out_name] = m.group(1)
    return mapping


def _job_gating_filters(job: dict, output_to_filter: dict[str, str]) -> set[str]:
    """The paths-filter keys job's `if:` gates on. `force_all` is an override
    ("run regardless"), not a coverage claim, and is excluded. A job with no
    `if:` runs unconditionally -- not a violation, so it yields no filters and
    is skipped entirely by the caller."""
    if_expr = job.get("if")
    if not if_expr:
        return set()
    names = set(re.findall(r"needs\.changes\.outputs\.(\w+)", str(if_expr)))
    names.discard("force_all")
    return {output_to_filter[n] for n in names if n in output_to_filter}


# Ratchet, same contract as KNOWN_DEAD (scripts/test_no_new_dead_code.py) and
# KNOWN_POLARS_BOUND (scripts/test_cli_polars_free_sweep.py): a floor of
# pre-existing (job, path) gaps to work DOWN, never a bucket to top up.
# Populated in one pass from `check_job_filter_coverage()` against this
# branch's ci.yml (with the dead_code filter fix already applied -- neither
# `dead_code` nor `goldenmatch_sweep_coverage` appears below). Each value is
# one line: what the job runs there, and why its current filter set misses it.
KNOWN_JOB_FILTER_GAPS: dict[tuple[str, str], str] = {
    ("workflow_lint", "scripts/test_distributed_test_files.py"): (
        "any_workflow/ci_workflow list the YAML checker but not its own gate test"
    ),
    ("workflow_lint", "scripts/test_workflow_yaml.py"): (
        "any_workflow/ci_workflow list the YAML checker but not its own gate test"
    ),
    ("python_goldenmatch", "scripts/build_native.py"): (
        "runs the native build script; python_goldenmatch only watches packages/**"
    ),
    ("python_goldenmatch", "scripts/check_map_elements.py"): (
        "runs this scripts/ checker; python_goldenmatch only watches packages/**"
    ),
    ("python_skipped_lanes", "docs/ci-lanes.md"): (
        "step reads/updates this doc; not covered by python_goldenmatch"
    ),
    ("python_postgres", "packages/python/goldenmatch"): (
        "cd's into the whole package; python_goldenmatch_postgres lists only "
        "db/memory + 3 test files"
    ),
    ("quality_gate", "scripts/build_native.py"): (
        "runs the native build script; quality_gate's REQUIRED list is source files"
    ),
    ("api_parity", "scripts/test_api_parity.py"): ("filter doesn't list its own gate test"),
    ("downstream_symbols", "packages/python/goldenmatch"): (
        "pip installs the outer package; filter covers only the nested "
        "goldenmatch/goldenmatch/** source dir"
    ),
    ("downstream_symbols", "scripts/test_downstream_symbols.py"): (
        "filter doesn't list its own gate test"
    ),
    ("unlocked_resolution", "scripts/audit_dep_ceilings.py"): (
        "gated on python_goldenmatch alone; doesn't cover this scripts/ tool"
    ),
    ("unlocked_resolution", "scripts/smoke_unlocked_install.py"): (
        "gated on python_goldenmatch alone; doesn't cover this scripts/ tool"
    ),
    ("typescript", "packages/python/goldenmatch/scripts/emit_key_integrity_golden.py"): (
        "TS lane regenerates this corpus; typescript filter doesn't cover it"
    ),
    ("typescript", "packages/python/goldenmatch/scripts/gen_documents_corpus.py"): (
        "TS lane regenerates this corpus; typescript filter doesn't cover it"
    ),
    ("wasm_score", "packages/typescript/goldenmatch/scripts/bench_wasm_scorer.mjs"): (
        "filter doesn't list its own bench script"
    ),
    ("analysis_wasm", "packages/typescript/goldenanalysis/scripts/bench_wasm_aggregate.mjs"): (
        "filter doesn't list its own bench script"
    ),
    ("wasm_flow", "packages/python/goldenflow/tests/parity/dates_corpus.jsonl"): (
        "parity fixture the job reads; wasm_flow doesn't list tests/parity/**"
    ),
    ("wasm_flow", "packages/python/goldenflow/tests/parity/identifiers_corpus.jsonl"): (
        "parity fixture the job reads; wasm_flow doesn't list tests/parity/**"
    ),
    ("wasm_flow", "packages/python/goldenflow/tests/parity/profile_corpus.jsonl"): (
        "parity fixture the job reads; wasm_flow doesn't list tests/parity/**"
    ),
    ("native", "packages/python/goldenmatch/tests/test_native_block_seq_parity.py"): (
        "one of 9 parity tests the job runs; the other 8 are listed, this one isn't"
    ),
    ("goldencheck_native", "packages/python/goldencheck/tests/core"): (
        "test dir the job runs; goldencheck_native doesn't list tests/core/**"
    ),
    ("goldencheck_native", "packages/python/goldencheck/tests/profilers/test_fuzzy_values.py"): (
        "test file the job runs; not in goldencheck_native's list"
    ),
    (
        "goldencheck_native",
        "packages/python/goldencheck/tests/relations/test_approx_duplicate.py",
    ): ("test file the job runs; not in goldencheck_native's list"),
    ("goldencheck_native", "packages/python/goldencheck/tests/relations/test_approx_fd.py"): (
        "test file the job runs; not in goldencheck_native's list"
    ),
    ("goldencheck_native", "packages/python/goldencheck/tests/relations/test_composite_key.py"): (
        "test file the job runs; not in goldencheck_native's list"
    ),
    (
        "goldencheck_native",
        "packages/python/goldencheck/tests/relations/test_functional_dependency.py",
    ): ("test file the job runs; not in goldencheck_native's list"),
    ("goldenanalysis_native", "packages/python/goldenanalysis/tests/core"): (
        "test dir the job runs; analysis_native doesn't list tests/core/**"
    ),
    ("native_flow", "packages/python/goldenflow/scripts/gen_identifiers_corpus.py"): (
        "corpus-gen script the job runs; not in native_flow's list"
    ),
    ("native_flow", "packages/python/goldenflow/scripts/gen_profile_corpus.py"): (
        "corpus-gen script the job runs; not in native_flow's list"
    ),
    ("native_flow", "packages/python/goldenflow/tests/transforms/test_dates.py"): (
        "test file the job runs; not in native_flow's list"
    ),
    ("native_flow", "packages/python/goldenflow/tests/transforms/test_identifiers_parity.py"): (
        "test file the job runs; not in native_flow's list"
    ),
    ("native_flow", "packages/python/goldenflow/tests/transforms/test_phone.py"): (
        "test file the job runs; not in native_flow's list"
    ),
    ("goldencheck_nopolars", "scripts/build_goldencheck_native.py"): (
        "builds the native ext before the polars-free proof; filter doesn't list it"
    ),
    ("goldenmatch_nopolars", "scripts/test_cli_polars_free_sweep.py"): (
        "filter doesn't list its own gate test"
    ),
    ("goldenmatch_nopolars", "scripts/test_mcp_polars_free_sweep.py"): (
        "filter doesn't list its own gate test"
    ),
    ("spark_connect", "./packages/python/goldenmatch"): (
        "pip installs the real package source into the executor venv; the "
        "spark filter covers only goldenmatch/spark/** + sail/**"
    ),
    ("docs_regen", "scripts/test_docs_sections.py"): ("filter doesn't list this gate test"),
    ("throughput-gate", "packages/python/goldenmatch"): (
        "pip install -e's the whole package; throughput's list is file-scoped"
    ),
    ("throughput-gate", "scripts/build_native.py"): (
        "runs the native build script; not in throughput's list"
    ),
    ("pgrx_sql_sync", "scripts/check_pgrx_sql_sync.py"): (
        "filter doesn't list its own checker script"
    ),
}


def check_job_filter_coverage(
    ci_path: Path = CI_WORKFLOW, filters_path: Path = FILTERS
) -> set[tuple[str, str]]:
    """For every job in `ci_path`, does its own gating filter set (from
    `filters_path`) cover every path its `run:` steps actually touch? See the
    module docstring. Paths are still existence-checked against REPO_ROOT
    (this repo's checkout), even when `ci_path`/`filters_path` point at a
    scratch copy used in a test -- so a synthetic job must reference a real
    on-disk path to be extracted at all.
    """
    ci_spec = yaml.safe_load(ci_path.read_text(encoding="utf-8"))
    filters_spec = yaml.safe_load(filters_path.read_text(encoding="utf-8"))
    output_to_filter = _output_to_filter_map(ci_spec)

    violations: set[tuple[str, str]] = set()
    for job_name, job in ci_spec["jobs"].items():
        if job_name == "changes":
            continue
        filter_names = _job_gating_filters(job, output_to_filter)
        if not filter_names:
            continue  # no if:, or gated only on force_all / a non-filter output
        patterns: list[str] = []
        for fname in filter_names:
            patterns.extend(filters_spec.get(fname) or [])
        paths: set[str] = set()
        for step in job.get("steps", []):
            run_text = step.get("run")
            if run_text:
                paths |= _extract_run_paths(run_text)
        for path in paths:
            if _covered(path, patterns) is None:
                violations.add((job_name, path))
    return violations


def main() -> int:
    spec = yaml.safe_load(FILTERS.read_text(encoding="utf-8"))
    failures: list[str] = []

    for name, cases in REQUIRED.items():
        pats = spec.get(name)
        if not pats:
            failures.append(f"{name}: filter missing entirely")
            continue
        for path, why in cases:
            if _matches(path, pats) is None:
                failures.append(
                    f"{name}: does NOT match {path}\n"
                    f"      why it must: {why}\n"
                    f"      -> add a pattern to `{name}:` in .github/filters.yml"
                )

    for name, paths in FORBIDDEN.items():
        pats = spec.get(name) or []
        for path in paths:
            hit = _matches(path, pats)
            if hit is not None:
                failures.append(
                    f"{name}: matches {path} via '{hit}' -- too broad; the gate "
                    f"would run on unrelated PRs"
                )

    # Generic job-vs-filter check (see module docstring), ratcheted against
    # KNOWN_JOB_FILTER_GAPS. NEW violations fail the check; a KNOWN entry that
    # no longer reproduces must be removed from the map, same as KNOWN_DEAD.
    found = check_job_filter_coverage()
    known = set(KNOWN_JOB_FILTER_GAPS)
    new_gaps = found - known
    stale_gaps = known - found

    for job_name, path in sorted(new_gaps):
        failures.append(
            f"{job_name}: runs {path} but none of its gating filters cover it\n"
            f"      -> add a pattern to the filter(s) `{job_name}` gates on in "
            f".github/filters.yml, or add a `# {job_name} doesn't run {path}` "
            f"style fix to the job itself"
        )
    for job_name, path in sorted(stale_gaps):
        failures.append(
            f"KNOWN_JOB_FILTER_GAPS[{(job_name, path)!r}] no longer reproduces -- "
            f"remove it from scripts/check_filter_coverage.py so the ratchet keeps its value"
        )

    if failures:
        print("CI filter coverage FAILED:\n")
        for f in failures:
            print(f"  - {f}")
        print(
            "\nA path-filtered job cannot catch what its filter does not match.\n"
            "If a file can move a number a gate pins, the filter must list it."
        )
        return 1

    total = sum(len(v) for v in REQUIRED.values())
    print(f"CI filter coverage OK ({total} required paths across {len(REQUIRED)} filters)")
    print(f"CI job-vs-filter coverage OK ({len(known)} known pre-existing gaps, 0 new)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
