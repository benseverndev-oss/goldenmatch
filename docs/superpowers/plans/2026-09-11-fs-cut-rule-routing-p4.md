# FS Link-Cut Routing, Phase P4: One Resolver, a Gated Router, and Its First Rows — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an opt-in, per-matchkey link-cut router whose rows reached the shipped table only by passing a blind design-and-held-out gate. Get there in four steps:
- collapse the four copies of the link-cut precedence into one resolver;
- score the Magellan held-out sets on labelled pairs only;
- add a routed arm to the matrix;
- gate the two candidate rows, A′ and B.

**Architecture:**
- **One resolver.** `resolve_link_cut(mk, em_result, calibrated) -> LinkCut` in `core/probabilistic.py` becomes the only copy of the precedence: `link_threshold` > calibrated > rule step > fallback. The four existing callers read it.
- **Router.** The rule step gains a router between the pin and the default. `fs_cut_rules.choose_cut_rule(diagnostics)` walks an ordered `ROWS` table of `CutRow`s. It sits behind `GOLDENMATCH_FS_CUT_ROUTER`, default off, and ships with no rows.
- **Sweep changes.** The per-rule sweep (`scripts/autoconfig_quality/rule_sweep.py`) gains two things:
  - a labelled-pairs metric for the DeepMatcher/Magellan datasets;
  - a `routed` arm that runs with the router on, whose partition must equal the arm pinning the rule it routed to.
- **Gate.** A new `scripts/autoconfig_quality/cut_gate.py` replays a routing table over each dataset's recorded `cut_diagnostics` and compares the routed rule's arm with the baseline. It prints full design verdicts and only PASS/FAIL plus a fired count for the held-out set.
- **Candidates and shipping.** Candidate rows live in `scripts/autoconfig_quality/cut_rows.py`. A row that passes both sets moves verbatim into `fs_cut_rules.ROWS`; a second matrix run then confirms the routed arms agree.

**Tech Stack:** Python 3.12, pyarrow, polars (the existing harness frames), pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-11-fs-cut-rule-routing-design.md` on branch `docs/fs-cut-rule-routing-spec`. The relevant sections are "Resolution and precedence", "Router", "Writing table rows", "Gate", "Error handling" and "Rollout" P4. The P4 rulings are amended into the spec in the same push as this plan. The plan builds on P3, draft PR #2943 (branch `feat/fs-cut-rule-matrix`, head `3a93b7a2a`).

## Global Constraints

**Behaviour**
- **Router default off.** `GOLDENMATCH_FS_CUT_ROUTER` unset (or `off`/`0`/`false`) means the router never runs. With it off, every cutoff, source, `cut_rule` and `cut_reason` is byte-identical to today, and the tests pin this.
- **Precedence (spec):**
  1. explicit `mk.link_threshold`;
  2. the EM-calibrated cutoff;
  3. a pinned `mk.link_cut_rule`;
  4. the router's choice, when the router is on;
  5. the default rule (`GOLDENMATCH_FS_LINEAR_CUT`, default `prior_mid`).

  Posterior scoring (`GOLDENMATCH_FS_CALIBRATED=posterior` / `GOLDENMATCH_FS_EVIDENCE_CUT`) bypasses rules and the router entirely.
- **Error handling (spec):**
  - no diagnostics, or λ outside (0, 1), routes nothing and gives the default;
  - an unknown router env value warns and leaves the router off;
  - a routed rule the model cannot supply falls through to the default with a note in the reason.
- **Reporting.** A routed cut reports source `evidence_rule` and `cut_reason` `routed by <row name>: <row reason>`.

**The gate and blindness (from the spec)**
- **Gate.** A table's routed rule must score at least `default − 0.01` (the harness `TOLERANCE`) on **every** design dataset and **every** held-out dataset.
  - A row that fails either set is **dropped, never tuned**.
  - A dataset no row fires on keeps the default and passes.
  - A dataset whose matchkeys route to different rules, or whose routed or baseline arm crashed or was voided, **fails**.
- **Metric.** `walmart_amazon`, `itunes_amazon` and `fodors_zagats` gate on F1 over labelled candidate pairs only (`gate_metric: "labelled"`). Every other dataset gates on F1.
- **Candidate rows are fixed verbatim before any held-out verdict exists:**
  - **A′ `sparse_prior_binds`:** `proportion_matched < 0.004 and prior_bits > midpoint_bits and n_fields <= 3` → `posterior_099`.
  - **B `midpoint_binds_wide`:** `proportion_matched < 0.1 and midpoint_bits > prior_bits and n_fields >= 5` → `evidence_5`.

  No threshold, comparison or rule in either row may change after the gate has been run.
- **Blind held-out.** Nobody writing or shipping a row reads held-out results.
  - Implementers never download matrix artifacts.
  - The controller downloads only the `cut-rule-matrix` artifact (the design matrix plus the gate markdown), never `cut-rule-matrix-holdout`, and never opens, `jq`s or prints held-out per-dataset content.
  - The held-out verdict the gate prints is `PASS`/`FAIL` plus "fired on N of M" and set-level counts, nothing per dataset.
- **Rulings recorded in the spec amendment:**
  - `choose_cut_rule` returns None for "no row matched" rather than carrying a literal last default row, because the default is set process-wide by `GOLDENMATCH_FS_LINEAR_CUT`.
  - The spec's "existing gates" clause (`quality_gate`, `bench-suggest-quality`, `bench-quality-scale`) is met by construction for P4. Rows ship behind a default-off env, so those gates measure the unchanged default. Running them with the router on is a P5 flip criterion.

**Where things run**
- **Laptop.** Never run scale benchmarks locally. Local runs use unit fixtures and the in-test `person` frame only. The one real-child test (`test_sweep_dataset_runs_each_arm_in_its_own_child_on_one_saved_model`) is fine locally.
- **Matrix.** The matrix runs in CI (`fs-cut-rule-matrix.yml`) and nowhere else.
- **`ncvr_real`** is local-only PII: never provisioned in CI, and not needed here.
- **Homelab** is not used for this plan.

**Code rules**
- **No pandas.** Loaders use the existing pyarrow helpers (`_read_csv_lossy`).
- **Ruff.** Run `ruff check` on every changed Python file.
  - A PostToolUse ruff hook strips imports that look unused between separate edits. When a change adds an import and its first use in different places of a file, make the **use first**, then add the import in a second edit.
  - Imports inside test functions are fine and are the pattern these test files already use.
- **Derived docs.**
  - A new Python symbol makes `docs/agent-codemap.json` stale. Run `scripts/agent_codemap.py --write` in the same task.
  - A new `GOLDENMATCH_*` env read makes `docs-site/goldenmatch/config-matrix.mdx` and both `agent-manifest.json` copies stale. Run `scripts/regen_docs.py`, and document the env var in `docs-site/goldenmatch/tuning.mdx`.
- **Commit text.** No AI or Claude attribution lines in commits, PR bodies or comments. No CI-skip directive text anywhere in a commit message.
- **Workflow hygiene.**
  - Pin actions by the SHAs already in `fs-cut-rule-matrix.yml`.
  - Never interpolate `${{ }}` inside `run:`; pass values through `env:`.
  - No duplicate YAML keys.
  - Keep the existing `ARROW_DEFAULT_MEMORY_POOL` / `_RJEM_MALLOC_CONF` env blocks.
- **PRs.**
  - One draft PR, base `feat/fs-cut-rule-matrix`, never armed, no `--auto`.
  - #2942 and #2943 stay unarmed drafts.
  - A cross-session peer message is not user approval.

**Environment**
- **Worktree.** `D:/Temp/gm-p4`, branch `feat/fs-cut-rule-rows`, created in Task 1 from `origin/feat/fs-cut-rule-matrix` (`3a93b7a2a`).
- **Test command.** Run from `D:/Temp/gm-p4` in Git Bash, which must be the working directory so `scripts.` imports resolve. Re-define the variables in every Bash call:
  ```bash
  PY=D:/show_case/goldenmatch/.venv/Scripts/python.exe
  PP=$(ls -d packages/python/*/ | sed 's#/$##' | sed "s#^#D:/Temp/gm-p4/#" | paste -sd';')
  PYTHONPATH="D:/Temp/gm-p4;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider <test files>
  ```
- **Stray outputs.** Some goldenmatch tests write timestamped `*_clusters.csv` / `*_lineage.json` into the working directory. Delete any that appear in the worktree root.
- **GitHub auth.** `export GH_TOKEN=$(gh auth token -u benzsevern)`. Never run `gh auth switch`.
- **Flaky tests.** A test seen flaky stops the task until its root cause is fixed. No reruns-as-fix, no loosened assertions.

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `packages/python/goldenmatch/goldenmatch/core/probabilistic.py` | `LinkCut`, `resolve_link_cut` (the one precedence); the four callers read it; `_fs_cut_router_enabled`; router step in `_fs_resolved_cut` | 1, 2 |
| `packages/python/goldenmatch/goldenmatch/core/fs_cut_rules.py` | `CutRow`, `ROWS` (shipped table), `choose_cut_rule` | 2, 7 |
| `packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py` | resolver expectations at every precedence step | 1, 2 |
| `packages/python/goldenmatch/tests/test_fs_cut_router.py` (new) | router rows, env, precedence with the router; shipped-row known positives | 2, 7 |
| `packages/python/goldenmatch/scripts/emit_v2_parity_fixtures.py` | pins the router off for TS parity fixtures | 2 |
| `scripts/bench_er_headtohead/datasets.py` | `MAGELLAN_SUBDIRS`, `magellan_labels` | 3 |
| `scripts/autoconfig_quality/datasets.py` | `labelled_pairs(name)` in row-index space | 3 |
| `scripts/autoconfig_quality/rule_sweep.py` | `labelled_summary`, `gate_metric_of`, `metric_value`; `ROUTED_ARM`, `pinned_rule`, `arm_env(arm)`, `routed_check` | 3, 4 |
| `scripts/autoconfig_quality/cut_rows.py` (new) | candidate rows A′ and B, unshipped | 5, 7 |
| `scripts/autoconfig_quality/cut_gate.py` (new) | replay a table over the matrix; blind verdicts; CLI | 5 |
| `scripts/autoconfig_quality/tests/test_rule_sweep.py` | sweep tests for the metric and the routed arm | 3, 4 |
| `scripts/autoconfig_quality/tests/test_cut_gate.py` (new) | gate tests | 5, 7 |
| `scripts/bench_er_headtohead/test_heldout_loaders.py` | Magellan label tests | 3 |
| `.github/workflows/fs-cut-rule-matrix.yml`, `fs-cut-rule-matrix-unit.yml` | gate steps, triggers, unit test list | 6 |
| `docs-site/goldenmatch/tuning.mdx`, `packages/python/goldenmatch/CHANGELOG.md` | router docs | 2, 7 |
| `docs-site/goldenmatch/config-matrix.mdx`, `docs/agent-manifest.json`, `packages/python/goldensuite-mcp/goldensuite_mcp/agent-manifest.json`, `docs/agent-codemap.json` | regenerated | 1, 2 |

---

### Task 1: Worktree and one link-cut resolver

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/probabilistic.py`:
  - `resolve_thresholds` (line ~3764);
  - `link_threshold_source` (~3803);
  - `link_cut_report` (~3845);
  - `_fs_link_threshold` (~4853);
  - a new `LinkCut` / `resolve_link_cut` placed just above `_fs_link_threshold`.
- Test: `packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py` (append).
- Regenerate: `docs/agent-codemap.json`.

**Interfaces:**
- Consumes: `_fs_resolved_cut(mk, em_result, calibrated) -> ResolvedCut | None`, `compute_thresholds(em_result, calibrated=...) -> (link, review)`, `_fs_unresolved_cut_reason(mk, em_result) -> str | None`, `LINK_THRESHOLD_CONFIGURED/CALIBRATED/EVIDENCE_RULE/FALLBACK`, all existing.
- Produces:
  - `@dataclass(frozen=True) class LinkCut(link: float, source: str, rule: str | None, reason: str | None)`.
  - `resolve_link_cut(mk: MatchkeyConfig, em_result: EMResult, calibrated: bool) -> LinkCut`, which Task 2's router extends only through `_fs_resolved_cut`.

- [ ] **Step 1: Create the worktree**

```bash
export GH_TOKEN=$(gh auth token -u benzsevern)
git -C D:/show_case/goldenmatch fetch origin feat/fs-cut-rule-matrix
git -C D:/show_case/goldenmatch worktree add -b feat/fs-cut-rule-rows D:/Temp/gm-p4 origin/feat/fs-cut-rule-matrix
git -C D:/Temp/gm-p4 log --oneline -1
```

Expected: the last line starts `3a93b7a2a`.

- [ ] **Step 2: Write the failing tests**

Append to `packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py`. `_mk`, `_em`, `_norm` and `_clear_cut_env` already exist in the file.

```python
# ─── one resolver (P4) ────────────────────────────────────────────────────────


def test_the_four_callers_have_no_precedence_of_their_own(monkeypatch):
    """P4: `_fs_link_threshold`, `resolve_thresholds`, `link_threshold_source` and
    `link_cut_report` all read `resolve_link_cut`. Replacing it must move every one of them;
    a caller that kept its own copy of the precedence would ignore the stub."""
    import goldenmatch.core.probabilistic as P

    _clear_cut_env(monkeypatch)
    stub = P.LinkCut(
        link=0.123, source=P.LINK_THRESHOLD_CONFIGURED, rule="evidence_5", reason="stub"
    )
    monkeypatch.setattr(P, "resolve_link_cut", lambda mk, em_result, calibrated: stub)
    mk, em = _mk(), _em()
    assert P._fs_link_threshold(mk, em, calibrated=False) == 0.123
    assert P.resolve_thresholds(mk, em)[0] == 0.123
    assert P.link_threshold_source(mk, em) == P.LINK_THRESHOLD_CONFIGURED
    report = P.link_cut_report(mk, em)
    assert (report["cut_rule"], report["cut_reason"]) == ("evidence_5", "stub")


#: case -> (link, or None to skip the check; source; cut_rule; cut_reason). Pinned from the
#: pre-P4 behaviour, so the consolidation cannot move a cutoff or a report.
_EXPECTED_CUT = {
    "configured": (0.42, LINK_THRESHOLD_CONFIGURED, None, None),
    "configured_pinned": (
        0.42, LINK_THRESHOLD_CONFIGURED, None,
        "link_cut_rule=evidence_9 not applied: link_threshold is set",
    ),
    "calibrated": (0.61, LINK_THRESHOLD_CALIBRATED, None, None),
    "calibrated_pinned": (
        0.61, LINK_THRESHOLD_CALIBRATED, None,
        "link_cut_rule=evidence_9 not applied: calibrated cutoff decided first",
    ),
    "pinned": (_norm(9.0), LINK_THRESHOLD_EVIDENCE_RULE, "evidence_9", "pinned by link_cut_rule"),
    "pinned_unavailable": (
        _norm(math.log2(99.0)), LINK_THRESHOLD_EVIDENCE_RULE, "prior_mid",
        "otsu unavailable: needs a training histogram of more than 50 pairs; default rule",
    ),
    "default": (_norm(math.log2(99.0)), LINK_THRESHOLD_EVIDENCE_RULE, "prior_mid", "default rule"),
    "fallback": (0.50, LINK_THRESHOLD_FALLBACK, None, None),
    "posterior": (None, LINK_THRESHOLD_FALLBACK, None, None),
    "posterior_pinned": (
        None, LINK_THRESHOLD_FALLBACK, None,
        "link_cut_rule=evidence_9 not applied: posterior scoring",
    ),
}


def _resolver_case(monkeypatch, case: str):
    _clear_cut_env(monkeypatch)
    if case == "configured":
        return _mk(link_threshold=0.42), _em()
    if case == "configured_pinned":
        return _mk(link_cut_rule="evidence_9", link_threshold=0.42), _em()
    if case == "calibrated":
        return _mk(), _em(calibrated=0.61)
    if case == "calibrated_pinned":
        return _mk(link_cut_rule="evidence_9"), _em(calibrated=0.61)
    if case == "pinned":
        return _mk(link_cut_rule="evidence_9"), _em()
    if case == "pinned_unavailable":
        return _mk(link_cut_rule="otsu"), _em()  # no training histogram -> the default rule
    if case == "default":
        return _mk(), _em()
    if case == "fallback":
        monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "off")
        return _mk(), _em()
    monkeypatch.setenv("GOLDENMATCH_FS_CALIBRATED", "posterior")
    return (_mk(link_cut_rule="evidence_9") if case == "posterior_pinned" else _mk()), _em()


@pytest.mark.parametrize("case", sorted(_EXPECTED_CUT))
def test_resolve_link_cut_is_what_every_caller_applies_and_reports(monkeypatch, case):
    from goldenmatch.core.probabilistic import link_cut_report, resolve_link_cut

    mk, em = _resolver_case(monkeypatch, case)
    link, source, rule, reason = _EXPECTED_CUT[case]
    posterior = _fs_calibration_mode() == "posterior"
    cut = resolve_link_cut(mk, em, calibrated=posterior)

    assert (cut.source, cut.rule, cut.reason) == (source, rule, reason)
    if link is not None:
        assert math.isclose(cut.link, link, rel_tol=1e-12)
    assert _fs_link_threshold(mk, em, calibrated=posterior) == cut.link
    assert resolve_thresholds(mk, em)[0] == float(cut.link)
    assert link_threshold_source(mk, em) == source
    report = link_cut_report(mk, em)
    assert (report["cut_rule"], report["cut_reason"]) == (rule, reason)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `PYTHONPATH="D:/Temp/gm-p4;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py -k "precedence_of_their_own or every_caller"`

Expected: FAIL. The first test raises `AttributeError: module 'goldenmatch.core.probabilistic' has no attribute 'LinkCut'`; the parametrized cases raise `ImportError: cannot import name 'resolve_link_cut'`.

- [ ] **Step 4: Add `LinkCut` and `resolve_link_cut`**

In `probabilistic.py`, insert this block directly above `def _fs_link_threshold(`:

```python
@dataclass(frozen=True)
class LinkCut:
    """The FS link cutoff and where it came from: the one resolution every caller reads."""

    #: The cutoff applied. ``mk.link_threshold`` and the calibrated cutoff pass through as
    #: given; callers that need a float convert, as they always have.
    link: float
    #: ``LINK_THRESHOLD_CONFIGURED`` / ``CALIBRATED`` / ``EVIDENCE_RULE`` / ``FALLBACK``.
    source: str
    #: The rule that placed the cut; None when an earlier step or the fallback decided.
    rule: str | None
    #: Why the rule step did or did not decide; None when no rule was asked for.
    reason: str | None


def resolve_link_cut(
    mk: MatchkeyConfig, em_result: EMResult, calibrated: bool
) -> LinkCut:
    """The FS link cutoff: the single copy of the precedence (spec 2026-09-11 "Resolution and
    precedence").

    1. explicit ``mk.link_threshold`` -> ``configured``;
    2. the EM-calibrated cutoff -> ``calibrated``;
    3. the rule step, :func:`_fs_resolved_cut` -> ``evidence_rule``. Posterior scoring
       (``calibrated``) places no rule;
    4. ``compute_thresholds`` -> ``fallback``.

    A pinned rule an earlier step overrode leaves ``rule`` None and says which step in
    ``reason``, so a leaked env var forcing posterior mode does not silently disable a pin.
    ``_fs_link_threshold``, ``resolve_thresholds``, ``link_threshold_source`` and
    ``link_cut_report`` all read this; none keeps a precedence of its own.
    """
    pinned = getattr(mk, "link_cut_rule", None)
    if mk.link_threshold is not None:
        reason = f"link_cut_rule={pinned} not applied: link_threshold is set" if pinned else None
        return LinkCut(mk.link_threshold, LINK_THRESHOLD_CONFIGURED, None, reason)
    calibrated_cut = getattr(em_result, "calibrated_link_threshold", None)
    if calibrated_cut is not None:
        reason = (
            f"link_cut_rule={pinned} not applied: calibrated cutoff decided first"
            if pinned
            else None
        )
        return LinkCut(calibrated_cut, LINK_THRESHOLD_CALIBRATED, None, reason)
    resolved = _fs_resolved_cut(mk, em_result, calibrated)
    if resolved is not None:
        return LinkCut(
            resolved.normalized, LINK_THRESHOLD_EVIDENCE_RULE, resolved.rule, resolved.reason
        )
    link, _review = compute_thresholds(em_result, calibrated=calibrated)
    if calibrated:
        reason = f"link_cut_rule={pinned} not applied: posterior scoring" if pinned else None
    else:
        reason = _fs_unresolved_cut_reason(mk, em_result)
    return LinkCut(link, LINK_THRESHOLD_FALLBACK, None, reason)
```

- [ ] **Step 5: Point the four callers at it**

Replace the whole of `_fs_link_threshold` with:

```python
def _fs_link_threshold(
    mk: MatchkeyConfig, em_result: EMResult, calibrated: bool
) -> float:
    """The FS link cutoff the scalar / vectorized / batched scorers apply:
    :func:`resolve_link_cut`'s ``link``. #1804 item 4 extracted it so the scorers cannot drift;
    P4 made the resolver the one copy of the precedence."""
    return resolve_link_cut(mk, em_result, calibrated).link
```

Replace the whole of `resolve_thresholds` with:

```python
def resolve_thresholds(
    mk: MatchkeyConfig, em_result: EMResult
) -> tuple[float, float]:
    """Resolve configured or calibrated ``(link, review)`` score cutoffs.

    The link cutoff is :func:`resolve_link_cut`'s. The review cutoff is clamped to the link
    cutoff so an explicit low link threshold cannot accidentally turn linked pairs into
    review candidates.
    """
    _computed_link, computed_review = compute_thresholds(em_result)
    link = float(
        resolve_link_cut(mk, em_result, _fs_calibration_mode() == "posterior").link
    )
    review = (
        float(mk.review_threshold)
        if mk.review_threshold is not None
        else computed_review
    )
    return link, min(review, link)
```

Replace the whole of `link_threshold_source` with:

```python
def link_threshold_source(mk: MatchkeyConfig, em_result: EMResult) -> str:
    """Which precedence step supplied the link cutoff (#2483): :func:`resolve_link_cut`'s
    ``source``, so a reporting layer cannot disagree with the resolver about where the number
    came from.

    ``"fallback"`` means nothing about THIS dataset chose the cut. That is the case worth
    surfacing: the reporter in #2483 had a 94.4% match rate from the fixed 0.50 default, with
    nothing in the config or the result saying a decision had never been made.
    """
    return resolve_link_cut(mk, em_result, _fs_calibration_mode() == "posterior").source
```

Replace the whole of `link_cut_report` with:

```python
def link_cut_report(mk: MatchkeyConfig, em_result: EMResult) -> dict:
    """``cut_rule`` / ``cut_reason`` / ``cut_diagnostics`` for the per-matchkey cutoff report.

    ``cut_rule`` and ``cut_reason`` are :func:`resolve_link_cut`'s ``rule`` and ``reason``:
    - both None when no rule was asked for;
    - ``cut_reason`` alone when a pinned rule was overridden by an earlier step (a configured
      ``link_threshold``, an EM-calibrated cutoff, or posterior scoring) or no rule could place
      the cut (:func:`_fs_unresolved_cut_reason`).

    ``cut_diagnostics`` is reported whenever the model has usable weights, so the measurement
    harness can read it even when no rule applied.
    """
    from dataclasses import asdict

    from goldenmatch.core.fs_cut_rules import cut_diagnostics

    diagnostics = cut_diagnostics(mk, em_result)
    cut = resolve_link_cut(mk, em_result, _fs_calibration_mode() == "posterior")
    return {
        "cut_rule": cut.rule,
        "cut_reason": cut.reason,
        "cut_diagnostics": asdict(diagnostics) if diagnostics is not None else None,
    }
```

- [ ] **Step 6: Run the new tests and every precedence test**

Run:
```bash
PYTHONPATH="D:/Temp/gm-p4;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider \
  packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py \
  packages/python/goldenmatch/tests/test_fs_linear_cut_rule.py \
  packages/python/goldenmatch/tests/test_fs_link_threshold_drift_2483.py \
  packages/python/goldenmatch/tests/test_fs_threshold_calibration.py \
  packages/python/goldenmatch/tests/test_link_threshold_report_2483.py \
  packages/python/goldenmatch/tests/test_fs_training_histogram.py \
  packages/python/goldenmatch/tests/test_fs_cut_rules.py \
  packages/python/goldenmatch/tests/test_spark_fs_unit.py
```

Expected: all pass (`test_spark_fs_unit.py` may skip without pyspark). A failing pre-existing test means the consolidation changed behaviour: fix the resolver, never the test.

- [ ] **Step 7: Regenerate the codemap, lint, commit**

```bash
PYTHONPATH="D:/Temp/gm-p4;$PP" $PY scripts/agent_codemap.py --write
$PY -c "import json;t=json.dumps(json.load(open('docs/agent-codemap.json')));print('resolve_link_cut' in t, 'LinkCut' in t)"
$PY -m ruff check packages/python/goldenmatch/goldenmatch/core/probabilistic.py packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py
git add packages/python/goldenmatch/goldenmatch/core/probabilistic.py packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py docs/agent-codemap.json
git commit -m "refactor(fs): one link-cut resolver behind the four precedence copies"
```

Expected: `True True` and ruff clean.

---

### Task 2: The router, behind `GOLDENMATCH_FS_CUT_ROUTER` (default off)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/fs_cut_rules.py` (append `CutRow`, `ROWS`, `choose_cut_rule`; add an import).
- Modify: `packages/python/goldenmatch/goldenmatch/core/probabilistic.py`:
  - add `_fs_cut_router_enabled` after `_fs_linear_cut_rule`;
  - replace `_fs_resolved_cut`.
- Modify: `packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py` (`_clear_cut_env`).
- Create: `packages/python/goldenmatch/tests/test_fs_cut_router.py`.
- Modify: `packages/python/goldenmatch/scripts/emit_v2_parity_fixtures.py`.
- Modify: `docs-site/goldenmatch/tuning.mdx` (a new row after the `GOLDENMATCH_FS_LINEAR_CUT` row) and `packages/python/goldenmatch/CHANGELOG.md`.
- Regenerate: `docs-site/goldenmatch/config-matrix.mdx`, `docs/agent-manifest.json`, `packages/python/goldensuite-mcp/goldensuite_mcp/agent-manifest.json`, `docs/agent-codemap.json`.

**Interfaces:**
- Consumes: `CutDiagnostics`, `CUT_RULES`, `cut_diagnostics(mk, em_result)` (existing); `resolve_link_cut` (Task 1).
- Produces:
  - `@dataclass(frozen=True) class CutRow(name: str, rule: str, reason: str, when: Callable[[CutDiagnostics], bool])`, which raises `ValueError` on an unknown rule.
  - `ROWS: tuple[CutRow, ...] = ()`.
  - `choose_cut_rule(diagnostics: CutDiagnostics | None, rows: Sequence[CutRow] | None = None) -> tuple[str, str] | None`: `(rule, "routed by <name>: <reason>")`. `rows=None` reads the module's `ROWS` at call time.
  - `_fs_cut_router_enabled() -> bool`.
  - Env var `GOLDENMATCH_FS_CUT_ROUTER`.

- [ ] **Step 1: Write the failing tests**

Create `packages/python/goldenmatch/tests/test_fs_cut_router.py`:

```python
"""FS link-cut router (spec 2026-09-11-fs-cut-rule-routing-design, P4)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core import fs_cut_rules as R
from goldenmatch.core.probabilistic import (
    LINK_THRESHOLD_EVIDENCE_RULE,
    _fs_resolved_cut,
    link_cut_report,
    link_threshold_source,
)

_ENV = (
    "GOLDENMATCH_FS_CUT_ROUTER",
    "GOLDENMATCH_FS_LINEAR_CUT",
    "GOLDENMATCH_FS_CALIBRATED",
    "GOLDENMATCH_FS_EVIDENCE_CUT",
    "GOLDENMATCH_FS_CALIBRATE_THRESHOLD",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in _ENV:
        monkeypatch.delenv(var, raising=False)


def _diag(**update) -> R.CutDiagnostics:
    fields = dict(
        proportion_matched=0.01,
        lo=-10.0,
        hi=14.0,
        midpoint_bits=2.0,
        prior_bits=6.63,
        n_fields=2,
        has_negative_evidence=False,
        field_weight_spans={"name": 16.0, "zip": 8.0},
        admitted_fraction=None,
    )
    fields.update(update)
    return R.CutDiagnostics(**fields)


def _mk(**update) -> MatchkeyConfig:
    mk = MatchkeyConfig(
        name="fs",
        type="probabilistic",
        fields=[
            MatchkeyField(field="name", scorer="jaro_winkler", levels=3, partial_threshold=0.8),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ],
    )
    return mk.model_copy(update=update) if update else mk


def _em(lam: float = 0.01):
    # lo = -10, hi = 14, midpoint = 2 bits.
    return SimpleNamespace(
        match_weights={"name": [-6.0, 1.0, 10.0], "zip": [-4.0, 4.0]},
        proportion_matched=lam,
        calibrated_link_threshold=None,
    )


_SPARSE = R.CutRow(
    name="sparse", rule="evidence_12", reason="lambda under 0.05",
    when=lambda d: d.proportion_matched < 0.05,
)
_WIDE = R.CutRow(
    name="wide", rule="evidence_3", reason="more than one field", when=lambda d: d.n_fields > 1
)


def test_first_matching_row_wins_and_names_itself_in_the_reason():
    assert R.choose_cut_rule(_diag(), rows=(_SPARSE, _WIDE)) == (
        "evidence_12", "routed by sparse: lambda under 0.05",
    )
    assert R.choose_cut_rule(_diag(), rows=(_WIDE, _SPARSE)) == (
        "evidence_3", "routed by wide: more than one field",
    )


def test_no_matching_row_means_the_default():
    assert R.choose_cut_rule(_diag(proportion_matched=0.2, n_fields=1), rows=(_SPARSE, _WIDE)) is None


def test_no_diagnostics_or_a_degenerate_match_rate_routes_nothing():
    always = R.CutRow(name="always", rule="evidence_9", reason="x", when=lambda d: True)
    assert R.choose_cut_rule(None, rows=(always,)) is None
    assert R.choose_cut_rule(_diag(proportion_matched=0.0), rows=(always,)) is None
    assert R.choose_cut_rule(_diag(proportion_matched=1.0), rows=(always,)) is None


def test_a_row_must_name_a_real_rule():
    with pytest.raises(ValueError, match="unknown link cut rule"):
        R.CutRow(name="x", rule="evidence_7", reason="x", when=lambda d: True)


def test_choose_cut_rule_reads_the_shipped_table_by_default(monkeypatch):
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    assert R.choose_cut_rule(_diag()) == ("evidence_12", "routed by sparse: lambda under 0.05")


def test_every_shipped_row_is_a_uniquely_named_cut_row():
    assert all(isinstance(row, R.CutRow) and row.rule in R.CUT_RULES for row in R.ROWS)
    assert len({row.name for row in R.ROWS}) == len(R.ROWS)


def test_the_router_is_off_by_default(monkeypatch):
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    resolved = _fs_resolved_cut(_mk(), _em(), calibrated=False)
    assert (resolved.rule, resolved.reason) == ("prior_mid", "default rule")


def test_the_router_on_places_the_routed_rule_and_reports_it(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "on")
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    resolved = _fs_resolved_cut(_mk(), _em(), calibrated=False)
    assert resolved.rule == "evidence_12"
    assert resolved.normalized == pytest.approx((12.0 + 10.0) / 24.0)
    assert link_threshold_source(_mk(), _em()) == LINK_THRESHOLD_EVIDENCE_RULE
    report = link_cut_report(_mk(), _em())
    assert (report["cut_rule"], report["cut_reason"]) == (
        "evidence_12", "routed by sparse: lambda under 0.05",
    )


def test_a_pinned_rule_beats_the_router(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "on")
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    resolved = _fs_resolved_cut(_mk(link_cut_rule="evidence_5"), _em(), calibrated=False)
    assert (resolved.rule, resolved.reason) == ("evidence_5", "pinned by link_cut_rule")


def test_no_matching_row_falls_through_to_the_default_rule(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "on")
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    resolved = _fs_resolved_cut(_mk(), _em(0.2), calibrated=False)
    assert (resolved.rule, resolved.reason) == ("prior_mid", "default rule")


def test_the_router_with_the_default_rule_off_places_only_routed_cuts(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "on")
    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "off")
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    assert _fs_resolved_cut(_mk(), _em(0.01), calibrated=False).rule == "evidence_12"
    assert _fs_resolved_cut(_mk(), _em(0.2), calibrated=False) is None


def test_posterior_scoring_bypasses_the_router(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "on")
    monkeypatch.setenv("GOLDENMATCH_FS_CALIBRATED", "posterior")
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    assert _fs_resolved_cut(_mk(), _em(), calibrated=True) is None
    assert link_cut_report(_mk(), _em())["cut_rule"] is None


def test_a_routed_rule_the_model_cannot_supply_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "on")
    otsu = R.CutRow(name="needs_hist", rule="otsu", reason="x", when=lambda d: True)
    monkeypatch.setattr(R, "ROWS", (otsu,))
    resolved = _fs_resolved_cut(_mk(), _em(), calibrated=False)
    assert resolved.rule == "prior_mid"
    assert resolved.reason == (
        "otsu unavailable: needs a training histogram of more than 50 pairs; default rule"
    )


def test_an_unknown_router_value_warns_and_stays_off(monkeypatch, caplog):
    monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "maybe")
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    with caplog.at_level(logging.WARNING):
        resolved = _fs_resolved_cut(_mk(), _em(), calibrated=False)
    assert resolved.rule == "prior_mid"
    assert "GOLDENMATCH_FS_CUT_ROUTER" in caplog.text
```

In `test_fs_cut_rule_resolution.py`, add the router var to `_clear_cut_env`'s tuple:

```python
def _clear_cut_env(monkeypatch):
    for var in (
        "GOLDENMATCH_FS_LINEAR_CUT", "GOLDENMATCH_FS_CALIBRATED",
        "GOLDENMATCH_FS_EVIDENCE_CUT", "GOLDENMATCH_FS_CALIBRATE_THRESHOLD",
        "GOLDENMATCH_FS_CUT_ROUTER",
    ):
        monkeypatch.delenv(var, raising=False)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH="D:/Temp/gm-p4;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider packages/python/goldenmatch/tests/test_fs_cut_router.py`

Expected: FAIL at collection or on the first test, with `AttributeError: module 'goldenmatch.core.fs_cut_rules' has no attribute 'CutRow'`.

- [ ] **Step 3: Add the table and `choose_cut_rule` to `fs_cut_rules.py`**

First append this block at the end of `fs_cut_rules.py`:

```python
@dataclass(frozen=True)
class CutRow:
    """One routing-table row: when ``when(diagnostics)`` holds, ``rule`` places the cut.

    ``when`` reads only :class:`CutDiagnostics` -- no labels, no scoring pass. ``reason`` is the
    human-readable mechanism, reported in ``cut_reason``.
    """

    name: str
    rule: str
    reason: str
    when: Callable[[CutDiagnostics], bool]

    def __post_init__(self) -> None:
        if self.rule not in CUT_RULES:
            raise ValueError(f"unknown link cut rule {self.rule!r}; expected one of {CUT_RULES}")


#: The shipped routing table, read by ``choose_cut_rule`` when ``GOLDENMATCH_FS_CUT_ROUTER`` is
#: on. The first match wins. No match means the default rule (``GOLDENMATCH_FS_LINEAR_CUT``,
#: default ``prior_mid``), which stands in for the spec's "last row is the shipped default".
#: A row lands here only after passing the design AND held-out gate
#: (``scripts/autoconfig_quality/cut_gate.py``); unshipped candidates live in
#: ``scripts/autoconfig_quality/cut_rows.py``.
ROWS: tuple[CutRow, ...] = ()


def choose_cut_rule(
    diagnostics: CutDiagnostics | None, rows: Sequence[CutRow] | None = None
) -> tuple[str, str] | None:
    """``(rule, reason)`` from the first row of ``rows`` (default: the shipped ``ROWS``) whose
    condition holds, or None for the default rule.

    Also None without diagnostics or with a degenerate match rate (λ outside (0, 1)): every
    failure degrades to the default."""
    table = ROWS if rows is None else rows
    if diagnostics is None or not 0.0 < diagnostics.proportion_matched < 1.0:
        return None
    for row in table:
        if row.when(diagnostics):
            return row.rule, f"routed by {row.name}: {row.reason}"
    return None
```

Then, as a second edit, add the import at the top of the file directly above `from dataclasses import dataclass`, which is where ruff's isort rule (`I`, selected in the root `pyproject.toml`) expects it:

```python
from collections.abc import Callable, Sequence
```

- [ ] **Step 4: Add the env reader and the router step in `probabilistic.py`**

Insert directly after the end of `_fs_linear_cut_rule()`:

```python
_FS_CUT_ROUTER_ON = ("on", "1", "true")
_FS_CUT_ROUTER_OFF = ("", "off", "0", "false")


def _fs_cut_router_enabled() -> bool:
    """``GOLDENMATCH_FS_CUT_ROUTER``: pick each matchkey's link-cut rule from its trained model
    with ``fs_cut_rules.choose_cut_rule``. **Default OFF** (spec P4: rows ship dark until P5).

    ``on``/``1``/``true`` turns it on. A pinned ``link_cut_rule`` still wins, posterior scoring
    bypasses it, and no matching row means the default rule. An unrecognised value warns and
    leaves the router off.
    """
    value = os.environ.get("GOLDENMATCH_FS_CUT_ROUTER", "").strip().lower()
    if value in _FS_CUT_ROUTER_ON:
        return True
    if value not in _FS_CUT_ROUTER_OFF:
        logger.warning(
            "GOLDENMATCH_FS_CUT_ROUTER=%r is not one of on/off; the link-cut router stays off",
            value,
        )
    return False
```

Replace the whole of `_fs_resolved_cut` with the following. It is one edit, so the new imports and their uses land together.

```python
def _fs_resolved_cut(
    mk: MatchkeyConfig, em_result: EMResult, calibrated: bool
) -> ResolvedCut | None:
    """The linear link cut placed by rule, or None when no rule applies.

    Callers check an explicit ``mk.link_threshold`` and an EM-calibrated cutoff first
    (:func:`resolve_link_cut`). Within the rule step, candidates are tried in order:

    1. a pinned ``mk.link_cut_rule``;
    2. otherwise, when ``GOLDENMATCH_FS_CUT_ROUTER`` is on, the rule
       ``fs_cut_rules.choose_cut_rule`` routes this model to;
    3. the ``GOLDENMATCH_FS_LINEAR_CUT`` default.

    A candidate this model cannot supply (``otsu`` without a training histogram) falls through
    to the next, and the reason says so.

    Returns None in posterior mode (the score is a probability, not the linear scale), when the
    model has no usable weights, or when the default is off and no pinned or routed rule is
    computable.
    Spec: docs/superpowers/specs/2026-09-11-fs-cut-rule-routing-design.md.
    """
    from goldenmatch.core.fs_cut_rules import (
        bits_to_normalized,
        choose_cut_rule,
        cut_diagnostics,
        otsu_split,
        rule_bits,
        weight_envelope,
    )

    if calibrated:
        return None
    envelope = weight_envelope(mk, em_result)
    if envelope is None or getattr(em_result, "proportion_matched", None) is None:
        return None
    candidates: list[tuple[str, str]] = []
    pinned = getattr(mk, "link_cut_rule", None)
    if pinned:
        candidates.append((pinned, "pinned by link_cut_rule"))
    elif _fs_cut_router_enabled():
        routed = choose_cut_rule(cut_diagnostics(mk, em_result))
        if routed is not None:
            candidates.append(routed)
    default = _fs_linear_cut_rule()
    if default is not None and all(default != rule for rule, _ in candidates):
        candidates.append((default, "default rule"))
    note = ""
    for rule, reason in candidates:
        bits = rule_bits(rule, envelope, em_result)
        if bits is None:
            note = f"{rule} unavailable: needs a training histogram of more than 50 pairs; "
            continue
        # `otsu`'s normalized cut is the calibrator's `t` VERBATIM, not a bits round trip
        # through bits_to_normalized -- that round trip can land a ulp off (I1).
        normalized = otsu_split(em_result) if rule == "otsu" else bits_to_normalized(bits, envelope)
        return ResolvedCut(
            rule=rule,
            reason=note + reason,
            bits=float(bits),
            normalized=normalized,
        )
    return None
```

- [ ] **Step 5: Pin the router off for the TS parity fixtures**

In `packages/python/goldenmatch/scripts/emit_v2_parity_fixtures.py`, directly after the existing line `    os.environ["GOLDENMATCH_FS_LINEAR_CUT"] = "off"`, add:

```python
    # The link-cut router (GOLDENMATCH_FS_CUT_ROUTER, default off) would move the same cutoff
    # per matchkey; pin it off so a later default flip cannot shift the fixtures.
    os.environ["GOLDENMATCH_FS_CUT_ROUTER"] = "off"
```

- [ ] **Step 6: Run the router tests and the precedence tests**

Run:
```bash
PYTHONPATH="D:/Temp/gm-p4;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider \
  packages/python/goldenmatch/tests/test_fs_cut_router.py \
  packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py \
  packages/python/goldenmatch/tests/test_fs_linear_cut_rule.py \
  packages/python/goldenmatch/tests/test_fs_cut_rules.py \
  packages/python/goldenmatch/tests/test_link_threshold_report_2483.py \
  packages/python/goldenmatch/tests/test_fs_training_histogram.py
```

Expected: all pass. If `test_an_unknown_router_value_warns_and_stays_off` does not capture the record, the goldenmatch logger is not propagating to the root logger. In that case copy how the unknown-`GOLDENMATCH_FS_LINEAR_CUT` warning test captures its warning in `tests/test_fs_linear_cut_rule.py` (around line 112), and keep the assertion.

- [ ] **Step 7: Document the env var**

In `docs-site/goldenmatch/tuning.mdx`, insert this row directly after the `GOLDENMATCH_FS_LINEAR_CUT` row:

```markdown
| `GOLDENMATCH_FS_CUT_ROUTER` | `off` | Routes the **linear** FS link-cut rule per probabilistic matchkey from its trained model: `fs_cut_rules.choose_cut_rule` reads the cut diagnostics (λ, the midpoint and prior cutoffs in bits, the field count, per-field weight spans) and the first matching row of the shipped table picks the rule; no match keeps the `GOLDENMATCH_FS_LINEAR_CUT` default. `on`/`1`/`true` enables it. A pinned `link_cut_rule`, an explicit `link_threshold`, a calibrated cutoff and posterior mode all win over it. A routed cut reports source `evidence_rule` and `cut_reason` `routed by <row>: <reason>`. A row ships only after never scoring below the default (within 0.01) on every design and held-out dataset of the link-cut matrix (`scripts/autoconfig_quality/cut_gate.py`). No row has passed the gate yet, so turning it on changes nothing. An unrecognised value warns and stays off. |
```

In `packages/python/goldenmatch/CHANGELOG.md`, insert directly under `## [Unreleased]` → `### Added` (before the existing `link_cut_rule` bullet):

```markdown
- **An opt-in router picks the FS link-cut rule per matchkey from the trained model.**
  `GOLDENMATCH_FS_CUT_ROUTER=on` (default off) routes each probabilistic matchkey's linear link
  cutoff through `fs_cut_rules.choose_cut_rule`: an ordered table of rows over the model's cut
  diagnostics, where the first match wins and no match keeps the default rule. A pinned
  `link_cut_rule`, an explicit `link_threshold`, a calibrated cutoff and posterior scoring all
  still win. A routed cut reports source `evidence_rule` and `cut_reason`
  `routed by <row>: <reason>`. No row ships yet: a row reaches the table only by passing the
  design and held-out link-cut gate.
```

- [ ] **Step 8: Regenerate derived docs, lint, commit**

```bash
PYTHONPATH="D:/Temp/gm-p4;$PP" PYTHONIOENCODING=utf-8 $PY scripts/regen_docs.py
PYTHONPATH="D:/Temp/gm-p4;$PP" PYTHONIOENCODING=utf-8 $PY scripts/regen_docs.py --check; echo "exit=$?"
rg -c GOLDENMATCH_FS_CUT_ROUTER docs-site/goldenmatch/config-matrix.mdx docs/agent-manifest.json
$PY -m ruff check packages/python/goldenmatch/goldenmatch/core/fs_cut_rules.py packages/python/goldenmatch/goldenmatch/core/probabilistic.py packages/python/goldenmatch/tests/test_fs_cut_router.py packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py packages/python/goldenmatch/scripts/emit_v2_parity_fixtures.py
git status --short
```

Expected: `exit=0`, and a non-zero count in both files.

If `regen_docs.py` dies locally on a `ModuleNotFoundError`, that is worktree env skew. Run the generators it wraps one by one instead:
- `scripts/gen_config_matrix.py --write goldenmatch`
- `scripts/gen_config_matrix.py --manifest`
- `scripts/agent_codemap.py --write`

Note it in the report; CI's `docs_regen` job is the final check. `git add` exactly the files listed under this task that changed, then:

```bash
git commit -m "feat(fs): opt-in link-cut router over a gated row table (GOLDENMATCH_FS_CUT_ROUTER, default off)"
```

---

### Task 3: Labelled-pairs metric for the Magellan datasets

**Files:**
- Modify: `scripts/bench_er_headtohead/datasets.py`:
  - add `MAGELLAN_SUBDIRS` and `magellan_labels` directly above `_magellan`;
  - `_magellan` reads its pairs through `magellan_labels`.
- Modify: `scripts/autoconfig_quality/datasets.py` (add `labelled_pairs` after `_from_headtohead`).
- Modify: `scripts/autoconfig_quality/rule_sweep.py`:
  - add `labelled_summary`, `gate_metric_of`, `metric_value`;
  - `execute_arm`, `arm_main`, `run_arm`, `killed_record`, `sweep_dataset`, `run`.
- Test: `scripts/bench_er_headtohead/test_heldout_loaders.py`, `scripts/autoconfig_quality/tests/test_rule_sweep.py`.

**Interfaces:**
- Consumes: `_read_csv_lossy`, `_MAGELLAN_SPLITS`, `DatasetUnavailable`, `load_dataset` (headtohead); `EvalResult` (`goldenmatch.core.evaluate`).
- Produces:
  - `MAGELLAN_SUBDIRS: dict[str, str]`;
  - `magellan_labels(subdir: str) -> list[tuple[str, str, bool]]`;
  - `labelled_pairs(name: str) -> dict[tuple[int, int], bool] | None`;
  - `labelled_summary(clusters: dict, labels: dict[tuple[int, int], bool]) -> dict` with keys `f1`, `precision`, `recall`, `labelled_pairs`;
  - `gate_metric_of(name: str) -> str` (`"labelled"` or `"f1"`);
  - `metric_value(arm_record: dict, metric: str) -> float | None`.
- Record changes:
  - every arm record gains `labelled: dict | None`;
  - every dataset record gains `gate_metric: str`;
  - `below_default` compares on the gate metric;
  - `execute_arm(df, gt, cfg, arm, model_dir, labels=None)`.

- [ ] **Step 1: Write the failing loader tests**

Append to `scripts/bench_er_headtohead/test_heldout_loaders.py`:

```python
def _fake_magellan(root, subdir: str, *, valid: str = "") -> None:
    base = root / "Magellan" / subdir
    _write(base / "tableA.csv", "id,title\n0,apple ipod\n1,bose speaker\n")
    _write(base / "tableB.csv", "id,title\n0,ipod apple 8gb\n1,sony tv\n")
    _write(base / "train.csv", "ltable_id,rtable_id,label\n1,1,0\n")
    _write(base / "valid.csv", "ltable_id,rtable_id,label\n" + valid)
    _write(base / "test.csv", "ltable_id,rtable_id,label\n0,0,1\n")


def test_magellan_subdirs_name_every_magellan_loader(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DATASETS_DIR", tmp_path)
    assert set(D.MAGELLAN_SUBDIRS) == {"walmart_amazon", "itunes_amazon", "fodors_zagats"}
    for name, subdir in D.MAGELLAN_SUBDIRS.items():
        _fake_magellan(tmp_path, subdir)
        records, _truth = D.load_dataset(name)
        assert records.num_rows == 4


def test_magellan_labels_keep_every_labelled_pair_in_file_order(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DATASETS_DIR", tmp_path)
    _fake_magellan(tmp_path, "Walmart-Amazon", valid="0,1,0\n")
    assert D.magellan_labels("Walmart-Amazon") == [
        ("a:1", "b:1", False),
        ("a:0", "b:1", False),
        ("a:0", "b:0", True),
    ]


def test_labelled_pairs_maps_labels_to_frame_row_indices(tmp_path, monkeypatch):
    from scripts.autoconfig_quality import datasets as Q

    monkeypatch.setattr(D, "DATASETS_DIR", tmp_path)
    _fake_magellan(tmp_path, "Walmart-Amazon", valid="0,0,0\n")
    # Rows follow records order: a:0, a:1, b:0, b:1. (a:0, b:0) is labelled 0 in valid and 1
    # in test, so it counts as a match, as the truth builder counts it.
    assert Q.labelled_pairs("walmart_amazon") == {(1, 3): False, (0, 2): True}
    assert Q.labelled_pairs("febrl3") is None


def test_labelled_pairs_is_none_when_the_data_is_absent(tmp_path, monkeypatch):
    from scripts.autoconfig_quality import datasets as Q

    monkeypatch.setattr(D, "DATASETS_DIR", tmp_path)
    assert Q.labelled_pairs("itunes_amazon") is None
```

- [ ] **Step 2: Write the failing sweep tests**

In `scripts/autoconfig_quality/tests/test_rule_sweep.py`:

(a) Replace `_record` so every record carries the new fields:

```python
def _record(
    corpus: str,
    f1_by_arm: dict,
    applied: dict | None = None,
    voided: dict | None = None,
    *,
    model_complete: bool = True,
    model_stable: bool = True,
    reload_partition_match: bool = True,
) -> dict:
    applied = applied or {}
    voided = voided or {}
    arms = {
        arm: {
            "f1": f1_by_arm.get(arm, 0.5),
            "precision": 0.0,
            "recall": 0.0,
            "pairs": 0,
            "digest": "d",
            "matchkeys": {},
            "applied": applied.get(arm, True),
            "voided": voided.get(arm, False),
            "crashed": None,
            "peak_rss_mb": 10.0,
            "wall_seconds": 0.1,
            "labelled": None,
        }
        for arm in S.ARMS
    }
    return {
        "corpus": corpus,
        "rows": 10,
        "gt_pairs": 3,
        "probabilistic_matchkeys": ["fs"],
        "arms": arms,
        "cut_diagnostics": {},
        "gate_metric": "f1",
        "models_saved": ["fs.json"],
        "model_complete": model_complete,
        "model_stable": model_stable,
        "reload_partition_match": reload_partition_match,
        "model_reload_delta": 0.0,
        "below_default": ["midpoint"],
        "crashed": {},
        "wall_seconds": 1.0,
    }
```

(b) Replace `_fake_run_arm` with this version, which can attach a labelled metric:

```python
def _fake_run_arm(crash: dict[str, str], labelled: dict[str, float] | None = None):
    """An arm runner that runs nothing: every arm applies at ``_SUMMARY``'s F1, except the
    arms in ``crash``, which come back killed with that status. ``labelled`` gives an arm a
    labelled-pairs F1."""

    def fake(argv, out_path, arm, expected_matchkeys, **kwargs):
        if arm in crash:
            return S.killed_record(crash[arm], 1, 20.0, 0.1), None
        rule = None if arm in ("default", S.BASELINE_ARM) else arm
        report = {mk: _entry(rule, "pinned by link_cut_rule") for mk in expected_matchkeys}
        lab = None
        if labelled and arm in labelled:
            value = labelled[arm]
            lab = {"f1": value, "precision": value, "recall": value, "labelled_pairs": 4}
        rec = S.arm_record(arm, _SUMMARY, _stats(**report), (1, "d"), expected_matchkeys)
        rec.update(
            crashed=None, peak_rss_mb=20.0, wall_seconds=0.1, process_seconds=0.2, labelled=lab
        )
        payload = {
            "summary": _SUMMARY,
            "report": report,
            "partition": [1, "d"],
            "wall_seconds": 0.1,
            "labelled": lab,
        }
        return rec, payload

    return fake
```

(c) Append these tests:

```python
# ─── labelled-pairs metric (P4) ───────────────────────────────────────────────


def test_labelled_summary_scores_only_labelled_pairs():
    clusters = {1: {"members": [0, 1, 2]}, 2: {"members": [3]}, 3: {"members": [4, 5]}}
    labels = {(0, 1): True, (0, 2): False, (3, 4): True, (4, 5): False}
    # (0,1) match together: TP. (0,2) non-match together: FP. (3,4) match apart: FN.
    # (4,5) non-match together: FP. The unlabelled (1,2) counts for nothing.
    assert S.labelled_summary(clusters, labels) == {
        "f1": 0.4, "precision": 0.3333, "recall": 0.5, "labelled_pairs": 4,
    }


def test_execute_arm_adds_the_labelled_metric_only_when_labels_are_given(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import goldenmatch

    clusters = {1: {"members": [0, 1]}, 2: {"members": [2]}}
    monkeypatch.setattr(
        goldenmatch, "dedupe_df", lambda df, config: SimpleNamespace(clusters=clusters, stats={})
    )
    plain = S.execute_arm(None, {(0, 1)}, _cfg(), "default_loaded", tmp_path)
    labelled = S.execute_arm(
        None, {(0, 1)}, _cfg(), "default_loaded", tmp_path, labels={(0, 1): True, (1, 2): False}
    )
    assert plain["labelled"] is None
    assert labelled["labelled"] == {
        "f1": 1.0, "precision": 1.0, "recall": 1.0, "labelled_pairs": 2,
    }
    assert labelled["summary"] == plain["summary"]


def test_gate_metric_is_labelled_for_magellan_datasets_only():
    assert S.gate_metric_of("walmart_amazon") == "labelled"
    assert S.gate_metric_of("fodors_zagats") == "labelled"
    assert S.gate_metric_of("abt_buy") == "f1"
    assert S.metric_value({"f1": 0.9, "labelled": {"f1": 0.7}}, "labelled") == 0.7
    assert S.metric_value({"f1": 0.9, "labelled": None}, "labelled") is None
    assert S.metric_value({"f1": 0.9, "labelled": {"f1": 0.7}}, "f1") == 0.9


def test_sweep_dataset_gates_magellan_datasets_on_the_labelled_metric(tmp_path, monkeypatch):
    _small_person(monkeypatch)
    labelled = {"default": 0.8, "default_loaded": 0.8, "evidence_9": 0.7}
    monkeypatch.setattr(S, "run_arm", _fake_run_arm({}, labelled=labelled))
    arms = ("default", "default_loaded", "evidence_9")

    magellan = S.sweep_dataset("walmart_amazon", tmp_path, arms=arms)
    plain = S.sweep_dataset("person", tmp_path, arms=arms)

    assert magellan["gate_metric"] == "labelled"
    assert magellan["below_default"] == ["evidence_9"]
    assert plain["gate_metric"] == "f1"
    assert plain["below_default"] == []


def test_run_reports_failure_when_the_labelled_metric_is_missing(tmp_path, monkeypatch):
    _clear_cut_env(monkeypatch)

    def fake(name, work_dir):
        rec = _record("holdout", {})
        rec["gate_metric"] = "labelled"
        return rec

    monkeypatch.setattr(S, "sweep_dataset", fake)
    out = tmp_path / "card.json"
    assert S.run(["--datasets", "walmart_amazon", "--out", str(out)]) == 1
    card = json.loads(out.read_text())
    assert "walmart_amazon: labelled metric missing on the baseline arm" in card["meta"]["failures"]
```

(d) In `test_sweep_dataset_runs_each_arm_in_its_own_child_on_one_saved_model`, add at the end:

```python
    assert out["gate_metric"] == "f1"
    assert all(rec["labelled"] is None for rec in out["arms"].values())
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `PYTHONPATH="D:/Temp/gm-p4;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider scripts/bench_er_headtohead/test_heldout_loaders.py scripts/autoconfig_quality/tests/test_rule_sweep.py -k "magellan or labelled or gate_metric"`

Expected: FAIL, with `AttributeError` on `MAGELLAN_SUBDIRS`, `magellan_labels`, `labelled_pairs`, `labelled_summary` and `gate_metric_of`.

- [ ] **Step 4: Implement the Magellan labels in the head-to-head loaders**

In `scripts/bench_er_headtohead/datasets.py`, insert directly after `_MAGELLAN_SPLITS = (...)`:

```python
#: Corpus names of the DeepMatcher/Magellan benchmarks, by their directory under ``Magellan/``.
MAGELLAN_SUBDIRS: dict[str, str] = {
    "walmart_amazon": "Walmart-Amazon",
    "itunes_amazon": "iTunes-Amazon",
    "fodors_zagats": "Fodors-Zagats",
}


def magellan_labels(subdir: str) -> list[tuple[str, str, bool]]:
    """Every labelled candidate pair of the train/valid/test splits as
    ``(a:<id>, b:<id>, is_match)``, in file order. Raises ``DatasetUnavailable`` when a split
    is missing."""
    base = DATASETS_DIR / "Magellan" / subdir
    splits = [base / s for s in _MAGELLAN_SPLITS]
    missing = [p.name for p in splits if not p.exists()]
    if missing:
        raise DatasetUnavailable(
            f"{subdir} Magellan splits not found under {base}; missing: {missing}"
        )
    labels: list[tuple[str, str, bool]] = []
    for split in splits:
        table = _read_csv_lossy(split)
        for left, right, label in zip(
            table.column("ltable_id").to_pylist(),
            table.column("rtable_id").to_pylist(),
            table.column("label").to_pylist(),
        ):
            labels.append((f"a:{left}", f"b:{right}", label.strip() == "1"))
    return labels
```

In `_magellan`, replace the pair-building loop:

```python
    pairs: list[tuple[Hashable, Hashable]] = []
    for split in paths[2:]:
        table = _read_csv_lossy(split)
        for left, right, label in zip(
            table.column("ltable_id").to_pylist(),
            table.column("rtable_id").to_pylist(),
            table.column("label").to_pylist(),
        ):
            if label.strip() == "1":
                pairs.append((f"a:{left}", f"b:{right}"))
```

with:

```python
    pairs: list[tuple[Hashable, Hashable]] = [
        (left, right) for left, right, is_match in magellan_labels(subdir) if is_match
    ]
```

- [ ] **Step 5: Implement `labelled_pairs`**

In `scripts/autoconfig_quality/datasets.py`, insert directly after `_from_headtohead`:

```python
def labelled_pairs(name: str) -> dict[tuple[int, int], bool] | None:
    """For a DeepMatcher/Magellan dataset: every labelled candidate pair, in this registry's
    row-index space (the order ``_records_truth_to_frame`` keeps), mapped to whether it is a
    match.

    None for any other dataset, or when the data is absent. Magellan truth covers only the
    labelled candidate positives, so plain F1 counts an unlabelled true match a rule links as a
    false positive; scoring only these pairs removes that bias (spec P4). A pair labelled in
    more than one split is a match when any split says so, as the truth builder counts it."""
    from scripts.bench_er_headtohead import datasets as headtohead

    subdir = headtohead.MAGELLAN_SUBDIRS.get(name)
    if subdir is None:
        return None
    try:
        records, _truth = headtohead.load_dataset(name)
        labels = headtohead.magellan_labels(subdir)
    except headtohead.DatasetUnavailable:
        return None
    row_of = {str(rid): row for row, rid in enumerate(records.column("record_id").to_pylist())}
    pairs: dict[tuple[int, int], bool] = {}
    for left, right, is_match in labels:
        i, j = row_of.get(left), row_of.get(right)
        if i is None or j is None or i == j:
            continue
        key = (min(i, j), max(i, j))
        pairs[key] = pairs.get(key, False) or is_match
    return pairs
```

- [ ] **Step 6: Implement the sweep side**

In `scripts/autoconfig_quality/rule_sweep.py`:

(a) Insert directly after `_model_hashes`:

```python
def labelled_summary(clusters: dict, labels: dict[tuple[int, int], bool]) -> dict:
    """Precision / recall / F1 over the labelled pairs only.

    A labelled match inside one predicted cluster is a TP, a labelled non-match inside one is
    an FP, and a labelled match split apart is an FN. Unlabelled pairs count for nothing."""
    from goldenmatch.core.evaluate import EvalResult

    cluster_of: dict[int, object] = {}
    for cid, info in clusters.items():
        for member in info.get("members", []):
            cluster_of[member] = cid
    tp = fp = fn = 0
    for (i, j), is_match in labels.items():
        together = i in cluster_of and cluster_of[i] == cluster_of.get(j)
        if is_match and together:
            tp += 1
        elif is_match:
            fn += 1
        elif together:
            fp += 1
    summary = EvalResult(tp=tp, fp=fp, fn=fn).summary()
    return {**{key: summary[key] for key in ("f1", "precision", "recall")}, "labelled_pairs": len(labels)}


def gate_metric_of(name: str) -> str:
    """``"labelled"`` for a DeepMatcher/Magellan dataset (F1 over labelled pairs only), else
    ``"f1"``."""
    from scripts.bench_er_headtohead.datasets import MAGELLAN_SUBDIRS

    return "labelled" if name in MAGELLAN_SUBDIRS else "f1"


def metric_value(arm_record: dict, metric: str) -> float | None:
    """An arm's gate metric: ``f1``, or the labelled-pairs F1; None when not measured."""
    if metric == "labelled":
        labelled = arm_record.get("labelled")
        return None if labelled is None else labelled["f1"]
    return arm_record.get("f1")
```

(b) In `killed_record`, add `"labelled": None,` to the returned dict, directly after `"digest": None,`.

(c) In `run_arm`, replace the final `record.update(...)` call with:

```python
    record.update(
        crashed=None,
        peak_rss_mb=round(peak, 1),
        wall_seconds=payload["wall_seconds"],
        process_seconds=round(process_seconds, 1),
        labelled=payload.get("labelled"),
    )
```

(d) Replace `execute_arm` with:

```python
def execute_arm(
    df, gt: set, cfg, arm: str, model_dir: Path, labels: dict | None = None
) -> dict:
    """Run one arm on one labelled frame and return its JSON-able result.

    The only place an arm's pipeline runs: the ``arm`` child process calls it, and so does any
    in-process caller. ``labels`` (``datasets.labelled_pairs``) adds the labelled-pairs
    metric."""
    import time

    import goldenmatch
    from goldenmatch.core.evaluate import evaluate_clusters

    from scripts.bench_er_headtohead.ab_lever import _partition_fingerprint

    start = time.perf_counter()
    rule = None if arm in ("default", BASELINE_ARM) else arm
    result = goldenmatch.dedupe_df(df, config=pin_rule(cfg, rule, model_dir))
    summary = evaluate_clusters(result.clusters, gt).summary()
    pairs, digest = _partition_fingerprint(result.clusters)
    return {
        "summary": {key: summary[key] for key in ("f1", "precision", "recall")},
        "report": (result.stats or {}).get("fs_link_thresholds") or {},
        "partition": [pairs, digest],
        "wall_seconds": round(time.perf_counter() - start, 1),
        "labelled": labelled_summary(result.clusters, labels) if labels is not None else None,
    }
```

(e) In `arm_main`, replace the two lines from `cfg = GoldenMatchConfig...` through `payload = execute_arm(...)` with:

```python
    cfg = GoldenMatchConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
    from scripts.autoconfig_quality.datasets import labelled_pairs

    payload = execute_arm(
        df, gt, cfg, args.arm, args.model_dir, labels=labelled_pairs(args.dataset)
    )
```

(f) In `sweep_dataset`, directly after `holdout = corpus_of(name) == "holdout"`, add:

```python
    metric = gate_metric_of(name)
```

Then replace the block from `hashes_after_last = _model_hashes(model_dir)` through the end of the returned dict with:

```python
    hashes_after_last = _model_hashes(model_dir)
    baseline_f1 = records[BASELINE_ARM]["f1"]
    baseline_gate = metric_value(records[BASELINE_ARM], metric)
    rules = [arm for arm in arms if arm in CUT_RULES]
    # Without both baselines there is nothing to compare a rule against; run() fails it.
    baseline_ok = not records["default"]["crashed"] and not records[BASELINE_ARM]["crashed"]
    model_complete = bool(models_saved) and sorted(models_saved) == sorted(
        f"{mk}.json" for mk in probabilistic_matchkeys
    )
    model_stable = (
        bool(hashes_after_default)
        and bool(hashes_after_last)
        and hashes_after_default == hashes_after_last
    )
    return {
        "corpus": corpus_of(name),
        "rows": rows,
        "gt_pairs": gt_pairs,
        "probabilistic_matchkeys": probabilistic_matchkeys,
        "arms": records,
        "cut_diagnostics": diagnostics,
        "gate_metric": metric,
        "models_saved": models_saved,
        "model_complete": model_complete,
        "model_stable": model_stable,
        "reload_partition_match": (
            records["default"]["digest"] == records[BASELINE_ARM]["digest"] if baseline_ok else None
        ),
        "model_reload_delta": (
            float(baseline_f1 - records["default"]["f1"]) if baseline_ok else None
        ),
        # A rule arm that was killed or crashed is worse than the baseline. Magellan datasets
        # compare on the labelled-pairs metric (gate_metric).
        "below_default": sorted(
            rule
            for rule in rules
            if baseline_ok
            and (
                records[rule]["crashed"]
                or (
                    records[rule]["applied"]
                    and baseline_gate is not None
                    and (metric_value(records[rule], metric) or 0.0) < baseline_gate - TOLERANCE
                )
            )
        ),
        "crashed": {rule: records[rule]["crashed"] for rule in rules if records[rule]["crashed"]},
        "wall_seconds": round(time.perf_counter() - start, 1),
    }
```

(g) In `run`, inside the `for name, record in results.items():` loop, directly after the `for arm in ("default", BASELINE_ARM):` block, add:

```python
        if record.get("gate_metric") == "labelled":
            baseline_arm = record["arms"].get(BASELINE_ARM, {})
            if not baseline_arm.get("crashed") and baseline_arm.get("labelled") is None:
                failures.append(f"{name}: labelled metric missing on the baseline arm")
```

- [ ] **Step 7: Run the whole sweep and loader suites**

Run: `PYTHONPATH="D:/Temp/gm-p4;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider scripts/bench_er_headtohead/test_heldout_loaders.py scripts/bench_er_headtohead/test_design_loaders.py scripts/autoconfig_quality/tests/test_rule_sweep.py scripts/autoconfig_quality/tests/test_corpus.py`

Expected: all pass. That includes the unchanged `test_magellan_links_only_label_one_pairs_across_all_splits`, which proves the truth is unchanged.

- [ ] **Step 8: Lint and commit**

```bash
$PY -m ruff check scripts/bench_er_headtohead/datasets.py scripts/bench_er_headtohead/test_heldout_loaders.py scripts/autoconfig_quality/datasets.py scripts/autoconfig_quality/rule_sweep.py scripts/autoconfig_quality/tests/test_rule_sweep.py
git add scripts/bench_er_headtohead/datasets.py scripts/bench_er_headtohead/test_heldout_loaders.py scripts/autoconfig_quality/datasets.py scripts/autoconfig_quality/rule_sweep.py scripts/autoconfig_quality/tests/test_rule_sweep.py
git commit -m "feat(quality): score the Magellan link-cut arms on labelled pairs only"
```

---

### Task 4: The routed arm

**Files:**
- Modify: `scripts/autoconfig_quality/rule_sweep.py`:
  - constants: `ROUTED_ARM`, `ARMS`, `ROUTER_ENV`, `CUT_ENV_VARS`;
  - add `pinned_rule` and `routed_check`;
  - `arm_record`, `execute_arm`, `arm_env`, `sweep_dataset`, `run`;
  - module docstring.
- Test: `scripts/autoconfig_quality/tests/test_rule_sweep.py`.

**Interfaces:**
- Consumes: `GOLDENMATCH_FS_CUT_ROUTER` (Task 2); `_record` / `_fake_run_arm` test helpers (Task 3).
- Produces:
  - `ROUTED_ARM = "routed"` and `ARMS = ("default", "default_loaded", *CUT_RULES, "routed")`;
  - `ROUTER_ENV = "GOLDENMATCH_FS_CUT_ROUTER"`, included in `CUT_ENV_VARS`;
  - `pinned_rule(arm: str) -> str | None`;
  - `arm_env(arm: str | None = None) -> dict[str, str]`, with `ROUTER_ENV` `"on"` for `ROUTED_ARM` and `"off"` otherwise;
  - `routed_check(arms: dict[str, dict]) -> tuple[dict[str, str | None], bool | None]`;
  - dataset record fields `routed_rules: dict[str, str | None]` and `routed_matches: bool | None`;
  - run failure `"<name>: routed arm partition differs from the arm pinning its rule"`.

- [ ] **Step 1: Write the failing tests**

In `scripts/autoconfig_quality/tests/test_rule_sweep.py`:

(a) Replace `test_arms_run_both_baselines_then_every_rule_once` with:

```python
def test_arms_run_both_baselines_every_rule_then_the_router():
    assert S.ARMS == ("default", "default_loaded", *CUT_RULES, "routed")
```

(b) In `_record`, add two keys to the returned dict, directly after `"gate_metric": "f1",`:

```python
        "routed_rules": {},
        "routed_matches": None,
```

(c) In `_fake_run_arm`, replace the two lines:

```python
        rule = None if arm in ("default", S.BASELINE_ARM) else arm
        report = {mk: _entry(rule, "pinned by link_cut_rule") for mk in expected_matchkeys}
```

with:

```python
        rule = S.pinned_rule(arm)
        reason = "pinned by link_cut_rule" if rule else "default rule"
        report = {mk: _entry(rule, reason) for mk in expected_matchkeys}
```

(d) Append these tests:

```python
# ─── the routed arm (P4) ──────────────────────────────────────────────────────


def test_cut_env_vars_include_the_router():
    assert "GOLDENMATCH_FS_CUT_ROUTER" in S.CUT_ENV_VARS


def test_arm_env_turns_the_router_on_for_the_routed_arm_only():
    assert S.arm_env("routed")["GOLDENMATCH_FS_CUT_ROUTER"] == "on"
    for arm in ("default", "default_loaded", *CUT_RULES):
        assert S.arm_env(arm)["GOLDENMATCH_FS_CUT_ROUTER"] == "off"


def test_the_routed_arm_pins_nothing_and_applies_whatever_the_router_chose():
    assert S.pinned_rule("routed") is None
    assert S.pinned_rule("default_loaded") is None
    assert S.pinned_rule("evidence_5") == "evidence_5"
    rec = S.arm_record(
        "routed", _SUMMARY, _stats(fs=_entry("evidence_5", "routed by wide: x")), (1, "d"), ["fs"]
    )
    assert (rec["applied"], rec["voided"]) == (True, False)


def _routing_arms(routed_matchkeys: dict, digests: dict | None = None, **overrides) -> dict:
    digests = digests or {}
    arms = {
        arm: {"digest": digests.get(arm, "base"), "voided": False, "crashed": None, "matchkeys": {}}
        for arm in S.ARMS
    }
    arms["routed"]["matchkeys"] = routed_matchkeys
    for arm, fields in overrides.items():
        arms[arm].update(fields)
    return arms


def test_routed_check_compares_an_unrouted_arm_with_the_baseline():
    arms = _routing_arms({"fs": _entry("prior_mid", "default rule")})
    assert S.routed_check(arms) == ({"fs": None}, True)


def test_routed_check_compares_a_routed_arm_with_the_arm_pinning_its_rule():
    entry = {"fs": _entry("evidence_5", "routed by wide: many fields")}
    same = _routing_arms(entry, {"routed": "e5", "evidence_5": "e5"})
    assert S.routed_check(same) == ({"fs": "evidence_5"}, True)
    differs = _routing_arms(entry, {"routed": "e5", "evidence_5": "other"})
    assert S.routed_check(differs) == ({"fs": "evidence_5"}, False)


def test_routed_check_has_nothing_to_compare_for_mixed_routes_or_a_crash():
    mixed = _routing_arms(
        {"fs_a": _entry("evidence_5", "routed by wide: x"), "fs_b": _entry("prior_mid", "default rule")}
    )
    assert S.routed_check(mixed) == ({"fs_a": "evidence_5", "fs_b": None}, None)
    crashed = _routing_arms({}, routed={"crashed": "timeout"})
    assert S.routed_check(crashed) == ({}, None)
    target_crashed = _routing_arms(
        {"fs": _entry("evidence_5", "routed by wide: x")}, evidence_5={"crashed": "memory_cap"}
    )
    assert S.routed_check(target_crashed) == ({"fs": "evidence_5"}, None)


def test_sweep_dataset_turns_the_router_on_in_the_routed_arm_child_only(tmp_path, monkeypatch):
    _small_person(monkeypatch)
    seen: dict[str, str] = {}
    instant = _fake_run_arm({})

    def spy(argv, out_path, arm, expected_matchkeys, **kwargs):
        seen[arm] = kwargs["env"]["GOLDENMATCH_FS_CUT_ROUTER"]
        return instant(argv, out_path, arm, expected_matchkeys, **kwargs)

    monkeypatch.setattr(S, "run_arm", spy)
    rec = S.sweep_dataset("person", tmp_path)
    assert seen == {arm: ("on" if arm == "routed" else "off") for arm in S.ARMS}
    assert rec["routed_rules"] and all(v is None for v in rec["routed_rules"].values())
    assert rec["routed_matches"] is True


def test_run_reports_failure_when_the_routed_arm_disagrees(tmp_path, monkeypatch):
    _clear_cut_env(monkeypatch)

    def fake(name, work_dir):
        rec = _record("design", {})
        rec["routed_matches"] = False
        return rec

    monkeypatch.setattr(S, "sweep_dataset", fake)
    out = tmp_path / "card.json"
    assert S.run(["--datasets", "person", "--out", str(out)]) == 1
    card = json.loads(out.read_text())
    assert (
        "person: routed arm partition differs from the arm pinning its rule"
        in card["meta"]["failures"]
    )
```

(e) In `test_sweep_dataset_runs_each_arm_in_its_own_child_on_one_saved_model`, change `arms = ("default", "default_loaded", "evidence_9")` to `arms = ("default", "default_loaded", "evidence_9", "routed")`. Add at the end:

```python
    # The routed arm loads the same model with the router on. Where no shipped row fired, it
    # must reproduce the baseline partition exactly.
    if all(rule is None for rule in out["routed_rules"].values()):
        assert out["routed_matches"] is True
    else:
        assert out["routed_matches"] in (True, None)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH="D:/Temp/gm-p4;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider scripts/autoconfig_quality/tests/test_rule_sweep.py`

Expected: FAIL on `test_arms_run_both_baselines_every_rule_then_the_router`, and with `AttributeError: ... 'pinned_rule'` / `'routed_check'` in the new tests.

- [ ] **Step 3: Implement**

In `scripts/autoconfig_quality/rule_sweep.py`:

(a) Replace the constants block from `BASELINE_ARM = "default_loaded"` through the end of `CUT_ENV_VARS = (...)` with:

```python
BASELINE_ARM = "default_loaded"
#: link_cut_rule unset, GOLDENMATCH_FS_CUT_ROUTER on, model loaded: the shipped routing table
#: end to end. Its partition must equal the arm pinning the rule it routed to.
ROUTED_ARM = "routed"
ARMS: tuple[str, ...] = ("default", BASELINE_ARM, *CUT_RULES, ROUTED_ARM)

#: The spec's gate tolerance: a rule is below the default when F1 < default - 0.01.
TOLERANCE = 0.01

ROUTER_ENV = "GOLDENMATCH_FS_CUT_ROUTER"

#: Env vars that move the cut or void a pin process-wide. A sweep run with any of
#: them set measures something other than the shipped default, so it refuses.
CUT_ENV_VARS = (
    "GOLDENMATCH_FS_LINEAR_CUT",
    "GOLDENMATCH_FS_CALIBRATED",
    "GOLDENMATCH_FS_EVIDENCE_CUT",
    "GOLDENMATCH_FS_CALIBRATE_THRESHOLD",
    # Passes a pair_filter to load_or_train_em, which stops model_path from
    # saving, so arms would retrain their own model instead of sharing one.
    "GOLDENMATCH_FS_SIGNATURE_PRUNE",
    # The sweep sets it per child (arm_env): on for ROUTED_ARM, off for every other arm.
    ROUTER_ENV,
)


def pinned_rule(arm: str) -> str | None:
    """The ``link_cut_rule`` an arm pins: its own name for a rule arm, None otherwise."""
    return arm if arm in CUT_RULES else None
```

(b) In the module docstring's arm list, add a line after `<rule>          one arm per fs_cut_rules.CUT_RULES name, model loaded`:

```
  routed          link_cut_rule unset, GOLDENMATCH_FS_CUT_ROUTER=on, model loaded
```

Also replace `Measurement only: nothing here chooses a rule.` with `Measurement only: nothing here chooses a rule (cut_gate.py gates rows on this output).`

(c) In `arm_record`, replace `rule = None if arm in ("default", BASELINE_ARM) else arm` with `rule = pinned_rule(arm)`. Make the same replacement in `execute_arm`.

(d) Replace `arm_env` with:

```python
def arm_env(arm: str | None = None) -> dict[str, str]:
    """A child's env: the parent's, plus one hash seed shared by every arm, no autoconfig
    memory, and the link-cut router on for ``ROUTED_ARM`` only."""
    return {
        **os.environ,
        "PYTHONHASHSEED": "0",
        "GOLDENMATCH_AUTOCONFIG_MEMORY": "0",
        ROUTER_ENV: "on" if arm == ROUTED_ARM else "off",
    }
```

(e) Insert directly before `def sweep_dataset(`:

```python
def routed_check(arms: dict[str, dict]) -> tuple[dict[str, str | None], bool | None]:
    """What the router picked per matchkey on the routed arm, and whether that arm's partition
    equals the arm pinning the same rule (the baseline when no row fired).

    ``{matchkey: rule}`` holds the rule only where ``cut_reason`` says a row routed it, else
    None. The match is None when there is nothing to compare:
    - no routed arm;
    - a crashed or voided arm on either side;
    - matchkeys routed to different rules, which no single pinned arm measures."""
    routed = arms.get(ROUTED_ARM)
    if routed is None or routed.get("crashed") or routed.get("voided"):
        return {}, None
    rules = {
        mk: entry["cut_rule"] if (entry.get("cut_reason") or "").startswith("routed by ") else None
        for mk, entry in routed["matchkeys"].items()
    }
    picked = set(rules.values())
    if len(picked) != 1:
        return rules, None
    target = arms.get(picked.pop() or BASELINE_ARM)
    if target is None or target.get("crashed") or target.get("voided"):
        return rules, None
    return rules, routed["digest"] == target["digest"]
```

(f) In `sweep_dataset`:
- delete the line `env = arm_env()`;
- in the `run_arm(...)` call, replace `env=env` with `env=arm_env(arm)`;
- directly after `hashes_after_last = _model_hashes(model_dir)`, add `routed_rules, routed_matches = routed_check(records)`;
- in the returned dict, directly after `"gate_metric": metric,`, add:

```python
        "routed_rules": routed_rules,
        "routed_matches": routed_matches,
```

(g) In `run`, directly after the labelled-metric failure block from Task 3, add:

```python
        if record.get("routed_matches") is False:
            failures.append(f"{name}: routed arm partition differs from the arm pinning its rule")
```

- [ ] **Step 4: Run the sweep suite**

Run: `PYTHONPATH="D:/Temp/gm-p4;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider scripts/autoconfig_quality/tests/test_rule_sweep.py scripts/autoconfig_quality/tests/test_corpus.py`

Expected: all pass, including the real-child end-to-end test with the routed arm.

- [ ] **Step 5: Lint and commit**

```bash
$PY -m ruff check scripts/autoconfig_quality/rule_sweep.py scripts/autoconfig_quality/tests/test_rule_sweep.py
git add scripts/autoconfig_quality/rule_sweep.py scripts/autoconfig_quality/tests/test_rule_sweep.py
git commit -m "feat(quality): a routed arm checks the link-cut router end to end"
```

---

### Task 5: Candidate rows and the blind gate

**Files:**
- Create: `scripts/autoconfig_quality/cut_rows.py`, `scripts/autoconfig_quality/cut_gate.py`.
- Test: `scripts/autoconfig_quality/tests/test_cut_gate.py`.

**Interfaces:**
- Consumes:
  - `CutRow`, `CutDiagnostics`, `choose_cut_rule`, `fs_cut_rules.ROWS` (Task 2);
  - `BASELINE_ARM`, `TOLERANCE`, `metric_value` (`rule_sweep`, Task 3);
  - the dataset record fields `probabilistic_matchkeys`, `cut_diagnostics`, `gate_metric`, `arms`, `routed_rules`, `routed_matches` (Tasks 3–4);
  - `ci_datasets` (`corpus.py`).
- Produces:
  - `cut_rows.CANDIDATES: tuple[CutRow, ...]`;
  - `cut_gate.Verdict(passed, fired, rule, delta, problem)` and `cut_gate.SetResult(passed, fired, measured, verdicts, missing, problems)`;
  - `route_record(record, rows) -> (rule | None, problem | None)`;
  - `judge(record, rows) -> Verdict`;
  - `routed_disagreement(record, rows) -> str | None`;
  - `gate_set(card, expected, rows, *, check_routed) -> SetResult`;
  - `tables(kind) -> list[(label, rows)]`;
  - `render(label, design, holdout) -> str`;
  - `main(argv) -> int` (0 pass or `--report-only`, 1 a table failed, 2 invalid input).

- [ ] **Step 1: Write the failing tests**

Create `scripts/autoconfig_quality/tests/test_cut_gate.py`:

```python
"""Link-cut routing gate (FS link-cut routing P4)."""

from __future__ import annotations

import json

from goldenmatch.core import fs_cut_rules
from goldenmatch.core.fs_cut_rules import CutDiagnostics, CutRow, choose_cut_rule

from scripts.autoconfig_quality import cut_gate as G
from scripts.autoconfig_quality import cut_rows
from scripts.autoconfig_quality import rule_sweep as S

_SPARSE = CutRow(
    name="sparse", rule="evidence_12", reason="r", when=lambda d: d.proportion_matched < 0.01
)


def _diag(lam: float = 0.002, mid: float = 3.0, prior: float = 9.0, n_fields: int = 3) -> dict:
    return {
        "proportion_matched": lam,
        "lo": -10.0,
        "hi": 30.0,
        "midpoint_bits": mid,
        "prior_bits": prior,
        "n_fields": n_fields,
        "has_negative_evidence": False,
        "field_weight_spans": {},
        "admitted_fraction": None,
    }


def _rec(
    f1: dict | None = None,
    *,
    diags: dict | None = None,
    metric: str = "f1",
    labelled: dict | None = None,
    crashed: dict | None = None,
    routed_rules: dict | None = None,
    routed_matches: bool | None = True,
) -> dict:
    f1 = f1 or {}
    diags = {"fs": _diag()} if diags is None else diags
    arms = {}
    for arm in S.ARMS:
        lab = None
        if labelled is not None:
            value = labelled.get(arm, labelled["default_loaded"])
            lab = {"f1": value, "precision": value, "recall": value, "labelled_pairs": 10}
        arms[arm] = {
            "f1": f1.get(arm, f1.get("default_loaded", 0.9)),
            "applied": True,
            "voided": False,
            "crashed": (crashed or {}).get(arm),
            "digest": "d",
            "matchkeys": {},
            "labelled": lab,
        }
    return {
        "probabilistic_matchkeys": sorted(diags),
        "cut_diagnostics": diags,
        "gate_metric": metric,
        "arms": arms,
        "routed_rules": {mk: None for mk in diags} if routed_rules is None else routed_rules,
        "routed_matches": routed_matches,
    }


def _card(sha: str = "abc", failures: list[str] | None = None, **datasets) -> dict:
    return {"meta": {"git_sha": sha, "failures": failures or []}, "datasets": datasets}


# ─── one dataset ──────────────────────────────────────────────────────────────


def test_a_dataset_no_row_fires_on_keeps_the_default_and_passes():
    v = G.judge(_rec(diags={"fs": _diag(lam=0.2)}), (_SPARSE,))
    assert (v.passed, v.fired, v.rule, v.problem) == (True, False, None, None)


def test_a_routed_rule_within_the_tolerance_passes():
    v = G.judge(_rec({"default_loaded": 0.9, "evidence_12": 0.891}), (_SPARSE,))
    assert (v.passed, v.fired, v.rule, v.delta) == (True, True, "evidence_12", -0.009)


def test_a_routed_rule_below_the_tolerance_fails():
    v = G.judge(_rec({"default_loaded": 0.9, "evidence_12": 0.88}), (_SPARSE,))
    assert (v.passed, v.delta) == (False, -0.02)


def test_a_crashed_routed_arm_fails():
    v = G.judge(_rec(crashed={"evidence_12": "memory_cap"}), (_SPARSE,))
    assert (v.passed, v.problem) == (False, "evidence_12 arm memory_cap")


def test_magellan_records_gate_on_the_labelled_metric():
    worse_labelled = _rec(
        {"default_loaded": 0.5, "evidence_12": 0.9},
        metric="labelled",
        labelled={"default_loaded": 0.8, "evidence_12": 0.7},
    )
    assert G.judge(worse_labelled, (_SPARSE,)).passed is False
    worse_plain = _rec(
        {"default_loaded": 0.9, "evidence_12": 0.5},
        metric="labelled",
        labelled={"default_loaded": 0.7, "evidence_12": 0.8},
    )
    assert G.judge(worse_plain, (_SPARSE,)).passed is True


def test_matchkeys_routed_to_different_rules_fail_as_unmeasurable():
    rec = _rec(diags={"fs_a": _diag(lam=0.002), "fs_b": _diag(lam=0.2)})
    v = G.judge(rec, (_SPARSE,))
    assert v.passed is False and "different rules" in v.problem


def test_a_record_without_a_gate_metric_fails():
    rec = _rec()
    del rec["gate_metric"]
    v = G.judge(rec, (_SPARSE,))
    assert v.passed is False and "gate_metric" in v.problem


# ─── one set ──────────────────────────────────────────────────────────────────


def test_a_set_fails_when_an_expected_dataset_is_missing_or_the_run_failed():
    card = _card(febrl3=_rec())
    missing = G.gate_set(card, ["febrl3", "febrl4"], (_SPARSE,), check_routed=False)
    assert missing.passed is False and missing.missing == ["febrl4"]
    failed = G.gate_set(
        _card(failures=["x: voided"], febrl3=_rec()), ["febrl3"], (_SPARSE,), check_routed=False
    )
    assert failed.passed is False and failed.problems == ["the matrix run recorded 1 failures"]
    ok = G.gate_set(card, ["febrl3"], (_SPARSE,), check_routed=False)
    assert (ok.passed, ok.fired, ok.measured) == (True, 1, 1)


def test_the_shipped_table_also_needs_the_routed_arm_to_agree():
    agree = _rec(routed_rules={"fs": "evidence_12"})
    assert G.gate_set(_card(d=agree), ["d"], (_SPARSE,), check_routed=True).passed is True

    picked_other = _rec(routed_rules={"fs": None})
    v = G.gate_set(_card(d=picked_other), ["d"], (_SPARSE,), check_routed=True).verdicts["d"]
    assert v.passed is False and "picked differently" in v.problem

    split = _rec(routed_rules={"fs": "evidence_12"}, routed_matches=False)
    v = G.gate_set(_card(d=split), ["d"], (_SPARSE,), check_routed=True).verdicts["d"]
    assert v.passed is False and "partition differs" in v.problem

    old = _rec()
    del old["routed_rules"]
    assert G.gate_set(_card(d=old), ["d"], (_SPARSE,), check_routed=True).passed is False


# ─── rendering and CLI ────────────────────────────────────────────────────────


def test_the_held_out_verdict_names_no_dataset_rule_or_score():
    design = G.gate_set(
        _card(febrl3=_rec({"default_loaded": 0.9, "evidence_12": 0.95})),
        ["febrl3"], (_SPARSE,), check_routed=False,
    )
    holdout = G.gate_set(
        _card(walmart_amazon=_rec({"default_loaded": 0.9, "evidence_12": 0.8})),
        ["walmart_amazon"], (_SPARSE,), check_routed=False,
    )
    md = G.render("candidate sparse -> evidence_12", design, holdout)
    design_part, held_out_part = md.split("**Held-out")
    assert "| febrl3 | evidence_12 | +0.0500 | PASS |" in design_part
    assert "**Held-out" + held_out_part.split("\n")[0] == "**Held-out: FAIL** (fired on 1 of 1)"
    for leaked in ("walmart_amazon", "evidence_12", "0.8", "-0.1000"):
        assert leaked not in held_out_part


def _write_cards(tmp_path, design: dict, holdout: dict):
    d, h = tmp_path / "design.json", tmp_path / "holdout.json"
    d.write_text(json.dumps(design), encoding="utf-8")
    h.write_text(json.dumps(holdout), encoding="utf-8")
    return d, h


def test_main_exits_1_when_a_candidate_fails_held_out_and_0_with_report_only(tmp_path, monkeypatch):
    monkeypatch.setattr(cut_rows, "CANDIDATES", (_SPARSE,))
    monkeypatch.setattr(fs_cut_rules, "ROWS", ())
    monkeypatch.setattr(
        G, "ci_datasets", lambda corpus: ["febrl3"] if corpus == "design" else ["walmart_amazon"]
    )
    d, h = _write_cards(
        tmp_path,
        _card(febrl3=_rec()),
        _card(walmart_amazon=_rec({"default_loaded": 0.9, "evidence_12": 0.5})),
    )
    out = tmp_path / "gate.md"
    argv = ["--design", str(d), "--holdout", str(h), "--table", "candidates", "--out", str(out)]
    assert G.main(argv) == 1
    assert "**Held-out: FAIL**" in out.read_text(encoding="utf-8")
    assert G.main([*argv, "--report-only"]) == 0


def test_main_refuses_matrices_from_different_commits(tmp_path):
    d, h = _write_cards(tmp_path, _card("abc"), _card("def"))
    argv = ["--design", str(d), "--holdout", str(h), "--table", "shipped", "--out", str(tmp_path / "g.md")]
    assert G.main(argv) == 2


def test_the_empty_shipped_table_passes_when_every_routed_arm_kept_the_default(tmp_path, monkeypatch):
    monkeypatch.setattr(fs_cut_rules, "ROWS", ())
    monkeypatch.setattr(
        G, "ci_datasets", lambda corpus: ["febrl3"] if corpus == "design" else ["abt_buy"]
    )
    d, h = _write_cards(tmp_path, _card(febrl3=_rec()), _card(abt_buy=_rec()))
    out = tmp_path / "gate.md"
    argv = ["--design", str(d), "--holdout", str(h), "--table", "shipped", "--out", str(out)]
    assert G.main(argv) == 0
    assert "shipped table (no rows)" in out.read_text(encoding="utf-8")


# ─── the candidates, as written from the design matrix ────────────────────────

#: (λ, midpoint bits, prior bits, n_fields) recorded by design matrix run 34638483397.
_DESIGN_SHAPES = {
    "dblp_acm": (0.00196, 2.87, 8.99, 3),
    "synth_biblio_d02": (0.0005, 5.39, 10.97, 3),
    "synth_person_d02": (0.00185, 0.0, 9.08, 5),
    "musicbrainz_20k": (0.0757, 17.3, 3.61, 5),
    "febrl3": (0.0826, 10.5, 3.47, 9),
    "febrl4": (0.042, 13.96, 4.51, 9),
    "dblp_scholar": (0.0646, 22.23, 3.86, 3),
    "historical_50k": (0.661, 10.82, -0.96, 6),
}


def test_candidates_fire_where_the_design_matrix_says():
    routed = {}
    for name, (lam, mid, prior, n_fields) in _DESIGN_SHAPES.items():
        choice = choose_cut_rule(
            CutDiagnostics(**_diag(lam, mid, prior, n_fields)), cut_rows.CANDIDATES
        )
        routed[name] = None if choice is None else choice[0]
    assert routed == {
        "dblp_acm": "posterior_099",
        "synth_biblio_d02": "posterior_099",
        "synth_person_d02": None,
        "musicbrainz_20k": "evidence_5",
        "febrl3": "evidence_5",
        "febrl4": "evidence_5",
        "dblp_scholar": None,
        "historical_50k": None,
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH="D:/Temp/gm-p4;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider scripts/autoconfig_quality/tests/test_cut_gate.py`

Expected: FAIL at collection, with `ImportError: cannot import name 'cut_gate'`.

- [ ] **Step 3: Write the candidate rows**

Create `scripts/autoconfig_quality/cut_rows.py`:

```python
"""Candidate link-cut routing rows, NOT shipped (spec 2026-09-11-fs-cut-rule-routing-design, P4).

Written from the DESIGN matrix only (run 34638483397), before any held-out result was read.
``cut_gate`` gates each one on the design AND held-out matrix:
- a row that passes both moves VERBATIM into ``goldenmatch.core.fs_cut_rules.ROWS``;
- a row that fails is deleted here, never tuned against the held-out result.

The ``n_fields`` bounds were chosen to exclude synth_person_d02 (A') and dblp_scholar (B) on
the design set, so only the held-out set tests them.
"""

from __future__ import annotations

from goldenmatch.core.fs_cut_rules import CutRow

CANDIDATES: tuple[CutRow, ...] = (
    # A'. Design: dblp_acm +0.0918, synth_biblio_d02 +0.0000.
    CutRow(
        name="sparse_prior_binds",
        rule="posterior_099",
        reason="match rate under 0.004, the prior cutoff above the midpoint, at most 3 fields",
        when=lambda d: (
            d.proportion_matched < 0.004 and d.prior_bits > d.midpoint_bits and d.n_fields <= 3
        ),
    ),
    # B. Design: musicbrainz_20k +0.2673, febrl3 +0.004, febrl4 +0.0002.
    CutRow(
        name="midpoint_binds_wide",
        rule="evidence_5",
        reason="match rate under 0.1, the midpoint cutoff above the prior, at least 5 fields",
        when=lambda d: (
            d.proportion_matched < 0.1 and d.midpoint_bits > d.prior_bits and d.n_fields >= 5
        ),
    ),
)
```

- [ ] **Step 4: Write the gate**

Create `scripts/autoconfig_quality/cut_gate.py`:

```python
"""Gate link-cut routing rows on the per-rule matrix (spec 2026-09-11-fs-cut-rule-routing-design, P4).

A routing table routes each dataset's probabilistic matchkeys from the ``cut_diagnostics`` the
matrix recorded on its baseline arm. The routed rule's arm must score at least
``default - 0.01`` on the dataset's gate metric (F1, or labelled-pairs F1 for Magellan) on
EVERY design dataset and EVERY held-out dataset. A dataset no row fires on keeps the default
and passes.

``--table shipped`` also requires each dataset's routed arm (the router run live) to have
picked what the table picks from the recorded diagnostics, and to reproduce the partition of
the arm pinning that rule.

Design verdicts are written in full. The held-out verdict is PASS/FAIL plus how many held-out
datasets the table fired on -- no dataset names, rules or scores -- so it can be read without
the held-out results reaching whoever writes rows.

Usage:
  python -m scripts.autoconfig_quality.cut_gate --design cut-rule-matrix.json \
    --holdout cut-rule-matrix-holdout.json --table candidates --out cut-rule-gate.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from goldenmatch.core import fs_cut_rules
from goldenmatch.core.fs_cut_rules import CutDiagnostics, CutRow, choose_cut_rule

from scripts.autoconfig_quality.corpus import ci_datasets
from scripts.autoconfig_quality.rule_sweep import BASELINE_ARM, TOLERANCE, metric_value


@dataclass(frozen=True)
class Verdict:
    """One dataset under one routing table."""

    passed: bool
    fired: bool
    rule: str | None
    delta: float | None
    problem: str | None


@dataclass(frozen=True)
class SetResult:
    """One corpus set (design or held-out) under one routing table."""

    passed: bool
    fired: int
    measured: int
    verdicts: dict[str, Verdict]
    missing: list[str]
    problems: list[str]


def _routes(record: dict, rows: Sequence[CutRow]) -> dict[str, str | None]:
    """``{matchkey: rule}`` that ``rows`` pick from the recorded diagnostics (None = default)."""
    diagnostics = record.get("cut_diagnostics") or {}
    routes: dict[str, str | None] = {}
    for mk in record.get("probabilistic_matchkeys") or []:
        d = diagnostics.get(mk)
        choice = choose_cut_rule(CutDiagnostics(**d) if d else None, rows)
        routes[mk] = None if choice is None else choice[0]
    return routes


def route_record(record: dict, rows: Sequence[CutRow]) -> tuple[str | None, str | None]:
    """``(rule, problem)``: the one rule ``rows`` route this dataset's probabilistic matchkeys
    to (None = the default), or a problem when they route to different rules."""
    picks = set(_routes(record, rows).values())
    if len(picks) > 1:
        return None, "matchkeys route to different rules; no pinned arm measures that"
    return (picks.pop() if picks else None), None


def judge(record: dict, rows: Sequence[CutRow]) -> Verdict:
    """The verdict for one dataset: the routed rule's arm against the baseline arm."""
    metric = record.get("gate_metric")
    if metric is None:
        return Verdict(False, False, None, None, "record has no gate_metric: re-run the matrix")
    rule, problem = route_record(record, rows)
    if problem is not None:
        return Verdict(False, True, None, None, problem)
    baseline = metric_value(record["arms"][BASELINE_ARM], metric)
    if baseline is None:
        return Verdict(False, rule is not None, rule, None, "baseline arm has no measurement")
    if rule is None:
        return Verdict(True, False, None, 0.0, None)
    arm = record["arms"][rule]
    if arm.get("crashed") or arm.get("voided"):
        status = arm.get("crashed") or "voided"
        return Verdict(False, True, rule, None, f"{rule} arm {status}")
    value = metric_value(arm, metric)
    if value is None:
        return Verdict(False, True, rule, None, f"{rule} arm has no measurement")
    return Verdict(not value < baseline - TOLERANCE, True, rule, round(value - baseline, 4), None)


def routed_disagreement(record: dict, rows: Sequence[CutRow]) -> str | None:
    """For the SHIPPED table: a problem when the matrix's routed arm did not pick what ``rows``
    pick from the recorded diagnostics, or did not reproduce the partition of the arm pinning
    that rule. None when both hold."""
    if "routed_rules" not in record:
        return "record has no routed arm: re-run the matrix"
    if record["routed_rules"] != _routes(record, rows):
        return "the routed arm picked differently from the recorded diagnostics"
    if record.get("routed_matches") is False:
        return "the routed arm's partition differs from the arm pinning its rule"
    return None


def gate_set(
    card: dict, expected: Sequence[str], rows: Sequence[CutRow], *, check_routed: bool
) -> SetResult:
    """Every dataset of one merged matrix card under ``rows``. The set passes only when every
    ``expected`` dataset was measured, the run recorded no failures, and every verdict passed."""
    datasets = card.get("datasets") or {}
    missing = [name for name in expected if name not in datasets]
    problems: list[str] = []
    if missing:
        problems.append(f"{len(missing)} of {len(expected)} expected datasets not measured")
    failures = (card.get("meta") or {}).get("failures") or []
    if failures:
        problems.append(f"the matrix run recorded {len(failures)} failures")
    verdicts: dict[str, Verdict] = {}
    for name in sorted(datasets):
        verdict = judge(datasets[name], rows)
        if check_routed and verdict.problem is None:
            disagreement = routed_disagreement(datasets[name], rows)
            if disagreement is not None:
                verdict = Verdict(False, verdict.fired, verdict.rule, verdict.delta, disagreement)
        verdicts[name] = verdict
    passed = not problems and all(v.passed for v in verdicts.values())
    fired = sum(1 for v in verdicts.values() if v.fired)
    return SetResult(passed, fired, len(verdicts), verdicts, missing, problems)


def tables(kind: str) -> list[tuple[str, tuple[CutRow, ...]]]:
    """``(label, rows)`` to gate.

    - ``shipped``: the shipped ``fs_cut_rules.ROWS`` as one table.
    - ``candidates``: each ``cut_rows.CANDIDATES`` row appended, on its own, after the shipped
      rows."""
    from scripts.autoconfig_quality.cut_rows import CANDIDATES

    shipped = tuple(fs_cut_rules.ROWS)
    if kind == "shipped":
        names = ", ".join(row.name for row in shipped) or "no rows"
        return [(f"shipped table ({names})", shipped)]
    return [(f"candidate {row.name} -> {row.rule}", (*shipped, row)) for row in CANDIDATES]


def _word(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def render(label: str, design: SetResult, holdout: SetResult) -> str:
    """Markdown for one table: full design detail, then the held-out verdict with counts only."""
    lines = [
        f"### {label}",
        "",
        f"**Design: {_word(design.passed)}** (fired on {design.fired} of {design.measured})",
        "",
    ]
    lines += [f"- {problem}" for problem in design.problems]
    lines += [f"- missing: {name}" for name in design.missing]
    shown = [
        (name, v) for name, v in design.verdicts.items() if v.fired or v.problem or not v.passed
    ]
    if shown:
        lines += [
            "",
            "| dataset | routed rule | Δ vs default | verdict | problem |",
            "|---|---|---|---|---|",
        ]
        for name, v in shown:
            delta = "" if v.delta is None else f"{v.delta:+.4f}"
            lines.append(
                f"| {name} | {v.rule or 'default'} | {delta} | {_word(v.passed)} | {v.problem or ''} |"
            )
    lines += [
        "",
        f"**Held-out: {_word(holdout.passed)}** (fired on {holdout.fired} of {holdout.measured})",
        "",
    ]
    lines += [f"- {problem}" for problem in holdout.problems]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Gate link-cut routing rows on the per-rule matrix.")
    ap.add_argument("--design", required=True, type=Path, help="merged design matrix JSON")
    ap.add_argument("--holdout", required=True, type=Path, help="merged held-out matrix JSON")
    ap.add_argument("--table", required=True, choices=["shipped", "candidates"])
    ap.add_argument("--out", required=True, type=Path, help="markdown verdicts to write")
    ap.add_argument("--report-only", action="store_true", help="exit 0 even when a table fails")
    args = ap.parse_args(argv)

    design_card = json.loads(args.design.read_text(encoding="utf-8"))
    holdout_card = json.loads(args.holdout.read_text(encoding="utf-8"))
    if design_card["meta"].get("git_sha") != holdout_card["meta"].get("git_sha"):
        print("cut-gate: the design and held-out matrices come from different commits", file=sys.stderr)
        return 2

    entries = tables(args.table)
    out = [
        f"## Link-cut routing gate ({args.table})",
        "",
        f"A routed rule must score at least default - {TOLERANCE} on every design and every "
        "held-out dataset (labelled-pairs F1 on Magellan). Held-out verdicts show counts only.",
        "",
    ]
    all_passed = True
    for label, rows in entries:
        check_routed = args.table == "shipped"
        design = gate_set(design_card, ci_datasets("design"), rows, check_routed=check_routed)
        holdout = gate_set(holdout_card, ci_datasets("holdout"), rows, check_routed=check_routed)
        all_passed = all_passed and design.passed and holdout.passed
        out.append(render(label, design, holdout))
        print(
            f"[cut-gate] {label}: design {_word(design.passed)}, held-out {_word(holdout.passed)}",
            file=sys.stderr,
        )
    if not entries:
        out.append("No candidate rows.\n")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(out), encoding="utf-8")
    return 0 if all_passed or args.report_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the gate tests and the sweep suite**

Run: `PYTHONPATH="D:/Temp/gm-p4;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider scripts/autoconfig_quality/tests/test_cut_gate.py scripts/autoconfig_quality/tests/test_rule_sweep.py`

Expected: all pass.

- [ ] **Step 6: Lint and commit**

```bash
$PY -m ruff check scripts/autoconfig_quality/cut_rows.py scripts/autoconfig_quality/cut_gate.py scripts/autoconfig_quality/tests/test_cut_gate.py
git add scripts/autoconfig_quality/cut_rows.py scripts/autoconfig_quality/cut_gate.py scripts/autoconfig_quality/tests/test_cut_gate.py
git commit -m "feat(quality): blind link-cut routing gate and the two candidate rows"
```

---

### Task 6: CI wiring, the draft PR, and the blind candidate gate

Steps 1–4 are implementer work. Steps 5–9 are **controller** work: they push to a shared remote, open the PR, and read CI artifacts under the blindness rules.

**Files:**
- Modify: `.github/workflows/fs-cut-rule-matrix.yml`, `.github/workflows/fs-cut-rule-matrix-unit.yml`.
- Create (outside the repo): `D:/Temp/claude/pr-fs-cut-rule-rows-body.md`.

**Interfaces:**
- Consumes: `python -m scripts.autoconfig_quality.cut_gate` (Task 5); the merge job's `cut-rule-matrix.json` / `cut-rule-matrix-holdout.json`.
- Produces: `cut-rule-gate-shipped.md` and `cut-rule-gate-candidates.md` in the `cut-rule-matrix` artifact and the step summary; the draft PR number; the candidate verdicts in the SDD ledger.

- [ ] **Step 1: Trigger the matrix on the gate code**

In `.github/workflows/fs-cut-rule-matrix.yml`, replace the `on.pull_request.paths` list with:

```yaml
    paths:
      - "scripts/autoconfig_quality/rule_sweep.py"
      - "scripts/autoconfig_quality/corpus.py"
      - "scripts/autoconfig_quality/cut_gate.py"
      - "scripts/autoconfig_quality/cut_rows.py"
      - "packages/python/goldenmatch/goldenmatch/core/fs_cut_rules.py"
      - ".github/workflows/fs-cut-rule-matrix.yml"
```

In the header comment, replace the line `# Measurement only: it chooses no rule and gates nothing. NOT in ci-required.` with:

```yaml
# P4: the merge job also gates routing rows (scripts/autoconfig_quality/cut_gate.py) --
# the shipped table must pass, the candidate rows are reported. NOT in ci-required.
```

- [ ] **Step 2: Add the gate steps to the merge job**

In the `merge` job, insert these steps directly after the `Merge and summarize` step and before `Upload the merged matrix`:

```yaml
      - name: Gate the candidate routing rows (report only)
        if: always() && hashFiles('cut-rule-matrix.json') != '' && hashFiles('cut-rule-matrix-holdout.json') != ''
        run: |
          uv run python -m scripts.autoconfig_quality.cut_gate \
            --design cut-rule-matrix.json --holdout cut-rule-matrix-holdout.json \
            --table candidates --out cut-rule-gate-candidates.md --report-only
      - name: Gate the shipped routing table
        if: always() && hashFiles('cut-rule-matrix.json') != '' && hashFiles('cut-rule-matrix-holdout.json') != ''
        run: |
          uv run python -m scripts.autoconfig_quality.cut_gate \
            --design cut-rule-matrix.json --holdout cut-rule-matrix-holdout.json \
            --table shipped --out cut-rule-gate-shipped.md
      - name: Summarize the gate
        if: always()
        run: |
          for f in cut-rule-gate-shipped.md cut-rule-gate-candidates.md; do
            if [ -f "$f" ]; then cat "$f" >> "$GITHUB_STEP_SUMMARY"; fi
          done
```

In the `Upload the merged matrix` step, replace its `path:` block with:

```yaml
          path: |
            cut-rule-matrix.json
            cut-rule-matrix.md
            cut-rule-gate-shipped.md
            cut-rule-gate-candidates.md
```

- [ ] **Step 3: Run the gate tests in the unit workflow**

In `.github/workflows/fs-cut-rule-matrix-unit.yml`, add this line to the pytest command directly after `scripts/autoconfig_quality/tests/test_rule_sweep.py`:

```yaml
          scripts/autoconfig_quality/tests/test_cut_gate.py
```

Its paths already cover `scripts/autoconfig_quality/**`.

- [ ] **Step 4: Validate the workflows and commit**

```bash
PYTHONPATH="D:/Temp/gm-p4;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider scripts/test_workflow_yaml.py
zizmor .github/workflows/fs-cut-rule-matrix.yml .github/workflows/fs-cut-rule-matrix-unit.yml
git add .github/workflows/fs-cut-rule-matrix.yml .github/workflows/fs-cut-rule-matrix-unit.yml
git commit -m "ci(quality): gate the link-cut routing rows in the matrix merge job"
```

Expected: the workflow YAML test passes, and zizmor reports no new findings (suppressed findings as before).

- [ ] **Step 5 (controller): Write the PR body**

Write `D:/Temp/claude/pr-fs-cut-rule-rows-body.md`:

```markdown
Stacked on #2943 (P3). This PR is **P4** of the FS link-cut routing design (`docs/superpowers/specs/2026-09-11-fs-cut-rule-routing-design.md`): one link-cut resolver, an opt-in router, and the gate that decides which routing rows ship.

The router is off by default (`GOLDENMATCH_FS_CUT_ROUTER`). With it off, every cutoff and every report is unchanged, pinned by tests.

## What changes

- **One resolver.** `resolve_link_cut` is the only copy of the link-cut precedence. `_fs_link_threshold`, `resolve_thresholds`, `link_threshold_source` and `link_cut_report` read it; each used to carry its own copy.
- **Router.** `fs_cut_rules.choose_cut_rule` walks an ordered table of `CutRow`s over the trained model's cut diagnostics.
  - The first match wins, and no match keeps the default rule.
  - It sits behind `GOLDENMATCH_FS_CUT_ROUTER=on`.
  - A pinned `link_cut_rule`, an explicit `link_threshold`, a calibrated cutoff and posterior scoring all still win.
  - A routed cut reports `cut_reason` `routed by <row>: <reason>`.
- **Labelled-pairs metric for Magellan.** Walmart-Amazon, iTunes-Amazon and Fodors-Zagats gate on F1 over labelled candidate pairs only. Their truth covers only labelled positives, which biased plain F1 toward high cuts.
- **Routed arm.** Every matrix dataset gains a `routed` arm with the router on. Its partition must equal the partition of the arm pinning the rule it routed to, or the sweep fails.
- **Gate.** `scripts/autoconfig_quality/cut_gate.py` routes each dataset from its recorded diagnostics.
  - The routed rule must score at least default − 0.01 on every design and every held-out dataset.
  - Design verdicts are shown in full.
  - The held-out verdict is PASS/FAIL plus a fired count, with no dataset names, rules or scores.

## Candidate rows

Both were written from the design matrix only (run 34638483397), before any held-out result existed:

| row | condition | rule | design evidence |
|---|---|---|---|
| `sparse_prior_binds` | λ < 0.004, prior cutoff above the midpoint, at most 3 fields | `posterior_099` | dblp_acm +0.0918, synth_biblio_d02 +0.0000 |
| `midpoint_binds_wide` | λ < 0.1, midpoint cutoff above the prior, at least 5 fields | `evidence_5` | musicbrainz_20k +0.2673, febrl3 +0.004, febrl4 +0.0002 |

The `n_fields` bounds exclude synth_person_d02 and dblp_scholar on the design set, so only the held-out set tests them.

## Gate results

Pending the first P4 matrix run.

## Deliberate scope notes, recorded in the spec

- `choose_cut_rule` returns None when no row matches, instead of carrying a literal default last row. The default is set process-wide by `GOLDENMATCH_FS_LINEAR_CUT`.
- The rows ship behind a default-off env, so `quality_gate`, `bench-suggest-quality` and `bench-quality-scale` measure the unchanged default. Running them with the router on is a P5 criterion.
```

- [ ] **Step 6 (controller): Push and open the draft PR**

```bash
export GH_TOKEN=$(gh auth token -u benzsevern)
git -C D:/Temp/gm-p4 push -u origin feat/fs-cut-rule-rows
gh pr create --repo benseverndev-oss/goldenmatch --draft \
  --base feat/fs-cut-rule-matrix --head feat/fs-cut-rule-rows \
  --title "feat(fs): link-cut routing P4 -- one resolver, a gated router, labelled Magellan metric" \
  --body-file D:/Temp/claude/pr-fs-cut-rule-rows-body.md
```

Expected: a PR URL. Do not arm it, and never pass `--auto`.

- [ ] **Step 7 (controller): Wait for the matrix run**

```bash
export GH_TOKEN=$(gh auth token -u benzsevern)
gh run list --repo benseverndev-oss/goldenmatch --workflow fs-cut-rule-matrix.yml --branch feat/fs-cut-rule-rows --limit 1 --json databaseId,status,headSha
```

Expected: `headSha` equals `git -C D:/Temp/gm-p4 rev-parse HEAD`. Wait for completion with the Monitor tool: an until-loop polling `gh run view <id> --repo benseverndev-oss/goldenmatch --json status -q .status` every 300 s, not short sleeps. If a job fails for infrastructure reasons (artifact 429, runner loss), run `gh run rerun <id> --failed` and wait again. A test or sweep failure is a defect to fix, not to rerun.

- [ ] **Step 8 (controller): Download the design artifact only and read the verdicts**

```bash
export GH_TOKEN=$(gh auth token -u benzsevern)
D=D:/Temp/claude/D--show-case-goldenmatch/f06cca1e-8ed2-49d0-80cd-d9158c1968c4/scratchpad/matrix-p4-<run id>
gh run download <run id> --repo benseverndev-oss/goldenmatch -n cut-rule-matrix -D "$D/design"
```

Read only these three files:
- `$D/design/cut-rule-matrix.md`
- `$D/design/cut-rule-gate-shipped.md`
- `$D/design/cut-rule-gate-candidates.md`

Never download `cut-rule-matrix-holdout`.

The run is valid when all of these hold:
- the design matrix lists every design dataset, with no `MISSING` rows and no **Failures** section;
- the shipped gate shows `Design: PASS` and `Held-out: PASS` for `shipped table (no rows)`, which proves every routed arm reproduced its baseline;
- the candidates file has one section per candidate.

An invalid run is diagnosed and fixed first (superpowers:systematic-debugging), then re-run from Step 7.

- [ ] **Step 9 (controller): Record the verdicts**

Append to the SDD ledger one line per candidate: `candidate <name>: design PASS|FAIL (fired N of M), held-out PASS|FAIL (fired N of M), run <id>`. Copy each candidate's design table verbatim into the ledger for Task 7. These verdicts are final: no candidate is edited and re-gated.

---

### Task 7: Ship only the rows that passed

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/fs_cut_rules.py` (`ROWS`).
- Modify: `scripts/autoconfig_quality/cut_rows.py` (`CANDIDATES`).
- Modify: `scripts/autoconfig_quality/tests/test_cut_gate.py` (the candidates test).
- Modify: `packages/python/goldenmatch/tests/test_fs_cut_router.py` (shipped-row tests).
- Modify: `docs-site/goldenmatch/tuning.mdx`, `packages/python/goldenmatch/CHANGELOG.md`.

**Interfaces:**
- Consumes: the Task 6 ledger verdicts. A row **passed** only when its section reads `Design: PASS` AND `Held-out: PASS`.
- Produces: `fs_cut_rules.ROWS` containing exactly the passed rows, in `CANDIDATES` order; `CANDIDATES == ()`.

- [ ] **Step 1: Move the passed rows verbatim**

For each passed row, cut its `CutRow(...)` expression unchanged from `CANDIDATES` in `scripts/autoconfig_quality/cut_rows.py` and paste it into `ROWS` in `fs_cut_rules.py`, keeping `CANDIDATES` order. Delete every failed row from `CANDIDATES`. When no candidate remains, `cut_rows.py` ends with `CANDIDATES: tuple[CutRow, ...] = ()`.

Above each shipped row, write one comment line in this exact format, filled from that row's design table in the ledger:

```python
    # Gated on matrix run <run id>: design PASS (<dataset> <Δ>, ...), held-out PASS.
```

With both rows passed, `ROWS` reads:

```python
ROWS: tuple[CutRow, ...] = (
    # Gated on matrix run <run id>: design PASS (<dataset> <Δ>, ...), held-out PASS.
    CutRow(
        name="sparse_prior_binds",
        rule="posterior_099",
        reason="match rate under 0.004, the prior cutoff above the midpoint, at most 3 fields",
        when=lambda d: (
            d.proportion_matched < 0.004 and d.prior_bits > d.midpoint_bits and d.n_fields <= 3
        ),
    ),
    # Gated on matrix run <run id>: design PASS (<dataset> <Δ>, ...), held-out PASS.
    CutRow(
        name="midpoint_binds_wide",
        rule="evidence_5",
        reason="match rate under 0.1, the midpoint cutoff above the prior, at least 5 fields",
        when=lambda d: (
            d.proportion_matched < 0.1 and d.midpoint_bits > d.prior_bits and d.n_fields >= 5
        ),
    ),
)
```

If no row passed, `ROWS` stays `()`, and Steps 2 and 4 use the "none passed" variants.

- [ ] **Step 2: Move the candidate test onto the shipped table**

In `scripts/autoconfig_quality/tests/test_cut_gate.py`, delete `_DESIGN_SHAPES` and `test_candidates_fire_where_the_design_matrix_says`, and append:

```python
def test_no_candidate_is_already_shipped():
    shipped = {row.name for row in fs_cut_rules.ROWS}
    assert not shipped & {row.name for row in cut_rows.CANDIDATES}
```

If at least one row passed, append to `packages/python/goldenmatch/tests/test_fs_cut_router.py`:

```python
#: (λ, midpoint bits, prior bits, n_fields) recorded by design matrix run 34638483397.
_DESIGN_SHAPES = {
    "dblp_acm": (0.00196, 2.87, 8.99, 3),
    "synth_biblio_d02": (0.0005, 5.39, 10.97, 3),
    "synth_person_d02": (0.00185, 0.0, 9.08, 5),
    "musicbrainz_20k": (0.0757, 17.3, 3.61, 5),
    "febrl3": (0.0826, 10.5, 3.47, 9),
    "febrl4": (0.042, 13.96, 4.51, 9),
    "dblp_scholar": (0.0646, 22.23, 3.86, 3),
    "historical_50k": (0.661, 10.82, -0.96, 6),
}


def test_the_shipped_rows_fire_where_the_design_matrix_says():
    """Known positives: removing a shipped row turns its datasets back to None and fails this."""
    routed = {}
    for name, (lam, mid, prior, n_fields) in _DESIGN_SHAPES.items():
        choice = R.choose_cut_rule(
            _diag(proportion_matched=lam, midpoint_bits=mid, prior_bits=prior, n_fields=n_fields)
        )
        routed[name] = None if choice is None else choice[0]
    assert routed == _EXPECTED_ROUTES
```

Then define `_EXPECTED_ROUTES` directly above that test, choosing the variant that matches the shipped rows:

```python
# Both rows shipped:
_EXPECTED_ROUTES = {
    "dblp_acm": "posterior_099", "synth_biblio_d02": "posterior_099", "synth_person_d02": None,
    "musicbrainz_20k": "evidence_5", "febrl3": "evidence_5", "febrl4": "evidence_5",
    "dblp_scholar": None, "historical_50k": None,
}
# Only sparse_prior_binds shipped:
_EXPECTED_ROUTES = {
    "dblp_acm": "posterior_099", "synth_biblio_d02": "posterior_099", "synth_person_d02": None,
    "musicbrainz_20k": None, "febrl3": None, "febrl4": None,
    "dblp_scholar": None, "historical_50k": None,
}
# Only midpoint_binds_wide shipped:
_EXPECTED_ROUTES = {
    "dblp_acm": None, "synth_biblio_d02": None, "synth_person_d02": None,
    "musicbrainz_20k": "evidence_5", "febrl3": "evidence_5", "febrl4": "evidence_5",
    "dblp_scholar": None, "historical_50k": None,
}
```

Keep exactly one `_EXPECTED_ROUTES` definition. If no row passed, add neither `_DESIGN_SHAPES` nor this test to `test_fs_cut_router.py`.

- [ ] **Step 3: Run the router, gate and sweep suites**

Run:
```bash
PYTHONPATH="D:/Temp/gm-p4;$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider \
  packages/python/goldenmatch/tests/test_fs_cut_router.py \
  packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py \
  scripts/autoconfig_quality/tests/test_cut_gate.py \
  scripts/autoconfig_quality/tests/test_rule_sweep.py
```

Expected: all pass. `test_every_shipped_row_is_a_uniquely_named_cut_row` now checks the real table. `test_the_router_is_off_by_default` still passes because it patches `ROWS`.

- [ ] **Step 4: Update the docs**

In the `GOLDENMATCH_FS_CUT_ROUTER` row of `docs-site/goldenmatch/tuning.mdx`, replace the sentence `No row has passed the gate yet, so turning it on changes nothing.` with the variant that matches the shipped rows. List only shipped rows, first match first:
- **Both rows shipped:** `Shipped rows, first match wins: \`sparse_prior_binds\` (λ < 0.004, prior cutoff above the midpoint, at most 3 fields → \`posterior_099\`); \`midpoint_binds_wide\` (λ < 0.1, midpoint cutoff above the prior, at least 5 fields → \`evidence_5\`).`
- **One row shipped:** the same sentence with only that row's entry.
- **None shipped:** leave the sentence as it is.

In the CHANGELOG router bullet, replace `No row ships yet: a row reaches the table only by passing the design and held-out link-cut gate.` with `It ships with <N> row(s) that passed the design and held-out link-cut gate: <names>.` using the real count and names. With none shipped, leave the sentence as it is.

- [ ] **Step 5: Lint and commit**

```bash
PYTHONPATH="D:/Temp/gm-p4;$PP" PYTHONIOENCODING=utf-8 $PY scripts/regen_docs.py --check; echo "exit=$?"
$PY -m ruff check packages/python/goldenmatch/goldenmatch/core/fs_cut_rules.py scripts/autoconfig_quality/cut_rows.py scripts/autoconfig_quality/tests/test_cut_gate.py packages/python/goldenmatch/tests/test_fs_cut_router.py
git add packages/python/goldenmatch/goldenmatch/core/fs_cut_rules.py scripts/autoconfig_quality/cut_rows.py scripts/autoconfig_quality/tests/test_cut_gate.py packages/python/goldenmatch/tests/test_fs_cut_router.py docs-site/goldenmatch/tuning.mdx packages/python/goldenmatch/CHANGELOG.md
git commit -m "feat(fs): ship the link-cut routing rows that passed the design and held-out gate"
```

With no row shipped, the commit message is `chore(quality): drop the link-cut candidate rows that failed the gate`.

- [ ] **Step 6 (controller): Confirm the shipped table end to end**

Skip this step when no row shipped, because Task 6's run already confirmed the empty table.

Otherwise:
1. Push (`git -C D:/Temp/gm-p4 push`). The `fs_cut_rules.py` change triggers the matrix.
2. Wait and download exactly as in Task 6, Steps 7–8.
3. Read `cut-rule-gate-shipped.md`. It must show `Design: PASS` and `Held-out: PASS` for the shipped table, and the design matrix must show no **Failures**. A routed-arm disagreement would appear in both places.

If the shipped table fails:
- Revert the shipping commit with `git revert --no-edit HEAD`, push, and record the failure.
- A routed-arm disagreement ("picked differently" / "partition differs") is a defect in how the router or the report reads diagnostics. Debug it; do not tune a row.
- A plain score failure means the row is dropped.

- [ ] **Step 7 (controller): Update the PR body and memory**

1. In `D:/Temp/claude/pr-fs-cut-rule-rows-body.md`, replace `Pending the first P4 matrix run.` with:
   - one line per candidate from the ledger, in the format `` `name`: design PASS|FAIL (fired N of M), held-out PASS|FAIL (fired N of M) ``;
   - the run links;
   - which rows shipped.

   No held-out dataset names, rules or scores appear.
2. Apply the body with `gh pr edit <PR> --repo benseverndev-oss/goldenmatch --body-file D:/Temp/claude/pr-fs-cut-rule-rows-body.md`. The PR stays a draft and unarmed.
3. Update `C:\Users\bsevern\.claude\projects\D--show-case-goldenmatch\memory\project_fs_method_routing.md` with the P4 outcome: PR number, run ids, the verdicts, and the shipped rows.
