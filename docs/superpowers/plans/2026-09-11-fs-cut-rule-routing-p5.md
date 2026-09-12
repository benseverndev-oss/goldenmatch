# FS Link-Cut Routing, Phase P5: The Router On by Default — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `GOLDENMATCH_FS_CUT_ROUTER` default on, with `off` as a kill switch, once three prerequisites are in place:
- the router is cheap on per-block scorer paths;
- the gate replays exactly what the live router reads;
- a declined route is reported distinctly.

Then prove the existing quality gates hold with the router on.

**Architecture:**
- **Cheap routing.** The router reads histogram-free diagnostics: `cut_diagnostics(mk, em, admitted=False)` skips the admitted-fraction pass (nine rule cutoffs plus an Otsu split). The gate replays with `admitted_fraction=None`. Live routing and replay therefore match by construction, and the router-on cost per link cut falls from about 47 µs to about 9 µs.
- **Exact replay.** Sweep scorecards keep full float precision, so a diagnostic near a row boundary replays exactly.
- **Declined routes.** A router that ran and matched no row reports `default rule (router: no row matched)`.
- **The flip.**
  - `_fs_cut_router_enabled()` treats unset as on.
  - The matrix now runs on `probabilistic.py` changes.
  - The flip ships as its own draft PR.
- **Gate evidence.**
  - CI runs the tests and `quality_gate`.
  - A triage task pins routing-independent tests.
  - `bench-suggest-quality` and `bench-quality-scale` are dispatched on the flip branch, with the router on through the code default. QIS is compared rung by rung against a router-off run.

**Tech Stack:** Python 3.12, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-11-fs-cut-rule-routing-design.md` on branch `docs/fs-cut-rule-routing-spec`. The relevant sections are "Router", "Router inputs", "Gate", "Error handling" and "Rollout" P5. The P5 amendment is pushed together with this plan. The plan builds on P4, draft PR #2945 (branch `feat/fs-cut-rule-rows`, head `929d552a2`). There, the shipped table `sparse_prior_binds` + `midpoint_binds_wide` passes the gate: design PASS, fired 5 of 20; held-out PASS, fired 3 of 14; routed arms agree.

## Global Constraints

**Behaviour**
- **Kill switch.** `GOLDENMATCH_FS_CUT_ROUTER=off` (or `0` / `false`, case- and space-insensitive) means the router never runs. Every cutoff, source, `cut_rule` and `cut_reason` is then byte-identical to P4's default. Tests pin this.
- **Default.** Unset, `on`, `1` or `true` turns the router on. An unrecognised value warns and uses the default, which is now on (spec, Error handling).
- **Precedence is unchanged:**
  1. explicit `link_threshold`;
  2. calibrated cutoff;
  3. pinned `link_cut_rule`;
  4. router;
  5. default rule (`GOLDENMATCH_FS_LINEAR_CUT`, default `prior_mid`).

  Posterior scoring bypasses rules and the router.
- **`cut_reason` strings:**
  - routed: `routed by <row>: <reason>` (`fs_cut_rules.ROUTED_REASON_PREFIX`);
  - router on, no row matched: `default rule (router: no row matched)`;
  - router off: `default rule`.
- **The shipped rows do not change in P5.** No new row, no edited threshold.
- **The routing path never reads `admitted_fraction`.** It is None there, and the gate replays with it None.

**The flip is gated (spec Gate and the P5 amendment).** Every item must hold at the flip PR's head:
- **`fs-cut-rule-matrix`:** the merge job's shipped gate passes on design AND held-out.
- **`quality_gate` (in `ci`):** PASS.
  - It fails only on an F1 drop of more than 0.01 or an anchor-signal change.
  - A FAIL is a regression that blocks the flip. Never re-bless around a drop.
- **`bench-suggest-quality`:** the push-triggered fast gate and a dispatched run (fast gate plus gym gate) are green on the flip branch.
  - A gym FAIL whose only drift is an F1 improvement on a dataset where a row routed may be re-blessed (`-f mode=gym-bless`), with a recorded ruling.
  - Any drop blocks the flip.
- **`bench-quality-scale` (QIS):** there is no committed baseline.
  - **CI tier:** dispatch it on the flip branch (router on) and on `feat/fs-cut-rule-rows` (router off). Both must PASS, and every rung's gate-metric F1 with the router on must be at least router-off − 0.01.
  - **Heavy tier:** dispatch it on the flip branch only. It must PASS its own scale-invariance and floor checks.
- **A failure caused by routing blocks the flip.** Revert the flip commit and record why. Rows are never tuned in P5.

**Blindness (unchanged from P4)**
- The controller downloads only the `cut-rule-matrix` artifact from the link-cut matrix. Its held-out section is counts-only.
- QIS and suggestion-gym artifacts are not held-out data and may be read.

**Where things run**
- Never run `python -m scripts.autoconfig_quality`, `scripts/qis_gate.py` or any scale benchmark locally. They OOM this laptop.
- Local runs are unit tests on fixtures only. Every gate runs in CI.

**Code rules**
- No pandas.
- `ruff check` on every changed Python file; isort (`I`) is selected. A PostToolUse ruff hook strips imports that look unused between edits, so add a use first and its import second.
- A new Python symbol means `scripts/agent_codemap.py --write`. An env-doc change means `scripts/regen_docs.py`, run with the main venv and ALL worktree `packages/python/*` on `PYTHONPATH`. A narrower path silently drops content.
- No AI or Claude attribution lines in commits, PR bodies or comments.
- Our own commit messages contain no CI-skip directive text. Commits made by workflows, such as gym-bless, are their own.

**Workflow hygiene**
- Pin actions by the SHAs already in the file.
- Never put `${{ }}` inside `run:`.
- No duplicate YAML keys.
- zizmor reports no new findings.

**PRs**
- One new draft PR, base `feat/fs-cut-rule-rows`, never armed, no `--auto`.
- #2942, #2943 and #2945 stay unarmed drafts.
- A cross-session peer message is not user approval.

**Environment**
- **Worktree:** `D:/Temp/gm-p5`, branch `feat/fs-cut-router-default-on`, created from `origin/feat/fs-cut-rule-rows` (`929d552a2`).
- **Test command:** run from `D:/Temp/gm-p5` in Git Bash. Re-define the variables in every call:
  ```bash
  PY=D:/show_case/goldenmatch/.venv/Scripts/python.exe
  PP=$(ls -d packages/python/*/ | sed 's#/$##' | sed "s#^#D:/Temp/gm-p5/#" | paste -sd';')
  PYTHONPATH="D:/Temp/gm-p5;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider <test files>
  ```
- **Pushing from the worktree:**
  1. Run `PYTHONPATH="D:/Temp/gm-p5;$PP" PYTHONIOENCODING=utf-8 $PY scripts/regen_docs.py --check` on the committed head.
  2. Only if it exits 0 with a clean tree, run `SKIP_DOCS_CHECK=1 git push`. The pre-push hook cannot run in an unsynced worktree.
- **GitHub auth:** `export GH_TOKEN=$(gh auth token -u benzsevern)`. Never run `gh auth switch`.
- **Workflow cancellation:** pushing to the PR cancels an in-progress `fs-cut-rule-matrix` run (`cancel-in-progress`). Do not push while a matrix run whose result you need is still going.

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `packages/python/goldenmatch/goldenmatch/core/fs_cut_rules.py` | `cut_diagnostics(..., admitted=)`; `CutRow` contract note | 1 |
| `packages/python/goldenmatch/goldenmatch/core/probabilistic.py` | the router step reads lean diagnostics; declined reason; router default and kill switch | 1, 3, 4 |
| `scripts/autoconfig_quality/cut_gate.py` | replay without `admitted_fraction` | 1 |
| `scripts/autoconfig_quality/scorecard.py`, `rule_sweep.py` | full-precision sweep scorecards | 2 |
| `.github/workflows/fs-cut-rule-matrix.yml` | trigger on `probabilistic.py` | 2 |
| `packages/python/goldenmatch/tests/test_fs_cut_router.py` | routing path, declined reason, default on, kill switch | 1, 3, 4 |
| `packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py` | resolver expectations with the router on by default | 4 |
| `scripts/autoconfig_quality/tests/test_cut_gate.py`, `tests/test_scorecard.py`, `tests/test_rule_sweep.py` | replay and precision tests | 1, 2 |
| `docs-site/goldenmatch/tuning.mdx`, `packages/python/goldenmatch/CHANGELOG.md` | router docs | 4 |
| goldenmatch tests that CI finds routing-sensitive | pinned or updated per the triage table | 5 |

---

### Task 1: Worktree and histogram-free routing diagnostics

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/fs_cut_rules.py`: `cut_diagnostics`, and the `CutRow` docstring.
- Modify: `packages/python/goldenmatch/goldenmatch/core/probabilistic.py`: the router branch of `_fs_resolved_cut`.
- Modify: `scripts/autoconfig_quality/cut_gate.py`: `_routes`.
- Test: `packages/python/goldenmatch/tests/test_fs_cut_router.py`, `scripts/autoconfig_quality/tests/test_cut_gate.py`.

**Interfaces:**
- Consumes: `CutDiagnostics`, `CutRow`, `ROWS`, `choose_cut_rule`, `rule_bits` (`fs_cut_rules`); `_fs_resolved_cut`, `_fs_cut_router_enabled` (`probabilistic`); `_routes`, `judge` (`cut_gate`). All come from P4.
- Produces: `cut_diagnostics(mk, em_result, *, admitted: bool = True) -> CutDiagnostics | None`. With `admitted=False`, `admitted_fraction` is None and no histogram pass runs. The router calls it with `admitted=False`, and `cut_gate._routes` replays with `admitted_fraction=None`.

- [ ] **Step 1: Create the worktree**

```bash
export GH_TOKEN=$(gh auth token -u benzsevern)
git -C D:/show_case/goldenmatch fetch origin feat/fs-cut-rule-rows
git -C D:/show_case/goldenmatch worktree add -b feat/fs-cut-router-default-on D:/Temp/gm-p5 origin/feat/fs-cut-rule-rows
git -C D:/Temp/gm-p5 log --oneline -1
```

Expected: a line starting `929d552a2`.

- [ ] **Step 2: Write the failing tests**

Append to `packages/python/goldenmatch/tests/test_fs_cut_router.py`. Its `_mk`, `_em`, `_diag` helpers and `R` import already exist.

```python
# ─── P5: the routing path is cheap and never reads admitted_fraction ──────────


def test_routing_diagnostics_skip_the_admitted_fraction_pass(monkeypatch):
    em = _em()
    em.training_score_histogram = {"counts": [5] * 100, "lo": -10.0, "hi": 14.0}
    calls: list[str] = []
    real = R.rule_bits
    monkeypatch.setattr(R, "rule_bits", lambda rule, *a, **k: calls.append(rule) or real(rule, *a, **k))

    lean = R.cut_diagnostics(_mk(), em, admitted=False)
    assert lean is not None and lean.admitted_fraction is None
    assert calls == [], "routing must not compute any rule cutoff for admitted_fraction"

    full = R.cut_diagnostics(_mk(), em)
    assert full.admitted_fraction is not None and calls
    same = {k: v for k, v in vars(lean).items() if k != "admitted_fraction"}
    assert same == {k: v for k, v in vars(full).items() if k != "admitted_fraction"}


def test_the_router_routes_on_diagnostics_without_admitted_fraction(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "on")
    em = _em()
    em.training_score_histogram = {"counts": [5] * 100, "lo": -10.0, "hi": 14.0}
    blind = R.CutRow(
        name="blind", rule="evidence_12", reason="admitted_fraction is absent",
        when=lambda d: d.admitted_fraction is None,
    )
    monkeypatch.setattr(R, "ROWS", (blind,))
    assert _fs_resolved_cut(_mk(), em, calibrated=False).rule == "evidence_12"
```

Append to `scripts/autoconfig_quality/tests/test_cut_gate.py`. Its `_diag`, `_rec`, `G` and `CutRow` already exist.

```python
def test_the_gate_replays_routing_without_admitted_fraction():
    """P5: the live router never sees admitted_fraction, so the replay must not either."""
    blind = CutRow(
        name="blind", rule="evidence_12", reason="r", when=lambda d: d.admitted_fraction is None
    )
    diag = {**_diag(), "admitted_fraction": {"prior_mid": 0.1}}
    rec = _rec({"default_loaded": 0.9, "evidence_12": 0.95}, diags={"fs": diag})
    v = G.judge(rec, (blind,))
    assert (v.fired, v.rule, v.passed) == (True, "evidence_12", True)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `PYTHONPATH="D:/Temp/gm-p5;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider packages/python/goldenmatch/tests/test_fs_cut_router.py scripts/autoconfig_quality/tests/test_cut_gate.py -k "admitted_fraction"`

Expected: 3 FAIL.
- The first fails with `TypeError: cut_diagnostics() got an unexpected keyword argument 'admitted'`.
- The router test fails because the rule is `prior_mid`, not `evidence_12`.
- The gate test fails on `fired`.

- [ ] **Step 4: Implement**

In `fs_cut_rules.py`, replace the signature, docstring and histogram guard of `cut_diagnostics`:

```python
def cut_diagnostics(mk: Any, em_result: Any, *, admitted: bool = True) -> CutDiagnostics | None:
    """Router inputs for ``mk`` from its trained model, or None without usable weights.

    ``admitted=False`` skips the training-histogram pass (every rule's cutoff plus the Otsu
    split) and leaves ``admitted_fraction`` None. The router reads the diagnostics this way,
    because a link cut is resolved once per scored block and the pass is most of its cost.
    """
```

Replace the line `    if hist and sum(hist["counts"]) > 0 and hist["hi"] > hist["lo"]:` with:

```python
    if admitted and hist and sum(hist["counts"]) > 0 and hist["hi"] > hist["lo"]:
```

In the `CutRow` docstring, replace:

```python
    ``when`` reads only :class:`CutDiagnostics` -- no labels, no scoring pass. ``reason`` is the
    human-readable mechanism, reported in ``cut_reason``.
```

with:

```python
    ``when`` reads only :class:`CutDiagnostics` -- no labels, no scoring pass -- and never
    ``admitted_fraction``, which is None on the routing path (``cut_diagnostics(...,
    admitted=False)``) and in the gate's replay. ``reason`` is the human-readable mechanism,
    reported in ``cut_reason``.
```

In `probabilistic.py`, `_fs_resolved_cut`, replace:

```python
        routed = choose_cut_rule(cut_diagnostics(mk, em_result))
```

with:

```python
        routed = choose_cut_rule(cut_diagnostics(mk, em_result, admitted=False))
```

In `scripts/autoconfig_quality/cut_gate.py`, `_routes`, replace:

```python
        choice = choose_cut_rule(CutDiagnostics(**d) if d else None, rows)
```

with:

```python
        # Exactly what the live router reads: no admitted_fraction (P5).
        replay = CutDiagnostics(**{**d, "admitted_fraction": None}) if d else None
        choice = choose_cut_rule(replay, rows)
```

- [ ] **Step 5: Run the tests**

Run:

```bash
PYTHONPATH="D:/Temp/gm-p5;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider \
  packages/python/goldenmatch/tests/test_fs_cut_router.py packages/python/goldenmatch/tests/test_fs_cut_rules.py \
  packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py packages/python/goldenmatch/tests/test_fs_training_histogram.py \
  scripts/autoconfig_quality/tests/test_cut_gate.py
```

Expected: all pass.

- [ ] **Step 6: Confirm the cost dropped, then commit**

Run this timing snippet from `D:/Temp/gm-p5`:

```bash
PYTHONPATH="D:/Temp/gm-p5;$PP" PYTHONIOENCODING=utf-8 $PY - <<'EOF'
import os, timeit
from types import SimpleNamespace
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core import probabilistic as P
fields = [MatchkeyField(field=f"f{i}", scorer="jaro_winkler", levels=3, partial_threshold=0.8) for i in range(5)]
mk = MatchkeyConfig(name="fs", type="probabilistic", fields=fields)
em = SimpleNamespace(match_weights={f"f{i}": [-6.0, 1.0, 10.0] for i in range(5)}, proportion_matched=0.05,
                     calibrated_link_threshold=None,
                     training_score_histogram={"counts": [((i * 37) % 11) + 1 for i in range(100)], "lo": -30.0, "hi": 50.0})
for v in ("off", "on"):
    os.environ["GOLDENMATCH_FS_CUT_ROUTER"] = v
    print(v, round(timeit.timeit(lambda: P._fs_resolved_cut(mk, em, False), number=20000) / 20000 * 1e6, 2), "us")
EOF
```

Expected: router `on` costs under 15 µs per call (P4 measured 47 µs), and `off` stays about 5 µs. Record both numbers in the report.

```bash
$PY -m ruff check packages/python/goldenmatch/goldenmatch/core/fs_cut_rules.py packages/python/goldenmatch/goldenmatch/core/probabilistic.py scripts/autoconfig_quality/cut_gate.py packages/python/goldenmatch/tests/test_fs_cut_router.py scripts/autoconfig_quality/tests/test_cut_gate.py
git add packages/python/goldenmatch/goldenmatch/core/fs_cut_rules.py packages/python/goldenmatch/goldenmatch/core/probabilistic.py scripts/autoconfig_quality/cut_gate.py packages/python/goldenmatch/tests/test_fs_cut_router.py scripts/autoconfig_quality/tests/test_cut_gate.py
git commit -m "perf(fs): route the link cut on diagnostics without the training-histogram pass"
```

---

### Task 2: Exact sweep diagnostics, and the matrix runs on `probabilistic.py`

**Files:**
- Modify: `scripts/autoconfig_quality/scorecard.py`: `_round_floats`, `build_scorecard`.
- Modify: `scripts/autoconfig_quality/rule_sweep.py`: the `build_scorecard` calls in `run` and `merge_cards`.
- Modify: `.github/workflows/fs-cut-rule-matrix.yml`: `on.pull_request.paths` and the header comment.
- Test: `scripts/autoconfig_quality/tests/test_scorecard.py`, `scripts/autoconfig_quality/tests/test_rule_sweep.py`.

**Interfaces:**
- Consumes: `build_scorecard`, `run`, `merge_cards` (P3/P4).
- Produces: `build_scorecard(results, *, native_version, git_sha, skipped=None, float_precision: int | None = 6)`. `None` keeps floats exact. Sweep scorecards are written with `float_precision=None`. The committed quality baseline keeps rounding.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/autoconfig_quality/tests/test_scorecard.py`:

```python
def test_float_precision_none_keeps_floats_exact():
    results = {"d": {"x": 2.1234567891234, "nested": [0.30000000000000004]}}
    exact = build_scorecard(results, native_version="v", git_sha="s", float_precision=None)
    assert exact["datasets"]["d"]["x"] == 2.1234567891234
    assert exact["datasets"]["d"]["nested"] == [0.30000000000000004]
    rounded = build_scorecard(results, native_version="v", git_sha="s")
    assert rounded["datasets"]["d"]["x"] == 2.123457
```

Append to `scripts/autoconfig_quality/tests/test_rule_sweep.py`:

```python
def test_sweep_scorecards_keep_cut_diagnostics_exact(tmp_path, monkeypatch):
    """P5: the gate replays recorded diagnostics against row boundaries; rounding to 6 dp could
    route a boundary dataset differently from the live router."""
    _clear_cut_env(monkeypatch)

    def fake(name, work_dir):
        rec = _record("design", {})
        rec["cut_diagnostics"] = {"fs": {"midpoint_bits": 2.1234567891234}}
        return rec

    monkeypatch.setattr(S, "sweep_dataset", fake)
    out = tmp_path / "card.json"
    assert S.run(["--datasets", "person", "--out", str(out)]) == 0
    card = json.loads(out.read_text())
    assert card["datasets"]["person"]["cut_diagnostics"]["fs"]["midpoint_bits"] == 2.1234567891234
    merged = S.merge_cards([card])
    assert merged["datasets"]["person"]["cut_diagnostics"]["fs"]["midpoint_bits"] == 2.1234567891234
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH="D:/Temp/gm-p5;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider scripts/autoconfig_quality/tests/test_scorecard.py scripts/autoconfig_quality/tests/test_rule_sweep.py -k "exact"`

Expected: FAIL. The first test raises `TypeError: ... unexpected keyword argument 'float_precision'`; the second fails on `2.123457 != 2.1234567891234`.

- [ ] **Step 3: Implement**

In `scripts/autoconfig_quality/scorecard.py`, replace `_round_floats` and `build_scorecard` with:

```python
def _round_floats(obj: Any, precision: int = _FLOAT_PRECISION) -> Any:
    """Recursively round every float for byte-stable serialization."""
    if isinstance(obj, float):
        return round(obj, precision)
    if isinstance(obj, dict):
        return {k: _round_floats(v, precision) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_floats(v, precision) for v in obj]
    return obj


def build_scorecard(
    results: dict[str, dict],
    *,
    native_version: str,
    git_sha: str,
    skipped: dict[str, str] | None = None,
    float_precision: int | None = _FLOAT_PRECISION,
) -> dict[str, Any]:
    """Assemble per-dataset records + a stable metadata header.

    ``float_precision=None`` keeps every float exact. The link-cut sweep uses that: its gate
    replays the recorded cut diagnostics against row boundaries, and a rounded value near a
    boundary could route differently from the live router."""
    return {
        "meta": {
            "native_version": native_version,
            "git_sha": git_sha,
            "datasets_run": sorted(results.keys()),
            "datasets_skipped": skipped or {},
        },
        "datasets": results if float_precision is None else _round_floats(results, float_precision),
    }
```

In `scripts/autoconfig_quality/rule_sweep.py`, `run`, replace:

```python
    card = build_scorecard(results, native_version=native_version, git_sha=git_sha, skipped=skipped)
```

with:

```python
    card = build_scorecard(
        results, native_version=native_version, git_sha=git_sha, skipped=skipped, float_precision=None
    )
```

In `merge_cards`, add `float_precision=None,` as the last keyword argument of the `merged = build_scorecard(` call, directly after its `skipped=...` argument.

In `.github/workflows/fs-cut-rule-matrix.yml`, add this line to `on.pull_request.paths` directly after `      - "packages/python/goldenmatch/goldenmatch/core/fs_cut_rules.py"`:

```yaml
      - "packages/python/goldenmatch/goldenmatch/core/probabilistic.py"
```

Then add these two comment lines at the end of the file's header comment block, directly above `name: fs-cut-rule-matrix`:

```yaml
# P5: the router is on by default, so any probabilistic.py change can move a routed cut;
# the matrix re-measures and re-gates the shipped table on those PRs.
```

- [ ] **Step 4: Run the tests and the workflow checks**

```bash
PYTHONPATH="D:/Temp/gm-p5;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider \
  scripts/autoconfig_quality/tests/test_scorecard.py scripts/autoconfig_quality/tests/test_rule_sweep.py \
  scripts/autoconfig_quality/tests/test_cut_gate.py scripts/test_workflow_yaml.py
zizmor .github/workflows/fs-cut-rule-matrix.yml
```

Expected: all pass. zizmor findings must equal those at `git show HEAD:.github/workflows/fs-cut-rule-matrix.yml`.

- [ ] **Step 5: Commit**

```bash
$PY -m ruff check scripts/autoconfig_quality/scorecard.py scripts/autoconfig_quality/rule_sweep.py scripts/autoconfig_quality/tests/test_scorecard.py scripts/autoconfig_quality/tests/test_rule_sweep.py
git add scripts/autoconfig_quality/scorecard.py scripts/autoconfig_quality/rule_sweep.py scripts/autoconfig_quality/tests/test_scorecard.py scripts/autoconfig_quality/tests/test_rule_sweep.py .github/workflows/fs-cut-rule-matrix.yml
git commit -m "fix(quality): keep link-cut sweep diagnostics exact and re-gate on probabilistic.py changes"
```

---

### Task 3: Say when the router ran and matched no row

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/probabilistic.py`: a new constant above `_fs_resolved_cut`, and the candidate block inside it.
- Test: `packages/python/goldenmatch/tests/test_fs_cut_router.py`.

**Interfaces:**
- Consumes: `_fs_resolved_cut` as Task 1 left it.
- Produces: `_FS_ROUTER_DECLINED_REASON = "default rule (router: no row matched)"`. This is the `cut_reason` when the router is on, no pin exists, and `choose_cut_rule` returns None. With the router off the reason stays `default rule`. A routed rule the model cannot supply still falls back with the `"<rule> unavailable: ...; default rule"` note.

- [ ] **Step 1: Write the failing tests**

In `packages/python/goldenmatch/tests/test_fs_cut_router.py`, replace the body of `test_no_matching_row_falls_through_to_the_default_rule` with:

```python
def test_no_matching_row_falls_through_to_the_default_rule(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "on")
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    resolved = _fs_resolved_cut(_mk(), _em(0.2), calibrated=False)
    assert (resolved.rule, resolved.reason) == ("prior_mid", "default rule (router: no row matched)")
```

Append:

```python
def test_router_off_keeps_the_plain_default_reason(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "off")
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    resolved = _fs_resolved_cut(_mk(), _em(0.2), calibrated=False)
    assert (resolved.rule, resolved.reason) == ("prior_mid", "default rule")


def test_a_declined_route_is_reported(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "on")
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    report = link_cut_report(_mk(), _em(0.2))
    assert (report["cut_rule"], report["cut_reason"]) == (
        "prior_mid", "default rule (router: no row matched)",
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH="D:/Temp/gm-p5;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider packages/python/goldenmatch/tests/test_fs_cut_router.py -k "default_rule or declined or plain_default"`

Expected: `test_no_matching_row_falls_through_to_the_default_rule` and `test_a_declined_route_is_reported` FAIL, because the reason is `default rule`. The router-off test passes.

- [ ] **Step 3: Implement**

In `probabilistic.py`, insert directly above `def _fs_resolved_cut(`:

```python
#: ``cut_reason`` when the router ran and no row matched: the default rule placed the cut.
_FS_ROUTER_DECLINED_REASON = "default rule (router: no row matched)"
```

In `_fs_resolved_cut`, replace this block:

```python
    candidates: list[tuple[str, str]] = []
    pinned = getattr(mk, "link_cut_rule", None)
    if pinned:
        candidates.append((pinned, "pinned by link_cut_rule"))
    elif _fs_cut_router_enabled():
        routed = choose_cut_rule(cut_diagnostics(mk, em_result, admitted=False))
        if routed is not None:
            candidates.append(routed)
    default = _fs_linear_cut_rule()
    if default is not None and all(default != rule for rule, _ in candidates):
        candidates.append((default, "default rule"))
```

with:

```python
    candidates: list[tuple[str, str]] = []
    declined = False
    pinned = getattr(mk, "link_cut_rule", None)
    if pinned:
        candidates.append((pinned, "pinned by link_cut_rule"))
    elif _fs_cut_router_enabled():
        routed = choose_cut_rule(cut_diagnostics(mk, em_result, admitted=False))
        if routed is not None:
            candidates.append(routed)
        declined = routed is None
    default = _fs_linear_cut_rule()
    if default is not None and all(default != rule for rule, _ in candidates):
        candidates.append((default, _FS_ROUTER_DECLINED_REASON if declined else "default rule"))
```

- [ ] **Step 4: Run the tests**

```bash
PYTHONPATH="D:/Temp/gm-p5;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider \
  packages/python/goldenmatch/tests/test_fs_cut_router.py packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py \
  packages/python/goldenmatch/tests/test_fs_linear_cut_rule.py packages/python/goldenmatch/tests/test_link_threshold_report_2483.py \
  scripts/autoconfig_quality/tests/test_rule_sweep.py scripts/autoconfig_quality/tests/test_cut_gate.py
```

Expected: all pass. The router is still off by default at this task, so default-path expectations are unchanged.

- [ ] **Step 5: Regenerate the codemap and commit**

```bash
PYTHONPATH="D:/Temp/gm-p5;$PP" $PY scripts/agent_codemap.py --write
$PY -m ruff check packages/python/goldenmatch/goldenmatch/core/probabilistic.py packages/python/goldenmatch/tests/test_fs_cut_router.py
git add packages/python/goldenmatch/goldenmatch/core/probabilistic.py packages/python/goldenmatch/tests/test_fs_cut_router.py docs/agent-codemap.json
git commit -m "feat(fs): report when the link-cut router ran and matched no row"
```

If `docs/agent-codemap.json` did not change, drop it from `git add`.

---

### Task 4: The router on by default, with `off` as the kill switch

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/probabilistic.py`: `_FS_CUT_ROUTER_ON`, `_FS_CUT_ROUTER_OFF`, `_fs_cut_router_enabled`.
- Modify: `packages/python/goldenmatch/tests/test_fs_cut_router.py`, `packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py`.
- Modify: `docs-site/goldenmatch/tuning.mdx` (the `GOLDENMATCH_FS_CUT_ROUTER` row) and `packages/python/goldenmatch/CHANGELOG.md` (the router bullet under `## [Unreleased]` / `### Added`).
- Regenerate: the derived docs via `scripts/regen_docs.py`.

**Interfaces:**
- Consumes: `_FS_ROUTER_DECLINED_REASON` (Task 3).
- Produces: `_fs_cut_router_enabled() -> bool` returns True unless the env is `off`/`0`/`false`. An unknown value warns and returns True.

- [ ] **Step 1: Write the failing tests**

In `packages/python/goldenmatch/tests/test_fs_cut_router.py`, replace `test_the_router_is_off_by_default` with:

```python
def test_the_router_is_on_by_default(monkeypatch):
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    resolved = _fs_resolved_cut(_mk(), _em(), calibrated=False)
    assert (resolved.rule, resolved.reason) == ("evidence_12", "routed by sparse: lambda under 0.05")


def test_the_kill_switch_turns_the_router_off(monkeypatch):
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    for value in ("off", "0", "false", " OFF "):
        monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", value)
        resolved = _fs_resolved_cut(_mk(), _em(), calibrated=False)
        assert (resolved.rule, resolved.reason) == ("prior_mid", "default rule"), value
```

Rename `test_an_unknown_router_value_warns_and_stays_off` to `test_an_unknown_router_value_warns_and_keeps_the_router_on`. Keep its warning-capture mechanism exactly as it is today; only the behaviour assertion changes, to:

```python
    assert resolved.rule == "evidence_12"
```

In `packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py`:

1. **Change the `"default"` entry of `_EXPECTED_CUT`** to:
   ```python
       "default": (
           _norm(math.log2(99.0)), LINK_THRESHOLD_EVIDENCE_RULE, "prior_mid",
           "default rule (router: no row matched)",
       ),
   ```
2. **Add an entry** directly after it:
   ```python
       "router_off": (_norm(math.log2(99.0)), LINK_THRESHOLD_EVIDENCE_RULE, "prior_mid", "default rule"),
   ```
3. **In `_resolver_case`, add this branch** directly after the `if case == "fallback":` branch's `return`:
   ```python
       if case == "router_off":
           monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "off")
           return _mk(), _em()
   ```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH="D:/Temp/gm-p5;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider packages/python/goldenmatch/tests/test_fs_cut_router.py packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py`

Expected: FAIL on `test_the_router_is_on_by_default`, `test_an_unknown_router_value_warns_and_keeps_the_router_on`, and the `default` parametrized case of `test_resolve_link_cut_is_what_every_caller_applies_and_reports`. The router is still off by default at this point.

- [ ] **Step 3: Implement**

In `probabilistic.py`, replace `_FS_CUT_ROUTER_ON`, `_FS_CUT_ROUTER_OFF` and `_fs_cut_router_enabled` with:

```python
_FS_CUT_ROUTER_ON = ("", "on", "1", "true")
_FS_CUT_ROUTER_OFF = ("off", "0", "false")


def _fs_cut_router_enabled() -> bool:
    """``GOLDENMATCH_FS_CUT_ROUTER``: pick each matchkey's link-cut rule from its trained model
    with ``fs_cut_rules.choose_cut_rule``. **Default ON** (spec P5: switched on once the gate held
    on the design and held-out sets and the existing quality gates held with it on).

    ``off``/``0``/``false`` is the kill switch: the router never runs and every cutoff is the
    unrouted default. A pinned ``link_cut_rule`` still wins, posterior scoring bypasses the
    router, and no matching row means the default rule. An unrecognised value warns and keeps
    the default (on).
    """
    value = os.environ.get("GOLDENMATCH_FS_CUT_ROUTER", "").strip().lower()
    if value in _FS_CUT_ROUTER_OFF:
        return False
    if value not in _FS_CUT_ROUTER_ON:
        logger.warning(
            "GOLDENMATCH_FS_CUT_ROUTER=%r is not one of on/off; the link-cut router stays on",
            value,
        )
    return True
```

- [ ] **Step 4: Run the router tests and every precedence test**

```bash
PYTHONPATH="D:/Temp/gm-p5;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider \
  packages/python/goldenmatch/tests/test_fs_cut_router.py packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py \
  packages/python/goldenmatch/tests/test_fs_linear_cut_rule.py packages/python/goldenmatch/tests/test_fs_link_threshold_drift_2483.py \
  packages/python/goldenmatch/tests/test_fs_threshold_calibration.py packages/python/goldenmatch/tests/test_link_threshold_report_2483.py \
  packages/python/goldenmatch/tests/test_fs_training_histogram.py packages/python/goldenmatch/tests/test_fs_cut_rules.py \
  scripts/autoconfig_quality/tests/test_rule_sweep.py scripts/autoconfig_quality/tests/test_cut_gate.py
```

Expected: all pass. For any other test in this list that now fails, apply the Task 5 triage table and record which row applied:
- **Routing-independent test** whose synthetic model now routes: pin `monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "off")` in that test.
- **Test that asserts the default cut reason:** update the expectation to the declined reason.

- [ ] **Step 5: Update the docs**

In `docs-site/goldenmatch/tuning.mdx`, replace the whole `GOLDENMATCH_FS_CUT_ROUTER` table row with:

```markdown
| `GOLDENMATCH_FS_CUT_ROUTER` | `on` | Routes the **linear** FS link-cut rule per probabilistic matchkey from its trained model: `fs_cut_rules.choose_cut_rule` reads the cut diagnostics (λ, the midpoint and prior cutoffs in bits, the field count, per-field weight spans) and the first matching row of the shipped table picks the rule; no match keeps the `GOLDENMATCH_FS_LINEAR_CUT` default. On by default; `off`/`0`/`false` is the kill switch and restores the unrouted cut exactly. A pinned `link_cut_rule`, an explicit `link_threshold`, a calibrated cutoff and posterior mode all win over it. A routed cut reports source `evidence_rule` and `cut_reason` `routed by <row>: <reason>`; when no row matches, `cut_reason` is `default rule (router: no row matched)`. A row ships only after never scoring below the default (within 0.01) on every design and held-out dataset of the link-cut matrix (`scripts/autoconfig_quality/cut_gate.py`). Shipped rows, first match wins: `sparse_prior_binds` (λ < 0.004, prior cutoff above the midpoint, at most 3 fields → `posterior_099`); `midpoint_binds_wide` (λ < 0.1, midpoint cutoff above the prior, at least 5 fields → `evidence_5`). Measured on the link-cut matrix: dblp_acm +0.0918, musicbrainz_20k +0.2673, febrl3 +0.0038 and febrl4 +0.0002 F1; every other design dataset unchanged. An unrecognised value warns and keeps the router on. |
```

In `packages/python/goldenmatch/CHANGELOG.md`, replace the whole bullet that starts `- **An opt-in router picks the FS link-cut rule per matchkey from the trained model.**` (all its wrapped lines, through `link-cut gate: \`sparse_prior_binds\`, \`midpoint_binds_wide\`.`) with:

```markdown
- **The FS link-cut rule is routed per matchkey from the trained model, on by default.**
  `GOLDENMATCH_FS_CUT_ROUTER` (default on; `off` is the kill switch) routes each probabilistic
  matchkey's linear link cutoff through `fs_cut_rules.choose_cut_rule`: an ordered table of rows
  over the model's cut diagnostics, where the first match wins and no match keeps the default
  rule. A pinned `link_cut_rule`, an explicit `link_threshold`, a calibrated cutoff and posterior
  scoring all still win. A routed cut reports source `evidence_rule` and `cut_reason`
  `routed by <row>: <reason>`; a router that matched no row reports
  `default rule (router: no row matched)`. It ships with 2 rows that passed the design and
  held-out link-cut gate: `sparse_prior_binds`, `midpoint_binds_wide`. Where a row fires the
  clusters change; on the link-cut matrix dblp_acm gains +0.0918 F1, musicbrainz_20k +0.2673,
  febrl3 +0.0038 and febrl4 +0.0002.
```

- [ ] **Step 6: Regenerate the derived docs, lint, commit**

```bash
PYTHONPATH="D:/Temp/gm-p5;$PP" PYTHONIOENCODING=utf-8 $PY scripts/regen_docs.py
$PY -m ruff check packages/python/goldenmatch/goldenmatch/core/probabilistic.py packages/python/goldenmatch/tests/test_fs_cut_router.py packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py
git status --short
```

`git add` exactly the files this task changed, plus any derived docs `regen_docs.py` rewrote because of the env default. Check each derived file with `git diff --text -U0 -- <file>`: its diff must only concern `GOLDENMATCH_FS_CUT_ROUTER`. Report and do not commit any other derived drift.

```bash
git commit -m "feat(fs): route the link cut by default; GOLDENMATCH_FS_CUT_ROUTER=off is the kill switch"
PYTHONPATH="D:/Temp/gm-p5;$PP" PYTHONIOENCODING=utf-8 $PY scripts/regen_docs.py --check; echo "exit=$?"; git status --short
```

Expected: `exit=0` and a clean tree.

---

### Task 5: Push the flip, open the draft PR, and triage CI

Steps 1–3 and 5–7 are **controller** steps. Step 4 is implementer work, dispatched only if CI finds routing-sensitive tests.

**Files:**
- Create, outside the repo: `D:/Temp/claude/pr-fs-cut-router-default-on-body.md`.
- Modify: only the goldenmatch test files CI reports as failing, per the triage table.

**Interfaces:**
- Consumes: the flip head from Task 4.
- Produces:
  - draft PR `<P5 PR>`;
  - green `ci` (including `quality_gate`), `fs-cut-rule-matrix-unit`, `fs-cut-rule-matrix` (shipped gate PASS) and the push-triggered `bench-suggest-quality` run at the P5 head;
  - a ledger line per triaged test.

- [ ] **Step 1 (controller): Write the PR body**

Write `D:/Temp/claude/pr-fs-cut-router-default-on-body.md`:

```markdown
Stacked on #2945 (P4). This PR is **P5** of the FS link-cut routing design (`docs/superpowers/specs/2026-09-11-fs-cut-rule-routing-design.md`): the link-cut router becomes the default. `GOLDENMATCH_FS_CUT_ROUTER=off` is the kill switch and restores the unrouted cut exactly.

## What changes

- **Default on.** `GOLDENMATCH_FS_CUT_ROUTER` is on unless it is set to `off`/`0`/`false`. An unrecognised value warns and keeps it on.
- **Cheap routing.** The router reads the cut diagnostics without the training-histogram pass, so a per-block link cut costs about 9 µs instead of 47 µs with the router on. The gate replays the same inputs.
- **Declined routes are visible.** A router that matched no row reports `default rule (router: no row matched)`.
- **Exact replay.** Link-cut sweep scorecards keep full float precision, so the gate replays exactly what the router read.
- **The matrix re-gates `probabilistic.py` changes.** With routing on by default, any change to that file can move a routed cut.

## What moves

The two shipped rows fire where the P4 gate measured them: dblp_acm +0.0918, musicbrainz_20k +0.2673, febrl3 +0.0038 and febrl4 +0.0002 F1. Every other design dataset is unchanged, and the held-out set passed (fired on 3 of 14).

## Gate for the flip

Pending: `quality_gate`, the link-cut matrix shipped gate, `bench-suggest-quality` (fast and gym), and `bench-quality-scale` (CI tier compared router on against router off; heavy tier on this branch).
```

- [ ] **Step 2 (controller): Push and open the draft PR**

```bash
export GH_TOKEN=$(gh auth token -u benzsevern)
cd D:/Temp/gm-p5
PYTHONPATH="D:/Temp/gm-p5;$PP" PYTHONIOENCODING=utf-8 $PY scripts/regen_docs.py --check && test -z "$(git status --short)" \
  && SKIP_DOCS_CHECK=1 git push -u origin feat/fs-cut-router-default-on
gh pr create --repo benseverndev-oss/goldenmatch --draft \
  --base feat/fs-cut-rule-rows --head feat/fs-cut-router-default-on \
  --title "feat(fs): link-cut routing P5 -- the router on by default, off is the kill switch" \
  --body-file D:/Temp/claude/pr-fs-cut-router-default-on-body.md
```

Never arm the PR, and never pass `--auto`.

- [ ] **Step 3 (controller): Wait for the PR runs and collect failures**

Use a persistent Monitor that finds the `pull_request` runs of `ci`, `fs-cut-rule-matrix-unit` and `fs-cut-rule-matrix` for the pushed head SHA, plus the `push` run of `bench-suggest-quality`. It polls every 240 s and reports each conclusion together with its non-success job names.

For each failed goldenmatch pytest lane, list the failing test ids:

```bash
gh run view <ci run id> --repo benseverndev-oss/goldenmatch --log-failed | rg -o "FAILED [^ ]+" | sort -u
```

Write them to the ledger. If a lane failed for infrastructure reasons (a cancelled `uv sync`, an artifact 429), re-run only that job: `gh run rerun <id> --job <job id>`.

- [ ] **Step 4 (implementer): Triage each routing-sensitive failing test**

For every failing test id, run it locally twice from `D:/Temp/gm-p5`:

```bash
PYTHONPATH="D:/Temp/gm-p5;$PP" PYTHONIOENCODING=utf-8 GOLDENMATCH_FS_CUT_ROUTER=off $PY -m pytest -q -p no:cacheprovider "<test id>"
PYTHONPATH="D:/Temp/gm-p5;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider "<test id>"
```

Apply exactly one row of this table:

| Evidence | Class | Action |
|---|---|---|
| Fails with the router `off` too | not caused by routing | Change nothing. Report it as pre-existing, with the output. |
| Passes `off`, fails on. Its subject is not the link cut (guard, shedding, refit, blocking, parity between two routes, reporting of other fields). | routing-independent test whose fixture now routes | Pin the P4 cut in that test (spec Testing, "Pinning"). Add `monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "off")` as its first line, with the comment `# routing-independent: pin the unrouted link cut (spec Testing: pinning)`. For a module- or class-scoped fixture, wrap its body in `with pytest.MonkeyPatch.context() as mp: mp.setenv("GOLDENMATCH_FS_CUT_ROUTER", "off")`. |
| Passes `off`, fails on. It asserts a cutoff, partition, pair set or F1 that the router moved. The run's `stats["fs_link_thresholds"][<mk>]["cut_reason"]` starts with `routed by `, and the router-on value is at least as good (F1 not lower). | intended routed change | Update the expectation to the router-on value, with the comment `# routed by <row name> (P5 default)`. |
| Passes `off`, fails on, and the router-on result is worse on a quality measure (lower F1, lost true pairs) | routing regression | STOP. Change nothing. Report BLOCKED with the test id, both outputs and the `cut_reason`. The flip is blocked; rows are never tuned. |

To read `cut_reason` for the "intended routed change" row, temporarily print `result.stats["fs_link_thresholds"]` inside the test, and remove that print before committing.

Commit all triage edits in one commit: `test(fs): pin routing-independent tests to the unrouted cut; accept routed expectations`. Its body lists each test id with its class. Do not push; the controller pushes.

- [ ] **Step 5 (controller): Re-push and confirm**

1. If Step 4 made a commit, run the docs check, push, and repeat Step 3 on the new head.
2. The task is complete when, at one head:
   - `ci` succeeds, and its `quality_gate` job is `success`;
   - `fs-cut-rule-matrix-unit` succeeds;
   - the push-triggered `bench-suggest-quality` run succeeds;
   - `fs-cut-rule-matrix` succeeds.
3. For the matrix, download only its `cut-rule-matrix` artifact. `cut-rule-gate-shipped.md` must show `shipped table (sparse_prior_binds, midpoint_binds_wide)` with `Design: PASS` and `Held-out: PASS`, and `cut-rule-matrix.md` must contain no `**Failures**` section.
4. If `quality_gate` fails:
   - Read its log with `gh api repos/benseverndev-oss/goldenmatch/actions/jobs/<job id>/logs`.
   - A drop on a dataset where a row routed blocks the flip. Revert the flip commit and record the drop.
   - Never re-bless a drop.

---

### Task 6: `bench-suggest-quality` and `bench-quality-scale` with the router on

All steps are **controller** steps. Nothing here edits code, unless a gym re-bless is ruled in Step 4.

**Interfaces:**
- Consumes: the green P5 head from Task 5.
- Produces:
  - a green dispatched `bench-suggest-quality` run (fast gate and gym gate) on `feat/fs-cut-router-default-on`;
  - QIS CI-tier runs on both refs with the router-on vs router-off comparison;
  - a QIS heavy-tier PASS on the flip branch;
  - the verdicts, recorded in the ledger and the PR body.

- [ ] **Step 1: Dispatch the runs**

```bash
export GH_TOKEN=$(gh auth token -u benzsevern)
R=benseverndev-oss/goldenmatch
gh workflow run bench-suggest-quality.yml --repo $R --ref feat/fs-cut-router-default-on -f mode=gate
gh workflow run bench-quality-scale.yml --repo $R --ref feat/fs-cut-router-default-on -f tier=both -f mode=check
gh workflow run bench-quality-scale.yml --repo $R --ref feat/fs-cut-rule-rows -f tier=ci -f mode=check
```

Notes on these runs:
- **What the suggestion dispatch runs:** the fast gate, then the gym gate (a dispatch runs both).
- **Runner cost:** the QIS tiers run on the paid `large-new-64GB` runner, and the heavy tier can take up to 4 hours. That is why the router-off reference is the CI tier only.
- **Queueing:** runs with the same tier queue behind each other; the concurrency group is per tier with no cancellation.

- [ ] **Step 2: Wait**

Use a persistent Monitor. It polls every 240 s the three `workflow_dispatch` runs created after Step 1 (`gh run list --workflow <file> --event workflow_dispatch --branch <ref>`), and reports each conclusion together with its non-success job names.

- [ ] **Step 3: Compare the QIS CI tier, router on vs router off**

```bash
D=D:/Temp/claude/D--show-case-goldenmatch/f06cca1e-8ed2-49d0-80cd-d9158c1968c4/scratchpad/p5-qis
gh run download <on-branch QIS run id> --repo $R -n qis-quality-ci -D "$D/on"
gh run download <off-branch QIS run id> --repo $R -n qis-quality-ci -D "$D/off"
cd D:/Temp/gm-p5
PYTHONPATH="D:/Temp/gm-p5;$PP" PYTHONIOENCODING=utf-8 $PY - "$D/on/qis_ci.json" "$D/off/qis_ci.json" <<'EOF'
"""QIS rung records, router on vs router off: gate-metric F1 on must be >= off - 0.01."""
import importlib.util, json, pathlib, sys
spec = importlib.util.spec_from_file_location("qis_gate", pathlib.Path("scripts/qis_gate.py"))
qg = importlib.util.module_from_spec(spec); spec.loader.exec_module(qg)
on, off = (json.loads(pathlib.Path(p).read_text(encoding="utf-8")) for p in sys.argv[1:3])
drops = 0
for n in sorted(set(on) | set(off), key=int):
    f_on = ((on.get(n) or {}).get(qg.METRIC) or {}).get("f1")
    f_off = ((off.get(n) or {}).get(qg.METRIC) or {}).get("f1")
    verdict = "n/a" if f_on is None or f_off is None else ("OK" if f_on >= f_off - 0.01 else "DROP")
    drops += verdict == "DROP"
    print(f"n={n:>9} {qg.METRIC} on={f_on} off={f_off} {verdict}")
sys.exit(1 if drops else 0)
EOF
echo "compare exit=$?"
```

Pass conditions:
- both CI-tier jobs concluded `success`;
- the compare exits 0;
- the heavy-tier job on the flip branch concluded `success`.

A `DROP` blocks the flip. Revert the flip commit, push, and record the rung, both F1 values and the metric.

- [ ] **Step 4: Judge the suggestion gym**

- **Run succeeded:** record it and continue.
- **Gym gate failed:** read its log (`gh run view <id> --log-failed`). If every failing dataset shows only an F1 increase, the flip is an intended improvement. Record the ruling `Ruling: gym re-bless — <datasets and deltas> — <cost if wrong>`, then run `gh workflow run bench-suggest-quality.yml --repo $R --ref feat/fs-cut-router-default-on -f mode=gym-bless`. That run commits the gym baseline to the branch with its own commit. Afterwards, push an empty commit with a clean message (`git commit --allow-empty -m "ci: re-run after the gym re-bless"`) so the PR's CI runs again, and repeat Task 5 Step 5.
- **Any gym F1 drop:** it blocks the flip. Revert the flip commit and record the drop.

- [ ] **Step 5: Record and hand over**

1. **PR body.** In `D:/Temp/claude/pr-fs-cut-router-default-on-body.md`, replace the `Pending: ...` line under "Gate for the flip" with one line per gate, each giving its run link and verdict: quality_gate, shipped matrix gate, suggestion fast gate, gym gate, QIS CI tier on vs off (the per-rung table), and QIS heavy tier. Apply it with `gh pr edit <P5 PR> --repo $R --body-file D:/Temp/claude/pr-fs-cut-router-default-on-body.md`. The PR stays a draft and unarmed.
2. **Memory.** Update `C:\Users\bsevern\.claude\projects\D--show-case-goldenmatch\memory\project_fs_method_routing.md` with the P5 PR number, run ids and verdicts, and any triage or re-bless rulings.
