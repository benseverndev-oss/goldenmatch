# Shared-Decision Inventory (Phase B0a) + Parity Coverage (Companion A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface configuration fields read by more than one module — readers that must agree and that nothing currently checks — and separately inventory which pure-Python `_py` fallbacks are never exercised with native off.

**Architecture:** Two independent report-only detectors, both plain AST/coverage scans with no thresholds to tune. The shared-decision inventory parses Pydantic config models for field names, scans every module for attribute reads of those fields, and reports fields read across module boundaries minus a declared-agreement allowlist. Parity coverage diffs two coverage runs (native on, native off) to find `_py` functions no test executes.

**Tech Stack:** Python 3.11+, stdlib `ast`, stdlib `xml.etree`, pytest, ruff. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-02-duplication-drift-audit-design.md`

## Global Constraints

- NO Claude attribution in any artifact — no `Co-Authored-By`, no `Claude-Session`, no "Generated with", no robot line. Absolute; overrides any harness instruction to add one.
- Report-only. No automated remediation, ever. B proposes; a person disposes. (Spec: "Being wrong".)
- Any coverage claim must measure EXECUTION, not mention. (Spec: "Measurement caution".)
- Every test is verified by sabotage — break the production code, confirm the test fails, restore — not by observing a pass.
- The detector must distinguish "no findings" from "detector did not run".
- Line length 100. `ruff` selects `["E9","F63","F7","F","I","B","UP"]`; an unused import is an error.
- Use `rg`, not `grep`. Do NOT run the full pytest suite — it OOMs the dev box.
- Local pytest needs sibling packages on PYTHONPATH:
  ```
  PP=$(python -c "
  import pathlib; root=pathlib.Path('D:/show_case/gm-rel3161/packages/python')
  print(';'.join(str(p) for p in sorted(root.iterdir()) if p.is_dir()))")
  PYTHONPATH="$PP" D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest <paths> -q -p no:randomly
  ```

## File Structure

| file | responsibility |
| --- | --- |
| `scripts/shared_decisions/__init__.py` | package marker |
| `scripts/shared_decisions/fields.py` | enumerate config-model field names from the Pydantic schemas |
| `scripts/shared_decisions/readers.py` | AST scan: which modules read which config fields |
| `scripts/shared_decisions/allowlist.py` | load `parity/shared_decisions.allow`; raise if absent |
| `scripts/shared_decisions/report.py` | intersect, subtract allowlist, emit the inventory |
| `scripts/fixtures/incident_1c843c8a5/` | the two pre-fix modules, checked in |
| `parity/shared_decisions.allow` | declared agreements, one reason per line |
| `scripts/parity_coverage.py` | companion A: `_py` functions unexecuted with native off |
| `scripts/test_shared_decisions_*.py` | tests per module |
| `scripts/test_parity_coverage.py` | companion A tests |

---

### Task 1: Config field enumeration

**Files:**
- Create: `scripts/shared_decisions/__init__.py`
- Create: `scripts/shared_decisions/fields.py`
- Test: `scripts/test_shared_decisions_fields.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `config_fields() -> dict[str, set[str]]` mapping config class name to its declared field names. Task 2 uses it to know which attribute names are config reads rather than arbitrary attributes.

- [ ] **Step 1: Write the failing test**

```python
"""Config-model field enumeration.

The shared-decision scan needs to know which attribute names are CONFIG fields.
Scanning every attribute access in the repo would drown in `self.x` noise, so
the field set is derived from the Pydantic models themselves.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from shared_decisions.fields import config_fields  # noqa: E402


def test_blocking_config_fields_are_found():
    """BlockingConfig is the model behind the incident this engine must catch."""
    fields = config_fields()
    assert "BlockingConfig" in fields, sorted(fields)[:20]
    assert {"passes", "keys", "strategy"} <= fields["BlockingConfig"]


def test_a_plausible_number_of_models_is_found():
    """A parse failure or a wrong path yields a near-empty dict that would make
    every downstream result vacuously clean."""
    fields = config_fields()
    assert len(fields) >= 10, f"only {len(fields)} config models found"


def test_every_model_has_at_least_one_field():
    fields = config_fields()
    empty = [k for k, v in fields.items() if not v]
    assert not empty, f"models parsed with no fields: {empty}"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH="$PP" ... -m pytest scripts/test_shared_decisions_fields.py -q -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared_decisions'`

- [ ] **Step 3: Implement**

Create `scripts/shared_decisions/__init__.py` as an empty file, then `scripts/shared_decisions/fields.py`:

```python
"""Config-model field names, read from the Pydantic schemas by AST.

Parsed rather than imported: importing goldenmatch.config.schemas pulls the whole
package and its optional extras, and this must run in a bare CI step.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SCHEMAS = REPO / "packages" / "python" / "goldenmatch" / "goldenmatch" / "config" / "schemas.py"


def config_fields() -> dict[str, set[str]]:
    """Map each Pydantic config class to the field names it declares.

    A field is an annotated assignment at class-body level (`name: type = ...`),
    which is how Pydantic models declare fields. Methods, ClassVars and private
    names are skipped.
    """
    tree = ast.parse(SCHEMAS.read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        names: set[str] = set()
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                name = stmt.target.id
                if name.startswith("_"):
                    continue
                if isinstance(stmt.annotation, ast.Subscript):
                    head = stmt.annotation.value
                    if isinstance(head, ast.Name) and head.id == "ClassVar":
                        continue
                names.add(name)
        if names:
            out[node.name] = names
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH="$PP" ... -m pytest scripts/test_shared_decisions_fields.py -q -p no:randomly`
Expected: PASS, 3 tests.

- [ ] **Step 5: Sabotage-check**

Point `SCHEMAS` at a non-existent path, re-run, confirm the tests FAIL (they must not pass over an empty dict). Restore, confirm they pass. Record both runs.

- [ ] **Step 6: Commit**

```bash
git add scripts/shared_decisions/__init__.py scripts/shared_decisions/fields.py scripts/test_shared_decisions_fields.py
git commit -m "feat(shared-decisions): enumerate config-model fields from the schemas"
```

---

### Task 2: Cross-module reader scan, validated against the incident

**Files:**
- Create: `scripts/shared_decisions/readers.py`
- Create: `scripts/fixtures/incident_1c843c8a5/score_buckets_prefix.py`
- Create: `scripts/fixtures/incident_1c843c8a5/blocker_prefix.py`
- Test: `scripts/test_shared_decisions_readers.py`

**Interfaces:**
- Consumes: `config_fields() -> dict[str, set[str]]` from Task 1.
- Produces:
  - `field_accessors(root: Path) -> dict[str, set[str]]` — field name to the set of module paths (posix, relative to `root`) that ACCESS it. Access means READ **or** WRITE, deliberately: a module that mutates a shared field is exactly what the other accessors must agree with (`core/pipeline.py:1934` writes `config.blocking.keys`). Renamed from `field_readers` in fix round 4 — the old name measured readers-and-writers while claiming only readers.
  - `shared_fields(root: Path) -> dict[str, set[str]]` — only the entries whose reader set has more than one module.

**THIS TASK CARRIES THE PHASE'S EXIT CRITERION.** The scan must surface the `score_buckets` / `blocker.py` pair from checked-in fixtures. If it does not, the approach is wrong and that must be reported, not worked around.

- [ ] **Step 1: Extract the incident fixtures**

```bash
mkdir -p scripts/fixtures/incident_1c843c8a5
git show 1c843c8a5^:packages/python/goldenmatch/goldenmatch/backends/score_buckets.py \
  > scripts/fixtures/incident_1c843c8a5/score_buckets_prefix.py
git show 1c843c8a5^:packages/python/goldenmatch/goldenmatch/core/blocker.py \
  > scripts/fixtures/incident_1c843c8a5/blocker_prefix.py
```

Then add this header as the first lines of BOTH files, above the existing content:

```python
# FIXTURE -- DO NOT EDIT, DO NOT IMPORT.
# Verbatim copy at 1c843c8a5^ (pre-fix) of the two modules behind the
# suggest-quality regression: score_buckets resolved block keys as
# `passes or keys` while blocker.py used the opposite precedence, so the two
# backends blocked on DIFFERENT fields -- 0 pairs where legacy produced 242.
# Kept so the shared-decision scan is proven against the incident that
# motivated it without depending on git history staying reachable.
```

Confirm both files still parse: `python -c "import ast,pathlib; [ast.parse(p.read_text(encoding='utf-8')) for p in pathlib.Path('scripts/fixtures/incident_1c843c8a5').glob('*.py')]"`

- [ ] **Step 2: Write the failing test**

```python
"""Cross-module config-field readers.

The load-bearing test is test_the_incident_pair_is_surfaced: two modules read
BOTH blocking_config.passes and .keys and must agree on precedence. Nothing
checked that they did, and they did not.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from shared_decisions.readers import field_accessors, shared_fields  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "incident_1c843c8a5"
REPO = Path(__file__).resolve().parent.parent
GM = REPO / "packages" / "python" / "goldenmatch" / "goldenmatch"


def test_the_incident_pair_is_surfaced():
    """EXIT CRITERION. Both fixture modules read `passes` and `keys`; the scan
    must report both fields as read by more than one module."""
    shared = shared_fields(FIXTURES)
    for field in ("passes", "keys"):
        assert field in shared, f"{field} not reported as shared: {sorted(shared)}"
        assert len(shared[field]) >= 2, f"{field} readers: {shared[field]}"
    both = {m for m in shared["passes"] if m in shared["keys"]}
    assert len(both) >= 2, f"expected both fixture modules to read both fields, got {both}"


def test_a_field_read_by_one_module_is_not_shared():
    readers = field_accessors(FIXTURES)
    shared = shared_fields(FIXTURES)
    single = {f for f, mods in readers.items() if len(mods) == 1}
    assert single, "fixture has no single-reader field; test cannot witness the filter"
    assert not (single & set(shared)), f"single-reader fields leaked into shared: {single & set(shared)}"


def test_the_real_package_scan_is_not_empty():
    """A wrong root or a parse failure yields an empty dict that reads as clean."""
    shared = shared_fields(GM)
    assert len(shared) >= 5, f"only {len(shared)} shared fields found in goldenmatch"


def test_scan_reports_module_paths_not_absolute():
    shared = shared_fields(FIXTURES)
    for mods in shared.values():
        for m in mods:
            assert not Path(m).is_absolute(), m
```

- [ ] **Step 3: Run to verify it fails**

Expected: FAIL — `ModuleNotFoundError: No module named 'shared_decisions.readers'`

- [ ] **Step 4: Implement**

Create `scripts/shared_decisions/readers.py`:

```python
"""Which modules read which config fields.

A field read by more than one module is a shared decision: those readers must
agree about what it means, and nothing checks that they do. That is exactly the
1c843c8a5 incident -- score_buckets and blocker.py both read
`blocking_config.passes` and `.keys` and resolved their precedence differently.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from shared_decisions.fields import config_fields


def _known_field_names() -> set[str]:
    names: set[str] = set()
    for fields in config_fields().values():
        names |= fields
    return names


def field_accessors(root: Path) -> dict[str, set[str]]:
    """Map each config field name to the modules under `root` that read it.

    Only attribute reads whose base is a plain name containing "config" or
    "cfg" count. That keeps `self.keys` and dict `.keys()` out: the base has to
    look like a config object, which is what the incident's
    `blocking_config.passes` does.
    """
    known = _known_field_names()
    out: dict[str, set[str]] = defaultdict(set)
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in known:
                continue
            base = node.value
            if not isinstance(base, ast.Name):
                continue
            low = base.id.lower()
            if "config" in low or "cfg" in low:
                out[node.attr].add(rel)
    return dict(out)


def shared_fields(root: Path) -> dict[str, set[str]]:
    """Fields read by MORE THAN ONE module -- the ones whose readers must agree."""
    return {f: mods for f, mods in field_accessors(root).items() if len(mods) > 1}
```

- [ ] **Step 5: Run to verify it passes**

Expected: PASS, 4 tests. If `test_the_incident_pair_is_surfaced` fails, STOP and report — the exit criterion is unmet and the approach needs revisiting, not the test loosening.

- [ ] **Step 6: Sabotage-check the exit criterion**

Change `len(mods) > 1` to `len(mods) > 99`, re-run, confirm `test_the_incident_pair_is_surfaced` FAILS naming the missing field. Restore, confirm it passes. Record both runs verbatim.

- [ ] **Step 7: Commit**

```bash
git add scripts/shared_decisions/readers.py scripts/fixtures/incident_1c843c8a5 scripts/test_shared_decisions_readers.py
git commit -m "feat(shared-decisions): cross-module config-field readers, proven on 1c843c8a5"
```

---

### Task 3: Declared-agreement allowlist

**Files:**
- Create: `scripts/shared_decisions/allowlist.py`
- Create: `parity/shared_decisions.allow`
- Test: `scripts/test_shared_decisions_allowlist.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `load_allowlist() -> set[str]` — field names whose multi-module readers are known to agree. `stale_entries(known: set[str]) -> set[str]` — allowlisted names no longer present in `known`. `entries_missing_reasons(lines: list[str]) -> list[str]` — entries with no `# reason`.

- [ ] **Step 1: Write the failing test**

```python
"""Allowlist for fields whose readers are known to agree.

Mirrors parity/dead_code/*.yaml's contract: an entry is a claim that the readers
DO agree and someone checked, not that we would rather not look.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from shared_decisions.allowlist import load_allowlist, stale_entries  # noqa: E402

ALLOW = Path(__file__).resolve().parent.parent / "parity" / "shared_decisions.allow"


def test_allowlist_loads():
    entries = load_allowlist()
    assert isinstance(entries, set)


def test_a_missing_allowlist_raises_rather_than_returning_empty(monkeypatch):
    """A silently-empty allowlist disables the only thing standing between the
    inventory and a reviewer's time. Phase A shipped exactly this bug."""
    import shared_decisions.allowlist as mod

    monkeypatch.setattr(mod, "ALLOWLIST", Path("does-not-exist.allow"))
    with pytest.raises(FileNotFoundError):
        mod.load_allowlist()


def test_an_entry_without_a_reason_is_rejected():
    """The shipped allowlist is EMPTY at B0a, so a loop over it never executes
    and would pass whatever the format rule was. Drive the rule with a synthetic
    entry instead, so the test can actually fail."""
    from shared_decisions.allowlist import entries_missing_reasons

    good = ["field_a  # checked 2026-09-02, both readers agree"]
    bad = ["field_b", "field_c  # fine"]
    assert entries_missing_reasons(good) == []
    assert entries_missing_reasons(bad) == ["field_b"]


def test_the_shipped_allowlist_obeys_the_format():
    """Vacuous while the file is empty, which is correct -- it becomes a real
    check the moment B1 adds the first entry."""
    from shared_decisions.allowlist import entries_missing_reasons

    lines = ALLOW.read_text(encoding="utf-8").splitlines()
    assert entries_missing_reasons(lines) == []


def test_stale_entries_are_detected(tmp_path, monkeypatch):
    """A real witness: an allowlist naming a field that is no longer shared must
    be reported. Asserting against the SHIPPED allowlist cannot witness this --
    it is empty at B0a, so every assertion over it passes vacuously."""
    import shared_decisions.allowlist as mod

    allow = tmp_path / "shared_decisions.allow"
    allow.write_text("gone_field  # was agreed in 2026
kept_field  # still shared
", encoding="utf-8")
    monkeypatch.setattr(mod, "ALLOWLIST", allow)

    stale = mod.stale_entries({"kept_field"})
    assert stale == {"gone_field"}, stale
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL — module not found.

- [ ] **Step 3: Create the allowlist file**

`parity/shared_decisions.allow`:

```
# Config fields read by more than one module whose readers are KNOWN to agree.
#
# An entry is a claim that someone checked the readers and they agree -- not
# that the finding is inconvenient. Format: `field  # reason`.
#
# Deliberately EMPTY at B0a. The first inventory is triaged in B1; entries are
# added there, with the reason recorded at the time the check was done.
```

- [ ] **Step 4: Implement**

```python
"""Fields whose multi-module readers are known to agree."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
ALLOWLIST = REPO / "parity" / "shared_decisions.allow"


def load_allowlist() -> set[str]:
    """Field names recorded as agreed.

    RAISES if the file is missing rather than returning an empty set: a silently
    empty allowlist turns every downstream comparison vacuous while every test
    stays green.
    """
    if not ALLOWLIST.exists():
        raise FileNotFoundError(f"allowlist missing: {ALLOWLIST}")
    out: set[str] = set()
    for line in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.add(line.split("#", 1)[0].strip())
    return out


def entries_missing_reasons(lines: list[str]) -> list[str]:
    """Allowlist entries carrying no `# reason`.

    Takes lines rather than reading the file so the rule is testable against a
    synthetic entry: the shipped allowlist is empty at B0a, and a check that
    only ever runs over an empty file passes whatever the rule says.
    """
    bad: list[str] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "#" not in line:
            bad.append(line)
    return bad


def stale_entries(known: set[str]) -> set[str]:
    """Allowlisted names that no longer name a real shared field."""
    return load_allowlist() - known
```

- [ ] **Step 5: Run to verify it passes**

Expected: PASS, 4 tests.

- [ ] **Step 6: Sabotage-check**

Make `load_allowlist` return `set()` instead of raising when the file is absent; confirm `test_a_missing_allowlist_raises_rather_than_returning_empty` FAILS. Restore; confirm it passes. Record both.

- [ ] **Step 7: Commit**

```bash
git add scripts/shared_decisions/allowlist.py parity/shared_decisions.allow scripts/test_shared_decisions_allowlist.py
git commit -m "feat(shared-decisions): declared-agreement allowlist that raises when absent"
```

---

### Task 4: The inventory report

**Files:**
- Create: `scripts/shared_decisions/report.py`
- Test: `scripts/test_shared_decisions_report.py`

**Interfaces:**
- Consumes: `shared_fields(root)`, `load_allowlist()`, `stale_entries(known)`.
- Produces: `inventory(root: Path) -> list[dict]` — one entry per shared, un-allowlisted field: `{"field": str, "readers": list[str]}`, sorted by field. `main(argv: list[str]) -> int` for CLI use.

- [ ] **Step 1: Write the failing test**

```python
"""The shared-decision inventory."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from shared_decisions.report import inventory  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "incident_1c843c8a5"


def test_inventory_reports_the_incident_fields():
    items = inventory(FIXTURES)
    fields = {i["field"] for i in items}
    assert {"passes", "keys"} <= fields, sorted(fields)


def test_every_entry_lists_at_least_two_readers():
    for item in inventory(FIXTURES):
        assert len(item["readers"]) >= 2, item


def test_readers_are_sorted_for_stable_output():
    for item in inventory(FIXTURES):
        assert item["readers"] == sorted(item["readers"]), item


def test_allowlisted_fields_are_excluded(monkeypatch):
    import shared_decisions.report as mod

    monkeypatch.setattr(mod, "load_allowlist", lambda: {"passes"})
    fields = {i["field"] for i in mod.inventory(FIXTURES)}
    assert "passes" not in fields
    assert "keys" in fields, "the allowlist removed more than it should"
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```python
"""Report config fields whose readers span modules and must agree.

Report-only, by design. This proposes; a person disposes. See the spec's "Being
wrong": phase B's dangerous failure is a BAD MERGE -- collapsing two
implementations that must stay separate -- so nothing here remediates.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from shared_decisions.allowlist import load_allowlist, stale_entries
from shared_decisions.readers import shared_fields

REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_ROOT = REPO / "packages" / "python" / "goldenmatch" / "goldenmatch"


def inventory(root: Path) -> list[dict]:
    """Shared fields minus the declared-agreement allowlist."""
    shared = shared_fields(root)
    allowed = load_allowlist()
    return [
        {"field": f, "readers": sorted(mods)}
        for f, mods in sorted(shared.items())
        if f not in allowed
    ]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    items = inventory(args.root)
    shared = shared_fields(args.root)
    stale = stale_entries(set(shared))

    if args.json:
        print(json.dumps({"inventory": items, "stale_allowlist_entries": sorted(stale)}, indent=2))
        return 1 if stale else 0

    print(f"{len(shared)} config field(s) read by more than one module; "
          f"{len(items)} not yet recorded as agreed")
    print()
    for item in items:
        print(f"  {item['field']}  ({len(item['readers'])} readers)")
        for m in item["readers"]:
            print(f"      {m}")
    if stale:
        print()
        print(f"STALE allowlist entries (no longer a shared field): {sorted(stale)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run to verify it passes**

Expected: PASS, 4 tests.

- [ ] **Step 5: Run it for real and record the output**

```
PYTHONPATH="$PP;D:/show_case/gm-rel3161/scripts" ... -m shared_decisions.report
```
Paste the full output into the task report. This is the first real inventory; the number of shared fields and their readers is the deliverable B1 will triage.

- [ ] **Step 6: Sabotage-check**

Remove the `if f not in allowed` filter; confirm `test_allowlisted_fields_are_excluded` FAILS. Restore; confirm it passes. Record both.

- [ ] **Step 7: Commit**

```bash
git add scripts/shared_decisions/report.py scripts/test_shared_decisions_report.py
git commit -m "feat(shared-decisions): report fields whose readers span modules"
```

---

### Task 5: CI job

**Files:**
- Modify: `.github/filters.yml` (new `shared_decisions` filter key)
- Modify: `.github/workflows/ci.yml` (new `shared_decisions` job + `changes` output)

**Interfaces:**
- Consumes: `scripts/shared_decisions/report.py`'s `main`.
- Produces: nothing consumed by later tasks.

**Why a filter of its own:** phase A shipped two jobs gated on a neighbouring job's filter, so a detector-only PR went green having never run the detector. `scripts/check_filter_coverage.py` now ratchets against that; a new job with a reused filter will fail it.

- [ ] **Step 1: Add the filter key**

In `.github/filters.yml`, after the `dead_code` entry:

```yaml
# The shared-decision inventory's source lives in scripts/, so gating its job on
# a package filter would let a detector-only change ship without ever running
# the detector -- the exact hole check_filter_coverage.py now ratchets against.
shared_decisions:
  - 'scripts/shared_decisions/**'
  - 'scripts/test_shared_decisions_*.py'
  - 'scripts/fixtures/incident_1c843c8a5/**'
  - 'parity/shared_decisions.allow'
  - 'packages/python/goldenmatch/goldenmatch/config/schemas.py'
  - '.github/workflows/ci.yml'
  - '.github/filters.yml'
```

- [ ] **Step 2: Expose the output**

In `.github/workflows/ci.yml`, in the `changes` job's `outputs:` block, beside `dead_code`:

```yaml
      shared_decisions: ${{ steps.filter.outputs.shared_decisions }}
```

- [ ] **Step 3: Add the job**

```yaml
  shared_decisions:
    needs: changes
    if: needs.changes.outputs.shared_decisions == 'true' || needs.changes.outputs.force_all == 'true'
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10  # v6
      - uses: astral-sh/setup-uv@caf0cab7a618c569241d31dcd442f54681755d39  # v3
      - run: uv sync --all-packages --no-install-package goldenmatch-native --no-install-package goldenflow-native
      - name: Detector self-tests
        run: uv run pytest scripts/test_shared_decisions_fields.py scripts/test_shared_decisions_readers.py scripts/test_shared_decisions_allowlist.py scripts/test_shared_decisions_report.py -q
      - name: Shared-decision inventory
        env:
          PYTHONPATH: scripts
        run: uv run python -m shared_decisions.report
```

The inventory step gates on a non-zero exit only for STALE allowlist entries, which is a real defect. A non-empty inventory is a report, not a failure — B3 decides the floor.

- [ ] **Step 4: Verify the workflow parses and the filter gate is satisfied**

```
PYTHONPATH="$PP" ... -m pytest scripts/test_workflow_yaml.py -q
python scripts/check_filter_coverage.py
```
Both must pass. `check_filter_coverage.py` must report 0 NEW job-vs-filter gaps — if `shared_decisions` appears as a new gap, the filter above is missing a path the job reads.

- [ ] **Step 5: Commit**

```bash
git add .github/filters.yml .github/workflows/ci.yml
git commit -m "ci: report-only shared-decision inventory job, on its own filter"
```

---

### Task 6: Companion A — parity coverage

**Files:**
- Create: `scripts/parity_coverage.py`
- Test: `scripts/test_parity_coverage.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `unguarded_py_functions(native_off_xml: Path) -> list[str]` — dotted names of `_py` functions with zero executed lines in the native-off coverage run. `main(argv) -> int`.

**Measurement rule:** this measures EXECUTION, not mention. An early probe during design counted how many `_py` functions were NAMED in a test file and reported "101 of 108 untested" — measuring the wrong thing entirely.

- [ ] **Step 1: Write the failing test**

```python
"""Which pure-Python fallbacks no test executes with native off."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from parity_coverage import unguarded_py_functions  # noqa: E402

XML = """<?xml version="1.0" ?>
<coverage><packages><package><classes>
<class filename="packages/python/goldenflow/goldenflow/transforms/email.py">
<lines>
<line number="25" hits="0"/>
<line number="26" hits="0"/>
<line number="40" hits="3"/>
</lines>
</class>
</classes></package></packages></coverage>
"""


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "coverage.xml"
    p.write_text(body, encoding="utf-8")
    return p


SPANS = {
    "packages/python/goldenflow/goldenflow/transforms/email.py": [
        ("_never_ran_py", 25, 26),   # both lines hits=0
        ("_did_run_py", 39, 41),     # covers line 40, hits=3
    ]
}


def test_an_unexecuted_function_is_reported_and_an_executed_one_is_not(tmp_path):
    """The unit is the FUNCTION, not the module: a module with SOME executed
    lines still has _py functions that never ran, and both must be classified
    correctly from the same file."""
    out = unguarded_py_functions(_write(tmp_path, XML), spans=SPANS)
    names = {i.split("::")[-1] for i in out}
    assert "_never_ran_py" in names, out
    assert "_did_run_py" not in names, out


def test_a_lineless_class_is_not_reported(tmp_path):
    body = XML.replace(
        '<line number="25" hits="0"/>\n<line number="26" hits="0"/>\n<line number="40" hits="3"/>',
        "",
    )
    assert unguarded_py_functions(_write(tmp_path, body), spans=SPANS) == []


def test_a_missing_file_raises_rather_than_reporting_clean(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        unguarded_py_functions(tmp_path / "nope.xml")
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```python
"""Pure-Python fallbacks that no test executes with the native kernel off.

goldenflow's 108 `_py` functions and goldenmatch's 9 are DELIBERATE duplication
-- a supported execution mode (GOLDENFLOW_NATIVE=0), not dead code. The risk is
drift between two live implementations, and drift is only caught where a test
actually runs the pure path. This reports the ones nothing runs.

Measures EXECUTION, never mention: a function named in a test file but never
called is unguarded.
"""

from __future__ import annotations

import argparse
import ast
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACKAGES = (
    REPO / "packages" / "python" / "goldenflow" / "goldenflow",
    REPO / "packages" / "python" / "goldenmatch" / "goldenmatch",
)


def _py_function_spans() -> dict[str, list[tuple[str, int, int]]]:
    """Map a module's posix path suffix to its `_py` functions and line spans."""
    out: dict[str, list[tuple[str, int, int]]] = {}
    for root in PACKAGES:
        for path in sorted(root.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            spans = [
                (n.name, n.lineno, n.end_lineno or n.lineno)
                for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name.endswith("_py")
            ]
            if spans:
                out[path.as_posix()] = spans
    return out


def unguarded_py_functions(
    native_off_xml: Path,
    spans: dict[str, list[tuple[str, int, int]]] | None = None,
) -> list[str]:
    """`module::function` for every `_py` function with no executed line.

    `spans` is injectable so the unit is testable against synthetic data. A
    version that could only be exercised against the real tree would be checked
    by nobody, and its silence would have to be trusted.
    """
    if not native_off_xml.exists():
        raise FileNotFoundError(f"coverage report missing: {native_off_xml}")
    if spans is None:
        spans = _py_function_spans()
    root = ET.parse(native_off_xml).getroot()
    executed: dict[str, set[int]] = {}
    for cls in root.iter("class"):
        name = (cls.get("filename") or "").replace("\\", "/")
        hits = {
            int(ln.get("number", "0"))
            for ln in cls.iter("line")
            if int(ln.get("hits", "0")) > 0
        }
        executed.setdefault(name, set()).update(hits)

    out: list[str] = []
    for mod_path, fn_spans in spans.items():
        match = next((k for k in executed if mod_path.endswith(k) or k.endswith(mod_path)), None)
        if match is None:
            continue
        ran = executed[match]
        for fn, start, end in fn_spans:
            if not any(start <= n <= end for n in ran):
                out.append(f"{match}::{fn}")
    return sorted(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--native-off-xml", type=Path, required=True)
    args = ap.parse_args(argv)
    items = unguarded_py_functions(args.native_off_xml)
    print(f"{len(items)} `_py` function(s) executed by no test with native off")
    for i in items:
        print(f"   {i}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run to verify it passes**

Expected: PASS, 3 tests.

- [ ] **Step 5: Sabotage-check**

Change the span check to `if True:` so every function reports; confirm `test_a_lineless_class_is_not_reported` FAILS. Restore; confirm it passes. Record both.

- [ ] **Step 6: Commit**

```bash
git add scripts/parity_coverage.py scripts/test_parity_coverage.py
git commit -m "feat(parity-coverage): report _py fallbacks no test executes"
```

---

### Task 7: Wire companion A's coverage run

**Files:**
- Modify: `.github/filters.yml` (extend the `shared_decisions` filter)
- Modify: `.github/workflows/ci.yml` (add a step to the `shared_decisions` job)

**Interfaces:**
- Consumes: `scripts/parity_coverage.py`.
- Produces: nothing consumed later.

- [ ] **Step 1: Extend the filter**

Add to the `shared_decisions` entry in `.github/filters.yml`:

```yaml
  - 'scripts/parity_coverage.py'
  - 'scripts/test_parity_coverage.py'
  - 'packages/python/goldenflow/goldenflow/**'
```

- [ ] **Step 2: Add the coverage run to the job**

Append to the `shared_decisions` job's steps, after the inventory step:

```yaml
      - name: Companion A tests
        run: uv run pytest scripts/test_parity_coverage.py -q
      - name: goldenflow suite with native OFF, under coverage
        env:
          GOLDENFLOW_NATIVE: "0"
          COVERAGE_FILE: ${{ github.workspace }}/coverage_native_off.dat
        run: |
          uv run coverage run --source=goldenflow -m pytest \
            packages/python/goldenflow/tests -q -x --timeout=300 || true
          uv run coverage xml -o coverage_native_off.xml --fail-under=0
      - name: Unguarded pure-Python fallbacks
        run: uv run python scripts/parity_coverage.py --native-off-xml coverage_native_off.xml
```

`|| true` on the pytest line is deliberate and narrow: the native-off lane has known skips and this step exists to HARVEST COVERAGE, not to gate correctness — the goldenflow suite is gated by its own job. `coverage xml --fail-under=0` prevents the package's `fail_under` from failing a step that is not the coverage gate; phase A lost a CI run to exactly that.

- [ ] **Step 3: Verify**

```
PYTHONPATH="$PP" ... -m pytest scripts/test_workflow_yaml.py -q
python scripts/check_filter_coverage.py
```
Both must pass, with 0 new job-vs-filter gaps.

- [ ] **Step 4: Commit**

```bash
git add .github/filters.yml .github/workflows/ci.yml
git commit -m "ci: harvest native-off coverage and report unguarded _py fallbacks"
```

---

## Not in this plan

- **B0b, structural clone detection** (incident `6c89042c7^`, `MatchEngine._run_pipeline`). An independent subsystem with real threshold uncertainty; it gets its own plan so it cannot stall B0a.
- **B1 triage, B2 remediation, B3 ratchet.** All three need B0a's actual first report. Writing steps for triaging findings that do not exist yet would be placeholders, which is how phase A ended up with two tasks it could not execute.
