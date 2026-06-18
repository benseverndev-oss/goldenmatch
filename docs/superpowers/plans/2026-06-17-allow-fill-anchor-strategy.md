# allow_fill + anchor Group-Winner Strategy Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two opt-in group-winner extensions to lock-step `field_groups` survivorship: `allow_fill` (per-cell back-fill of a winner's null group cells from the best other row by the group's strategy) and an `anchor` group strategy (pick the winner by one designated column), with provenance that surfaces through the workstream-1 audit trail. Byte-identical when neither lever is used.

**Architecture:** One unifying refactor in `core/survivorship/winner.py`: compute a strategy-ordered ranking of row indices once; winner = `ranking[0]`, `allow_fill` walks `ranking[1:]` per null cell, `anchor` is a new ranking. `GroupResult`/`GroupProvenance` each gain a `filled` map (positional in the former, row-id in the latter; `resolve.py` remaps). The config gains `anchor` + `allow_fill`. The NL renderer gains a back-fill line; the `golden_records` serializer omits empty `filled`.

**Tech Stack:** Python 3.11+, Pydantic v2 (`config/schemas.py`), dataclasses, pytest. Spec: `docs/superpowers/specs/2026-06-17-allow-fill-anchor-strategy-design.md`.

**Dependency:** Stacks on workstream 1 (GroupProvenance surfacing, PR #1053) which stacks on v1 (merged). Branch off `origin/main` only AFTER #1053 merges (the `render_cluster_provenance_nl` surfacing wiring must be present for the NL tests).

---

## Conventions for every task

- **Run tests** (targeted local runs only, never the full xdist suite locally):
  `POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 <repo>/.venv/Scripts/python.exe -m pytest <path> -v`
  (set `GOLDENMATCH_NATIVE=0` if a stale native wheel interferes).
- **Commit** after each green step. Squash-merge via PR at the end.
- **No em dashes / ASCII only** in committed strings.
- New tests live in `packages/python/goldenmatch/tests/survivorship/`.

---

## File Structure

**Modified files**
- `packages/python/goldenmatch/goldenmatch/config/schemas.py` — `GoldenGroupRule` gains `anchor` + `allow_fill`; `_GROUP_STRATEGIES` gains `"anchor"`; `_validate_group` updated.
- `.../core/survivorship/winner.py` — `_ranking` helper; `group_winner` gains `anchor` + `allow_fill` params + `GroupResult.filled`; confidence counts fills.
- `.../core/survivorship/resolve.py` — pass `anchor`/`allow_fill`; remap `filled` positional -> row id; thread into `GroupProvenance`; filled-cell `source_row_id`.
- `.../core/golden.py` — `GroupProvenance.filled` field.
- `.../core/lineage.py` — `render_group_provenance_line` back-fill lines; `_serialize_golden_records` omits empty `filled`.
- Docs: `docs-site/goldenmatch/configuration.mdx` (field-groups section).

**New test files**
- `tests/survivorship/test_allow_fill_anchor.py`

(Existing `tests/survivorship/test_winner.py` is extended for the refactor-parity gate.)

---

## Task 0: Branch setup

- [ ] **Step 1:** Confirm #1053 merged, branch off fresh `origin/main`.
```bash
git fetch origin
git switch -c feat/allow-fill-anchor origin/main
# sanity: workstream-1 surfacing must be present
grep -n "def render_cluster_provenance_nl" packages/python/goldenmatch/goldenmatch/core/lineage.py
grep -n "def golden_provenance_for_run" packages/python/goldenmatch/goldenmatch/core/lineage.py
```
- [ ] **Step 2:** Smoke-run the existing group tests green.
Run: `POLARS_SKIP_CPU_CHECK=1 .venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/survivorship/test_winner.py -q`
Expected: PASS (baseline before any change).

---

## Phase A — Config

### Task A1: `GoldenGroupRule` gains `anchor` + `allow_fill`

**Files:**
- Modify: `config/schemas.py`
- Test: `tests/survivorship/test_allow_fill_anchor.py`

- [ ] **Step 1: Write the failing test**
```python
# tests/survivorship/test_allow_fill_anchor.py
import pytest
from pydantic import ValidationError
from goldenmatch.config.schemas import GoldenGroupRule


def test_defaults():
    g = GoldenGroupRule(name="g", columns=["a", "b"])
    assert g.anchor is None and g.allow_fill is False


def test_allow_fill_orthogonal():
    g = GoldenGroupRule(name="g", columns=["a", "b"], allow_fill=True)
    assert g.allow_fill is True


def test_anchor_strategy_valid():
    g = GoldenGroupRule(name="g", columns=["plan_id", "plan_name"], strategy="anchor", anchor="plan_id")
    assert g.strategy == "anchor" and g.anchor == "plan_id"


def test_anchor_requires_anchor_column():
    with pytest.raises(ValidationError):
        GoldenGroupRule(name="g", columns=["a", "b"], strategy="anchor")   # no anchor


def test_anchor_must_be_in_columns():
    with pytest.raises(ValidationError):
        GoldenGroupRule(name="g", columns=["a", "b"], strategy="anchor", anchor="c")


def test_anchor_with_non_anchor_strategy_rejected():
    with pytest.raises(ValidationError):
        GoldenGroupRule(name="g", columns=["a", "b"], strategy="most_complete", anchor="a")
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement.** Add the fields + `"anchor"` to `_GROUP_STRATEGIES`, and extend `_validate_group`:
```python
_GROUP_STRATEGIES = frozenset({"most_complete", "source_priority", "most_recent", "anchor"})

class GoldenGroupRule(BaseModel):
    name: str
    columns: list[str]
    category: str | None = None
    strategy: str = "most_complete"
    date_column: str | None = None
    source_priority: list[str] | None = None
    anchor: str | None = None
    allow_fill: bool = False

    @model_validator(mode="after")
    def _validate_group(self) -> "GoldenGroupRule":
        # ... keep existing >=2 columns, reserved-prefix, strategy-in-allowlist,
        #     most_recent->date_column, source_priority->source_priority checks ...
        if self.strategy == "anchor":
            if not self.anchor:
                raise ValueError(f"Group '{self.name}' strategy 'anchor' requires 'anchor'.")
            if self.anchor not in self.columns:
                raise ValueError(f"Group '{self.name}' anchor '{self.anchor}' must be one of its columns.")
        elif self.anchor is not None:
            raise ValueError(f"Group '{self.name}' sets 'anchor' but strategy is '{self.strategy}' (anchor only valid with strategy 'anchor').")
        return self
```

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat(config): GoldenGroupRule anchor strategy + allow_fill`.

---

## Phase B — winner.py (core)

### Task B1: ranking refactor (REFACTOR-PARITY)

**Files:**
- Modify: `core/survivorship/winner.py`
- Test: `tests/survivorship/test_winner.py` (append a refactor-parity test, incl. TIE clusters)

This refactor must produce the IDENTICAL winner/values/confidence/tie as today for `most_complete`/`source_priority`/`most_recent`. Today's winner is the LOWEST index among ties (`min`/`max` return the first extreme; `most_complete` uses `winners[0]`). **Python's `sorted(..., reverse=True)` is guaranteed STABLE** (equal-key elements keep ascending input order), so `reverse=True` does NOT invert ties; it is correct here. The TIE-cluster parity test below is the guard.

- [ ] **Step 1: Write the failing/guard test** (append to `test_winner.py`)
```python
from goldenmatch.core.survivorship.winner import group_winner


def _rows(spec):
    return [{"__pos__": i, **r} for i, r in enumerate(spec)]


def test_most_complete_tiebreak_lowest_index():
    # two rows tie on populated count -> winner is the LOWER index (parity with old winners[0])
    rows = _rows([{"a": "x", "b": "y"}, {"a": "p", "b": "q"}])
    res = group_winner(rows, ["a", "b"], strategy="most_complete")
    assert res.winner_pos == 0
    assert res.tie is True and res.confidence == 0.7


def test_source_priority_winner_and_no_tie():
    rows = _rows([{"a": "x", "__source__": "billing"}, {"a": "p", "__source__": "crm"}])
    res = group_winner(rows, ["a"], strategy="source_priority", source_priority=["crm", "billing"])
    assert res.winner_pos == 1 and res.tie is False


def test_most_recent_tie_lowest_index():
    rows = _rows([{"a": "x"}, {"a": "p"}])
    res = group_winner(rows, ["a"], strategy="most_recent", dates=["2024-01-01", "2024-01-01"])
    assert res.winner_pos == 0   # equal dates -> lowest index, parity with old max()
```

- [ ] **Step 2: Run.** The existing `test_winner.py` cases + these must stay green THROUGHOUT the refactor (this is a refactor, not new behavior).

- [ ] **Step 3: Implement the ranking.** Replace the per-strategy `best`/`tie` block with a `_ranking` helper; keep `values`/confidence identical:
```python
def _ranking(rows, columns, strategy, *, source_priority=None, dates=None, anchor=None):
    n = len(rows)
    if strategy == "source_priority":
        rank = {s: i for i, s in enumerate(source_priority or [])}
        order = sorted(range(n), key=lambda i: rank.get(rows[i].get("__source__"), len(rank)))  # asc; stable ties
        return order, False
    if strategy == "most_recent":
        def keyf(i):
            d = dates[i] if dates and i < len(dates) else None
            return (d is not None, d)
        order = sorted(range(n), key=keyf, reverse=True)  # desc; stable -> ties keep asc index
        return order, False
    if strategy == "anchor":
        counts = [_populated(rows[i], columns) for i in range(n)]
        present = [rows[i].get(anchor) is not None for i in range(n)]
        order = sorted(range(n), key=lambda i: (present[i], counts[i]), reverse=True)  # anchor-present first, then most-complete
        # tie among the TOP group (same present + same count as the winner)
        w = order[0]
        top_key = (present[w], counts[w])
        tie = sum(1 for i in range(n) if (present[i], counts[i]) == top_key) > 1
        return order, tie
    # most_complete
    counts = [_populated(rows[i], columns) for i in range(n)]
    order = sorted(range(n), key=lambda i: counts[i], reverse=True)
    top = counts[order[0]]
    tie = sum(1 for c in counts if c == top) > 1
    return order, tie


def group_winner(rows, columns, strategy="most_complete", *,
                 source_priority=None, dates=None, anchor=None, allow_fill=False) -> GroupResult:
    n = len(rows)
    if n == 0:
        return GroupResult(-1, {c: None for c in columns}, 0.0, False)
    ranking, tie = _ranking(rows, columns, strategy, source_priority=source_priority, dates=dates, anchor=anchor)
    best = ranking[0]
    values = {c: rows[best].get(c) for c in columns}
    filled: dict[str, int] = {}
    # (allow_fill block added in B3)
    winner_populated = _populated(rows[best], columns)
    base_conf = (winner_populated + len(filled)) / len(columns) if columns else 0.0
    conf = base_conf * 0.7 if tie else base_conf
    return GroupResult(rows[best].get("__pos__", best), values, conf, tie, filled)
```
(Note: with `filled` empty, `base_conf` reduces to today's `winner_populated/len`, so B1 alone is byte-identical. `GroupResult.filled` is added in B2/B3 below; for B1 add it as a defaulted field so the dataclass is ready.)

Add to `GroupResult`:
```python
from dataclasses import dataclass, field

@dataclass
class GroupResult:
    winner_pos: int
    values: dict[str, Any]
    confidence: float
    tie: bool
    filled: dict[str, int] = field(default_factory=dict)
```

- [ ] **Step 4: Run** `test_winner.py` — ALL existing + new pass (refactor parity).
- [ ] **Step 5: Commit** `refactor(survivorship): ranking-based group_winner (parity-preserving)`.

### Task B2: `anchor` strategy behavior

**Files:** Modify `winner.py` (already has the anchor branch from B1); Test: `test_allow_fill_anchor.py`

- [ ] **Step 1: Write the failing test**
```python
from goldenmatch.core.survivorship.winner import group_winner

def _rows(spec):
    return [{"__pos__": i, **r} for i, r in enumerate(spec)]


def test_anchor_picks_anchor_bearing_most_complete():
    rows = _rows([
        {"plan_id": None, "plan_name": "Gold", "plan_tier": "G"},   # no anchor
        {"plan_id": "P1", "plan_name": "Gold", "plan_tier": None},  # anchor, 2/3
        {"plan_id": "P1", "plan_name": "Gold", "plan_tier": "G"},   # anchor, 3/3 -> winner
    ])
    res = group_winner(rows, ["plan_id", "plan_name", "plan_tier"], strategy="anchor", anchor="plan_id")
    assert res.winner_pos == 2
    assert res.values["plan_tier"] == "G"


def test_anchor_fallback_to_most_complete_when_none_have_anchor():
    rows = _rows([{"plan_id": None, "plan_name": "A", "plan_tier": "X"},
                  {"plan_id": None, "plan_name": "B", "plan_tier": None}])
    res = group_winner(rows, ["plan_id", "plan_name", "plan_tier"], strategy="anchor", anchor="plan_id")
    assert res.winner_pos == 0   # most-complete fallback
```

- [ ] **Step 2: Run to verify pass** (the B1 anchor branch should already satisfy these; if a test fails, fix the anchor ranking).
- [ ] **Step 3:** (no new impl beyond B1's anchor branch unless a test fails).
- [ ] **Step 4: Commit** `feat(survivorship): anchor group-winner strategy`.

### Task B3: `allow_fill` per-cell back-fill

**Files:** Modify `winner.py`; Test: `test_allow_fill_anchor.py`

- [ ] **Step 1: Write the failing test**
```python
def test_allow_fill_fills_winner_nulls_from_strategy_best_other_row():
    # winner (pos 0) most-complete but missing zip; pos 1 has zip
    rows = _rows([
        {"street": "1 Main St", "city": "LA", "zip": None},   # 2/3 -> winner
        {"street": "1 Main", "city": "LA", "zip": "90001"},   # 2/3 (lower rank, has zip)
    ])
    res = group_winner(rows, ["street", "city", "zip"], strategy="most_complete", allow_fill=True)
    assert res.winner_pos == 0
    assert res.values["zip"] == "90001"        # back-filled
    assert res.filled == {"zip": 1}            # positional source
    assert res.confidence == 1.0               # 2 winner + 1 filled = 3/3


def test_allow_fill_off_keeps_winner_null():
    rows = _rows([{"street": "1 Main St", "city": "LA", "zip": None},
                  {"street": "1 Main", "city": "LA", "zip": "90001"}])
    res = group_winner(rows, ["street", "city", "zip"], strategy="most_complete")  # allow_fill default False
    assert res.values["zip"] is None and res.filled == {}


def test_allow_fill_nothing_to_fill():
    rows = _rows([{"a": "x", "b": "y"}, {"a": "p", "b": None}])
    res = group_winner(rows, ["a", "b"], strategy="most_complete", allow_fill=True)
    assert res.filled == {}                    # winner already complete
```

- [ ] **Step 2: Run to verify it fails** (no fill yet).
- [ ] **Step 3: Implement** the fill block in `group_winner` (between `values = {...}` and the confidence calc):
```python
    if allow_fill:
        for c in columns:
            if values[c] is None:
                for j in ranking[1:]:
                    if rows[j].get(c) is not None:
                        values[c] = rows[j].get(c)
                        filled[c] = rows[j].get("__pos__", j)
                        break
```
(The confidence calc from B1 already uses `len(filled)`.)

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat(survivorship): allow_fill per-cell back-fill of group nulls`.

---

## Phase C — resolve.py threading

### Task C1: thread anchor/allow_fill + map filled -> row ids

**Files:** Modify `core/survivorship/resolve.py`; Test: `test_allow_fill_anchor.py`

- [ ] **Step 1: Write the failing test** (end-to-end through `resolve_cluster`)
```python
import polars as pl
from goldenmatch.config.schemas import GoldenRulesConfig, GoldenGroupRule
from goldenmatch.core.survivorship.conditions import build_resolution_order
from goldenmatch.core.survivorship.resolve import resolve_cluster


def test_resolve_allow_fill_records_filled_row_id():
    df = pl.DataFrame({
        "__cluster_id__": [5, 5], "__row_id__": [10, 11],
        "street": ["1 Main St", "1 Main"], "city": ["LA", "LA"], "zip": [None, "90001"],
    })
    rules = GoldenRulesConfig(default_strategy="most_complete", field_groups=[
        GoldenGroupRule(name="addr", columns=["street", "city", "zip"], allow_fill=True)])
    order = build_resolution_order(rules.field_rules, rules.field_groups, ["street", "city", "zip"])
    rec, prov = resolve_cluster(df, rules, order, provenance=True, cluster_id=5)
    gp = prov.groups[0]
    assert gp.values["zip"] == "90001"
    assert gp.filled == {"zip": 11}            # mapped positional -> row_id
    # the resolved golden value is the filled value
    assert rec["zip"]["value"] == "90001"
    assert rec["zip"]["source_row_id"] == 11   # filled-cell source points at the fill record
```

- [ ] **Step 2: Run to verify it fails** (`gp.filled` attr missing until C/D; or filled not mapped).
- [ ] **Step 3: Implement** in the group branch of `resolve.py`:
```python
            res = group_winner(rows, list(g.columns), strategy=g.strategy,
                               source_priority=g.source_priority, dates=dates,
                               anchor=g.anchor, allow_fill=g.allow_fill)
            wid = (row_id_array[res.winner_pos] if (row_id_array is not None and res.winner_pos is not None and res.winner_pos >= 0) else None)
            wsrc = (source_array[res.winner_pos] if (source_array is not None and res.winner_pos is not None and res.winner_pos >= 0) else None)
            filled_ids = ({c: row_id_array[p] for c, p in res.filled.items()}
                          if row_id_array is not None else dict(res.filled))
            for c in g.columns:
                v = res.values.get(c)
                fd = {"value": v, "confidence": res.confidence}
                if provenance:
                    fd["source_row_id"] = filled_ids.get(c, wid)   # filled-cell -> fill source, else winner
                field_dicts[c] = fd
                resolved[c] = v
            confidences.append(res.confidence)
            if provenance:
                group_provs.append(GroupProvenance(
                    name=g.name, columns=list(g.columns), strategy=g.strategy,
                    winner_row_id=wid, winner_source=wsrc,
                    values=dict(res.values), tie=res.tie, confidence=res.confidence,
                    filled=filled_ids,
                ))
```
(The `source_row_id`/`filled` writes are inside the existing `if provenance:` guards; the non-provenance path is unchanged for byte-parity. `GroupProvenance.filled` is added in D1.)

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat(survivorship): thread anchor/allow_fill + filled provenance through resolve`.

---

## Phase D — provenance + NL

### Task D1: `GroupProvenance.filled`

**Files:** Modify `core/golden.py`; Test: covered by C1 (gp.filled).

- [ ] **Step 1:** Add the field to the `GroupProvenance` dataclass:
```python
@dataclass
class GroupProvenance:
    name: str
    columns: list[str]
    strategy: str
    winner_row_id: int
    winner_source: str | None
    values: dict[str, Any]
    tie: bool
    confidence: float
    filled: dict[str, int] = dataclass_field(default_factory=dict)   # {col: source row id}
```
(Use the module's existing `dataclass_field` alias. Defaulted -> existing construction unaffected.)

- [ ] **Step 2: Run** C1's test -> now passes.
- [ ] **Step 3: Commit** `feat(golden): GroupProvenance.filled field`.

### Task D2: back-fill NL line

**Files:** Modify `core/lineage.py` (`render_group_provenance_line`); Test: `test_allow_fill_anchor.py`

- [ ] **Step 1: Write the failing test**
```python
from goldenmatch.core.golden import GroupProvenance
from goldenmatch.core.lineage import render_group_provenance_line


def test_render_group_line_includes_backfill():
    gp = GroupProvenance(name="mailing_address", columns=["street", "city", "zip"], strategy="most_complete",
                         winner_row_id=7, winner_source=None, values={}, tie=False, confidence=1.0,
                         filled={"zip": 12})
    out = render_group_provenance_line(gp)
    assert "promoted together from record 7" in out
    assert "mailing_address: zip back-filled from record 12" in out


def test_render_group_line_no_fill_unchanged():
    gp = GroupProvenance(name="addr", columns=["a", "b"], strategy="most_complete",
                         winner_row_id=7, winner_source=None, values={}, tie=False, confidence=1.0)
    out = render_group_provenance_line(gp)
    assert "back-filled" not in out
```

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement** — widen the renderer to append fill lines (it already returns a string; `render_cluster_provenance_nl` joins per-group output with newlines, so a multi-line return is fine):
```python
def render_group_provenance_line(gp) -> str:
    cols = ", ".join(gp.columns)
    line = (f"{cols} promoted together from record {gp.winner_row_id} "
            f"via {gp.strategy} (group '{gp.name}')")
    fills = [f"{gp.name}: {col} back-filled from record {rid}"
             for col, rid in (getattr(gp, "filled", {}) or {}).items()]
    return "\n".join([line, *fills]) if fills else line
```

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat(lineage): back-fill NL audit line for allow_fill`.

### Task D3: omit empty `filled` in `golden_records` serialization

**Files:** Modify `core/lineage.py` (`_serialize_golden_records`); Test: `test_allow_fill_anchor.py`

- [ ] **Step 1: Write the failing test**
```python
import json
from goldenmatch.core.golden import ClusterProvenance, GroupProvenance
from goldenmatch.core.lineage import save_lineage


def _cp(filled):
    g = GroupProvenance(name="addr", columns=["street", "zip"], strategy="most_complete",
                        winner_row_id=7, winner_source=None, values={}, tie=False, confidence=1.0, filled=filled)
    return [ClusterProvenance(cluster_id=5, cluster_quality="strong", cluster_confidence=0.9, fields={}, groups=[g])]


def test_filled_omitted_when_empty(tmp_path):
    path = save_lineage([], tmp_path, "run", golden_provenance=_cp({}))
    grp = json.loads(path.read_text(encoding="utf-8"))["golden_records"][0]["groups"][0]
    assert "filled" not in grp


def test_filled_present_when_nonempty(tmp_path):
    path = save_lineage([], tmp_path, "run", golden_provenance=_cp({"zip": 12}))
    grp = json.loads(path.read_text(encoding="utf-8"))["golden_records"][0]["groups"][0]
    assert grp["filled"] == {"zip": 12}
```

- [ ] **Step 2: Run to verify it fails** (empty `filled: {}` present).
- [ ] **Step 3: Implement** — in `_serialize_golden_records`, after building `records`, drop empty `filled` from each serialized group dict:
```python
def _serialize_golden_records(provenance: list) -> list[dict]:
    records = _serialize_provenance(provenance)
    for cp, rec in zip(provenance, records):
        audit = _safe_cluster_audit(cp)
        if audit:
            rec["audit"] = audit
        for grp in rec.get("groups", []):
            if not grp.get("filled"):
                grp.pop("filled", None)
    return records
```

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `fix(lineage): omit empty filled in golden_records (parity)`.

---

## Phase E — combined + docs + parity

### Task E1: allow_fill + anchor together + parity gate

**Files:** Test: `tests/survivorship/test_allow_fill_anchor.py`

- [ ] **Step 1: Write the tests**
```python
def test_anchor_plus_allow_fill():
    rows = _rows([
        {"plan_id": "P1", "plan_name": "Gold", "plan_tier": None},  # anchor winner (2/3)
        {"plan_id": None, "plan_name": "Gold", "plan_tier": "G"},   # has tier
    ])
    res = group_winner(rows, ["plan_id", "plan_name", "plan_tier"],
                       strategy="anchor", anchor="plan_id", allow_fill=True)
    assert res.winner_pos == 0
    assert res.values["plan_tier"] == "G" and res.filled == {"plan_tier": 1}


def test_no_levers_byte_identical():
    # strict lock-step (no allow_fill, default strategy) pins winner's null + empty filled
    rows = _rows([{"a": "x", "b": None}, {"a": "p", "b": "q"}])
    res = group_winner(rows, ["a", "b"], strategy="most_complete")
    assert res.values["b"] is None and res.filled == {}
```

- [ ] **Step 2: Run to verify pass.**
- [ ] **Step 3: Commit** `test(survivorship): anchor+allow_fill combined + parity`.

### Task E2: docs

**Files:** `docs-site/goldenmatch/configuration.mdx`

- [ ] **Step 1:** In the "Lock-step field groups" section, document `strategy: anchor` (with `anchor: <column>`) and `allow_fill: true` (per-cell back-fill of the winner's nulls; the back-fill is recorded in the lineage audit and `filled`). Match the existing voice; ASCII only.
- [ ] **Step 2: Commit** `docs: anchor strategy + allow_fill in configuration`.

---

## Open items carried from the spec (resolve during execution)

- **Refactor parity (B1) is the highest-risk step.** Keep ALL existing `test_winner.py` cases green throughout; the new TIE-cluster tests pin the lowest-index winner. `sorted(reverse=True)` is stable in Python (ties keep ascending index) -> safe; the tests are the guard.
- **`source_priority`/`most_recent` fill order:** the fill walks the SAME strategy ranking (`ranking[1:]`), so fills come from the strategy-best other row -- intended per spec.
- **Non-provenance path untouched:** the `source_row_id`/`filled` writes are inside `if provenance:`; `provenance=False` stays byte-identical.
