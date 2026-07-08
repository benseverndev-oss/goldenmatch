# goldenpipe profile_complexity cross-surface parity — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove TS `profileComplexity` reproduces Python `profile_complexity`'s null density, via one hand-authored vector + two in-language replay tests.

**Architecture:** A shared `profile_complexity.json` vector (input rows → expected {max,mean} null density) lives in the canonical cross-surface vectors dir. A Python replay test (box-runnable) and a TS replay test (CI-only) each call the real function and assert against the vector. No bridge fns, no emitter, no Rust leg (profiling is impure host glue).

**Tech Stack:** Python (polars, pytest — box), TypeScript (vitest — CI). Hand-authored JSON vector.

**Spec:** `docs/superpowers/specs/2026-07-07-goldenpipe-profile-complexity-parity-design.md`

---

## Environment

```bash
cd "D:/show_case/gg-local-llm"
INTERP="D:/show_case/goldenmatch/.venv/Scripts/python.exe"
export PYTHONPATH="packages/python/goldenpipe;packages/python/infermap;packages/python/goldencheck-types"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
```
(`;` separator, native Windows.) Branch `feat/goldenpipe-profile-complexity-parity` (off fresh origin/main, spec committed). **Python is box-runnable; TS is CI-only (vitest OOMs).**

**Expecteds already verified on the box** (all 6 cases, real `profile_complexity`):

| case | rows | max | mean |
|------|------|-----|------|
| no_nulls | `[{a:1,b:2},{a:3,b:4}]` | 0.0 | 0.0 |
| one_null_heavy | `[{a:1,b:null},{a:null,b:null},{a:3,b:4},{a:4,b:null}]` | 0.75 | 0.5 |
| all_null_col | `[{a:null,b:1},{a:null,b:2}]` | 1.0 | 0.5 |
| single_no_null | `[{a:1,b:2}]` | 0.0 | 0.0 |
| single_all_null | `[{a:null,b:null}]` | 1.0 | 1.0 |
| empty | `[]` | 0.0 | 0.0 |

## File Structure

| File | Responsibility |
|------|----------------|
| `packages/rust/extensions/goldenpipe-core/tests/vectors/profile_complexity.json` | The shared contract (new) |
| `packages/python/goldenpipe/tests/test_profile_complexity_parity.py` | Python replay (new, box) |
| `packages/typescript/goldenpipe/tests/parity/profile-complexity-parity.test.ts` | TS replay (new, CI) |

---

### Task 1: Vector + Python replay test (box-verified)

**Files:**
- Create: `packages/rust/extensions/goldenpipe-core/tests/vectors/profile_complexity.json`
- Create: `packages/python/goldenpipe/tests/test_profile_complexity_parity.py`

- [ ] **Step 1: Author the vector** — `packages/rust/extensions/goldenpipe-core/tests/vectors/profile_complexity.json`. UNIFORM keys per case; snake_case `expected` keys; float literals. `expected` values are the box-verified table above.

```json
[
  {"_comment": "Python+TS glue-parity ONLY. profile_complexity is impure host glue (Polars in Python, Row[] in TS), NOT in the portable goldenpipe-core decision core, so golden_vectors.rs does NOT replay this file. Every case uses UNIFORM keys (Python derives columns from the frame-schema union; TS from Object.keys(rows[0]) — they agree only when every row has the same keys, which is the CSV-real case). Replayed by test_profile_complexity_parity.py (Python) and profile-complexity-parity.test.ts (TS)."},
  {"comment": "no nulls -> zeros",
   "input": {"rows": [{"a": 1, "b": 2}, {"a": 3, "b": 4}]},
   "expected": {"max_null_density": 0.0, "mean_null_density": 0.0}},
  {"comment": "one null-heavy column: a 1/4, b 3/4",
   "input": {"rows": [{"a": 1, "b": null}, {"a": null, "b": null}, {"a": 3, "b": 4}, {"a": 4, "b": null}]},
   "expected": {"max_null_density": 0.75, "mean_null_density": 0.5}},
  {"comment": "all-null column: a 1.0, b 0.0",
   "input": {"rows": [{"a": null, "b": 1}, {"a": null, "b": 2}]},
   "expected": {"max_null_density": 1.0, "mean_null_density": 0.5}},
  {"comment": "single row, no null",
   "input": {"rows": [{"a": 1, "b": 2}]},
   "expected": {"max_null_density": 0.0, "mean_null_density": 0.0}},
  {"comment": "single row, all null",
   "input": {"rows": [{"a": null, "b": null}]},
   "expected": {"max_null_density": 1.0, "mean_null_density": 1.0}},
  {"comment": "empty rows -> zeros",
   "input": {"rows": []},
   "expected": {"max_null_density": 0.0, "mean_null_density": 0.0}}
]
```
NOTE: the leading `{"_comment": ...}` object is a doc entry; the tests SKIP any array element without an `"input"` key (see the test below). (Mirrors how `auto_config.json`-style vectors carry prose without breaking the replay loop.)

- [ ] **Step 2: Write the Python replay test** — `packages/python/goldenpipe/tests/test_profile_complexity_parity.py`:

```python
"""Cross-surface parity: Python profile_complexity == the shared vector
(which the TS profile-complexity-parity.test.ts also replays). Box-runnable."""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from goldenpipe.autoconfig_glue import profile_complexity
from goldenpipe.models.context import PipeContext

# test is at packages/python/goldenpipe/tests/test_*.py
# parents: [0]=tests [1]=goldenpipe [2]=python [3]=packages [4]=REPO ROOT.
_VECTOR = (
    Path(__file__).resolve().parents[4]
    / "packages/rust/extensions/goldenpipe-core/tests/vectors/profile_complexity.json"
)


def _cases() -> list[dict]:
    data = json.loads(_VECTOR.read_text())
    return [c for c in data if "input" in c]  # skip the leading _comment entry


@pytest.mark.parametrize("case", _cases())
def test_profile_complexity_matches_vector(case: dict) -> None:
    rows = case["input"]["rows"]
    df = pl.DataFrame(rows)
    comp = profile_complexity(PipeContext(df=df))
    exp = case["expected"]
    assert comp.max_null_density == pytest.approx(exp["max_null_density"]), case.get("comment")
    assert comp.mean_null_density == pytest.approx(exp["mean_null_density"]), case.get("comment")
```

- [ ] **Step 3: Run the Python test — verify PASS (box)**
```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_profile_complexity_parity.py -q
```
Expected: 6 passed. If a case fails, the hand-authored `expected` is wrong — fix the VECTOR to the box-verified value (do NOT weaken the assertion). Confirm the `_comment` entry is skipped (6 parametrized cases, not 7).

- [ ] **Step 4: Ruff + commit**
```bash
"$INTERP" -m ruff check packages/python/goldenpipe/tests/test_profile_complexity_parity.py
git add packages/rust/extensions/goldenpipe-core/tests/vectors/profile_complexity.json packages/python/goldenpipe/tests/test_profile_complexity_parity.py
git commit -m "test(goldenpipe): profile_complexity parity vector + Python replay (Leg A)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

### Task 2: TS replay test (CI-only)

**Files:**
- Create: `packages/typescript/goldenpipe/tests/parity/profile-complexity-parity.test.ts`

- [ ] **Step 1: Write the TS replay test.** Mirror `planner-parity.test.ts`'s vector cross-read. The vector's `expected` is snake_case; `profileComplexity` returns camelCase — field-map per assertion.
```ts
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { profileComplexity } from "../../src/core/autoconfigGlue.js";
import type { Row } from "../../src/core/index.js";

const VEC = fileURLToPath(
  new URL(
    "../../../../rust/extensions/goldenpipe-core/tests/vectors/profile_complexity.json",
    import.meta.url,
  ),
);

interface Case {
  comment?: string;
  input?: { rows: Row[] };
  expected?: { max_null_density: number; mean_null_density: number };
}

const cases = (JSON.parse(readFileSync(VEC, "utf8")) as Case[]).filter((c) => c.input);

describe("profileComplexity == goldenpipe-core profile_complexity vector", () => {
  for (const c of cases) {
    it(c.comment ?? "case", () => {
      const comp = profileComplexity(c.input!.rows);
      expect(comp.maxNullDensity).toBeCloseTo(c.expected!.max_null_density, 10);
      expect(comp.meanNullDensity).toBeCloseTo(c.expected!.mean_null_density, 10);
    });
  }
});
```

- [ ] **Step 2: Eyeball-verify (no vitest on box)** — the read path matches `planner-parity.test.ts` (`../../../../rust/extensions/goldenpipe-core/tests/vectors/`); the snake→camel mapping is correct (`max_null_density`→`maxNullDensity`, `mean_null_density`→`meanNullDensity`); `profileComplexity` is exported from `autoconfigGlue.ts` and takes `readonly Row[]`; the `.filter((c) => c.input)` skips the `_comment` entry.

- [ ] **Step 3: Commit**
```bash
git add packages/typescript/goldenpipe/tests/parity/profile-complexity-parity.test.ts
git commit -m "test(goldenpipe-ts): profile_complexity parity replay (Leg B)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

### Task 3: Ship

**Files:** none.

- [ ] **Step 1: Rebase + push + PR**
```bash
unset GH_TOKEN; gh auth switch --user benzsevern >/dev/null 2>&1; export GH_TOKEN=$(gh auth token --user benzsevern)
git fetch origin main -q && git rebase origin/main
git push -u origin feat/goldenpipe-profile-complexity-parity --force-with-lease
gh pr create --repo benseverndev-oss/goldenmatch --base main --head feat/goldenpipe-profile-complexity-parity \
  --title "test(goldenpipe): profile_complexity cross-surface parity (null density)" \
  --body "<summary: one hand-authored vector (packages/rust/.../tests/vectors/profile_complexity.json) + two in-language replay tests prove TS profileComplexity == Python profile_complexity on null density. Closes the null-density parity gap C2 deferred (a null-heavy pipe_parity fixture is a no-op: low_confidence==default stages; null density observable only via the >=100k refuse). Uniform-key cases (Python frame-schema union vs TS Object.keys(rows[0]) agree only there — the CSV-real case). Python leg box-verified; TS CI-only. No parseCsv change, no Rust leg (impure glue).>

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

- [ ] **Step 2: Watch the typescript job** (box can't run vitest). The TS replay lives in the `typescript` job.
```bash
gh pr checks <PR#> --repo benseverndev-oss/goldenmatch
# if the typescript job fails:
gh run view <run-id> --repo benseverndev-oss/goldenmatch --log-failed | grep -iE "profile-complexity|profileComplexity|toBeCloseTo|error TS|Expected|Received" | head -20
```
Likely-red causes (all avoidable): read-path depth wrong, snake/camel field mismatch, or the `_comment` entry not filtered. Fix, commit, push, re-check.

- [ ] **Step 3: Arm auto-merge + STOP**
```bash
gh pr merge <PR#> --auto --squash   # WITHOUT --delete-branch (merge queue); if 'strategy set by queue', run: gh pr merge <PR#> --auto
```
Then STOP.

---

## Cross-cutting reminders
- **Uniform keys** in every vector case (Python union vs TS first-row-keys agree only there).
- **snake_case in the vector `expected`**; TS field-maps to camelCase, Python reads snake_case directly.
- **Python test uses `parents[4]`** (test at `tests/`, one dir shallower than `tests/core/` which uses `parents[5]`).
- **Both tests skip the leading `_comment` entry** (filter on `"input"` presence).
- Python box-verified; TS CI-gated. No `parseCsv` change; no Rust/WASM leg.
- The vector file is inert to `golden_vectors.rs` (it hard-codes family names — no dir glob) and to the freshness gate (which only scans goldenmatch/goldenpipe fixture dirs).
