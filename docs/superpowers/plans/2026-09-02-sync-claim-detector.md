# Unenforced Sync-Claim Detector (Phase C0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the report-only detector that finds docstring claims of the form "X mirrors Y" whose relationship no test enforces, validated against the `6c89042c7` incident from a checked-in fixture.

**Architecture:** Three small AST modules under `scripts/sync_claims/`, mirroring the proven `scripts/shared_decisions/` layout from phase B. `claims.py` extracts claims from docstrings and resolves their targets against the package's symbol table. `enforcement.py` collects the executable references in each test file and reports which claims no single test file references both halves of. `report.py` prints the three buckets and the counts. Nothing gates in this stage.

**Tech Stack:** Python 3.12, stdlib `ast` and `re` only. pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-02-sync-claim-audit-design.md`

## Global Constraints

- **Scope is `packages/python/goldenmatch/goldenmatch`**, tests at `packages/python/goldenmatch/tests`. No other package.
- **Read every source file with `encoding="utf-8-sig"`.** Two goldenmatch modules carry a UTF-8 BOM (`core/autoconfig_planner.py`, `core/execution_plan.py`); reading them as plain utf-8 silently drops them from the scan. This cost phase B two modules before it was caught.
- **Enforcement counts EXECUTABLE references only** — `ast.Name.id`, `ast.Attribute.attr`, `ast.alias` (using `asname or name`, last dotted segment). Never raw text. At `6c89042c7^` a test names both halves of the incident inside a docstring; counting text classifies the incident as enforced and the phase misses the bug it exists to catch.
- **The signal is sound as a negative and only suggestive as a positive.** The finding is the unenforced set. Claims with a co-reference are labelled `UNVERIFIED`, never `enforced` and never `safe`.
- **Module-level claims are extracted and reported in their own bucket, never triaged.** A module has no single symbol a test can reference.
- **The report prints claim count and finding count separately**, so a drop in total claims cannot masquerade as progress.
- **The report prints the matched claim window** for each finding, so a wrong target resolution is visible rather than silent.
- **Every test is sabotage-verified**, and each sabotage must assert it actually applied before the test result is read. A sabotage that does not apply is not a sabotage — phase B's ratchet check planted a change at a site the scan never saw and read the resulting green as proof the gate worked.
- **No Claude attribution in any commit message, ever.** No `Co-Authored-By`, no `Claude-Session`, no footer.
- Report-only. No CI gate, no allowlist, no ratchet — those are stages C3.

---

### Task 1: Claim extraction and target resolution

**Files:**
- Create: `scripts/sync_claims/__init__.py` (empty)
- Create: `scripts/sync_claims/claims.py`
- Create: `scripts/fixtures/incident_6c89042c7/engine_at_6c89042c7.py`
- Test: `scripts/test_sync_claims_claims.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `@dataclass(frozen=True) class Claim` with fields `module: str`, `symbol: str`, `kind: str` (`"module"` or `"symbol"`), `keyword: str`, `window: str`, `target: str | None`, `lineno: int`
  - `def claims(root: Path, *, symbols: set[str] | None = None) -> list[Claim]` — every claim under `root`, targets resolved against `symbols` (defaulting to `declared_symbols(root)`)
  - `def declared_symbols(root: Path) -> set[str]` — every function/class name declared under `root`
  - `CLAIM_PATTERN: re.Pattern` and `WINDOW = 200`

- [ ] **Step 1: Create the incident fixture, verbatim**

The detector must find the motivating incident without git history staying reachable, so the pre-fix file is checked in. Run from the repo root:

```bash
mkdir -p scripts/fixtures/incident_6c89042c7
git show "6c89042c7^:packages/python/goldenmatch/goldenmatch/tui/engine.py" \
  > scripts/fixtures/incident_6c89042c7/engine_at_6c89042c7.py
```

Verify it is 530 lines and parses standalone (it is never imported, only parsed):

```bash
wc -l scripts/fixtures/incident_6c89042c7/engine_at_6c89042c7.py
python -c "import ast,pathlib; ast.parse(pathlib.Path('scripts/fixtures/incident_6c89042c7/engine_at_6c89042c7.py').read_text(encoding='utf-8-sig')); print('parses')"
```

Expected: `530` and `parses`.

Add a `README.md` beside it:

```markdown
# Fixture: `tui/engine.py` at `6c89042c7^`

Verbatim copy of the file immediately BEFORE `6c89042c7` ("delete
MatchEngine's copy of the pipeline"). `_run_pipeline`'s docstring reads
"Core pipeline logic - mirrors run_dedupe but returns EngineResult", and
nothing enforced that: 2 tests referenced `_run_pipeline`, 10 referenced
`run_dedupe`, 0 referenced both.

Checked in so the detector can be validated without git history staying
reachable. Parsed by AST only, never imported. Do not edit.
```

- [ ] **Step 2: Write the failing test**

```python
"""Tests for sync-claim extraction and target resolution."""

from __future__ import annotations

from pathlib import Path

from sync_claims.claims import Claim, claims, declared_symbols

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "scripts" / "fixtures" / "incident_6c89042c7"
GOLDENMATCH = REPO / "packages" / "python" / "goldenmatch" / "goldenmatch"


def test_the_incident_claim_is_extracted_with_its_target():
    """The motivating example, from a checked-in fixture, not git history.

    `_run_pipeline`'s docstring says "mirrors run_dedupe but returns
    EngineResult". The target is a BARE identifier -- no backticks, no call
    suffix -- which is exactly what an earlier target rule could not see.

    Targets resolve against the REAL package, not the fixture: `run_dedupe`
    lives in `core/pipeline.py`, not in `tui/engine.py`, so resolving against
    the fixture alone returns None and this test could never pass. That is
    what the `symbols` keyword is for.
    """
    package_symbols = declared_symbols(GOLDENMATCH)
    assert "run_dedupe" in package_symbols, (
        "run_dedupe is no longer declared in goldenmatch -- the fixture's claim "
        "names a symbol that has been renamed or removed, so this test's premise "
        "is gone. Fix the premise, do not weaken the assertion."
    )

    found = [
        c
        for c in claims(FIXTURE, symbols=package_symbols)
        if c.symbol == "_run_pipeline"
    ]

    assert len(found) == 1, f"expected one claim on _run_pipeline, got {found}"
    claim = found[0]
    assert claim.keyword.lower() == "mirrors"
    assert claim.target == "run_dedupe", (
        f"target did not resolve to run_dedupe: {claim.target!r}. The claim "
        f"names it as a bare word, so resolution -- not punctuation -- has to "
        f"be the filter."
    )
    assert claim.kind == "symbol"
    assert "run_dedupe" in claim.window


def test_declared_symbols_finds_functions_and_classes():
    symbols = declared_symbols(FIXTURE)
    assert "_run_pipeline" in symbols
    assert "MatchEngine" in symbols


def test_a_docstring_with_no_claim_yields_nothing(tmp_path):
    (tmp_path / "m.py").write_text(
        '''
def plain():
    """Does a thing. Returns a value."""
'''.strip(),
        encoding="utf-8",
    )
    assert claims(tmp_path) == []


def test_an_unresolvable_claim_is_kept_with_target_none(tmp_path):
    """A claim naming nothing real is still a claim -- it is reported in its
    own bucket, not dropped. Dropping it would hide that someone wrote a
    synchronisation promise nobody can check."""
    (tmp_path / "m.py").write_text(
        '''
def widget():
    """Mirrors the legacy behaviour of the old system."""
'''.strip(),
        encoding="utf-8",
    )
    found = claims(tmp_path)
    assert len(found) == 1
    assert found[0].target is None
    assert found[0].keyword.lower() == "mirrors"


def test_a_claim_never_resolves_to_its_own_claimant(tmp_path):
    """`def build(): "mirrors build"` is a self-reference, not a relationship."""
    (tmp_path / "m.py").write_text(
        '''
def build():
    """Mirrors build exactly."""
'''.strip(),
        encoding="utf-8",
    )
    assert claims(tmp_path)[0].target is None


def test_module_level_claims_are_kept_and_marked(tmp_path):
    """Module claims are reported but never triaged -- a module has no single
    symbol a test can reference. Marking the kind is what lets the report
    separate them."""
    (tmp_path / "m.py").write_text(
        '''
"""This module mirrors helper."""


def helper():
    pass
'''.strip(),
        encoding="utf-8",
    )
    found = claims(tmp_path)
    assert [c.kind for c in found] == ["module"]
    assert found[0].symbol == "<module>"
    assert found[0].target == "helper"


def test_a_bom_prefixed_file_is_not_skipped(tmp_path):
    """Two goldenmatch modules carry a UTF-8 BOM. Reading as plain utf-8
    raises on the first line and the file vanishes from the scan -- phase B
    lost two modules to exactly this before it was caught."""
    (tmp_path / "bom.py").write_bytes(
        b"\xef\xbb\xbf" + b'def a():\n    """Mirrors b."""\n\n\ndef b():\n    pass\n'
    )
    assert [c.target for c in claims(tmp_path)] == ["b"]
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
cd /d/show_case/gm-rel3161
PYTHONPATH=scripts python -m pytest scripts/test_sync_claims_claims.py -q
```

Expected: collection error, `ModuleNotFoundError: No module named 'sync_claims'`.

- [ ] **Step 4: Write the implementation**

Create `scripts/sync_claims/__init__.py` empty, then `scripts/sync_claims/claims.py`:

```python
"""Docstring claims that one piece of code must stay in step with another.

The codebase says where its traps are. 319 docstrings in goldenmatch assert a
synchronisation relationship -- "mirrors", "byte-identical to", "must match",
"keep in sync with" -- and nothing checks any of them. `MatchEngine._run_pipeline`
said "mirrors run_dedupe", stopped mirroring it, and shipped an ImportError on a
default install (6c89042c7).

TARGET RESOLUTION IS THE FILTER, deliberately. An earlier rule accepted a target
only in backticks or with a call suffix, and could not extract this phase's own
motivating example -- the incident names `run_dedupe` as a bare word. So any word
in the window after the claim keyword counts if it names a declared symbol, first
match wins. The cost is that a claim mentioning several symbols can resolve to the
wrong one; `Claim.window` carries the matched text so triage can see what it keyed
on.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

# Phrases that assert a relationship to another symbol. Kept narrow on purpose:
# every entry states that two things must AGREE, not merely that they are
# related. "see also" and "used by" are not claims.
CLAIM_PATTERN = re.compile(
    r"\b(mirror(s|ed|ing)?|keep (in|them) sync|in sync with|stay(s)? in sync|"
    r"parallel (to|of)|same (rule|logic|order|shape|contract) as|must match|"
    r"byte-identical to|identical to|counterpart|duplicat(e|ed|es) of|"
    r"copy of|port of)\b",
    re.IGNORECASE,
)

# How much text after the keyword can name the target. 200 characters is about
# two sentences -- long enough for "mirrors run_dedupe but returns EngineResult",
# short enough that an unrelated symbol three paragraphs down is not picked up.
WINDOW = 200

_WORD = re.compile(r"[A-Za-z_][\w.]*")


@dataclass(frozen=True)
class Claim:
    """One docstring assertion that this code must stay in step with `target`."""

    module: str
    symbol: str
    kind: str  # "module" or "symbol"
    keyword: str
    window: str
    target: str | None
    lineno: int


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8-sig", errors="ignore"))
    except SyntaxError:
        return None


def declared_symbols(root: Path) -> set[str]:
    """Every function and class name declared under `root`."""
    out: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.add(node.name)
    return out


def claims(root: Path, *, symbols: set[str] | None = None) -> list[Claim]:
    """Every synchronisation claim under `root`, with targets resolved.

    `symbols` defaults to `declared_symbols(root)`. Pass it explicitly when the
    claims live in a fixture but must resolve against the real package.
    """
    known = declared_symbols(root) if symbols is None else symbols
    out: list[Claim] = []
    for path in sorted(root.rglob("*.py")):
        tree = _parse(path)
        if tree is None:
            continue
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            doc = ast.get_docstring(node)
            if not doc:
                continue
            match = CLAIM_PATTERN.search(doc)
            if match is None:
                continue
            is_module = isinstance(node, ast.Module)
            name = "<module>" if is_module else node.name
            window = doc[match.end() : match.end() + WINDOW]
            target = next(
                (
                    word.split(".")[-1]
                    for word in _WORD.findall(window)
                    if word.split(".")[-1] in known and word.split(".")[-1] != name
                ),
                None,
            )
            out.append(
                Claim(
                    module=rel,
                    symbol=name,
                    kind="module" if is_module else "symbol",
                    keyword=match.group(0),
                    window=" ".join(window.split()),
                    target=target,
                    lineno=0 if is_module else node.lineno,
                )
            )
    return out
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
PYTHONPATH=scripts python -m pytest scripts/test_sync_claims_claims.py -q
```

Expected: `7 passed`.

- [ ] **Step 6: Sabotage-verify the two load-bearing rules**

Each sabotage must be confirmed to have landed before the result is read. Run both, restoring in between:

```bash
cp scripts/sync_claims/claims.py /tmp/claims.bak && test -s /tmp/claims.bak && echo "backup ok"

# (a) Narrow the target rule back to backticks-or-call-suffix.
python - <<'EOF'
import pathlib
p = pathlib.Path("scripts/sync_claims/claims.py"); s = p.read_text(encoding="utf-8")
old = '_WORD = re.compile(r"[A-Za-z_][\\w.]*")'
new = '_WORD = re.compile(r"`([A-Za-z_][\\w.]*)`")'
assert s.count(old) == 1, "sabotage did not apply"
p.write_text(s.replace(old, new), encoding="utf-8")
print("sabotage (a) applied")
EOF
PYTHONPATH=scripts python -m pytest scripts/test_sync_claims_claims.py -q
# Expected: test_the_incident_claim_is_extracted_with_its_target FAILS
cp /tmp/claims.bak scripts/sync_claims/claims.py

# (b) Read as plain utf-8 so a BOM file is skipped.
python - <<'EOF'
import pathlib
p = pathlib.Path("scripts/sync_claims/claims.py"); s = p.read_text(encoding="utf-8")
old = 'encoding="utf-8-sig"'
new = 'encoding="utf-8"'
assert s.count(old) == 1, "sabotage did not apply"
p.write_text(s.replace(old, new), encoding="utf-8")
print("sabotage (b) applied")
EOF
PYTHONPATH=scripts python -m pytest scripts/test_sync_claims_claims.py -q
# Expected: test_a_bom_prefixed_file_is_not_skipped FAILS
cp /tmp/claims.bak scripts/sync_claims/claims.py
PYTHONPATH=scripts python -m pytest scripts/test_sync_claims_claims.py -q
# Expected: 7 passed
```

Both sabotages must print "sabotage applied" AND produce the named failure. If a sabotage prints nothing or the tests stay green, the sabotage did not land and has measured nothing.

- [ ] **Step 7: Run ruff and commit**

```bash
python -m ruff check scripts/sync_claims/ scripts/test_sync_claims_claims.py
git add scripts/sync_claims/ scripts/test_sync_claims_claims.py scripts/fixtures/incident_6c89042c7/
git commit -F - <<'EOF'
feat(sync-claims): extract docstring sync claims and resolve their targets

319 docstrings in goldenmatch assert that one piece of code must stay in
step with another -- "mirrors", "byte-identical to", "must match" -- and
nothing checks any of them. MatchEngine._run_pipeline said "mirrors
run_dedupe", stopped, and shipped an ImportError on a default install.

Target resolution is the filter rather than punctuation. A rule accepting
only backticked or called names could not extract this phase's own
motivating example: the incident names run_dedupe as a bare word.

Proven against a checked-in copy of tui/engine.py at 6c89042c7^, so the
detector does not depend on git history staying reachable.
EOF
```

---

### Task 2: Enforcement check

**Files:**
- Create: `scripts/sync_claims/enforcement.py`
- Create: `scripts/fixtures/sync_enforcement/src/lane.py`
- Create: `scripts/fixtures/sync_enforcement/tests/test_enforced.py`
- Create: `scripts/fixtures/sync_enforcement/tests/test_unenforced.py`
- Create: `scripts/fixtures/sync_enforcement/tests/test_docstring_only.py`
- Test: `scripts/test_sync_claims_enforcement.py`

**Interfaces:**
- Consumes: `Claim` and `claims(root, *, symbols=None)` from `sync_claims.claims`.
- Produces:
  - `def executable_references(path: Path) -> set[str]` — the `Name`/`Attribute`/`alias` names in one file
  - `def test_reference_sets(tests_root: Path) -> list[set[str]]`
  - `def unenforced(claim_list: list[Claim], reference_sets: list[set[str]]) -> list[Claim]`

- [ ] **Step 1: Create the synthetic enforcement fixture**

Three test files against one source file. The third is the trap that decides the phase.

`scripts/fixtures/sync_enforcement/src/lane.py`:

```python
"""Synthetic fixture: four claims -- enforced, unenforced, prose-only, unresolvable."""


def fast_lane():
    """Mirrors slow_lane but skips validation."""
    return 1


def slow_lane():
    return 1


def orphan_lane():
    """Mirrors slow_lane and nothing tests them together."""
    return 1


def prose_lane():
    """Mirrors slow_lane; a test mentions both only in prose."""
    return 1


def stray_lane():
    """Mirrors the legacy pipeline that no longer exists here."""
    return 1
```

`stray_lane`'s claim names nothing declared in the fixture, so its target
resolves to `None`. It exists so the unresolvable-claim bucket has a member to
assert on — without it those assertions iterate an empty list and pass while
checking nothing.

`scripts/fixtures/sync_enforcement/tests/test_enforced.py`:

```python
from lane import fast_lane, slow_lane


def test_the_lanes_agree():
    assert fast_lane() == slow_lane()
```

`scripts/fixtures/sync_enforcement/tests/test_unenforced.py`:

```python
from lane import orphan_lane


def test_orphan_runs():
    assert orphan_lane() == 1
```

`scripts/fixtures/sync_enforcement/tests/test_docstring_only.py`:

```python
from lane import prose_lane


def test_prose_runs():
    """`prose_lane` must behave the same way `slow_lane` does.

    This docstring names both symbols. Nothing in the CODE references
    slow_lane, so nothing compares them -- which is exactly the shape that
    made tests/test_engine.py look like it enforced the 6c89042c7 incident.
    """
    assert prose_lane() == 1
```

- [ ] **Step 2: Write the failing test**

```python
"""Tests for the executable-reference enforcement check."""

from __future__ import annotations

from pathlib import Path

from sync_claims.claims import claims, declared_symbols
from sync_claims.enforcement import (
    executable_references,
    test_reference_sets,
    unenforced,
)

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "scripts" / "fixtures" / "sync_enforcement"
SRC = FIXTURE / "src"
TESTS = FIXTURE / "tests"


def _claims():
    return claims(SRC, symbols=declared_symbols(SRC))


def test_a_docstring_only_co_mention_is_not_enforcement():
    """THE TRAP THIS PHASE TURNS ON.

    At 6c89042c7^, tests/test_engine.py named both `_run_pipeline` and
    `run_dedupe` -- inside a docstring. A text scan calls that enforced, and
    the phase misses the bug that motivates it.
    """
    names = executable_references(TESTS / "test_docstring_only.py")
    assert "prose_lane" in names
    assert "slow_lane" not in names, (
        "slow_lane appears only in a docstring; counting it means counting "
        "prose as enforcement"
    )


def test_the_unenforced_claims_are_reported():
    found = {c.symbol for c in unenforced(_claims(), test_reference_sets(TESTS))}
    assert found == {"orphan_lane", "prose_lane"}, found


def test_the_enforced_claim_is_not_reported():
    found = {c.symbol for c in unenforced(_claims(), test_reference_sets(TESTS))}
    assert "fast_lane" not in found, (
        "test_enforced.py references both fast_lane and slow_lane in code"
    )


def test_a_claim_with_no_target_is_never_reported_unenforced():
    """An unresolvable claim has nothing to be enforced against. Reporting it
    as unenforced would inflate the finding count with claims nobody can act on.

    `stray_lane` exists in the fixture to give this something to assert on. An
    earlier draft filtered for `target is None` against a fixture that had no
    such claim, so it iterated an empty list and passed while checking nothing.
    """
    unresolved = [c for c in _claims() if c.target is None]
    assert {c.symbol for c in unresolved} == {"stray_lane"}, (
        "the fixture must contain an unresolvable claim or this test is vacuous"
    )
    assert unenforced(unresolved, []) == []


def test_executable_references_covers_all_three_node_kinds():
    """Name, Attribute and alias. Missing `alias` loses every symbol a test
    only imports, which is most of them."""
    path = TESTS / "test_enforced.py"
    names = executable_references(path)
    assert {"fast_lane", "slow_lane"} <= names


def test_an_empty_tests_directory_yields_no_reference_sets(tmp_path):
    """An empty list is how 'nothing was scanned' reaches the report.

    Distinguishing that from 'nothing is enforced' is the report's job
    (test_sync_claims_report.py); this pins the signal it keys on."""
    assert test_reference_sets(tmp_path) == []
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
PYTHONPATH=scripts python -m pytest scripts/test_sync_claims_enforcement.py -q
```

Expected: `ModuleNotFoundError: No module named 'sync_claims.enforcement'`.

- [ ] **Step 4: Write the implementation**

`scripts/sync_claims/enforcement.py`:

```python
"""Does any test exercise a claimant alongside what it claims to mirror?

A claim is UNENFORCED when no single test file references both the claimant
and its target in EXECUTABLE code. That definition is load-bearing in both
directions:

  * EXECUTABLE, not textual. At 6c89042c7^ `tests/test_engine.py` named both
    `_run_pipeline` and `run_dedupe` -- in a docstring. Counting text marks
    the motivating incident enforced and the whole phase misses it. Counting
    Name/Attribute/alias nodes: 2 tests referenced the claimant, 10 the
    target, 0 both.

  * SOUND AS A NEGATIVE, SUGGESTIVE AS A POSITIVE. No co-reference genuinely
    proves nothing compares them. Co-reference proves only that one file
    mentions both -- never that it compares them. So the finding is the
    unenforced set, and a co-referenced claim is UNVERIFIED, never "safe".
"""

from __future__ import annotations

import ast
from pathlib import Path

from sync_claims.claims import Claim


def executable_references(path: Path) -> set[str]:
    """Names this file references in code. Docstrings and comments are not code."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="ignore"))
    except SyntaxError:
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.alias):
            out.add((node.asname or node.name).split(".")[-1])
    return out


def test_reference_sets(tests_root: Path) -> list[set[str]]:
    """One reference set per test file. Empty list means nothing was scanned."""
    return [executable_references(p) for p in sorted(tests_root.rglob("*.py"))]


def unenforced(
    claim_list: list[Claim], reference_sets: list[set[str]]
) -> list[Claim]:
    """Claims no single test file references both halves of.

    Claims with no resolved target are excluded: there is nothing for a test to
    enforce them against, and counting them would inflate the finding list with
    items nobody can act on. They are reported in their own bucket instead.
    """
    out: list[Claim] = []
    for claim in claim_list:
        if claim.target is None:
            continue
        if not any(
            claim.symbol in names and claim.target in names
            for names in reference_sets
        ):
            out.append(claim)
    return out
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
PYTHONPATH=scripts python -m pytest scripts/test_sync_claims_enforcement.py -q
```

Expected: `6 passed`.

- [ ] **Step 6: Sabotage-verify the docstring trap**

```bash
cp scripts/sync_claims/enforcement.py /tmp/enf.bak && test -s /tmp/enf.bak && echo "backup ok"
python - <<'EOF'
import pathlib
p = pathlib.Path("scripts/sync_claims/enforcement.py"); s = p.read_text(encoding="utf-8")
old = """    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="ignore"))
    except SyntaxError:
        return set()"""
new = """    import re
    return set(re.findall(r"[A-Za-z_]\\w*", path.read_text(encoding="utf-8-sig", errors="ignore")))
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="ignore"))
    except SyntaxError:
        return set()"""
assert s.count(old) == 1, "sabotage did not apply"
p.write_text(s.replace(old, new), encoding="utf-8")
print("sabotage applied: executable_references now counts raw text")
EOF
PYTHONPATH=scripts python -m pytest scripts/test_sync_claims_enforcement.py -q
# Expected: test_a_docstring_only_co_mention_is_not_enforcement FAILS,
#           and test_the_unenforced_claims_are_reported FAILS (prose_lane vanishes)
cp /tmp/enf.bak scripts/sync_claims/enforcement.py
PYTHONPATH=scripts python -m pytest scripts/test_sync_claims_enforcement.py -q
# Expected: 6 passed
```

- [ ] **Step 7: Run ruff and commit**

```bash
python -m ruff check scripts/sync_claims/ scripts/test_sync_claims_enforcement.py
git add scripts/sync_claims/enforcement.py scripts/test_sync_claims_enforcement.py scripts/fixtures/sync_enforcement/
git commit -F - <<'EOF'
feat(sync-claims): report claims no test enforces

A claim is unenforced when no single test file references both the
claimant and its target in EXECUTABLE code.

Executable, not textual, and the distinction decides the phase. At
6c89042c7^ tests/test_engine.py named both halves of the incident -- in a
docstring. A text scan marks the motivating bug enforced and the detector
misses it. The fixture pins that case: test_docstring_only.py mentions
both symbols in prose and must still come out unenforced.

The signal is sound as a negative and only suggestive as a positive. No
co-reference proves nothing compares them; co-reference proves only that
one file mentions both. Claims with no resolved target are excluded --
there is nothing to enforce them against.
EOF
```

---

### Task 3: Report

**Files:**
- Create: `scripts/sync_claims/report.py`
- Test: `scripts/test_sync_claims_report.py`

**Interfaces:**
- Consumes: `claims`, `declared_symbols` from `sync_claims.claims`; `test_reference_sets`, `unenforced` from `sync_claims.enforcement`.
- Produces:
  - `DEFAULT_ROOT: Path`, `DEFAULT_TESTS: Path`
  - `def inventory(root: Path, tests_root: Path) -> dict` with keys `counts`, `unenforced`, `unverified`, `unresolvable`, `module_level`
  - `def main(argv: list[str]) -> int` — always returns 0 in C0 (report-only)

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the sync-claim report."""

from __future__ import annotations

from pathlib import Path

from sync_claims.report import DEFAULT_ROOT, DEFAULT_TESTS, inventory, main

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "scripts" / "fixtures" / "sync_enforcement"


def test_inventory_buckets_the_fixture():
    inv = inventory(FIXTURE / "src", FIXTURE / "tests")
    assert {c["symbol"] for c in inv["unenforced"]} == {"orphan_lane", "prose_lane"}
    assert {c["symbol"] for c in inv["unverified"]} == {"fast_lane"}
    assert {c["symbol"] for c in inv["unresolvable"]} == {"stray_lane"}


def test_claim_count_and_finding_count_are_separate():
    """Deleting a claim must not read as progress. Reporting only a finding
    count lets six words removed from a docstring look like a fix."""
    inv = inventory(FIXTURE / "src", FIXTURE / "tests")
    counts = inv["counts"]
    assert counts["claims"] >= counts["unenforced"]
    assert {"claims", "resolvable", "unenforced", "unverified",
            "unresolvable", "module_level"} <= set(counts)


def test_the_report_names_the_matched_window(capsys):
    """A wrong target resolution must be visible, not silent. The first-match
    rule can pick the wrong symbol when a claim mentions several."""
    rc = main(["--root", str(FIXTURE / "src"), "--tests", str(FIXTURE / "tests")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "slow_lane" in out
    assert "orphan_lane" in out


def test_the_report_states_its_scope(capsys):
    """Silence outside the scanned tree is not a clean bill, and the header
    has to say so -- module-level claims are reported but never triaged."""
    main(["--root", str(FIXTURE / "src"), "--tests", str(FIXTURE / "tests")])
    out = capsys.readouterr().out.lower()
    assert "scope" in out
    assert "module-level" in out


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


def test_the_default_roots_exist():
    """A default path that does not exist makes every CI run vacuously clean."""
    assert DEFAULT_ROOT.is_dir(), DEFAULT_ROOT
    assert DEFAULT_TESTS.is_dir(), DEFAULT_TESTS
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
PYTHONPATH=scripts python -m pytest scripts/test_sync_claims_report.py -q
```

Expected: `ModuleNotFoundError: No module named 'sync_claims.report'`.

- [ ] **Step 3: Write the implementation**

`scripts/sync_claims/report.py`:

```python
"""Report docstring sync claims that no test enforces. C0 is report-only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sync_claims.claims import Claim, claims, declared_symbols
from sync_claims.enforcement import test_reference_sets, unenforced

REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_ROOT = REPO / "packages" / "python" / "goldenmatch" / "goldenmatch"
DEFAULT_TESTS = REPO / "packages" / "python" / "goldenmatch" / "tests"

SCOPE_NOTE = (
    "scope: claims are read from docstrings under {root} and enforcement from "
    "{tests} only -- other packages, the TypeScript port and _archive are out "
    "of reach by construction, and their silence here is not a clean bill. "
    "Module-level claims are reported but NOT triaged: a module has no single "
    "symbol a test can reference. A claim listed as UNVERIFIED is not safe -- "
    "some test references both names, which does not mean it compares them."
)


def _as_dict(claim: Claim) -> dict:
    return {
        "module": claim.module,
        "symbol": claim.symbol,
        "lineno": claim.lineno,
        "keyword": claim.keyword,
        "target": claim.target,
        "window": claim.window,
    }


def inventory(root: Path, tests_root: Path) -> dict:
    """Bucket every claim under `root` by enforcement state."""
    all_claims = claims(root, symbols=declared_symbols(root))
    symbol_claims = [c for c in all_claims if c.kind == "symbol"]
    module_claims = [c for c in all_claims if c.kind == "module"]
    resolvable = [c for c in symbol_claims if c.target is not None]
    unresolvable = [c for c in symbol_claims if c.target is None]

    reference_sets = test_reference_sets(tests_root)
    findings = unenforced(resolvable, reference_sets)
    finding_ids = {(c.module, c.symbol, c.lineno) for c in findings}
    unverified = [
        c for c in resolvable if (c.module, c.symbol, c.lineno) not in finding_ids
    ]

    return {
        "counts": {
            "claims": len(all_claims),
            "resolvable": len(resolvable),
            "unenforced": len(findings),
            "unverified": len(unverified),
            "unresolvable": len(unresolvable),
            "module_level": len(module_claims),
            "test_files_scanned": len(reference_sets),
        },
        "unenforced": [_as_dict(c) for c in findings],
        "unverified": [_as_dict(c) for c in unverified],
        "unresolvable": [_as_dict(c) for c in unresolvable],
        "module_level": [_as_dict(c) for c in module_claims],
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--tests", type=Path, default=DEFAULT_TESTS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    inv = inventory(args.root, args.tests)
    if args.json:
        print(json.dumps(inv, indent=2))
        return 0

    counts = inv["counts"]
    print(SCOPE_NOTE.format(root=args.root, tests=args.tests))
    print()
    if counts["test_files_scanned"] == 0:
        # Every claim looks unenforced when nothing was scanned. That is a
        # broken run, not a perfect score, and it must not read as findings.
        print(
            f"NO TEST FILES SCANNED under {args.tests} -- every claim below "
            f"would be reported unenforced for that reason alone. Fix --tests "
            f"before reading this as a result."
        )
        print()

    print(
        f"{counts['claims']} claim(s); {counts['resolvable']} resolvable and "
        f"symbol-level; {counts['unenforced']} UNENFORCED, "
        f"{counts['unverified']} unverified"
    )
    print(
        f"  reported but not triaged: {counts['unresolvable']} unresolvable, "
        f"{counts['module_level']} module-level"
    )
    print(f"  test files scanned: {counts['test_files_scanned']}")
    print()

    for entry in sorted(
        inv["unenforced"], key=lambda e: (e["module"], e["lineno"])
    ):
        print(f"  {entry['module']}:{entry['lineno']}  {entry['symbol']}")
        print(f"      --{entry['keyword']}--> {entry['target']}")
        print(f"      claim: {entry['window'][:100]}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
PYTHONPATH=scripts python -m pytest scripts/test_sync_claims_report.py -q
```

Expected: `7 passed`.

- [ ] **Step 5: Run the report against the real package**

```bash
cd scripts && PYTHONPATH=. python -m sync_claims.report | head -30
```

Expected: roughly `319 claim(s); 212 resolvable and symbol-level; 168 UNENFORCED, 44 unverified`. These counts drift as docstrings change — they are NOT asserted in any test. Record the actual numbers in the commit message.

- [ ] **Step 6: Sabotage-verify the empty-tests guard**

```bash
cp scripts/sync_claims/report.py /tmp/rep.bak && test -s /tmp/rep.bak && echo "backup ok"
python - <<'EOF'
import pathlib
p = pathlib.Path("scripts/sync_claims/report.py"); s = p.read_text(encoding="utf-8")
old = 'if counts["test_files_scanned"] == 0:'
new = 'if False:'
assert s.count(old) == 1, "sabotage did not apply"
p.write_text(s.replace(old, new), encoding="utf-8")
print("sabotage applied: empty-tests guard disabled")
EOF
PYTHONPATH=scripts python -m pytest scripts/test_sync_claims_report.py -q
# Expected: test_an_empty_tests_root_is_reported_not_presented_as_findings FAILS
cp /tmp/rep.bak scripts/sync_claims/report.py
PYTHONPATH=scripts python -m pytest scripts/test_sync_claims_report.py -q
# Expected: 7 passed
```

- [ ] **Step 7: Run ruff and commit**

```bash
python -m ruff check scripts/sync_claims/ scripts/test_sync_claims_report.py
git add scripts/sync_claims/report.py scripts/test_sync_claims_report.py
git commit -F - <<'EOF'
feat(sync-claims): report the four buckets with separate counts

Unenforced findings, unverified co-references, unresolvable claims and
module-level claims, each in its own bucket.

Claim count and finding count print separately, so deleting six words from
a docstring cannot read as progress. Each finding prints the matched claim
window, so a wrong first-match target resolution is visible to triage
rather than silent.

An empty tests root prints NO TEST FILES SCANNED before the findings.
Without that, pointing --tests at the wrong directory reports every claim
as unenforced and looks like a result rather than a broken run.

Exits 0 on findings. C0 is report-only; the gate is C3.
EOF
```

---

### Task 4: CI job and path filter

**Files:**
- Modify: `.github/filters.yml`
- Modify: `.github/workflows/ci.yml`
- Test: `scripts/test_workflow_yaml.py` (existing; the filter-coverage gate reads it)

**Interfaces:**
- Consumes: `scripts/sync_claims/report.py` as `python -m sync_claims.report`.
- Produces: a `sync_claims` job, report-only.

- [ ] **Step 1: Add the path filter**

In `.github/filters.yml`, add a `sync_claims` entry. It must watch the code the audit reads, not just the audit itself — a filter that does not cover the scanned tree means the job never re-runs when a claim is added:

```yaml
sync_claims:
  - 'scripts/sync_claims/**'
  - 'scripts/test_sync_claims_*.py'
  - 'scripts/fixtures/incident_6c89042c7/**'
  - 'scripts/fixtures/sync_enforcement/**'
  - 'packages/python/goldenmatch/goldenmatch/**'
  - 'packages/python/goldenmatch/tests/**'
  - '.github/workflows/ci.yml'
```

- [ ] **Step 2: Wire the filter into the `changes` job's outputs**

**Without this the job never runs.** `ci.yml`'s `changes` job declares one explicit output line per filter (see its `outputs:` block). A filter that has no line produces an empty output, `needs.changes.outputs.sync_claims` is falsy, and the `if:` gate below is never true — the job is skipped on every run and the lane reports green having measured nothing. PR #2839 shipped exactly this, with two jobs silently skipped.

Add to the `changes` job's `outputs:` block, alongside `scripts_lint` and `docs_regen`:

```yaml
      sync_claims: ${{ steps.filter.outputs.sync_claims }}
```

- [ ] **Step 3: Add the job**

In `.github/workflows/ci.yml`, after the `shared_decisions` job:

```yaml
  sync_claims:
    needs: changes
    if: needs.changes.outputs.sync_claims == 'true' || needs.changes.outputs.force_all == 'true'
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
        run: uv run pytest scripts/test_sync_claims_claims.py scripts/test_sync_claims_enforcement.py scripts/test_sync_claims_report.py -q
      - name: Unenforced sync-claim report
        # Report-only by design: main() returns 0 whatever it finds. The gate
        # is stage C3, after the C1 triage establishes a floor.
        env:
          PYTHONPATH: scripts
        run: uv run python -m sync_claims.report
```

- [ ] **Step 4: Do NOT add an entry to `check_filter_coverage.py`'s `REQUIRED` map**

Called out because it is the tempting move and it is wrong. That map's own comment reads: *"Each entry is a real regression or a real near-miss, not a hypothetical."* A `sync_claims` entry today would be a hypothetical, and adding one dilutes a curated list whose value is that every line records something that actually happened.

The generic `check_job_filter_coverage` in the same file already checks every job in `ci.yml` against the filter gating it, with no curation needed. That is what covers this job. If it reports a NEW gap for `sync_claims`, the filter is wrong — widen the filter; never add the job to `KNOWN_JOB_FILTER_GAPS`.

- [ ] **Step 5: Run the gates**

```bash
python scripts/check_filter_coverage.py
PYTHONPATH=scripts python -m pytest scripts/test_workflow_yaml.py -q
```

Expected: `CI filter coverage OK (...)`, `CI job-vs-filter coverage OK (...)`, and the workflow-yaml tests passing. If the job-vs-filter gate reports a NEW gap, the filter is wrong — fix the filter, do not add the job to the known-gaps list.

- [ ] **Step 6: Prove the job is not skipped**

A filter gate that is wired wrong fails silently, so assert the wiring rather than trusting it. `scripts/test_workflow_yaml.py` already parses `ci.yml`; add:

```python
def test_sync_claims_job_is_reachable():
    """A job whose gating output is never emitted is skipped on every run.

    The `changes` job needs an explicit `outputs:` line per filter. Without it
    `needs.changes.outputs.sync_claims` is empty, the `if:` is false, and the
    lane reports green having measured nothing -- PR #2839's defect.
    """
    spec = _load_ci()  # existing helper in this file
    outputs = spec["jobs"]["changes"]["outputs"]
    assert "sync_claims" in outputs, (
        "the changes job emits no sync_claims output, so the job can never run"
    )
    assert "sync_claims" in spec["jobs"]["sync_claims"]["if"]
```

Run: `PYTHONPATH=scripts python -m pytest scripts/test_workflow_yaml.py -q`
Expected: all pass, including the new test. If `_load_ci` is named differently in that file, use whatever helper the existing tests use — do not add a second YAML loader.

- [ ] **Step 7: Commit**

```bash
git add .github/filters.yml .github/workflows/ci.yml scripts/test_workflow_yaml.py
git commit -F - <<'EOF'
ci: report-only sync-claim job on its own filter

The changes job emits an explicit sync_claims output. Without that line the
gating expression is empty, the if: is false, and the job is skipped on
every run while the lane reports green -- PR #2839's defect. A workflow
test asserts the output exists.

The filter watches the goldenmatch package and its tests, not just the
detector: a claim is added by editing a docstring in the scanned tree, and
a filter covering only scripts/ would never re-run the audit when that
happens. Phase A shipped a dead-code job whose filter had exactly that
hole, and it passed green with the job skipped.

Report-only. The report exits 0 whatever it finds; the gate is C3, after
C1 establishes a triaged floor.
EOF
```

---

## Self-Review

**Spec coverage.** Every requirement maps to a task:

| spec requirement | task |
| --- | --- |
| claim extraction from docstrings | 1 |
| target resolution, bare identifiers, first match | 1 |
| incident fixture at `6c89042c7^`, claim extracted | 1 |
| module-level claims extracted and marked | 1 |
| executable references only | 2 |
| docstring-only co-mention is not enforcement | 2 |
| synthetic enforcement fixture, three states | 2 |
| unresolvable claims in their own bucket | 2, 3 |
| UNVERIFIED never presented as safe | 3 (SCOPE_NOTE) |
| claim count and finding count separate | 3 |
| report prints the matched window | 3 |
| "no findings" vs "detector did not run" | 3 (empty-tests guard) |
| every test sabotage-verified, sabotage asserted to apply | 1, 2, 3 |
| report-only, no gate | 3, 4 |

Not in this plan, by design: C1 triage, C2 remediation, C3 ratchet.

**Placeholder scan.** No TBD/TODO; every code step carries the code.

**Type consistency.** `Claim` is defined in Task 1 and used unchanged in Tasks 2 and 3. `claims(root, *, symbols=None)` keeps its keyword-only `symbols` argument across Tasks 2 and 3. `unenforced(claim_list, reference_sets)` and `test_reference_sets(tests_root)` match their Task-2 definitions where Task 3 calls them. `inventory(root, tests_root)` and `main(argv)` match their Task-3 test.

**Two defects found and fixed during this self-review**, both in Task 4:

1. `check_filter_coverage.py`'s `REQUIRED` map takes `(path, reason)` tuples, not bare strings — the draft would not have parsed.
2. More importantly, that file states its own contract: *"Each entry is a real regression or a real near-miss, not a hypothetical."* Adding speculative `sync_claims` entries would violate it. The generic `check_job_filter_coverage` added in phase A already checks every job against its filter automatically, so Task 4 relies on that and touches no curated list.
