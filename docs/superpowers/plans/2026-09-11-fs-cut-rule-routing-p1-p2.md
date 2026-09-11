# FS Link-Cut Routing, Phases P1–P2: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Fellegi-Sunter linear link-cut method a named rule that a matchkey can pin with `link_cut_rule`, report which rule placed each run's cut and why, and store the training-sample score histogram plus the diagnostics a future router will read. Nothing routes automatically yet.

**Architecture:**
- **Rules in bits.** A new module, `goldenmatch/core/fs_cut_rules.py`, defines each rule as a cutoff on total match evidence `W`, in bits. It converts that cutoff to the normalized linear-score threshold every scorer already applies.
- **One resolver.** `_fs_link_threshold` and `resolve_thresholds` resolve the rule through one helper, keeping the existing precedence:
  1. explicit `link_threshold`;
  2. EM-calibrated cutoff;
  3. pinned `link_cut_rule`;
  4. the `GOLDENMATCH_FS_LINEAR_CUT` default (`prior_mid`).
- **Training histogram.** `train_em` stores its training sample's linear-score histogram on `EMResult`. The `otsu` rule and the diagnostics read it.
- **No scorer or native-kernel changes.**

**Tech Stack:** Python 3.12, pydantic v2 (`MatchkeyConfig`), numpy, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-09-11-fs-cut-rule-routing-design.md` (branch `docs/fs-cut-rule-routing-spec`).

## Global Constraints

- **Worktree and base.** Work in `D:/Temp/gm-route` on branch `feat/fs-cut-rule-router`. It is based on PR #2939's branch `feat/fs-label-free-cutoff` (head `ce5085ca0`), which already has `prior_mid`, `LINK_THRESHOLD_EVIDENCE_RULE` and `GOLDENMATCH_FS_LINEAR_CUT`.
- **No router in P1–P2.** Do not add `GOLDENMATCH_FS_CUT_ROUTER`, `choose_cut_rule` or table rows; they are P4.
- **Default behaviour is unchanged.** With `link_cut_rule` and `GOLDENMATCH_FS_LINEAR_CUT` both unset, runs link exactly the same pairs as today. The only intended output differences are:
  - new keys in the per-matchkey cutoff report;
  - a new `training_score_histogram` key in serialized `EMResult` JSON.
- **Rule names, in this exact order everywhere:** `("midpoint", "prior", "prior_mid", "posterior_099", "evidence_3", "evidence_5", "evidence_9", "evidence_12", "otsu")`. `prior` stays because #2939 documented it as a `GOLDENMATCH_FS_LINEAR_CUT` value.
- **Schema imports.** `goldenmatch/config/schemas.py` must not import `goldenmatch.core`. A test keeps its `Literal` equal to `fs_cut_rules.CUT_RULES`.
- **Parity surfaces** (checked while planning, per the spec's P1 note):
  - `EMResult` has exactly one definition in the repo (`core/probabilistic.py`).
  - TypeScript mirrors no `MatchkeyConfig` field (`link_threshold_observed` has no TS counterpart), so the Python↔TS API gate is unaffected.
  - The config matrix and both agent manifests regenerate through `scripts/regen_docs.py`.
- **Commit text.** No AI or Claude attribution lines in commit messages or PR bodies. No CI-skip directive text anywhere in a commit message.
- **Ruff.**
  - Run `ruff check` on every changed Python file before committing.
  - A PostToolUse ruff hook reformats and strips "unused" imports between separate edits to the same `.py` file. When a step adds an import in one place and its use in another, apply both in ONE edit (or one scripted patch).
  - Tests import helpers inside the test function when a later task adds the first use.
- **Local runs.** Never run scale benchmarks locally. Everything below uses small fixtures.
- **Test command,** from `D:/Temp/gm-route` in Git Bash:
  ```bash
  PY=D:/show_case/goldenmatch/.venv/Scripts/python.exe
  PP=$(ls -d packages/python/*/ | sed 's#/$##' | sed "s#^#D:/Temp/gm-route/#" | paste -sd';')
  T=packages/python/goldenmatch/tests
  PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider <test files>
  ```
- **GitHub auth.** `export GH_TOKEN=$(gh auth token -u benzsevern)`. Never run `gh auth switch`.

---

### Task 1: Rule arithmetic module

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/core/fs_cut_rules.py`
- Create: `packages/python/goldenmatch/tests/test_fs_cut_rules.py`
- Modify: `scripts/coverage_baseline.json` (new module entry; `_meta.module_count` 488 → 489)

**Interfaces:**
- **Consumes** two existing functions in `goldenmatch.core.probabilistic`: `_fs_ne_weight_range(em, mk) -> tuple[float, float]` and `prior_weight(proportion_matched: float) -> float`. Import both inside functions to avoid an import cycle.
- **Produces:**
  - `CUT_RULES: tuple[str, ...]`
  - `Envelope(lo: float, hi: float)`, a frozen dataclass with a `midpoint` property
  - `weight_envelope(mk, em_result) -> Envelope | None`
  - `prior_bits(proportion_matched: float) -> float`
  - `rule_bits(rule: str, envelope: Envelope, em_result) -> float | None`
  - `bits_to_normalized(bits: float, envelope: Envelope) -> float`

- [ ] **Step 1: Write the failing test**

Create `packages/python/goldenmatch/tests/test_fs_cut_rules.py`:

```python
"""Link-cut rules as evidence cutoffs in bits (spec 2026-09-11-fs-cut-rule-routing-design)."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.fs_cut_rules import (
    CUT_RULES,
    Envelope,
    bits_to_normalized,
    prior_bits,
    rule_bits,
    weight_envelope,
)


def _mk() -> MatchkeyConfig:
    return MatchkeyConfig(
        name="fs",
        type="probabilistic",
        fields=[
            MatchkeyField(field="name", scorer="jaro_winkler", levels=3, partial_threshold=0.8),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ],
    )


def _em(lam: float, **extra):
    # lo = -6 + -4 = -10, hi = 10 + 4 = 14; midpoint = 2 bits.
    return SimpleNamespace(
        match_weights={"name": [-6.0, 1.0, 10.0], "zip": [-4.0, 4.0]},
        proportion_matched=lam,
        calibrated_link_threshold=None,
        **extra,
    )


def test_rule_names_are_fixed():
    assert CUT_RULES == (
        "midpoint", "prior", "prior_mid", "posterior_099",
        "evidence_3", "evidence_5", "evidence_9", "evidence_12", "otsu",
    )


def test_envelope_sums_field_extremes():
    env = weight_envelope(_mk(), _em(0.01))
    assert env == Envelope(lo=-10.0, hi=14.0)
    assert env.midpoint == 2.0


def test_envelope_is_none_without_weights_or_range():
    assert weight_envelope(_mk(), SimpleNamespace(proportion_matched=0.01)) is None
    flat = SimpleNamespace(match_weights={"name": [1.0, 1.0, 1.0], "zip": [0.0, 0.0]},
                           proportion_matched=0.01)
    assert weight_envelope(_mk(), flat) is None


def test_prior_bits_is_log_odds_against_a_match():
    assert math.isclose(prior_bits(0.01), math.log2(99.0), rel_tol=1e-12)
    assert prior_bits(0.9) < 0


@pytest.mark.parametrize(
    "rule, lam, expected",
    [
        ("midpoint", 0.01, 2.0),
        ("prior", 0.01, math.log2(99.0)),
        ("prior", 0.9, 0.0),
        ("prior_mid", 0.4, 2.0),
        ("prior_mid", 0.01, math.log2(99.0)),
        ("posterior_099", 0.01, math.log2(99.0) + math.log2(99.0)),
        ("evidence_3", 0.01, 3.0),
        ("evidence_5", 0.01, 5.0),
        ("evidence_9", 0.01, 9.0),
        ("evidence_12", 0.01, 12.0),
    ],
)
def test_rule_bits(rule, lam, expected):
    env = weight_envelope(_mk(), _em(lam))
    assert math.isclose(rule_bits(rule, env, _em(lam)), expected, rel_tol=1e-12, abs_tol=1e-12)


def test_otsu_needs_a_training_histogram():
    env = weight_envelope(_mk(), _em(0.01))
    assert rule_bits("otsu", env, _em(0.01)) is None


def test_unknown_rule_raises():
    env = weight_envelope(_mk(), _em(0.01))
    with pytest.raises(ValueError, match="unknown link cut rule"):
        rule_bits("median", env, _em(0.01))


def test_bits_to_normalized_is_affine_and_clamped():
    env = Envelope(lo=-10.0, hi=14.0)
    assert bits_to_normalized(2.0, env) == 0.5
    assert bits_to_normalized(-100.0, env) == 0.0
    assert bits_to_normalized(100.0, env) == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider $T/test_fs_cut_rules.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'goldenmatch.core.fs_cut_rules'`

- [ ] **Step 3: Write the module**

Create `packages/python/goldenmatch/goldenmatch/core/fs_cut_rules.py`:

```python
"""Link-cut rules for the Fellegi-Sunter linear score, as evidence cutoffs in bits.

Spec: docs/superpowers/specs/2026-09-11-fs-cut-rule-routing-design.md.

The linear FS score is ``(W - lo) / (hi - lo)``. ``W`` is a pair's summed match weight;
``lo``/``hi`` are the summed per-field minimum and maximum weights plus the negative-evidence
range. Every rule returns a cutoff on ``W`` in bits, and ``bits_to_normalized`` turns that into
the linear cutoff every scorer already applies, native kernel included. All rules are monotone
in ``W``, so no scorer changes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

#: Every rule a matchkey may pin with ``MatchkeyConfig.link_cut_rule``. The schema's ``Literal``
#: repeats these names (schemas must not import core); tests/test_fs_cut_rules.py keeps them equal.
CUT_RULES: tuple[str, ...] = (
    "midpoint", "prior", "prior_mid", "posterior_099",
    "evidence_3", "evidence_5", "evidence_9", "evidence_12", "otsu",
)

_EVIDENCE_BITS = {"evidence_3": 3.0, "evidence_5": 5.0, "evidence_9": 9.0, "evidence_12": 12.0}


@dataclass(frozen=True)
class Envelope:
    """The summed per-field weight extremes the linear score normalizes against."""

    lo: float
    hi: float

    @property
    def midpoint(self) -> float:
        return (self.lo + self.hi) / 2.0


def weight_envelope(mk: Any, em_result: Any) -> Envelope | None:
    """``lo``/``hi`` built exactly as the scorers build them.

    None when there is nothing to place a cut from: no match weights, or a degenerate range.
    """
    from goldenmatch.core.probabilistic import _fs_ne_weight_range

    match_weights = getattr(em_result, "match_weights", None)
    if not match_weights:
        return None
    lo, hi = _fs_ne_weight_range(em_result, mk)
    for f in mk.fields:
        weights = match_weights.get(f.field)
        if weights:
            lo += min(weights)
            hi += max(weights)
    if hi <= lo:
        return None
    return Envelope(lo=float(lo), hi=float(hi))


def prior_bits(proportion_matched: float) -> float:
    """``log2((1 - lambda) / lambda)``: the evidence at which the posterior crosses 0.5."""
    from goldenmatch.core.probabilistic import prior_weight

    return -prior_weight(proportion_matched)


def rule_bits(rule: str, envelope: Envelope, em_result: Any) -> float | None:
    """The cutoff on ``W`` for ``rule``, or None when this model cannot supply it."""
    if rule == "midpoint":
        return envelope.midpoint
    if rule in _EVIDENCE_BITS:
        return _EVIDENCE_BITS[rule]
    if rule == "otsu":
        return None  # needs EMResult.training_score_histogram (Tasks 5-6)
    if rule not in CUT_RULES:
        raise ValueError(f"unknown link cut rule {rule!r}; expected one of {CUT_RULES}")
    lam = getattr(em_result, "proportion_matched", None)
    if lam is None:
        return None
    prior = prior_bits(lam)
    if rule == "prior":
        return max(prior, 0.0)
    if rule == "prior_mid":
        return max(prior, envelope.midpoint, 0.0)
    return math.log2(99.0) + prior  # posterior_099: where sigmoid(prior_weight + W) = 0.99


def bits_to_normalized(bits: float, envelope: Envelope) -> float:
    """The linear-score cutoff equivalent to ``W >= bits``, clamped to [0, 1]."""
    return min(max((bits - envelope.lo) / (envelope.hi - envelope.lo), 0.0), 1.0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider $T/test_fs_cut_rules.py`
Expected: PASS (all tests).

- [ ] **Step 5: Add the coverage baseline entry**

Measure:

```bash
PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY -m coverage run --source=goldenmatch.core.fs_cut_rules -m pytest -q -p no:cacheprovider $T/test_fs_cut_rules.py
$PY -m coverage report
```

In `scripts/coverage_baseline.json`:
1. Under `"modules"`, add `"goldenmatch/core/fs_cut_rules.py": {"rate": <Cover% / 100, 4 decimals>, "statements": <Stmts>}`, placed alphabetically among the `goldenmatch/core/` entries.
2. Change `"_meta"."module_count"` from 488 to 489. `test_committed_baseline_is_present_and_sane` asserts it equals `len(modules)`.

Then run:

```bash
PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider scripts/test_coverage_baseline.py
```

Expected: PASS.

- [ ] **Step 6: Lint and commit**

```bash
$PY -m ruff check packages/python/goldenmatch/goldenmatch/core/fs_cut_rules.py $T/test_fs_cut_rules.py
git add packages/python/goldenmatch/goldenmatch/core/fs_cut_rules.py $T/test_fs_cut_rules.py scripts/coverage_baseline.json
git commit -m "feat(fs): link-cut rules as evidence cutoffs in bits (fs_cut_rules)"
```

---

### Task 2: `MatchkeyConfig.link_cut_rule`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/config/schemas.py` (insert after the `link_threshold_observed` field; that block ends just before `review_threshold: float | None = Field(`)
- Modify: `packages/python/goldenmatch/tests/test_fs_cut_rules.py` (append tests)
- Regenerate: derived docs via `scripts/regen_docs.py` (`docs-site/goldenmatch/config-matrix.mdx`, `docs/agent-manifest.json`, `packages/python/goldensuite-mcp/goldensuite_mcp/agent-manifest.json`, codemap)

**Interfaces:**
- **Consumes:** `CUT_RULES` (Task 1).
- **Produces:** `MatchkeyConfig.link_cut_rule`, typed `Literal["midpoint", "prior", "prior_mid", "posterior_099", "evidence_3", "evidence_5", "evidence_9", "evidence_12", "otsu"] | None`, default `None`.

- [ ] **Step 1: Write the failing tests**

Append to `packages/python/goldenmatch/tests/test_fs_cut_rules.py`:

```python
def test_schema_literal_matches_cut_rules():
    import typing

    ann = MatchkeyConfig.model_fields["link_cut_rule"].annotation
    literal = next(a for a in typing.get_args(ann) if typing.get_origin(a) is typing.Literal)
    assert typing.get_args(literal) == CUT_RULES


def test_link_cut_rule_accepts_rule_names_and_defaults_to_none():
    assert _mk().link_cut_rule is None
    pinned = _mk().model_copy(update={"link_cut_rule": "evidence_9"})
    assert pinned.link_cut_rule == "evidence_9"


def test_link_cut_rule_rejects_unknown_names():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MatchkeyConfig(
            name="fs", type="probabilistic", link_cut_rule="median",
            fields=[MatchkeyField(field="zip", scorer="exact", levels=2)],
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider $T/test_fs_cut_rules.py`
Expected: the 3 new tests FAIL (`KeyError: 'link_cut_rule'` / no such attribute; the unknown name does not raise).

- [ ] **Step 3: Add the field**

In `packages/python/goldenmatch/goldenmatch/config/schemas.py`, directly after the `link_threshold_observed` field block and before `review_threshold: float | None = Field(`, insert:

```python
    # Probabilistic-only: pin the rule that places the LINEAR link cutoff, as an evidence
    # cutoff in bits. These names must match goldenmatch.core.fs_cut_rules.CUT_RULES.
    # None uses the default rule; an explicit link_threshold or an EM-calibrated cutoff
    # still wins over a pinned rule.
    link_cut_rule: Literal[
        "midpoint", "prior", "prior_mid", "posterior_099",
        "evidence_3", "evidence_5", "evidence_9", "evidence_12", "otsu",
    ] | None = Field(
        default=None,
        description=(
            "Rule that places the probabilistic link cutoff by evidence bits, for example "
            "prior_mid or evidence_9. Unset uses the default rule; link_threshold, when set, "
            "still wins."
        ),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider $T/test_fs_cut_rules.py`
Expected: PASS.

- [ ] **Step 5: Regenerate derived docs**

```bash
PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY scripts/regen_docs.py
git status --short
```

Expected: `docs-site/goldenmatch/config-matrix.mdx` gains a `link_cut_rule` row. The agent manifests and codemap may change too.

- [ ] **Step 6: Lint and commit**

```bash
$PY -m ruff check packages/python/goldenmatch/goldenmatch/config/schemas.py $T/test_fs_cut_rules.py
git add -A
git commit -m "feat(fs): link_cut_rule matchkey field to pin the link-cut rule"
```

---

### Task 3: Resolve the cut through the rule module

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/probabilistic.py`, the block from `_FS_LINEAR_CUT_RULES = ("prior", "prior_mid")` (line ~4599) through the end of `_fs_linear_rule_link_threshold` (line ~4668)
- Create: `packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py`

**Interfaces:**
- **Consumes:** `weight_envelope`, `rule_bits`, `bits_to_normalized`, `CUT_RULES` (Task 1); `MatchkeyConfig.link_cut_rule` (Task 2).
- **Produces** (in `goldenmatch.core.probabilistic`):
  - `ResolvedCut(rule: str, reason: str, bits: float, normalized: float)`, a frozen dataclass
  - `_fs_resolved_cut(mk, em_result, calibrated: bool) -> ResolvedCut | None`
  - `_fs_linear_rule_link_threshold(mk, em_result, calibrated: bool) -> float | None` (signature unchanged; now delegates)
  - `_fs_linear_cut_rule() -> str | None`, which now accepts any `CUT_RULES` name except `midpoint`, which stays an off alias

- [ ] **Step 1: Write the failing tests**

Create `packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py`:

```python
"""Link-cut rule resolution: pinned link_cut_rule > GOLDENMATCH_FS_LINEAR_CUT default."""

from __future__ import annotations

import math
from types import SimpleNamespace

from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.probabilistic import (
    LINK_THRESHOLD_EVIDENCE_RULE,
    LINK_THRESHOLD_FALLBACK,
    _fs_link_threshold,
    _fs_resolved_cut,
    link_threshold_source,
    resolve_thresholds,
)


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


def _em(lam: float = 0.01, calibrated=None):
    # lo = -10, hi = 14, midpoint = 2 bits.
    return SimpleNamespace(
        match_weights={"name": [-6.0, 1.0, 10.0], "zip": [-4.0, 4.0]},
        proportion_matched=lam,
        calibrated_link_threshold=calibrated,
    )


def _norm(bits: float) -> float:
    return (bits + 10.0) / 24.0


def test_pinned_rule_beats_the_default_rule(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_FS_LINEAR_CUT", raising=False)
    got = _fs_resolved_cut(_mk(link_cut_rule="evidence_12"), _em(), calibrated=False)
    assert got.rule == "evidence_12"
    assert got.reason == "pinned by link_cut_rule"
    assert math.isclose(got.normalized, _norm(12.0), rel_tol=1e-12)


def test_pinned_rule_applies_when_the_default_is_off(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "off")
    got = _fs_resolved_cut(_mk(link_cut_rule="evidence_9"), _em(), calibrated=False)
    assert got.rule == "evidence_9"
    assert math.isclose(got.normalized, _norm(9.0), rel_tol=1e-12)


def test_pinned_midpoint_is_a_chosen_rule_not_a_fallback(monkeypatch):
    """The env value `midpoint` stays an alias for off, as #2939 shipped it; a pinned
    `midpoint` is a decision about this matchkey, so it reports as a rule."""
    monkeypatch.delenv("GOLDENMATCH_FS_LINEAR_CUT", raising=False)
    mk = _mk(link_cut_rule="midpoint")
    got = _fs_resolved_cut(mk, _em(), calibrated=False)
    assert got.rule == "midpoint" and got.normalized == 0.5
    assert link_threshold_source(mk, _em()) == LINK_THRESHOLD_EVIDENCE_RULE


def test_default_off_and_nothing_pinned_resolves_nothing(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "off")
    assert _fs_resolved_cut(_mk(), _em(), calibrated=False) is None
    assert link_threshold_source(_mk(), _em()) == LINK_THRESHOLD_FALLBACK


def test_env_default_accepts_any_rule_name(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "evidence_5")
    got = _fs_resolved_cut(_mk(), _em(), calibrated=False)
    assert got.rule == "evidence_5"
    assert got.reason == "default rule"
    assert math.isclose(got.normalized, _norm(5.0), rel_tol=1e-12)


def test_uncomputable_pin_falls_back_to_the_default_with_a_reason(monkeypatch):
    """KNOWN-POSITIVE: a pinned otsu on a model with no training histogram."""
    monkeypatch.delenv("GOLDENMATCH_FS_LINEAR_CUT", raising=False)
    got = _fs_resolved_cut(_mk(link_cut_rule="otsu"), _em(), calibrated=False)
    assert got.rule == "prior_mid"
    assert got.reason.startswith("otsu unavailable: no training histogram")


def test_uncomputable_pin_with_default_off_resolves_nothing(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "off")
    assert _fs_resolved_cut(_mk(link_cut_rule="otsu"), _em(), calibrated=False) is None


def test_posterior_mode_ignores_rules(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_FS_LINEAR_CUT", raising=False)
    assert _fs_resolved_cut(_mk(link_cut_rule="evidence_9"), _em(), calibrated=True) is None


def test_explicit_threshold_and_calibrated_cutoff_still_win(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_FS_LINEAR_CUT", raising=False)
    pinned = _mk(link_cut_rule="evidence_12", link_threshold=0.42)
    assert _fs_link_threshold(pinned, _em(), calibrated=False) == 0.42
    assert _fs_link_threshold(_mk(link_cut_rule="evidence_12"), _em(calibrated=0.61), False) == 0.61


def test_both_resolvers_and_the_source_agree_on_a_pinned_rule(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "off")
    monkeypatch.delenv("GOLDENMATCH_FS_CALIBRATED", raising=False)
    monkeypatch.delenv("GOLDENMATCH_FS_EVIDENCE_CUT", raising=False)
    mk, em = _mk(link_cut_rule="evidence_9"), _em()
    link, review = resolve_thresholds(mk, em)
    assert math.isclose(link, _fs_link_threshold(mk, em, calibrated=False), rel_tol=1e-12)
    assert math.isclose(link, _norm(9.0), rel_tol=1e-12)
    assert review <= link
    assert link_threshold_source(mk, em) == LINK_THRESHOLD_EVIDENCE_RULE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider $T/test_fs_cut_rule_resolution.py`
Expected: FAIL with `ImportError: cannot import name '_fs_resolved_cut'`

- [ ] **Step 3: Replace the rule block in `probabilistic.py`**

Replace everything from `_FS_LINEAR_CUT_RULES = ("prior", "prior_mid")` through the final `return min(max((cut_bits - lo) / (hi - lo), 0.0), 1.0)` of `_fs_linear_rule_link_threshold` with the code below, as ONE edit.

- `_fs_link_threshold`, `resolve_thresholds` and `link_threshold_source` already call `_fs_linear_rule_link_threshold`, so they need no change.
- `dataclass` is already imported at the top of the module (line 24).

```python
_FS_LINEAR_CUT_DEFAULT = "prior_mid"
_FS_LINEAR_CUT_OFF = ("off", "0", "false", "none", "midpoint")


def _fs_linear_cut_rule() -> str | None:
    """``GOLDENMATCH_FS_LINEAR_CUT``: the default link-cut rule for matchkeys that pin none.

    Any ``fs_cut_rules.CUT_RULES`` name; unset means ``prior_mid``. ``off`` (or
    ``0``/``false``/``none``/``midpoint``) turns the rule step off, so the fixed 0.50 midpoint
    cut applies and reports as a fallback. A matchkey that pins ``link_cut_rule="midpoint"``
    is a chosen rule instead, and reports as one. An unrecognised value warns and keeps the
    default rather than silently reverting.
    """
    from goldenmatch.core.fs_cut_rules import CUT_RULES

    value = os.environ.get("GOLDENMATCH_FS_LINEAR_CUT", "").strip().lower()
    if not value:
        return _FS_LINEAR_CUT_DEFAULT
    if value in _FS_LINEAR_CUT_OFF:
        return None
    if value in CUT_RULES:
        return value
    logger.warning(
        "GOLDENMATCH_FS_LINEAR_CUT=%r is not one of %s or off; using %s",
        value, "/".join(CUT_RULES), _FS_LINEAR_CUT_DEFAULT,
    )
    return _FS_LINEAR_CUT_DEFAULT


@dataclass(frozen=True)
class ResolvedCut:
    """The linear link cut a rule placed: which rule, why, and where."""

    rule: str
    reason: str
    bits: float
    normalized: float


def _fs_resolved_cut(
    mk: MatchkeyConfig, em_result: EMResult, calibrated: bool
) -> ResolvedCut | None:
    """The linear link cut placed by rule, or None when no rule applies.

    Callers check an explicit ``mk.link_threshold`` and an EM-calibrated cutoff first. Within
    the rule step, a pinned ``mk.link_cut_rule`` beats the ``GOLDENMATCH_FS_LINEAR_CUT``
    default. A pinned rule this model cannot supply (``otsu`` without a training histogram)
    falls back to the default rule, and the reason says so.

    Returns None in posterior mode (the score is a probability, not the linear scale), when
    the model has no usable weights, or when the default is off and nothing pinned is
    computable.
    Spec: docs/superpowers/specs/2026-09-11-fs-cut-rule-routing-design.md.
    """
    from goldenmatch.core.fs_cut_rules import bits_to_normalized, rule_bits, weight_envelope

    if calibrated:
        return None
    envelope = weight_envelope(mk, em_result)
    if envelope is None or getattr(em_result, "proportion_matched", None) is None:
        return None
    candidates: list[tuple[str, str]] = []
    pinned = getattr(mk, "link_cut_rule", None)
    if pinned:
        candidates.append((pinned, "pinned by link_cut_rule"))
    default = _fs_linear_cut_rule()
    if default is not None and default != pinned:
        candidates.append((default, "default rule"))
    note = ""
    for rule, reason in candidates:
        bits = rule_bits(rule, envelope, em_result)
        if bits is None:
            note = f"{rule} unavailable: no training histogram on this model; "
            continue
        return ResolvedCut(
            rule=rule,
            reason=note + reason,
            bits=float(bits),
            normalized=bits_to_normalized(bits, envelope),
        )
    return None


def _fs_linear_rule_link_threshold(
    mk: MatchkeyConfig, em_result: EMResult, calibrated: bool
) -> float | None:
    """Normalized linear link cutoff from :func:`_fs_resolved_cut`, or None.

    The linear score is ``(W - lo) / (hi - lo)``, so the fixed 0.50 cut links at the midpoint
    of the per-field weight extremes; a rule places the cut by evidence bits instead.

    MEASURED (fs-lever-gate full panel, 50,000-pair u sample, run 34551694684), midpoint vs
    ``prior_mid``:
    - ncvr_synthetic 0.9743 -> 0.9990;
    - dblp_acm 0.8058 -> 0.8159;
    - amazon_google 0.0217 -> 0.0475;
    - the other seven datasets' clusters identical.

    historical_50k (midpoint +10.8 bits, lambda 0.661) and dblp_scholar (+22.2, 0.065) keep
    their midpoint, which is why pure ``prior`` is not the default.
    """
    resolved = _fs_resolved_cut(mk, em_result, calibrated)
    return None if resolved is None else resolved.normalized
```

- [ ] **Step 4: Run the new and existing rule tests**

Run:

```bash
PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider $T/test_fs_cut_rule_resolution.py $T/test_fs_linear_cut_rule.py $T/test_link_threshold_report_2483.py $T/test_fs_link_threshold_drift_2483.py $T/test_fs_threshold_calibration.py $T/test_probabilistic_cutoff_2483.py
```

Expected: PASS. `test_fs_linear_cut_rule.py::test_unknown_value_keeps_the_default` still passes because `median` is not a rule name.

- [ ] **Step 5: Run the FS and probabilistic suites**

Run: `PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider $(ls $T/test_probabilistic*.py $T/test_fs_*.py) $T/test_link_threshold_report_2483.py $T/test_link_threshold_stamp_2637.py $T/test_stamp_only_calibrated_threshold.py`
Expected: PASS.

- [ ] **Step 6: Lint and commit**

```bash
$PY -m ruff check packages/python/goldenmatch/goldenmatch/core/probabilistic.py $T/test_fs_cut_rule_resolution.py
git add packages/python/goldenmatch/goldenmatch/core/probabilistic.py $T/test_fs_cut_rule_resolution.py
git commit -m "feat(fs): resolve the linear link cut through named rules; pinned link_cut_rule wins"
```

---

### Task 4: Report which rule placed the cut

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/probabilistic.py` (add `_fs_unresolved_cut_reason` and `link_cut_report` directly after `link_threshold_source`)
- Modify: `packages/python/goldenmatch/goldenmatch/core/pipeline.py` (the `threshold_out[mk.name] = {...}` block, line ~988–998)
- Modify: `packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py` (append tests)

**Interfaces:**
- **Consumes:** `_fs_resolved_cut`, `_fs_linear_cut_rule` (Task 3); `weight_envelope` (Task 1).
- **Produces:**
  - `link_cut_report(mk, em_result) -> dict` with keys `cut_rule: str | None` and `cut_reason: str | None`
  - `DedupeResult.stats["fs_link_thresholds"][<matchkey>]` gains those keys

- [ ] **Step 1: Write the failing tests**

Append to `packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py`:

```python
def _clear_cut_env(monkeypatch):
    for var in (
        "GOLDENMATCH_FS_LINEAR_CUT", "GOLDENMATCH_FS_CALIBRATED",
        "GOLDENMATCH_FS_EVIDENCE_CUT", "GOLDENMATCH_FS_CALIBRATE_THRESHOLD",
    ):
        monkeypatch.delenv(var, raising=False)


def test_link_cut_report_names_the_rule_and_reason(monkeypatch):
    from goldenmatch.core.probabilistic import link_cut_report

    _clear_cut_env(monkeypatch)
    report = link_cut_report(_mk(link_cut_rule="evidence_3"), _em())
    assert report["cut_rule"] == "evidence_3"
    assert report["cut_reason"] == "pinned by link_cut_rule"


def test_link_cut_report_is_empty_when_a_threshold_decided_first(monkeypatch):
    from goldenmatch.core.probabilistic import link_cut_report

    _clear_cut_env(monkeypatch)
    configured = link_cut_report(_mk(link_cut_rule="evidence_3", link_threshold=0.4), _em())
    assert configured["cut_rule"] is None and configured["cut_reason"] is None
    calibrated = link_cut_report(_mk(), _em(calibrated=0.6))
    assert calibrated["cut_rule"] is None and calibrated["cut_reason"] is None


def test_link_cut_report_is_silent_when_no_rule_was_asked_for(monkeypatch):
    from goldenmatch.core.probabilistic import link_cut_report

    _clear_cut_env(monkeypatch)
    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "off")
    assert link_cut_report(_mk(), _em()) == {"cut_rule": None, "cut_reason": None}


def test_link_cut_report_says_why_a_requested_rule_did_not_apply(monkeypatch):
    """KNOWN-POSITIVE: a degenerate model, and an uncomputable pin with the default off."""
    from goldenmatch.core.probabilistic import link_cut_report

    _clear_cut_env(monkeypatch)
    flat = SimpleNamespace(
        match_weights={"name": [1.0, 1.0, 1.0], "zip": [0.0, 0.0]},
        proportion_matched=0.01, calibrated_link_threshold=None,
    )
    degenerate = link_cut_report(_mk(), flat)
    assert degenerate["cut_rule"] is None
    assert degenerate["cut_reason"].startswith("degenerate model")

    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "off")
    otsu = link_cut_report(_mk(link_cut_rule="otsu"), _em())
    assert otsu["cut_rule"] is None
    assert otsu["cut_reason"] == (
        "otsu unavailable: no training histogram on this model; fixed 0.50 cut applies"
    )


def test_dedupe_result_reports_the_cut_rule(monkeypatch):
    """A pinned rule reaches the run's cutoff report. Frame and config shape are the ones
    tests/test_link_threshold_stamp_2637.py already drives through FS scoring."""
    import warnings

    import goldenmatch as gm
    import polars as pl
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig, GoldenMatchConfig

    _clear_cut_env(monkeypatch)
    first = ["ada", "grace", "alan", "edsger", "barbara", "donald"]
    last = ["lovelace", "hopper", "turing", "dijkstra", "liskov", "knuth"]
    df = pl.DataFrame([
        {
            "rid": f"r{i}",
            "name": (f"{first[i % 6]} {last[(i // 2) % 6]}" if i % 3
                     else f"{first[i % 6][0]}. {last[(i // 2) % 6]}"),
            "city": ["leeds", "york", "hull"][i % 3],
        }
        for i in range(240)
    ])
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="p", type="probabilistic", link_cut_rule="evidence_3",
            fields=[MatchkeyField(field="name", scorer="jaro_winkler")],
        )],
        blocking=BlockingConfig(
            keys=[BlockingKeyConfig(fields=["name"], transforms=["lowercase"])]
        ),
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = gm.dedupe_df(df, config=cfg)
    thresholds = (res.stats or {}).get("fs_link_thresholds") or {}
    assert "p" in thresholds, f"run never reported an FS cutoff: {thresholds!r}"
    entry = thresholds["p"]
    assert entry["cut_rule"] == "evidence_3"
    assert entry["cut_reason"] == "pinned by link_cut_rule"
    assert entry["source"] == LINK_THRESHOLD_EVIDENCE_RULE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider $T/test_fs_cut_rule_resolution.py`
Expected: the 5 new tests FAIL. Four fail with `ImportError: cannot import name 'link_cut_report'`; the e2e test fails with `KeyError: 'cut_rule'`.

- [ ] **Step 3: Add the report helpers**

In `probabilistic.py`, directly after `link_threshold_source` (which ends `return LINK_THRESHOLD_FALLBACK`), add:

```python
def _fs_unresolved_cut_reason(mk: MatchkeyConfig, em_result: EMResult) -> str | None:
    """Why the rule step placed no cut, for the report; None when no rule was asked for."""
    from goldenmatch.core.fs_cut_rules import weight_envelope

    rule = getattr(mk, "link_cut_rule", None) or _fs_linear_cut_rule()
    if rule is None:
        return None
    if (
        weight_envelope(mk, em_result) is None
        or getattr(em_result, "proportion_matched", None) is None
    ):
        return "degenerate model: no usable weight envelope or match rate"
    return f"{rule} unavailable: no training histogram on this model; fixed 0.50 cut applies"


def link_cut_report(mk: MatchkeyConfig, em_result: EMResult) -> dict:
    """``cut_rule`` / ``cut_reason`` for the per-matchkey cutoff report.

    - Both are None when a configured or calibrated cutoff decided first, in posterior mode,
      or when no rule was asked for.
    - ``cut_reason`` alone is set when a rule was asked for but could not place the cut.

    Mirrors the precedence in :func:`_fs_link_threshold` / :func:`resolve_thresholds`.
    """
    report = {"cut_rule": None, "cut_reason": None}
    if mk.link_threshold is not None:
        return report
    if getattr(em_result, "calibrated_link_threshold", None) is not None:
        return report
    if _fs_calibration_mode() == "posterior":
        return report
    resolved = _fs_resolved_cut(mk, em_result, calibrated=False)
    if resolved is not None:
        report["cut_rule"] = resolved.rule
        report["cut_reason"] = resolved.reason
    else:
        report["cut_reason"] = _fs_unresolved_cut_reason(mk, em_result)
    return report
```

- [ ] **Step 4: Record it in the pipeline report**

In `packages/python/goldenmatch/goldenmatch/core/pipeline.py`, replace:

```python
        from goldenmatch.core.probabilistic import link_threshold_source

        threshold_out[mk.name] = {
            "link_threshold": float(link_threshold),
            "source": link_threshold_source(mk, em_result),
            "refit": _refit_decision,
        }
```

with:

```python
        from goldenmatch.core.probabilistic import link_cut_report, link_threshold_source

        threshold_out[mk.name] = {
            "link_threshold": float(link_threshold),
            "source": link_threshold_source(mk, em_result),
            "refit": _refit_decision,
            **link_cut_report(mk, em_result),
        }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider $T/test_fs_cut_rule_resolution.py $T/test_link_threshold_report_2483.py $T/test_link_threshold_stamp_2637.py $T/test_stamp_only_calibrated_threshold.py`
Expected: PASS.

- [ ] **Step 6: Lint and commit**

```bash
$PY -m ruff check packages/python/goldenmatch/goldenmatch/core/probabilistic.py packages/python/goldenmatch/goldenmatch/core/pipeline.py $T/test_fs_cut_rule_resolution.py
git add packages/python/goldenmatch/goldenmatch/core/probabilistic.py packages/python/goldenmatch/goldenmatch/core/pipeline.py $T/test_fs_cut_rule_resolution.py
git commit -m "feat(fs): report which link-cut rule placed the cut and why"
```

---

### Task 5: Training-sample score histogram on `EMResult`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/probabilistic.py`:
  - `EMResult` (new field after `training_config`, plus `to_dict` and `from_dict`);
  - `_otsu_threshold` (refactored onto a counts helper);
  - new `_training_score_histogram` just before `_calibrate_link_threshold`;
  - the final `return EMResult(...)` of `train_em` (line ~2873).
- Create: `packages/python/goldenmatch/tests/test_fs_training_histogram.py`

**Interfaces:**
- **Produces:**
  - `EMResult.training_score_histogram: dict | None`, shaped `{"lo": float, "hi": float, "counts": list[int]}` with 100 counts
  - `_training_score_histogram(comp_matrix, mk, match_weights) -> dict | None`
  - `_otsu_split_from_counts(counts) -> float | None`

- [ ] **Step 1: Write the failing tests**

Create `packages/python/goldenmatch/tests/test_fs_training_histogram.py`:

```python
"""EMResult.training_score_histogram: the EM training sample's linear-score histogram."""

from __future__ import annotations

import numpy as np
import polars as pl
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.probabilistic import (
    EMResult,
    _otsu_split_from_counts,
    _otsu_threshold,
    train_em,
)


def _blocked_fixture():
    """200 rows (100 entities x 2 records) in zip blocks of 10 (5 entities each).

    900 blocked pairs. The 100 true duplicates agree on both names; every other pair
    disagrees on both. That gives two separated score masses, well past the calibrator's
    50-pair minimum.
    """
    from goldenmatch.core.blocker import build_blocks

    df = pl.DataFrame({
        "__row_id__": list(range(200)),
        "zip": [f"Z{i // 10}" for i in range(200)],
        "first_name": [f"F{i // 2}" for i in range(200)],
        "last_name": [f"L{(i // 2) % 37}" for i in range(200)],
    })
    mk = MatchkeyConfig(
        name="fs", type="probabilistic",
        fields=[
            MatchkeyField(field="first_name", scorer="exact", levels=2),
            MatchkeyField(field="last_name", scorer="exact", levels=2),
        ],
    )
    blocks = build_blocks(
        df.lazy(),
        BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["zip"])]),
    )
    return df, mk, blocks


def test_train_em_stores_the_training_histogram():
    df, mk, blocks = _blocked_fixture()
    em = train_em(df, mk, n_sample_pairs=1000, max_iterations=10,
                  blocks=blocks, blocking_fields=["zip"])
    hist = em.training_score_histogram
    assert hist is not None
    assert len(hist["counts"]) == 100
    assert sum(hist["counts"]) > 50
    assert hist["lo"] < hist["hi"]
    assert sum(1 for c in hist["counts"] if c) >= 2, "both score masses must be present"


def test_histogram_round_trips_through_json_and_is_optional(tmp_path):
    hist = {"lo": -3.0, "hi": 5.0, "counts": [1] * 100}
    em = EMResult(
        m_probs={}, u_probs={}, match_weights={}, converged=True, iterations=1,
        proportion_matched=0.1, training_score_histogram=hist,
    )
    path = str(tmp_path / "model.json")
    em.save_json(path)
    assert EMResult.load_json(path).training_score_histogram == hist

    bare = EMResult(m_probs={}, u_probs={}, match_weights={}, converged=True,
                    iterations=1, proportion_matched=0.1)
    assert "training_score_histogram" not in bare.to_dict()
    bare_path = str(tmp_path / "bare.json")
    bare.save_json(bare_path)
    assert EMResult.load_json(bare_path).training_score_histogram is None


def test_otsu_on_counts_equals_otsu_on_scores():
    rng = np.random.default_rng(7)
    scores = np.concatenate([rng.normal(0.2, 0.05, 500), rng.normal(0.8, 0.05, 200)]).clip(0, 1)
    counts, _ = np.histogram(scores, bins=100, range=(0.0, 1.0))
    assert _otsu_split_from_counts(counts) == _otsu_threshold(scores)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider $T/test_fs_training_histogram.py`
Expected: FAIL with `ImportError: cannot import name '_otsu_split_from_counts'`

- [ ] **Step 3: Add the field and its serialization**

In `EMResult`, directly after `training_config: dict | None = None`, and before `_source_schema_version`, so existing positional field order is untouched, add:

```python
    # Linear-score histogram of EM's training sample: {"lo", "hi", "counts"}, 100 equal bins
    # over [0, 1], normalized against the regular-field envelope exactly as the Otsu
    # calibrator normalizes it. Read by the `otsu` link-cut rule and the cut diagnostics
    # (core/fs_cut_rules.py). None when a model was not trained through train_em's sample
    # path (counted EM, per-pass sessions, supervised estimation) or was saved before this
    # field existed.
    training_score_histogram: dict | None = None
```

In `to_dict`, directly after the `training_config` block (`data["training_config"] = self.training_config`), add:

```python
        if self.training_score_histogram is not None:
            data["training_score_histogram"] = self.training_score_histogram
```

In `from_dict`, directly after `training_config=data.get("training_config"),`, add:

```python
                training_score_histogram=data.get("training_score_histogram"),
```

- [ ] **Step 4: Refactor Otsu onto counts**

Replace the whole `_otsu_threshold` function with:

```python
def _otsu_split_from_counts(counts) -> float | None:
    """Otsu split of a [0, 1] histogram given as bin counts.

    The split is the cutoff maximizing between-class variance, returned as the upper edge of
    the split bin.
    """
    hist = np.asarray(counts, dtype=np.float64)
    nbins = hist.shape[0]
    total = hist.sum()
    if total <= 0:
        return None
    prob = hist / total
    centers = (np.arange(nbins) + 0.5) / nbins
    omega = np.cumsum(prob)                    # class-0 (below) weight
    mu = np.cumsum(prob * centers)             # class-0 cumulative mean
    mu_t = mu[-1]
    denom = omega * (1.0 - omega)
    with np.errstate(divide="ignore", invalid="ignore"):
        sigma_b = np.where(denom > 1e-12, (mu_t * omega - mu) ** 2 / denom, 0.0)
    k = int(np.argmax(sigma_b))
    return float((k + 1) / nbins)              # upper edge of the split bin


def _otsu_threshold(scores) -> float | None:
    """Otsu split: the cutoff maximizing between-class variance of a [0,1] score histogram.

    For a bimodal non-match/match distribution this is the class boundary. For
    well-separated (unimodal-ish) data the exact split barely matters (flat F1 curve), so it
    stays safe.
    """
    hist, _ = np.histogram(scores, bins=100, range=(0.0, 1.0))
    return _otsu_split_from_counts(hist)
```

Before replacing it, diff the old body against `_otsu_split_from_counts`. The arithmetic must be the same statements; `test_otsu_on_counts_equals_otsu_on_scores` checks equality on one input, and the existing calibration tests cover the rest.

- [ ] **Step 5: Compute the histogram in `train_em`**

Directly before `def _calibrate_link_threshold(`, add:

```python
_TRAINING_HISTOGRAM_BINS = 100


def _training_score_histogram(comp_matrix, mk, match_weights) -> dict | None:
    """Linear-score histogram of EM's training sample.

    Normalized exactly as :func:`_calibrate_link_threshold` normalizes it (regular fields,
    observed levels), so an Otsu split of these counts is that calibrator's split.
    """
    fields = [f for f in mk.fields if f.field in match_weights]
    if not fields or comp_matrix is None or comp_matrix.shape[0] == 0:
        return None
    weights = {f.field: np.asarray(match_weights[f.field], dtype=np.float64) for f in fields}
    lo = float(sum(w.min() for w in weights.values()))
    hi = float(sum(w.max() for w in weights.values()))
    if hi <= lo:
        return None
    total = np.zeros(comp_matrix.shape[0], dtype=np.float64)
    for j, f in enumerate(fields):
        lv = comp_matrix[:, j]
        obs = lv >= 0
        total[obs] += weights[f.field][lv[obs]]
    norm = np.clip((total - lo) / (hi - lo), 0.0, 1.0)
    counts, _ = np.histogram(norm, bins=_TRAINING_HISTOGRAM_BINS, range=(0.0, 1.0))
    return {"lo": lo, "hi": hi, "counts": [int(c) for c in counts]}
```

In `train_em`, directly before its final `return EMResult(` (after the field-dependence block), add:

```python
    training_score_histogram = _training_score_histogram(comp_matrix, mk, match_weights)
```

and add `training_score_histogram=training_score_histogram,` to that `EMResult(...)` call after `training_config=_training_config_manifest(mk),`. `comp_matrix` is bound unconditionally at line ~2724; the only earlier exit is `return _fallback_result(mk)`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider $T/test_fs_training_histogram.py $T/test_fs_threshold_calibration.py $(ls $T/test_probabilistic*.py)`
Expected: PASS.

- [ ] **Step 7: Lint and commit**

```bash
$PY -m ruff check packages/python/goldenmatch/goldenmatch/core/probabilistic.py $T/test_fs_training_histogram.py
git add packages/python/goldenmatch/goldenmatch/core/probabilistic.py $T/test_fs_training_histogram.py
git commit -m "feat(fs): store the EM training sample's score histogram on EMResult"
```

---

### Task 6: `otsu` rule and cut diagnostics

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/fs_cut_rules.py` (otsu branch; add `otsu_bits`, `CutDiagnostics`, `cut_diagnostics`)
- Modify: `packages/python/goldenmatch/goldenmatch/core/probabilistic.py` (`link_cut_report` adds `cut_diagnostics`)
- Modify: `packages/python/goldenmatch/tests/test_fs_cut_rules.py`, `packages/python/goldenmatch/tests/test_fs_training_histogram.py`, `packages/python/goldenmatch/tests/test_fs_cut_rule_resolution.py`

**Interfaces:**
- **Consumes:**
  - `EMResult.training_score_histogram` and `_otsu_split_from_counts` (Task 5);
  - `_CALIBRATE_MIN`, `_CALIBRATE_MAX` (existing, `probabilistic.py` line ~3819: 0.40 and 0.90);
  - `link_cut_report` and `_fs_unresolved_cut_reason` (Task 4).
- **Produces:**
  - `otsu_bits(em_result, envelope: Envelope) -> float | None`
  - `CutDiagnostics`, a frozen dataclass with `proportion_matched`, `lo`, `hi`, `midpoint_bits`, `prior_bits`, `n_fields`, `has_negative_evidence`, `field_weight_spans: dict[str, float]` and `admitted_fraction: dict[str, float] | None`
  - `cut_diagnostics(mk, em_result) -> CutDiagnostics | None`
  - `link_cut_report(...)` gains the key `cut_diagnostics: dict | None`

- [ ] **Step 1: Write the failing tests**

Append to `test_fs_cut_rules.py` (keep the existing `test_otsu_needs_a_training_histogram`, which still holds):

```python
def test_otsu_splits_the_training_histogram_with_the_calibrator_clamp():
    """KNOWN-POSITIVE for the clamp: two point masses make every split between them tie,
    argmax takes the first (0.11), and the calibrator's 0.40 floor lifts it."""
    from goldenmatch.core.fs_cut_rules import otsu_bits

    counts = [0] * 100
    counts[10], counts[80] = 500, 200
    em = _em(0.01, training_score_histogram={"lo": -10.0, "hi": 14.0, "counts": counts})
    env = weight_envelope(_mk(), em)
    bits = otsu_bits(em, env)
    assert math.isclose(bits_to_normalized(bits, env), 0.40, abs_tol=1e-12)
    assert rule_bits("otsu", env, em) == bits


def test_cut_diagnostics_without_a_histogram():
    from goldenmatch.core.fs_cut_rules import cut_diagnostics

    d = cut_diagnostics(_mk(), _em(0.01))
    assert (d.lo, d.hi, d.midpoint_bits) == (-10.0, 14.0, 2.0)
    assert math.isclose(d.prior_bits, math.log2(99.0), rel_tol=1e-12)
    assert d.n_fields == 2 and d.has_negative_evidence is False
    assert d.field_weight_spans == {"name": 16.0, "zip": 8.0}
    assert d.admitted_fraction is None


def test_cut_diagnostics_admitted_fraction():
    """Uniform counts over [-10, 14] bits: a cut at c bits admits the bins from
    ceil(100 * (c + 10) / 24) up."""
    from goldenmatch.core.fs_cut_rules import cut_diagnostics

    em = _em(0.01, training_score_histogram={"lo": -10.0, "hi": 14.0, "counts": [10] * 100})
    adm = cut_diagnostics(_mk(), em).admitted_fraction
    assert math.isclose(adm["midpoint"], 0.50, abs_tol=1e-12)
    assert math.isclose(adm["evidence_3"], 0.45, abs_tol=1e-12)
    assert math.isclose(adm["evidence_12"], 0.08, abs_tol=1e-12)
    assert "otsu" in adm


def test_cut_diagnostics_is_none_without_weights():
    from goldenmatch.core.fs_cut_rules import cut_diagnostics

    assert cut_diagnostics(_mk(), SimpleNamespace(proportion_matched=0.01)) is None
```

Append to `test_fs_training_histogram.py`:

```python
def test_otsu_rule_equals_the_existing_calibrator(monkeypatch):
    """Pinning `otsu` reproduces GOLDENMATCH_FS_CALIBRATE_THRESHOLD's cut."""
    import math

    from goldenmatch.core.fs_cut_rules import bits_to_normalized, otsu_bits, weight_envelope

    monkeypatch.setenv("GOLDENMATCH_FS_CALIBRATE_THRESHOLD", "1")
    monkeypatch.delenv("GOLDENMATCH_FS_CALIBRATED", raising=False)
    df, mk, blocks = _blocked_fixture()
    em = train_em(df, mk, n_sample_pairs=1000, max_iterations=10,
                  blocks=blocks, blocking_fields=["zip"])
    assert em.calibrated_link_threshold is not None, "fixture too small for the calibrator"
    env = weight_envelope(mk, em)
    assert math.isclose(
        bits_to_normalized(otsu_bits(em, env), env), em.calibrated_link_threshold, abs_tol=1e-12
    )
```

Append to `test_fs_cut_rule_resolution.py`:

```python
def test_link_cut_report_carries_diagnostics(monkeypatch):
    from goldenmatch.core.probabilistic import link_cut_report

    _clear_cut_env(monkeypatch)
    report = link_cut_report(_mk(), _em())
    assert report["cut_rule"] == "prior_mid"
    assert report["cut_diagnostics"]["midpoint_bits"] == 2.0
    assert report["cut_diagnostics"]["admitted_fraction"] is None
    configured = link_cut_report(_mk(link_threshold=0.4), _em())
    assert configured["cut_rule"] is None
    assert configured["cut_diagnostics"]["midpoint_bits"] == 2.0
```

Then update the one Task 4 test whose exact-dict assertion now gains a key. In `test_link_cut_report_is_silent_when_no_rule_was_asked_for`, replace

```python
    assert link_cut_report(_mk(), _em()) == {"cut_rule": None, "cut_reason": None}
```

with

```python
    report = link_cut_report(_mk(), _em())
    assert report["cut_rule"] is None and report["cut_reason"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider $T/test_fs_cut_rules.py $T/test_fs_training_histogram.py $T/test_fs_cut_rule_resolution.py`
Expected: FAIL with `ImportError: cannot import name 'otsu_bits'` / `'cut_diagnostics'`, and `KeyError: 'cut_diagnostics'`.

- [ ] **Step 3: Implement `otsu_bits` and diagnostics in `fs_cut_rules.py`**

Replace the line `        return None  # needs EMResult.training_score_histogram (Tasks 5-6)` with:

```python
        return otsu_bits(em_result, envelope)
```

Append to `fs_cut_rules.py`:

```python
def otsu_bits(em_result: Any, envelope: Envelope) -> float | None:
    """The Otsu calibrator's cut, in bits.

    Splits ``EMResult.training_score_histogram`` exactly as ``_calibrate_link_threshold``
    (GOLDENMATCH_FS_CALIBRATE_THRESHOLD) does, clamp and rounding included. The calibrator
    applies its split ``t`` directly as the linear cutoff, so the bits are placed on the
    scoring envelope: ``bits_to_normalized(otsu_bits(em, env), env)`` is ``t``.

    None when the model carries no training histogram.
    """
    import numpy as np

    from goldenmatch.core.probabilistic import (
        _CALIBRATE_MAX,
        _CALIBRATE_MIN,
        _otsu_split_from_counts,
    )

    hist = getattr(em_result, "training_score_histogram", None)
    if not hist:
        return None
    t = _otsu_split_from_counts(hist["counts"])
    if t is None:
        return None
    t = round(float(np.clip(t, _CALIBRATE_MIN, _CALIBRATE_MAX)), 4)
    return envelope.lo + t * (envelope.hi - envelope.lo)


@dataclass(frozen=True)
class CutDiagnostics:
    """What a link-cut router may read after EM. No labels, no scoring pass."""

    proportion_matched: float
    lo: float
    hi: float
    midpoint_bits: float
    prior_bits: float
    n_fields: int
    has_negative_evidence: bool
    field_weight_spans: dict[str, float]
    #: Share of the training sample each rule would admit; None without a training histogram.
    #: Bin resolution only (1/100 of the regular-field envelope), and the training sample is a
    #: biased stand-in for the scored candidates -- see the spec's risks.
    admitted_fraction: dict[str, float] | None


def cut_diagnostics(mk: Any, em_result: Any) -> CutDiagnostics | None:
    """Router inputs for ``mk`` from its trained model, or None without usable weights."""
    envelope = weight_envelope(mk, em_result)
    lam = getattr(em_result, "proportion_matched", None)
    if envelope is None or lam is None:
        return None
    match_weights = em_result.match_weights
    spans = {
        f.field: float(max(w) - min(w))
        for f in mk.fields
        if (w := match_weights.get(f.field))
    }
    admitted = None
    hist = getattr(em_result, "training_score_histogram", None)
    if hist and sum(hist["counts"]) > 0 and hist["hi"] > hist["lo"]:
        counts = hist["counts"]
        total = float(sum(counts))
        admitted = {}
        for rule in CUT_RULES:
            bits = rule_bits(rule, envelope, em_result)
            if bits is None:
                continue
            t = min(max((bits - hist["lo"]) / (hist["hi"] - hist["lo"]), 0.0), 1.0)
            start = min(math.ceil(t * len(counts)), len(counts))
            admitted[rule] = sum(counts[start:]) / total
    return CutDiagnostics(
        proportion_matched=float(lam),
        lo=envelope.lo,
        hi=envelope.hi,
        midpoint_bits=envelope.midpoint,
        prior_bits=prior_bits(lam),
        n_fields=len(mk.fields),
        has_negative_evidence=bool(getattr(mk, "negative_evidence", None)),
        field_weight_spans=spans,
        admitted_fraction=admitted,
    )
```

- [ ] **Step 4: Add diagnostics to the report**

Replace the whole `link_cut_report` function in `probabilistic.py` (Task 4) with:

```python
def link_cut_report(mk: MatchkeyConfig, em_result: EMResult) -> dict:
    """``cut_rule`` / ``cut_reason`` / ``cut_diagnostics`` for the per-matchkey cutoff report.

    - ``cut_rule`` and ``cut_reason`` are None when a configured or calibrated cutoff decided
      first, in posterior mode, or when no rule was asked for.
    - ``cut_reason`` alone is set when a rule was asked for but could not place the cut.
    - ``cut_diagnostics`` is reported whenever the model has usable weights, so the
      measurement harness can read it even when no rule applied.

    Mirrors the precedence in :func:`_fs_link_threshold` / :func:`resolve_thresholds`.
    """
    from dataclasses import asdict

    from goldenmatch.core.fs_cut_rules import cut_diagnostics

    diagnostics = cut_diagnostics(mk, em_result)
    report = {
        "cut_rule": None,
        "cut_reason": None,
        "cut_diagnostics": asdict(diagnostics) if diagnostics is not None else None,
    }
    if mk.link_threshold is not None:
        return report
    if getattr(em_result, "calibrated_link_threshold", None) is not None:
        return report
    if _fs_calibration_mode() == "posterior":
        return report
    resolved = _fs_resolved_cut(mk, em_result, calibrated=False)
    if resolved is not None:
        report["cut_rule"] = resolved.rule
        report["cut_reason"] = resolved.reason
    else:
        report["cut_reason"] = _fs_unresolved_cut_reason(mk, em_result)
    return report
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider $T/test_fs_cut_rules.py $T/test_fs_training_histogram.py $T/test_fs_cut_rule_resolution.py $T/test_link_threshold_stamp_2637.py`
Expected: PASS.

- [ ] **Step 6: Lint and commit**

```bash
$PY -m ruff check packages/python/goldenmatch/goldenmatch/core/fs_cut_rules.py packages/python/goldenmatch/goldenmatch/core/probabilistic.py $T/test_fs_cut_rules.py $T/test_fs_training_histogram.py $T/test_fs_cut_rule_resolution.py
git add -A
git commit -m "feat(fs): otsu link-cut rule from the training histogram, plus cut diagnostics"
```

---

### Task 7: Verify, document, open the stacked PR

**Files:**
- Modify: `scripts/coverage_baseline.json` (refresh the `fs_cut_rules.py` entry after Task 6)
- Regenerate: derived docs
- Create: a PR body file in the session scratchpad (not committed)

- [ ] **Step 1: Run the affected suites and ratchets**

```bash
PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider $(ls $T/test_probabilistic*.py $T/test_fs_*.py) $T/test_link_threshold_report_2483.py $T/test_link_threshold_stamp_2637.py $T/test_stamp_only_calibrated_threshold.py $T/test_threshold_degenerate_match_rate.py $T/test_cluster.py
PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY -m pytest -q -p no:cacheprovider scripts/test_no_new_shared_decisions.py scripts/test_coverage_baseline.py
```

Expected: PASS. `link_cut_rule` is read only in `core/probabilistic.py`, so the shared-decisions ratchet must not flag it. If it does, stop and triage the field the way that file's docstring says; do not add it to an allowlist blind.

- [ ] **Step 2: Refresh the coverage entry**

```bash
PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY -m coverage run --source=goldenmatch.core.fs_cut_rules -m pytest -q -p no:cacheprovider $T/test_fs_cut_rules.py $T/test_fs_cut_rule_resolution.py $T/test_fs_training_histogram.py
$PY -m coverage report
```

Update `"modules"."goldenmatch/core/fs_cut_rules.py"` in `scripts/coverage_baseline.json` to the new `Stmts` and `Cover%/100`, to 4 decimals. `_meta.module_count` stays 489.

- [ ] **Step 3: Docs checks**

```bash
PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY scripts/regen_docs.py
PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY scripts/regen_docs.py --check
PYTHONPATH="$PP" PYTHONIOENCODING=utf-8 $PY scripts/check_docs_staleness.py
```

Expected: `Docs are current -- nothing to regenerate.` and `Docs staleness OK`.

- [ ] **Step 4: Commit and push**

```bash
git add -A
git commit -m "chore(fs): refresh coverage baseline and derived docs for link-cut rules"
export GH_TOKEN=$(gh auth token -u benzsevern)
SKIP_DOCS_CHECK=1 git push -u origin feat/fs-cut-rule-router
```

- [ ] **Step 5: Open a DRAFT PR stacked on #2939, not armed**

Write the PR body with these sections:
- what changes (Tasks 1–6);
- the default-behaviour guarantee, and the only output differences (report keys, one JSON key);
- tests;
- what P3–P5 add.

No AI attribution. Then:

```bash
gh pr create --repo benseverndev-oss/goldenmatch --draft \
  --base feat/fs-label-free-cutoff --head feat/fs-cut-rule-router \
  --title "feat(fs): named link-cut rules + link_cut_rule pinning (routing P1-P2)" \
  --body-file <path to the body file>
```

Do NOT run `gh pr merge --auto` on this PR. Its base is a feature branch, and an armed stacked PR merges into that base immediately. Retarget it to `main` after #2939 merges.
