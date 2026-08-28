# Ray CI Gate Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the ray-executing distributed suite able to fail a merge, by first removing the 59% of that job which is a duplicate of two gates that already block.

**Architecture:** A single Python module owns the partition of `tests/test_distributed_*.py` across the three distributed CI jobs, and each job asks it for its file list. The partition becomes executable rather than asserted, so the dead `--ignore` flags cannot come back and a newly added test file cannot land unassigned. With the duplication gone the job runs in roughly a third of its current wall, which is what makes promoting it to `ci-required` affordable.

**Tech Stack:** Python 3.12, pytest, GitHub Actions, `dorny/paths-filter@v3`

**Spec:** `docs/superpowers/specs/2026-08-28-ray-production-readiness-design.md`

## Global Constraints

- Gate scripts live at repo-root `scripts/check_*.py` or `scripts/<name>.py`; their unit tests live beside them as `scripts/test_<name>.py` and run in the `scripts_lint` job, which is in `ci-required`.
- Gate scripts start with `from __future__ import annotations`, carry a module docstring naming the rule and the spec, and exit 1 on failure.
- Every gate must guard against passing while scanning nothing — the repo has been bitten by this class and `scripts/test_workflow_yaml.py` documents it explicitly.
- `scripts/` is linted by `uv run ruff check scripts/` using the workspace ruff, not a pinned one.
- Ray tests must be invoked with `.venv/bin/python`, never `uv run` — under `uv run` the raylet spawns workers that cannot import ray and every task hangs.
- Ray requires `pandas<3`; Ray 2.56's hash partitioner raises `ValueError: output array is read-only` under pandas 3.x.
- Do not touch `_PAIR_PEAK_BYTES` in `clustering.py` or its test. That constant is correct.

---

### Task 1: The distributed test partition

**Files:**
- Create: `scripts/distributed_test_files.py`
- Create: `scripts/test_distributed_test_files.py`

**Interfaces:**
- Produces: `partition(tests_dir: Path) -> dict[str, list[Path]]` returning keys `"invariance"`, `"wcc"`, `"broad"`; `GATED: dict[str, str]` mapping job key to the single filename it owns; `main(argv: list[str] | None = None) -> int` driving `--job <key>` and printing one path per line.

- [ ] **Step 1: Write the failing test**

Create `scripts/test_distributed_test_files.py`:

```python
"""Unit tests for the distributed test partition.

The partition's value is entirely in two things it must not do: let a gate file
leak into the broad job (which is how 106 of 181 tests became duplicates), and
report a clean partition while scanning an empty directory.

Spec: docs/superpowers/specs/2026-08-28-ray-production-readiness-design.md
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import distributed_test_files as mod  # noqa: E402


@pytest.fixture
def tests_dir(tmp_path):
    d = tmp_path / "tests"
    d.mkdir()
    for name in (
        "test_distributed_clustering.py",
        "test_distributed_randomized_contraction_wcc.py",
        "test_distributed_golden.py",
        "test_distributed_pipeline.py",
        "test_unrelated.py",
    ):
        (d / name).write_text("", encoding="utf-8")
    return d


def test_gate_files_go_to_their_own_jobs(tests_dir):
    part = mod.partition(tests_dir)
    assert [p.name for p in part["invariance"]] == ["test_distributed_clustering.py"]
    assert [p.name for p in part["wcc"]] == [
        "test_distributed_randomized_contraction_wcc.py"
    ]


def test_gate_files_never_appear_in_broad(tests_dir):
    """The bug this module exists to prevent: --ignore is a no-op against
    explicitly-named paths, so the glob re-ran both blocking gates."""
    broad = {p.name for p in mod.partition(tests_dir)["broad"]}
    assert "test_distributed_clustering.py" not in broad
    assert "test_distributed_randomized_contraction_wcc.py" not in broad


def test_broad_takes_every_other_distributed_file(tests_dir):
    broad = {p.name for p in mod.partition(tests_dir)["broad"]}
    assert broad == {"test_distributed_golden.py", "test_distributed_pipeline.py"}


def test_non_distributed_files_are_not_claimed(tests_dir):
    everything = {p.name for files in mod.partition(tests_dir).values() for p in files}
    assert "test_unrelated.py" not in everything


def test_partition_is_disjoint(tests_dir):
    part = mod.partition(tests_dir)
    seen = [p for files in part.values() for p in files]
    assert len(seen) == len(set(seen))


def test_empty_directory_raises_rather_than_passing_clean(tmp_path):
    """A partition over nothing is the 'gate that scans nothing' failure."""
    empty = tmp_path / "tests"
    empty.mkdir()
    with pytest.raises(SystemExit):
        mod.partition(empty)


def test_missing_gate_file_raises(tmp_path):
    """If a gate file is renamed, fail loudly instead of silently gating nothing."""
    d = tmp_path / "tests"
    d.mkdir()
    (d / "test_distributed_golden.py").write_text("", encoding="utf-8")
    with pytest.raises(SystemExit):
        mod.partition(d)


def test_main_prints_one_path_per_line(tests_dir, capsys):
    rc = mod.main(["--job", "broad", "--tests-dir", str(tests_dir)])
    assert rc == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    assert all(line.endswith(".py") for line in lines)


def test_main_rejects_an_unknown_job(tests_dir):
    with pytest.raises(SystemExit):
        mod.main(["--job", "nope", "--tests-dir", str(tests_dir)])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest scripts/test_distributed_test_files.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'distributed_test_files'`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/distributed_test_files.py`:

```python
"""Owns the partition of tests/test_distributed_*.py across the three
distributed CI jobs.

`.github/workflows/ci.yml` used to express the split as a shell glob plus two
`--ignore` flags. The glob expands to explicit paths and `--ignore` only filters
directory traversal, so both flags were no-ops: on run 33176961803 the
`distributed_broad` job collected 51 tests from test_distributed_clustering.py
and 55 from test_distributed_randomized_contraction_wcc.py -- 106 of its 181
tests were a second run of the two jobs that already block the merge queue.

Each job now asks this module for its file list, so the partition is executed
rather than asserted.

Usage::

    python3 scripts/distributed_test_files.py --job broad

Spec: docs/superpowers/specs/2026-08-28-ray-production-readiness-design.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_DEFAULT_TESTS_DIR = Path("packages/python/goldenmatch/tests")

# Files with their own blocking job. Everything else matching the glob is broad.
GATED: dict[str, str] = {
    "invariance": "test_distributed_clustering.py",
    "wcc": "test_distributed_randomized_contraction_wcc.py",
}

_GLOB = "test_distributed_*.py"


def partition(tests_dir: Path) -> dict[str, list[Path]]:
    """Split the distributed test files across the three jobs.

    Exits non-zero rather than returning an empty or incomplete partition: a
    gate that reports clean while scanning nothing is the failure mode this
    module was written to remove.
    """
    found = sorted(tests_dir.glob(_GLOB))
    if not found:
        sys.exit(
            f"error: no {_GLOB} files under {tests_dir} -- "
            "the partition would gate nothing"
        )

    by_name = {p.name: p for p in found}
    missing = [name for name in GATED.values() if name not in by_name]
    if missing:
        sys.exit(
            "error: gated file(s) not found: "
            + ", ".join(missing)
            + " -- if a gate file was renamed, update GATED in this module"
        )

    result: dict[str, list[Path]] = {job: [by_name[name]] for job, name in GATED.items()}
    result["broad"] = [p for p in found if p.name not in set(GATED.values())]
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--job", required=True, choices=["invariance", "wcc", "broad"])
    ap.add_argument("--tests-dir", default=str(_DEFAULT_TESTS_DIR))
    args = ap.parse_args(argv)

    for path in partition(Path(args.tests_dir))[args.job]:
        print(path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest scripts/test_distributed_test_files.py -q`
Expected: PASS, 8 passed

- [ ] **Step 5: Lint**

Run: `uv run ruff check scripts/distributed_test_files.py scripts/test_distributed_test_files.py`
Expected: no findings

- [ ] **Step 6: Verify it reports the real repo correctly**

Run: `python scripts/distributed_test_files.py --job broad | wc -l`
Expected: `20` — all 22 top-level `test_distributed_*.py` files minus the two gate files. Thirteen of the 20 require ray and run nowhere else; the other seven also run in `python_goldenmatch`.

Run: `python scripts/distributed_test_files.py --job broad | grep -c -E 'clustering\.py|randomized_contraction_wcc\.py'`
Expected: `0`

- [ ] **Step 7: Commit**

```bash
git add scripts/distributed_test_files.py scripts/test_distributed_test_files.py
git commit -m "test(ci): own the distributed test partition in one module

--ignore does not filter paths named explicitly on the command line, so the
distributed_broad job's two --ignore flags were no-ops and it re-ran both
blocking gates: 106 of its 181 tests on run 33176961803 were duplicates."
```

---

### Task 2: Wire the three CI jobs to the partition

**Files:**
- Modify: `.github/workflows/ci.yml` — the `distributed` job's pytest step, the `distributed_wcc` job's pytest step, and the `distributed_broad` job's pytest step

**Interfaces:**
- Consumes: `scripts/distributed_test_files.py --job {invariance,wcc,broad}` from Task 1.

- [ ] **Step 1: Replace the `distributed` job's pytest step**

Find the step named `Distributed quality-invariance gate (blocking)` and replace its `run:` body with:

```yaml
        run: |
          .venv/bin/python -c "import ray, scipy, polars; print('ray', ray.__version__)"
          FILES=$(python3 scripts/distributed_test_files.py --job invariance)
          echo "$FILES"
          .venv/bin/python -m pytest $FILES \
            --timeout=300 --durations=20 -v
```

- [ ] **Step 2: Replace the `distributed_wcc` job's pytest step**

Find the step named `Randomized-contraction WCC ray gate (blocking)` and replace its `run:` body with:

```yaml
        run: |
          FILES=$(python3 scripts/distributed_test_files.py --job wcc)
          echo "$FILES"
          .venv/bin/python -m pytest $FILES \
            --timeout=300 --durations=20 -v
```

- [ ] **Step 3: Replace the `distributed_broad` job's pytest step**

Find the step named `Distributed broad coverage (non-blocking)` and replace the whole step with:

```yaml
      - name: Distributed execution suite
        run: |
          FILES=$(python3 scripts/distributed_test_files.py --job broad)
          echo "$FILES"
          .venv/bin/python -m pytest $FILES \
            --timeout=300 --durations=20 -v
```

The `--ignore` flags are deleted, not corrected — the partition module is now the only place the exclusion is expressed.

- [ ] **Step 4: Add the partition unit tests to the two lint lanes**

In the `workflow_lint` job, after the step named `Gate unit tests (the duplicate-key detector must actually detect)`, add:

```yaml
      - name: Gate unit tests (the distributed partition must stay disjoint)
        shell: bash
        run: |
          python3 -m pip install --quiet pytest
          python3 -m pytest scripts/test_distributed_test_files.py -q
```

`workflow_lint` fires on any workflow edit; `scripts_lint` fires on `scripts/**`. Both are in `ci-required`, and between them every edit that can break the partition runs its tests.

- [ ] **Step 5: Validate the workflow parses**

Run: `python3 scripts/check_workflow_yaml.py`
Expected: exit 0, no duplicate keys

Run: `python3 -m pytest scripts/test_workflow_yaml.py scripts/test_distributed_test_files.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "fix(ci): stop distributed_broad re-running both blocking gates

The job's two --ignore flags never fired -- the shell glob names the files
explicitly and --ignore only filters directory traversal. Each distributed job
now asks scripts/distributed_test_files.py for its file list."
```

- [ ] **Step 7: Push and read the job's real wall**

```bash
git push -u origin HEAD
```

Then, once CI has run, confirm the duplication is gone and record the new wall:

```bash
JOB=$(gh run view --repo benseverndev-oss/goldenmatch <run-id> --json jobs \
  --jq '.jobs[] | select(.name=="distributed_broad") | .databaseId')
gh run view --repo benseverndev-oss/goldenmatch --job "$JOB" --log \
  | rg -o 'tests/test_distributed_[a-z0-9_]+\.py::' | sort | uniq -c | sort -rn
```

Expected: no `test_distributed_clustering.py` or `test_distributed_randomized_contraction_wcc.py` rows, and a summary line near `75 passed` rather than `177 passed`. Record the wall — it was 669s and 929s on the two most recent main runs, and Task 3 depends on it dropping.

**Note:** `distributed_broad` is still `continue-on-error` at this point, so `gh run view --json` will report `conclusion: success` regardless of the real exit code. Read the summary line from the raw log, never the JSON conclusion.

---

### Task 3: Promote the execution suite to blocking

**Files:**
- Modify: `.github/workflows/ci.yml` — the `distributed_broad` job block and the `ci-required` `needs:` list

**Interfaces:**
- Consumes: the reduced job wall from Task 2.

**Precondition:** five consecutive green runs of the job on `main` *where it actually ran*. Path filters skip it on most commits, so count runs in which it produced a pytest summary, not calendar days. As of 2026-08-28 there are two: run `33176961803` (177 passed) and `33170819859` (167 passed), both under the pre-Task-2 duplicated configuration.

- [ ] **Step 1: Confirm the precondition**

```bash
for run in $(gh run list --repo benseverndev-oss/goldenmatch --workflow ci.yml \
    --branch main --limit 25 --json databaseId --jq '.[].databaseId'); do
  jid=$(gh run view "$run" --repo benseverndev-oss/goldenmatch --json jobs \
    --jq '.jobs[] | select(.name=="distributed_broad") | .databaseId' 2>/dev/null)
  [ -n "$jid" ] || continue
  sum=$(gh run view --repo benseverndev-oss/goldenmatch --job "$jid" --log 2>/dev/null \
    | rg -o '[0-9]+ (passed|failed).*' | tail -1)
  echo "$run  ${sum:-skipped}"
done
```

Expected: at least five lines showing `N passed` and none showing `failed`. If any run failed, stop and fix the failure — do not promote a job with a known red.

- [ ] **Step 2: Rename the job and drop `continue-on-error`**

Replace the `distributed_broad:` job header block. The name no longer describes it — it is the execution gate, not broad coverage — but renaming a job changes its check name, so keep the key and change only the comment and the flag:

```yaml
  # GATE (blocking): the ray-EXECUTING suite -- controller, dataset, scoring,
  # pipeline, golden, fs and phase5 end-to-end. These are the thirteen files that
  # require ray and therefore skip in python_goldenmatch, so this job is the only
  # place they run. #2797 is why they must gate: five worker-side handlers
  # swallowed exceptions and under-matched silently while reporting SUCCESS, and
  # no blocking job covered the path.
  #
  # Was continue-on-error with a comment citing fragile fixtures. That reason
  # went stale -- the suite has been green on every main run where it ran -- and
  # the wall objection went with it once the two blocking gates stopped being
  # re-run here (see scripts/distributed_test_files.py).
  #
  # Same ray/uv venv gotcha as the `distributed` job above.
  distributed_broad:
    needs: changes
    if: needs.changes.outputs.distributed == 'true' || needs.changes.outputs.force_all == 'true'
    runs-on: ubuntu-latest
    timeout-minutes: 20
```

That is the previous block with the `continue-on-error: true` line removed and the comment rewritten.

- [ ] **Step 3: Add it to `ci-required`**

In the `ci-required` job's `needs:` list, replace these three lines:

```yaml
      - distributed_wcc
      # NB: distributed_broad is deliberately ABSENT -- it's continue-on-error
      # (known-fragile fixtures) and must not gate the merge queue.
```

with:

```yaml
      - distributed_wcc
      # distributed_broad gates as of this change: it holds the thirteen
      # ray-EXECUTING suites, which skip in python_goldenmatch for want of ray
      # and so run nowhere else. Rollback is one line -- restore
      # `continue-on-error: true` on the job and drop it from this list.
      - distributed_broad
```

- [ ] **Step 4: Validate**

Run: `python3 scripts/check_workflow_yaml.py && python3 -m pytest scripts/test_workflow_yaml.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: gate on the ray-executing distributed suite

Thirteen files require ray, so they skip in python_goldenmatch and ran only in
this job, which could not fail a merge. #2797 showed what that costs. The
fragile-fixture rationale is stale and the wall objection went with the
duplicate gate runs."
```

---

### Task 4: Make bench provenance non-optional

**Files:**
- Modify: `.github/workflows/bench-ray-cluster.yml` — the `Record the resolved environment` step and the `Upload bench artifact` step

**Interfaces:**
- Consumes: nothing from earlier tasks; independently reviewable.

The provenance chain already exists and is good: `.ray/cluster-gce.yaml` pins polars, pandas, pyarrow, numpy and `goldenmatch-native` exactly, installs goldenmatch by git SHA, and writes `pip freeze` to `/tmp/bench_env.txt`; the workflow captures per-instance `cpuPlatform` and uploads all of it. What is missing is that none of it is enforced — a run can lose its result or its environment and stay green.

- [ ] **Step 1: Make the pip-freeze fetch fatal**

In the `Record the resolved environment` step, replace this line:

```bash
          ray rsync-down .ray/cluster-gce.yaml /tmp/bench_env.txt ".profile_tmp/qis/pipfreeze_${LABEL}.txt" || echo "could not fetch pip freeze from the head (non-fatal)"
```

with:

```bash
          # Fatal, not advisory. A rung number whose resolved environment was not
          # captured cannot be compared to any later run, which is exactly how the
          # June baseline became unattributable -- and the artifact would still
          # upload, so nothing downstream would notice.
          if ! ray rsync-down .ray/cluster-gce.yaml /tmp/bench_env.txt \
               ".profile_tmp/qis/pipfreeze_${LABEL}.txt"; then
            echo "::error::could not fetch pip freeze from the head; the run is unattributable" >&2
            exit 1
          fi
          if ! grep -q '^goldenmatch @' ".profile_tmp/qis/pipfreeze_${LABEL}.txt"; then
            echo "::error::pip freeze does not record the goldenmatch git SHA" >&2
            exit 1
          fi
```

The `grep` matters: goldenmatch is installed from `git+https://...@<sha>`, so `pip freeze` is where the SHA reaches the artifact. If the install form ever changes to a plain version, the SHA silently stops being recorded and every later comparison inherits the ambiguity that §6 of the operational guide is currently stuck in.

- [ ] **Step 2: Make a missing artifact fail**

In the `Upload bench artifact` step, replace:

```yaml
          if-no-files-found: warn
```

with:

```yaml
          # `warn` let a run whose result JSON never came back finish green with
          # no result -- the same class as a continue-on-error gate.
          if-no-files-found: error
```

- [ ] **Step 3: Validate the workflow parses**

Run: `python3 scripts/check_workflow_yaml.py`
Expected: exit 0

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/bench-ray-cluster.yml
git commit -m "fix(bench): fail a ray bench that loses its result or its environment

pip freeze carries the goldenmatch git SHA, so a non-fatal rsync plus
if-no-files-found: warn meant an unattributable run could finish green."
```

---

### Task 5: Reconcile the record with the code

**Files:**
- Modify: `docs/distributed/ray-optimal-setup.md` — §6 trade table and the paragraph under it
- Modify: `docs/distributed-ray-roadmap.md` — the status block at the top
- Modify: `docs/scale-envelope.md` — the `>= 50M` row

`clustering.py` compares `33074452121` against `33087786765` — same code, differing only in route — and reports +12.2%. §6 instead takes a median across `32992647463`, which predates #2781 (merged 2026-08-26), and §8 of the same file attributes that very step to the code change. The prose changes; the constant and its test do not.

- [ ] **Step 1: Replace §6's trade paragraph**

In `docs/distributed/ray-optimal-setup.md`, find the paragraph beginning "So distributing costs roughly" and replace it and the one after it with:

```markdown
The like-for-like comparison is `33074452121` against `33087786765`: same code,
same shape, differing only in the clustering route. Distributing costs
**+12.2% dedupe wall** and returns **-61% driver peak RSS**. This is the pair
`clustering.py` calibrates against, and the only pair in the table that isolates
the route.

The other two in-memory rows are **not replicates of it**. `32992647463`
predates #2781 (`+ single projected pass`, merged 2026-08-26) and
`33006980567` is the run that measured it -- the 3237s to 2906s step is that
code change, as §8 records. Do not take a median across them: run-to-run noise
on this lane has never been measured, because no two runs of one unchanged SHA
have been compared. Establishing that band is open work.
```

- [ ] **Step 2: Re-stamp the roadmap's status block**

In `docs/distributed-ray-roadmap.md`, replace the `**Status as of 2026-05-19:**` paragraph with:

```markdown
**Status as of 2026-08-28.** The five phases below are largely SHIPPED in code:
`goldenmatch/distributed/` carries the loader (`dataset.py`), controller
(`sample.py`, `indicators.py`), distributed clustering (`clustering.py`), golden
(`golden.py`) and identity (`identity.py`, `identity_partition.py`), and
clustering correctness is gated by two blocking CI jobs. Treat the phase
descriptions below as historical scope, not as remaining work.

What blocks production is narrower and is tracked in
`docs/superpowers/specs/2026-08-28-ray-production-readiness-design.md`: the
default path does not distribute, the v3 planner's soft-revert gate
(`_ray_auto_select_enabled()`, from the 2026-05-18 kill-criterion failure) is
still shut, the driver keeps a 50.9 GB baseline at 100M, and issue #957 has
scoring using roughly a quarter of the cluster. Read that spec first.
```

- [ ] **Step 3: Record the survivorship limitation**

In `docs/scale-envelope.md`, in the `>= 50M` row of the TL;DR picker, append to the Notes cell:

```markdown
Correlated survivorship (`field_groups`, conditional, validate) is REFUSED on the distributed streaming pipeline (`GOLDENMATCH_DISTRIBUTED_PIPELINE=2`) and fails fast -- the driver's staged per-cluster pass has no distributed equivalent, and running plain `most_complete` instead would be silently wrong. Use the in-memory path for those configs.
```

- [ ] **Step 4: Run the docs gates**

Run: `python3 scripts/check_docs_links.py && python3 scripts/check_docs_consistency.py`
Expected: exit 0 for both. The new spec path must resolve.

- [ ] **Step 5: Commit**

```bash
git add docs/distributed/ray-optimal-setup.md docs/distributed-ray-roadmap.md docs/scale-envelope.md
git commit -m "docs: reconcile the ray record with the code

The roadmap's May status described shipped work as six months out. §6 took a
median across a code boundary that §8 attributes to #2781. clustering.py was
right all along; only the prose moves."
```

---

## Self-Review

**Spec coverage.** Gate 1 → Task 2. Gate 2 → Tasks 1 and 2 (the partition raises on a missing gate file and on an empty scan; a new `test_distributed_*.py` file joins `broad`, which after Task 3 blocks). Gate 3 → Task 3. Gate 4 → Task 4. Gate 5 → Task 5 steps 1–2. Gate 9 → Task 5 step 3; the spec's work-breakdown table assigns Gate 9 to the default-path plan and should be corrected to point here. Gates 6, 7, 8 and 10 are out of this plan's scope by design and are covered by Protocols P1/P2 and the default-path plan.

**Placeholder scan.** No TBDs. Every code step carries the actual content. Task 3's precondition is a command that produces the evidence rather than an instruction to "check stability".

**Type consistency.** `partition()` returns `dict[str, list[Path]]` with keys `invariance`/`wcc`/`broad` in Task 1, and Task 2 calls `--job` with exactly those three values. `GATED` is keyed by job name and valued by filename in both the implementation and the tests.

**Known gap.** Task 3's precondition asks for five green runs; only two exist today, and both predate Task 2's change to what the job runs. The honest sequence is to land Tasks 1–2, let the reduced job accumulate greens on `main`, then execute Task 3. Tasks 4 and 5 are independent of that wait and can land immediately.
