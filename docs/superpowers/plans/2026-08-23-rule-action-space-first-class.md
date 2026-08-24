# Rule Action Space as a First-Class Surface — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make it impossible to add a RED health verdict that no auto-config rule can act on, and give every rule a uniform way to check whether its own last action worked.

**Architecture:** Three additions, each independently useful. (1) Each RED-capable sub-profile names its RED condition with a stable slug via `red_reason()`, and `health()` derives from it so the two cannot drift. (2) Rules declare which slugs they answer with a `@targets(...)` decorator, and a CI gate asserts every reachable slug is claimed. (3) `PolicyDecision` gains a `predicts` field naming the profile metric the rule expects to move, and a generic `rule_effect_was_negative()` helper replaces the bespoke history check that `rule_low_transitivity` currently hand-rolls.

**Tech Stack:** Python 3.11+, pytest, pydantic v2 (config schemas), dataclasses (profiles/history). No new dependencies.

**Spec:** No separate spec document — the argument and its evidence are inline under "Why this exists" below, and the measurements are reproducible with the commands given there. If a standalone spec is wanted later, extract that section.

## Global Constraints

- **Default-off / behaviour-preserving where possible.** New `PolicyDecision` fields default to empty, so all 17 existing rules keep working unchanged until decorated.
- **`quality_gate` is the arbiter for any behaviour change.** It has caught default flips in this repo before (see `project_fs_threshold_calibration`). Tasks 4 and 5 add rules that can fire on real data and MUST be measured against it.
- **`DEFAULT_RULES` is a module-level list holding function references.** Rebinding a module attribute does NOT change the list entry. Any test that swaps a rule must patch `DEFAULT_RULES[i]` and assert the swap took by reading the emitted `rule_name`.
- **Never lower a floor or widen an allowlist to make a lane green.** The `_UNCOVERED` allowlist in Task 3 shrinks only; Tasks 4 and 5 each remove exactly one entry.
- **Run derived-doc regeneration as the LAST step before every push:** `python scripts/regen_docs.py`. A new Python symbol without it reds `config_matrix` + `docs_regen`.
- Tests run with `PYTHONPATH` at `packages/python/goldenmatch` and `GOLDENMATCH_AUTOCONFIG_MEMORY=0`.

---

## Why this exists

Measured on this repo, 2026-08-23:

```
7 sub-profiles compute a health verdict
17 rules in DEFAULT_RULES can respond
they act on 3 config surfaces: blocking.*, matchkeys[0].threshold, cluster.split_weak_bridges
```

Reproduce the second and third numbers:

```bash
rg -c "^def rule_" packages/python/goldenmatch/goldenmatch/core/autoconfig_rules.py
rg -o 'config_diff=\{"[a-z_.\[\]0-9]+' \
   packages/python/goldenmatch/goldenmatch/core/autoconfig_rules.py \
   | sed 's/.*config_diff={"//' | sort -u
```

The asymmetry is not theoretical. `DataProfile.health`'s own docstring records
the failure mode:

> v23 telemetry (#577) showed this signal stayed YELLOW for all 5 controller
> iterations with no rule addressing it because the verdict isn't actionable

and #2717 hit it three times in one issue: a runtime warning about concatenated
sources with no lever, a RED cluster verdict with no cluster action at all, and
`rule_low_transitivity` walking a threshold that provably cannot move
transitivity in either direction (measured both ways; it falls either way,
because removing an edge from a still-connected cluster leaves more open
triples).

**Two coverage holes exist right now, both verified:**

| RED trigger | where | rule that answers it |
|---|---|---|
| `cluster_size_max > 0.1 * n_rows` | `ClusterProfile.health` | **none** |
| a matchkey field with `post_transform_cardinality_ratio == 0.0` | `MatchkeyProfile.health` | **none** |

`rule_low_transitivity` is the only rule that reads `profile.cluster`, and it
returns `None` unless `transitivity_rate < 0.85` — so a giant-cluster RED with
healthy transitivity produces no proposal. Both rules that read
`profile.matchkey.per_field` (`rule_unimodal_scoring`,
`rule_matchkey_demote_high_cardinality_field`) sort by *highest* cardinality;
neither handles the zero end.

---

## File structure

| File | Responsibility | Change |
|---|---|---|
| `goldenmatch/core/complexity_profile.py` | Sub-profile health verdicts | Add `red_reason()` to the 5 RED-capable profiles; `health()` derives from it |
| `goldenmatch/core/autoconfig_history.py` | `PolicyDecision` audit record | Add `targets`, `predicts`, `predicts_direction`, `observed_delta` |
| `goldenmatch/core/autoconfig_rules.py` | The rules themselves | Add `@targets` decorator, decorate 17 rules, add 2 new rules, migrate `rule_low_transitivity` |
| `goldenmatch/core/autoconfig_policy.py` | Rule dispatch | Stamp `targets` from the rule onto the decision |
| `goldenmatch/core/autoconfig_controller.py` | Iteration loop | Record `observed_delta` after the next iteration |
| `tests/test_rule_action_coverage.py` | **new** — the gate | Every reachable RED slug is claimed |
| `tests/test_rule_effect_feedback.py` | **new** | `predicts` / `rule_effect_was_negative` |
| `tests/test_cluster_giant_rule.py` | **new** | Task 4's rule |
| `tests/test_matchkey_collapsed_rule.py` | **new** | Task 5's rule |

---

### Task 1: Name every RED condition

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/complexity_profile.py`
- Test: `packages/python/goldenmatch/tests/test_red_reason.py` (create)

**Interfaces:**
- Produces: `DataProfile.red_reason() -> str | None`, `BlockingProfile.red_reason(n_rows: int) -> str | None`, `ScoringProfile.red_reason() -> str | None`, `MatchkeyProfile.red_reason(n_full_rows: int | None = None) -> str | None`, `ClusterProfile.red_reason(n_rows: int) -> str | None`. Module constant `RED_REASONS: frozenset[str]` listing every slug any profile can return.

- [ ] **Step 1: Write the failing test**

```python
"""Every RED verdict names its condition, and the name cannot drift from the verdict."""
from goldenmatch.core.complexity_profile import (
    RED_REASONS,
    ClusterProfile,
    HealthVerdict,
    MatchkeyProfile,
)


def test_cluster_giant_and_low_transitivity_are_distinct_reasons():
    giant = ClusterProfile(n_clusters=3, cluster_size_max=60, transitivity_rate=1.0)
    chained = ClusterProfile(n_clusters=50, cluster_size_max=4, transitivity_rate=0.2)
    assert giant.red_reason(n_rows=100) == "cluster_giant"
    assert chained.red_reason(n_rows=100) == "cluster_low_transitivity"


def test_red_reason_agrees_with_health():
    """The two must not drift: a reason implies RED, and RED implies a reason."""
    for profile, kwargs in [
        (ClusterProfile(n_clusters=3, cluster_size_max=60, transitivity_rate=1.0), {"n_rows": 100}),
        (ClusterProfile(n_clusters=9, cluster_size_max=2, transitivity_rate=1.0), {"n_rows": 100}),
    ]:
        is_red = profile.health(**kwargs) == HealthVerdict.RED
        assert (profile.red_reason(**kwargs) is not None) == is_red


def test_every_reason_is_registered():
    giant = ClusterProfile(n_clusters=3, cluster_size_max=60, transitivity_rate=1.0)
    assert giant.red_reason(n_rows=100) in RED_REASONS
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest packages/python/goldenmatch/tests/test_red_reason.py -q`
Expected: FAIL — `ImportError: cannot import name 'RED_REASONS'`

- [ ] **Step 3: Add `red_reason` to `ClusterProfile` and make `health` derive from it**

In `complexity_profile.py`, replace `ClusterProfile.health` with:

```python
    def red_reason(self, n_rows: int) -> str | None:
        """The named RED condition, or None. Single source of truth for
        `health`, so a new RED branch cannot be added without naming it --
        and the coverage gate keys on the name (see
        tests/test_rule_action_coverage.py)."""
        if n_rows > 0 and self.cluster_size_max > 0.1 * n_rows:
            return "cluster_giant"
        if self.transitivity_rate < 0.85:
            return "cluster_low_transitivity"
        return None

    def health(self, n_rows: int) -> HealthVerdict:
        if self.red_reason(n_rows) is not None:
            return HealthVerdict.RED
        if self.oversized_cluster_count > 0:
            return HealthVerdict.YELLOW
        return HealthVerdict.GREEN
```

- [ ] **Step 4: Run the test — cluster cases pass**

Run: `python -m pytest packages/python/goldenmatch/tests/test_red_reason.py -q`
Expected: still FAIL on `RED_REASONS` import.

- [ ] **Step 5: Repeat the same split for the other four profiles and register the slugs**

Apply the identical `red_reason` / `health` split to `DataProfile`
(`data_empty`), `BlockingProfile` (`blocking_no_blocks` plus each existing RED
branch), `ScoringProfile` (`scoring_no_candidates`, `scoring_nothing_above_threshold`),
and `MatchkeyProfile` (`matchkey_collapsed_field`). Then, at module level:

```python
#: Every RED condition any sub-profile can report. The coverage gate in
#: tests/test_rule_action_coverage.py asserts each of these is claimed by at
#: least one rule, so adding a RED branch without an action fails CI.
RED_REASONS: frozenset[str] = frozenset({
    "data_empty",
    "blocking_no_blocks",
    "scoring_no_candidates",
    "scoring_nothing_above_threshold",
    "matchkey_collapsed_field",
    "cluster_giant",
    "cluster_low_transitivity",
})
```

- [ ] **Step 6: Run the full profile + controller suites**

Run:
```bash
python -m pytest packages/python/goldenmatch/tests/test_red_reason.py \
  packages/python/goldenmatch/tests/test_complexity_profile.py \
  packages/python/goldenmatch/tests/test_autoconfig_controller.py -q
```
Expected: PASS. `health()` behaviour is unchanged — only its implementation moved.

- [ ] **Step 7: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/complexity_profile.py \
        packages/python/goldenmatch/tests/test_red_reason.py
git commit -m "refactor(profile): name every RED condition, derive health from it"
```

---

### Task 2: Rules declare what they answer

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_history.py`
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_rules.py`
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_policy.py`
- Test: `packages/python/goldenmatch/tests/test_rule_targets.py` (create)

**Interfaces:**
- Consumes: `RED_REASONS` from Task 1.
- Produces: decorator `targets(*reasons: str)` in `autoconfig_rules`, setting `fn.targets: tuple[str, ...]`. `PolicyDecision.targets: tuple[str, ...] = ()`, stamped by `HeuristicRefitPolicy.propose`.

- [ ] **Step 1: Write the failing test**

```python
from goldenmatch.core.autoconfig_rules import DEFAULT_RULES, targets


def test_decorator_records_the_reasons_on_the_function():
    @targets("cluster_giant")
    def rule_stub(profile, current, history):
        return None

    assert rule_stub.targets == ("cluster_giant",)


def test_every_default_rule_declares_its_targets():
    """A rule that answers nothing named is the accident this removes."""
    undeclared = [r.__name__ for r in DEFAULT_RULES if not getattr(r, "targets", ())]
    assert undeclared == [], f"rules with no declared target: {undeclared}"


def test_declared_targets_are_real_reasons():
    from goldenmatch.core.complexity_profile import RED_REASONS

    for rule in DEFAULT_RULES:
        for reason in getattr(rule, "targets", ()):
            assert reason in RED_REASONS, f"{rule.__name__} targets unknown {reason!r}"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest packages/python/goldenmatch/tests/test_rule_targets.py -q`
Expected: FAIL — `ImportError: cannot import name 'targets'`

- [ ] **Step 3: Add the decorator and the `PolicyDecision` field**

In `autoconfig_rules.py`, above the first rule:

```python
def targets(*reasons: str):
    """Declare which RED conditions (see `complexity_profile.RED_REASONS`) this
    rule answers.

    The point is coverage, not documentation: `test_rule_action_coverage.py`
    asserts every reachable RED reason is claimed by at least one rule, so a new
    RED branch without an action fails CI rather than producing a verdict the
    controller can only report. A rule may answer several.
    """
    def _decorate(fn):
        fn.targets = reasons
        return fn
    return _decorate
```

In `autoconfig_history.py`, add to `PolicyDecision`:

```python
    #: RED conditions this rule declares it answers (from the `@targets`
    #: decorator, stamped by the policy so rules never repeat themselves).
    targets: tuple[str, ...] = ()
```

- [ ] **Step 4: Stamp it in the policy**

In `autoconfig_policy.py`, inside `propose`, immediately after `new_config, decision = outcome`:

```python
            # Stamp the rule's declared targets onto its decision so the audit
            # trail records what the action was meant to fix, not just what it
            # changed. Rules do not repeat themselves -- the decorator is the
            # single source.
            declared = getattr(rule, "targets", ())
            if declared and not decision.targets:
                decision.targets = declared
```

- [ ] **Step 5: Decorate all 17 rules**

Add one `@targets(...)` line above each rule in `DEFAULT_RULES`, mapping it to the RED reason it answers. Use this mapping (derived from which sub-profile each rule reads and acts on):

```
rule_blocking_field_null_heavy          -> blocking_no_blocks
rule_blocking_singleton_trap            -> scoring_no_candidates
rule_blocking_key_swap                  -> scoring_nothing_above_threshold
rule_blocking_adaptive_on_p99_outlier   -> blocking_no_blocks
rule_blocking_too_coarse                -> blocking_no_blocks
rule_uniform_heavy_blocking             -> blocking_no_blocks
rule_corruption_normalize               -> scoring_nothing_above_threshold
rule_unimodal_scoring                   -> scoring_nothing_above_threshold
rule_low_reduction_ratio                -> blocking_no_blocks
rule_cross_blocking_disagreement        -> scoring_no_candidates
rule_low_transitivity                   -> cluster_low_transitivity
rule_no_matches                         -> scoring_nothing_above_threshold
rule_matchkey_demote_high_cardinality_field -> matchkey_collapsed_field
rule_select_probabilistic_matchkey      -> scoring_nothing_above_threshold
rule_recall_gap_suspected               -> scoring_no_candidates
rule_precision_anchor_threshold_raise   -> scoring_nothing_above_threshold
rule_sparse_match_expand                -> scoring_no_candidates
```

- [ ] **Step 6: Run the test**

Run: `python -m pytest packages/python/goldenmatch/tests/test_rule_targets.py packages/python/goldenmatch/tests/test_autoconfig_policy.py -q`
Expected: PASS.

- [ ] **Step 7: Regenerate derived docs and commit**

```bash
python scripts/regen_docs.py
git add -A
git commit -m "feat(autoconfig): rules declare which RED condition they answer"
```

---

### Task 3: The coverage gate

**Files:**
- Test: `packages/python/goldenmatch/tests/test_rule_action_coverage.py` (create)

**Interfaces:**
- Consumes: `RED_REASONS` (Task 1), `DEFAULT_RULES` + `rule.targets` (Task 2).
- Produces: module constant `_UNCOVERED` in the test file, which Tasks 4 and 5 each shrink by one.

- [ ] **Step 1: Write the gate, with the two known holes named**

```python
"""Every RED verdict the controller can reach must have a rule that answers it.

Seven sub-profiles compute a health verdict; the rules act on three config
surfaces. That asymmetry is how a run reaches RED with nothing to do about it --
`DataProfile.health`'s own docstring records a signal that "stayed YELLOW for
all 5 controller iterations with no rule addressing it", and #2717 hit the same
shape three times.

This gate makes the gap fail CI instead of surfacing as a bad benchmark months
later.
"""
from goldenmatch.core.autoconfig_rules import DEFAULT_RULES
from goldenmatch.core.complexity_profile import RED_REASONS

#: RED conditions with no rule yet. SHRINKS ONLY -- adding an entry to make a
#: red gate green defeats the point. Each is a measured hole, not a hypothetical:
#:
#: - cluster_giant: `rule_low_transitivity` is the ONLY rule reading
#:   profile.cluster and it returns None unless transitivity < 0.85, so a giant
#:   cluster with healthy transitivity produces no proposal at all.
#: - matchkey_collapsed_field: both rules reading profile.matchkey.per_field
#:   sort by HIGHEST cardinality; neither handles a field collapsing to one value.
_UNCOVERED: frozenset[str] = frozenset({
    "cluster_giant",              # removed by Task 4
    "matchkey_collapsed_field",   # removed by Task 5
})


def _claimed() -> set[str]:
    return {r for rule in DEFAULT_RULES for r in getattr(rule, "targets", ())}


def test_every_red_reason_has_a_rule():
    missing = RED_REASONS - _claimed() - _UNCOVERED
    assert not missing, (
        f"RED conditions no rule can answer: {sorted(missing)}. Either add a "
        f"rule that targets it, or -- if it is genuinely unactionable -- remove "
        f"the RED branch, as DataProfile did with its uniform-types clause."
    )


def test_the_allowlist_only_shrinks():
    """An entry that is now covered must be deleted, not left to rot."""
    stale = _UNCOVERED & _claimed()
    assert not stale, (
        f"{sorted(stale)} are covered by a rule now -- delete them from _UNCOVERED"
    )


def test_the_allowlist_names_real_reasons():
    assert _UNCOVERED <= RED_REASONS
```

- [ ] **Step 2: Run it**

Run: `python -m pytest packages/python/goldenmatch/tests/test_rule_action_coverage.py -q`
Expected: PASS (3 passed) — the two holes are allowlisted with their evidence.

- [ ] **Step 3: Commit**

```bash
git add packages/python/goldenmatch/tests/test_rule_action_coverage.py
git commit -m "test(autoconfig): gate that every RED verdict has a rule that answers it"
```

---

### Task 4: A rule for the giant-cluster RED

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_rules.py`
- Modify: `packages/python/goldenmatch/tests/test_rule_action_coverage.py` (shrink `_UNCOVERED`)
- Test: `packages/python/goldenmatch/tests/test_cluster_giant_rule.py` (create)

**Interfaces:**
- Consumes: `targets` decorator (Task 2), `ClusterConfig` from `goldenmatch.config.schemas` (already exists — `split_weak_bridges`, `weak_bridge_margin`).
- Produces: `rule_cluster_giant(profile, current, history)` in `DEFAULT_RULES`, ordered immediately before `rule_low_transitivity`.

- [ ] **Step 1: Write the failing test**

```python
"""`cluster_size_max > 0.1 * n_rows` is RED and nothing answered it."""
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig, BlockingKeyConfig, ClusterConfig, GoldenMatchConfig,
    MatchkeyConfig, MatchkeyField,
)
from goldenmatch.core.autoconfig_history import RunHistory
from goldenmatch.core.autoconfig_rules import rule_cluster_giant
from goldenmatch.core.complexity_profile import (
    BlockingProfile, ClusterProfile, ComplexityProfile, DataProfile, ScoringProfile,
)


def _cfg(threshold: float = 0.7, cluster: ClusterConfig | None = None) -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="mk", type="weighted", threshold=threshold,
                                  fields=[MatchkeyField(field="name", scorer="token_sort",
                                                        weight=1.0)])],
        blocking=BlockingConfig(strategy="static",
                                keys=[BlockingKeyConfig(fields=["name"])]),
        cluster=cluster,
    )


def _profile(n_rows: int, cluster_size_max: int) -> ComplexityProfile:
    return ComplexityProfile(
        data=DataProfile(n_rows=n_rows, n_cols=3),
        blocking=BlockingProfile(n_blocks=20, reduction_ratio=0.9),
        scoring=ScoringProfile(n_pairs_scored=500, candidates_compared=5000,
                               candidates_counted=True, mass_above_threshold=1.0),
        cluster=ClusterProfile(n_clusters=10, cluster_size_max=cluster_size_max,
                               transitivity_rate=1.0),
    )


def test_fires_on_a_giant_cluster_and_asks_for_splitting():
    out = rule_cluster_giant(_profile(n_rows=1000, cluster_size_max=400), _cfg(), RunHistory())
    assert out is not None
    new_cfg, decision = out
    assert new_cfg.cluster is not None and new_cfg.cluster.split_weak_bridges is True
    assert decision.targets == ("cluster_giant",)


def test_does_not_fire_when_no_cluster_is_giant():
    assert rule_cluster_giant(_profile(n_rows=1000, cluster_size_max=40), _cfg(), RunHistory()) is None


def test_raises_the_threshold_once_splitting_is_already_on():
    """Splitting first because it is targeted; the threshold is the blunt fallback."""
    cfg = _cfg(cluster=ClusterConfig(split_weak_bridges=True))
    out = rule_cluster_giant(_profile(n_rows=1000, cluster_size_max=400), cfg, RunHistory())
    assert out is not None
    assert out[0].matchkeys[0].threshold == pytest.approx(0.75)


def test_stops_at_the_ceiling():
    cfg = _cfg(threshold=0.95, cluster=ClusterConfig(split_weak_bridges=True))
    assert rule_cluster_giant(_profile(n_rows=1000, cluster_size_max=400), cfg, RunHistory()) is None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest packages/python/goldenmatch/tests/test_cluster_giant_rule.py -q`
Expected: FAIL — `ImportError: cannot import name 'rule_cluster_giant'`

- [ ] **Step 3: Implement the rule**

In `autoconfig_rules.py`, immediately before `rule_low_transitivity`:

```python
#: A cluster this far above the ceiling is not a merge, it is a collapse.
_GIANT_CLUSTER_FRACTION = 0.1
_GIANT_THRESHOLD_STEP = 0.05
_GIANT_THRESHOLD_CEILING = 0.95


@targets("cluster_giant")
def rule_cluster_giant(
    profile: ComplexityProfile, current: GoldenMatchConfig, history: RunHistory
) -> tuple[GoldenMatchConfig, PolicyDecision] | None:
    """Answer `ClusterProfile.red_reason() == "cluster_giant"`.

    Before this, `rule_low_transitivity` was the ONLY rule reading
    profile.cluster and it returns None unless transitivity < 0.85 -- so a run
    where one cluster swallowed 10%+ of the data, with healthy transitivity,
    produced no proposal at all. The controller reported RED and did nothing.

    Splitting is tried FIRST because it targets the pathology: a giant cluster
    is usually distinct entities chained through weak bridges. Raising the
    threshold is the blunt fallback -- it also drops true pairs -- so it only
    runs once splitting is already on and the cluster is still giant.
    """
    n_rows = profile.data.n_rows
    cp = profile.cluster
    if n_rows <= 0 or cp.cluster_size_max <= _GIANT_CLUSTER_FRACTION * n_rows:
        return None

    from goldenmatch.config.schemas import ClusterConfig

    cluster_cfg = getattr(current, "cluster", None)
    if cluster_cfg is None or not getattr(cluster_cfg, "split_weak_bridges", False):
        new_cluster = (
            ClusterConfig(split_weak_bridges=True)
            if cluster_cfg is None
            else cluster_cfg.model_copy(update={"split_weak_bridges": True})
        )
        return current.model_copy(update={"cluster": new_cluster}), PolicyDecision(
            rule_name="cluster_giant",
            rationale=(
                f"largest cluster {cp.cluster_size_max} is "
                f">{_GIANT_CLUSTER_FRACTION:.0%} of {n_rows} rows; splitting weak "
                f"transitive bridges"
            ),
            config_diff={"cluster.split_weak_bridges": True},
        )

    mk = _first_weighted_mk(current)
    if mk is None or mk.threshold is None:
        return None
    new_threshold = min(_GIANT_THRESHOLD_CEILING, mk.threshold + _GIANT_THRESHOLD_STEP)
    if new_threshold == mk.threshold:
        return None
    new_mk = mk.model_copy(update={"threshold": new_threshold})
    new_cfg = current.model_copy(update={
        "matchkeys": [new_mk if m is mk else m for m in (current.matchkeys or [])]
    })
    return new_cfg, PolicyDecision(
        rule_name="cluster_giant",
        rationale=(
            f"largest cluster {cp.cluster_size_max} still "
            f">{_GIANT_CLUSTER_FRACTION:.0%} of {n_rows} rows with splitting on; "
            f"raising threshold {mk.threshold:.2f} -> {new_threshold:.2f}"
        ),
        config_diff={"matchkeys[0].threshold": new_threshold},
    )
```

Register it in `DEFAULT_RULES` immediately before `rule_low_transitivity`:

```python
    rule_cluster_giant,                    # 11b cluster: one cluster swallowed the data
```

- [ ] **Step 4: Run the test**

Run: `python -m pytest packages/python/goldenmatch/tests/test_cluster_giant_rule.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Shrink the allowlist**

In `test_rule_action_coverage.py`, delete the `"cluster_giant"` line from `_UNCOVERED`, leaving:

```python
_UNCOVERED: frozenset[str] = frozenset({
    "matchkey_collapsed_field",   # removed by Task 5
})
```

- [ ] **Step 6: Verify the gate and the benchmarks together**

Run:
```bash
python -m pytest packages/python/goldenmatch/tests/test_rule_action_coverage.py \
  packages/python/goldenmatch/tests/test_autoconfig_policy.py \
  packages/python/goldenmatch/tests/test_autoconfig_controller.py -q
python scripts/run_benchmarks.py --datasets products \
  --datasets-dir packages/python/goldenmatch/tests/benchmarks/datasets
```
Expected: tests PASS. Benchmarks: all four rows within their quarantine tolerance
(`Abt-Buy (dedupe)` 0.0881 ±0.03, `Amazon-Google (dedupe)` 0.1014 ±0.03).
**If a row drifts, stop and report** — a new rule that changes committed configs
is exactly what `quality_gate` exists to arbitrate.

- [ ] **Step 7: Regenerate docs and commit**

```bash
python scripts/regen_docs.py
git add -A
git commit -m "feat(autoconfig): a rule for the giant-cluster RED, which nothing answered"
```

---

### Task 5: A rule for the collapsed-matchkey-field RED

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_rules.py`
- Modify: `packages/python/goldenmatch/tests/test_rule_action_coverage.py` (empty `_UNCOVERED`)
- Test: `packages/python/goldenmatch/tests/test_matchkey_collapsed_rule.py` (create)

**Interfaces:**
- Consumes: `targets` decorator (Task 2), `MatchkeyProfile.per_field[name].post_transform_cardinality_ratio`.
- Produces: `rule_matchkey_collapsed_field(profile, current, history)` in `DEFAULT_RULES`, ordered immediately before `rule_matchkey_demote_high_cardinality_field`.

- [ ] **Step 1: Write the failing test**

```python
"""A matchkey field with cardinality 0.0 contributes no signal and is RED."""
from goldenmatch.config.schemas import (
    BlockingConfig, BlockingKeyConfig, GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
)
from goldenmatch.core.autoconfig_history import RunHistory
from goldenmatch.core.autoconfig_rules import rule_matchkey_collapsed_field
from goldenmatch.core.complexity_profile import (
    BlockingProfile, ComplexityProfile, DataProfile, FieldStats, MatchkeyProfile,
    ScoringProfile,
)


def _cfg(fields: list[str]) -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="mk", type="weighted", threshold=0.7,
            fields=[MatchkeyField(field=f, scorer="token_sort", weight=1.0) for f in fields],
        )],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["name"])]),
    )


def _profile(cardinalities: dict[str, float]) -> ComplexityProfile:
    return ComplexityProfile(
        data=DataProfile(n_rows=1000, n_cols=3),
        blocking=BlockingProfile(n_blocks=20, reduction_ratio=0.9),
        scoring=ScoringProfile(n_pairs_scored=500, candidates_compared=5000,
                               candidates_counted=True, mass_above_threshold=1.0),
        matchkey=MatchkeyProfile(per_field={
            name: FieldStats(post_transform_cardinality_ratio=c)
            for name, c in cardinalities.items()
        }),
    )


def test_drops_the_collapsed_field():
    out = rule_matchkey_collapsed_field(
        _profile({"name": 0.9, "country": 0.0}), _cfg(["name", "country"]), RunHistory()
    )
    assert out is not None
    new_cfg, decision = out
    assert [f.field for f in new_cfg.matchkeys[0].fields] == ["name"]
    assert decision.targets == ("matchkey_collapsed_field",)


def test_does_not_fire_when_every_field_discriminates():
    assert rule_matchkey_collapsed_field(
        _profile({"name": 0.9, "city": 0.3}), _cfg(["name", "city"]), RunHistory()
    ) is None


def test_refuses_to_empty_the_matchkey():
    """Dropping the last field leaves a matchkey that scores nothing -- worse
    than a weak field. The rule declines and lets another rule try."""
    assert rule_matchkey_collapsed_field(
        _profile({"name": 0.0}), _cfg(["name"]), RunHistory()
    ) is None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest packages/python/goldenmatch/tests/test_matchkey_collapsed_rule.py -q`
Expected: FAIL — `ImportError: cannot import name 'rule_matchkey_collapsed_field'`

- [ ] **Step 3: Implement the rule**

In `autoconfig_rules.py`, immediately before `rule_matchkey_demote_high_cardinality_field`:

```python
@targets("matchkey_collapsed_field")
def rule_matchkey_collapsed_field(
    profile: ComplexityProfile, current: GoldenMatchConfig, history: RunHistory
) -> tuple[GoldenMatchConfig, PolicyDecision] | None:
    """Answer `MatchkeyProfile.red_reason() == "matchkey_collapsed_field"`.

    A field whose post-transform cardinality is 0.0 has one distinct value, so
    every pair scores identically on it: it contributes weight and no
    discrimination. Both rules that previously read `profile.matchkey.per_field`
    (`rule_unimodal_scoring`, `rule_matchkey_demote_high_cardinality_field`)
    sort by HIGHEST cardinality -- neither handles this end, so the verdict was
    reported and never acted on.

    Declines rather than emptying the matchkey: a matchkey with no fields scores
    nothing, which is worse than a weak field, and returning None lets the
    policy advance to a rule that can help.
    """
    mk = _first_weighted_mk(current)
    if mk is None or not mk.fields:
        return None
    collapsed = {
        name for name, fs in profile.matchkey.per_field.items()
        if fs.post_transform_cardinality_ratio == 0.0
    }
    keep = [f for f in mk.fields if f.field not in collapsed]
    if len(keep) == len(mk.fields) or not keep:
        return None
    dropped = sorted({f.field for f in mk.fields if f.field in collapsed})
    new_mk = mk.model_copy(update={"fields": keep})
    new_cfg = current.model_copy(update={
        "matchkeys": [new_mk if m is mk else m for m in (current.matchkeys or [])]
    })
    return new_cfg, PolicyDecision(
        rule_name="matchkey_collapsed_field",
        rationale=(
            f"matchkey field(s) {dropped} have a single distinct value after "
            f"transforms, contributing weight and no discrimination; dropping them"
        ),
        config_diff={"matchkeys[0].fields": [f.field for f in keep]},
    )
```

Register it in `DEFAULT_RULES` immediately before `rule_matchkey_demote_high_cardinality_field`.

- [ ] **Step 4: Run the test**

Run: `python -m pytest packages/python/goldenmatch/tests/test_matchkey_collapsed_rule.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Empty the allowlist and tighten the gate**

In `test_rule_action_coverage.py`, replace `_UNCOVERED` with:

```python
#: Empty, and it should stay that way. An entry here is a RED verdict the
#: controller can only report -- add a rule that targets it, or remove the RED
#: branch if it is genuinely unactionable (as DataProfile did with its
#: uniform-types clause).
_UNCOVERED: frozenset[str] = frozenset()
```

- [ ] **Step 6: Verify and measure**

Run:
```bash
python -m pytest packages/python/goldenmatch/tests/test_rule_action_coverage.py \
  packages/python/goldenmatch/tests/test_autoconfig_policy.py \
  packages/python/goldenmatch/tests/test_autoconfig_controller.py \
  packages/python/goldenmatch/tests/test_autoconfig.py -q
python scripts/run_benchmarks.py --datasets products \
  --datasets-dir packages/python/goldenmatch/tests/benchmarks/datasets
```
Expected: tests PASS, all four benchmark rows within tolerance. **Stop and report on any drift.**

- [ ] **Step 7: Regenerate docs and commit**

```bash
python scripts/regen_docs.py
git add -A
git commit -m "feat(autoconfig): a rule for the collapsed-matchkey-field RED; coverage allowlist now empty"
```

---

### Task 6: Rules can check whether their own action worked

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_history.py`
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_rules.py`
- Test: `packages/python/goldenmatch/tests/test_rule_effect_feedback.py` (create)

**Interfaces:**
- Consumes: `PolicyDecision` (Task 2).
- Produces: `PolicyDecision.predicts: str | None`, `PolicyDecision.predicts_direction: str = "up"`, and `rule_effect_was_negative(history: RunHistory, rule_name: str, *, margin: float = 0.0) -> bool` in `autoconfig_history`.

- [ ] **Step 1: Write the failing test**

```python
"""A rule can ask whether its own last action moved what it predicted."""
from goldenmatch.core.autoconfig_history import (
    HistoryEntry, PolicyDecision, RunHistory, rule_effect_was_negative,
)
from goldenmatch.core.complexity_profile import (
    BlockingProfile, ClusterProfile, ComplexityProfile, DataProfile, ScoringProfile,
)


def _entry(iteration: int, transitivity: float, decision: PolicyDecision | None) -> HistoryEntry:
    return HistoryEntry(
        iteration=iteration, config=None,
        profile=ComplexityProfile(
            data=DataProfile(n_rows=1000, n_cols=3),
            blocking=BlockingProfile(n_blocks=20, reduction_ratio=0.9),
            scoring=ScoringProfile(n_pairs_scored=500, candidates_compared=5000,
                                   candidates_counted=True, mass_above_threshold=1.0),
            cluster=ClusterProfile(n_clusters=50, transitivity_rate=transitivity),
        ),
        decision=decision, error=None, wall_clock_ms=1,
    )


def _decision() -> PolicyDecision:
    return PolicyDecision(
        rule_name="low_transitivity", rationale="x", config_diff={},
        predicts="cluster.transitivity_rate", predicts_direction="up",
    )


def test_reports_negative_when_the_metric_moved_the_wrong_way():
    history = RunHistory()
    history.entries.append(_entry(0, 0.20, _decision()))
    history.entries.append(_entry(1, 0.14, None))
    assert rule_effect_was_negative(history, "low_transitivity") is True


def test_reports_not_negative_when_it_worked():
    history = RunHistory()
    history.entries.append(_entry(0, 0.20, _decision()))
    history.entries.append(_entry(1, 0.55, None))
    assert rule_effect_was_negative(history, "low_transitivity") is False


def test_a_move_inside_the_margin_counts_as_no_progress():
    """`transitivity_rate` samples up to 1000 triples and drifts ~0.003-0.005 on
    an unchanged config, so a move inside that band is noise, not evidence."""
    history = RunHistory()
    history.entries.append(_entry(0, 0.200, _decision()))
    history.entries.append(_entry(1, 0.204, None))
    assert rule_effect_was_negative(history, "low_transitivity", margin=0.01) is True


def test_no_prior_firing_is_not_negative():
    assert rule_effect_was_negative(RunHistory(), "low_transitivity") is False
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest packages/python/goldenmatch/tests/test_rule_effect_feedback.py -q`
Expected: FAIL — `ImportError: cannot import name 'rule_effect_was_negative'`

- [ ] **Step 3: Add the fields and the helper**

In `autoconfig_history.py`, add to `PolicyDecision`:

```python
    #: Dotted path into the ComplexityProfile this action expects to move, e.g.
    #: "cluster.transitivity_rate". With `predicts_direction`, this is what lets
    #: a rule check whether its OWN last action worked instead of re-applying it
    #: blindly -- the failure that made `rule_low_transitivity` walk a threshold
    #: to its floor on every iteration while the metric fell (#2717, and #195
    #: at 2M before it).
    predicts: str | None = None
    #: "up" when the rule expects `predicts` to increase, "down" to decrease.
    predicts_direction: str = "up"
```

And at module level:

```python
def _read_metric(profile: Any, dotted: str) -> float | None:
    """Read a dotted path like "cluster.transitivity_rate" off a profile."""
    node: Any = profile
    for part in dotted.split("."):
        node = getattr(node, part, None)
        if node is None:
            return None
    return float(node) if isinstance(node, (int, float)) else None


def rule_effect_was_negative(
    history: RunHistory, rule_name: str, *, margin: float = 0.0
) -> bool:
    """Did `rule_name`'s own last action fail to move what it predicted?

    False when the rule has not fired, when it declared no prediction, or when
    the metric is unreadable -- absence of evidence is not evidence of failure,
    and a rule should not mute itself on a missing measurement.
    """
    for i in range(len(history.entries) - 1, -1, -1):
        decision = history.entries[i].decision
        if decision is None or decision.rule_name != rule_name:
            continue
        if not decision.predicts:
            return False
        before = _read_metric(history.entries[i].profile, decision.predicts)
        after = _read_metric(history.entries[-1].profile, decision.predicts)
        if before is None or after is None:
            return False
        if decision.predicts_direction == "down":
            return after >= before - margin
        return after <= before + margin
    return False
```

- [ ] **Step 4: Run the test**

Run: `python -m pytest packages/python/goldenmatch/tests/test_rule_effect_feedback.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Migrate `rule_low_transitivity` onto the generic helper**

In `autoconfig_rules.py`, delete `_last_low_transitivity_rate` and replace its
use with the shared helper, keeping the existing margin constant:

```python
    if rule_effect_was_negative(
        history, "low_transitivity", margin=_TRANSITIVITY_PROGRESS_MARGIN
    ):
        return None
```

Add `predicts="cluster.transitivity_rate"` and `predicts_direction="up"` to both
`PolicyDecision(...)` constructions in that rule. Import the helper at the top of
the module.

- [ ] **Step 6: Verify the migration changed no behaviour**

Run:
```bash
python -m pytest packages/python/goldenmatch/tests/test_autoconfig_policy.py \
  packages/python/goldenmatch/tests/test_rule_effect_feedback.py \
  packages/python/goldenmatch/tests/test_mass_above_is_a_real_fraction.py -q
python scripts/run_benchmarks.py --datasets abt-buy \
  --datasets-dir packages/python/goldenmatch/tests/benchmarks/datasets
```
Expected: tests PASS. Abt-Buy unchanged (`(dedupe)` 0.0881, `(linkage)` 0.7024)
and `stop_reason=policy_satisfied` on both rows — the bespoke check and the
generic one must agree.

- [ ] **Step 7: Regenerate docs and commit**

```bash
python scripts/regen_docs.py
git add -A
git commit -m "feat(autoconfig): rules can check whether their own action worked"
```

---

## Out of scope, deliberately

**Making the controller perceive an action's benefit.** `rule_low_transitivity`
can now request cluster splitting, but the controller does not commit the
iteration that enables it: splitting 13 of 1202 sample clusters barely moves a
transitivity estimate sampled over 1000 triples, so `pick_committed` prefers v0.
This plan closes *diagnosis -> action* and *action -> feedback*; it does not fix
*action -> perceptible signal*, which needs a design decision about the profile
metric rather than plumbing. Measured on Abt-Buy: forcing splitting on is worth
0.1723 -> 0.1805 at margin 0.05, so the signal is real and the profile is too
coarse to see it.

**Re-calibrating the RED thresholds themselves** (`0.85` transitivity, `0.1 *
n_rows` giant, `0.0` cardinality). Task 1 makes them addressable; whether they
are in the right place is a separate measured question.

## Self-review notes

- **Coverage:** every claim in "Why this exists" maps to a task — the 7-vs-3
  asymmetry to Tasks 1-3, the two verified holes to Tasks 4 and 5, the
  `rule_low_transitivity` blind re-application to Task 6.
- **Placeholders:** none. Both new rules are written out in full, and the two
  `_UNCOVERED` entries are named holes with the evidence for each, not "TBD".
- **Type consistency:** `targets` is `tuple[str, ...]` on both the decorator
  (`fn.targets`) and `PolicyDecision.targets`. `red_reason` returns `str | None`
  on every profile. `rule_effect_was_negative` returns `bool` and is imported by
  `autoconfig_rules` in Task 6 only.
- **Known risk:** Tasks 4 and 5 add rules that can fire on real data and change
  committed configs. Both tasks end with a benchmark run and an explicit
  instruction to stop on drift; `quality_gate` is the CI arbiter and has caught
  this class of change before.
