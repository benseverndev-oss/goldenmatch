---
title: allow_fill + anchor group-winner strategy
date: 2026-06-17
status: design (approved in brainstorming; pre-spec-review)
owner: Ben Severn
related:
  - docs/superpowers/specs/2026-06-17-correlated-survivorship-and-conditional-golden-rules-design.md
  - docs/superpowers/specs/2026-06-17-groupprovenance-surfacing-design.md
---

# allow_fill + anchor group-winner strategy

> v2 workstreams 3 + 4 of the correlated-survivorship program, bundled.
> Stacks on workstream 1 (GroupProvenance surfacing, PR #1053) which stacks
> on v1 (merged). Design-only until #1053 lands.

## 1. Problem

v1 lock-step `field_groups` promote a set of columns together from ONE
winning source record (`core/survivorship/winner.py::group_winner`). Two
real survivorship needs the v1 spec deferred:

**allow_fill.** Strict lock-step pins the winner's values **including its
nulls** (`winner.py`: `values = {c: rows[best].get(c) for c in columns}`).
If the winning record is missing a group cell, the golden record is null
there even when a sibling record in the cluster has the value. Users want
to optionally back-fill those gaps.

**anchor strategy.** The three group strategies (`most_complete`,
`source_priority`, `most_recent`) pick the winner by a property of the
*whole group*. Sometimes the right winner is "whoever has the key column":
for `{plan_id, plan_name, plan_tier}` you want the record that HAS a
`plan_id`, then bring `plan_name`/`plan_tier` along. There is no strategy
that selects on one designated member column.

Confirmed against current code: `group_winner` has the three strategies
and the strict-lock-step pin; `GoldenGroupRule` (`config/schemas.py`)
validates `strategy in _GROUP_STRATEGIES = {most_complete, source_priority,
most_recent}`; `GroupResult` (`winner.py`) is
`(winner_pos, values, confidence, tie)`; `GroupProvenance` (`core/golden.py`)
is `(name, columns, strategy, winner_row_id, winner_source, values, tie,
confidence)`; the surfacing (workstream 1) renders
`render_group_provenance_line(gp)` into lineage/explain/MCP.

## 2. Goals / non-goals

**Goals**
- `allow_fill` (per-group, opt-in): when the winner's group cell is null,
  back-fill it **per-cell** from the best *other* row by the group's own
  strategy. Maximizes completeness.
- `anchor` (new group strategy): pick the winner among rows whose anchor
  column is non-null (most-complete tiebreak); fall back to plain
  `most_complete` if no row has the anchor.
- Both record their provenance (which cells were filled, from which
  record) and surface it through the workstream-1 audit trail.
- **Byte-identical output when neither lever is used** (`allow_fill=False`
  and no `anchor` strategy). Parity gate.
- Fail-safe: the features are pure group-winner logic; no new external
  deps, no distributed-path change (survivorship already refuses Ray/Sail).

**Non-goals**
- Single-runner-up / ranked-fill policies (the brainstorm chose per-cell
  from any row).
- Cross-group fill or filling from outside the cluster.
- A configurable anchor sub-strategy (anchor uses most_complete tiebreak;
  a sub-strategy knob is deferred — YAGNI until asked).
- Changing strict-lock-step semantics when `allow_fill=False`.

## 3. Design overview

One unifying refactor in `winner.py`: compute a **strategy-ordered ranking
of row indices** once; the winner is `ranking[0]`, and `allow_fill` walks
the rest of the ranking per null cell. `anchor` is a new ranking. Both new
behaviors are additive and gated; `GroupResult` and `GroupProvenance` each
gain a `filled` map. The config gains two fields. `resolve.py` threads the
new inputs/outputs. The NL renderer gains one fill line.

---

## Section 1 — Config surface (`config/schemas.py`)

`GoldenGroupRule` gains two fields:

```python
class GoldenGroupRule(BaseModel):
    name: str
    columns: list[str]
    category: str | None = None
    strategy: str = "most_complete"
    date_column: str | None = None
    source_priority: list[str] | None = None
    anchor: str | None = None        # NEW: required iff strategy == "anchor"
    allow_fill: bool = False         # NEW: per-cell back-fill of winner nulls
```

`_GROUP_STRATEGIES` gains `"anchor"`. `_validate_group` adds:
- `strategy == "anchor"` requires `anchor` set, and `anchor in columns`.
- `anchor` set with a non-anchor strategy is allowed-but-ignored? No —
  reject (`anchor` only meaningful for `strategy == "anchor"`) to avoid
  silent misconfig. (Decision recorded; flag for spec review if the
  reviewer prefers ignore-with-warning.)

`allow_fill` is orthogonal to strategy: valid with any strategy including
`anchor`. Default `False` everywhere → strict lock-step unchanged.

---

## Section 2 — `winner.py`

### 2.1 Ranking refactor

Introduce an internal `_ranking(rows, columns, strategy, *, source_priority,
dates, anchor) -> list[int]` returning row indices best-to-worst:
- `most_complete`: by `_populated(row, columns)` desc (stable).
- `source_priority`: by source rank asc (then stable).
- `most_recent`: by date desc, nulls last (then stable).
- `anchor`: rows with non-null `anchor` first (most-complete among them),
  then the rest by most_complete. If NO row has the anchor, the ranking is
  exactly the `most_complete` ranking (the documented fallback).

Winner = `ranking[0]`. `tie` is computed as today per strategy
(`most_complete`/`anchor`: more than one row tied on the top key →
`tie=True`, 0.7 confidence penalty; `source_priority`/`most_recent`:
index-stable, no tie penalty).

**Stable-sort hazard (load-bearing for refactor parity).** Today's winner
is the LOWEST index among ties (`min`/`max` keep the first maximal element;
`most_complete` uses `winners[0]`). A naive `sorted(range(n), key=...,
reverse=True)` would INVERT tie order and pick a different winner, silently
breaking parity. Build the descending ranking WITHOUT reversing tie order
(e.g. sort an ascending key already oriented best-first, or negate the
numeric key and sort ascending so equal keys keep input order). The
refactor-parity test MUST include crafted *tie* clusters — that is the one
place "same winner as today" can quietly fail.

### 2.2 `allow_fill`

`group_winner(..., allow_fill=False)`. When `allow_fill` is True, after
pinning the winner's values, for each column `c` where the winner is null,
walk `ranking[1:]` and take the first row whose `c` is non-null:

```python
filled: dict[str, int] = {}
if allow_fill:
    for c in columns:
        if values[c] is None:
            for j in ranking[1:]:
                if rows[j].get(c) is not None:
                    values[c] = rows[j].get(c)
                    filled[c] = rows[j].get("__pos__", j)   # positional id
                    break
```

`filled` is `{column: source positional index}` (resolve maps to row id).
Empty when `allow_fill` is False or nothing was filled.

### 2.3 `GroupResult`

```python
@dataclass
class GroupResult:
    winner_pos: int
    values: dict[str, Any]
    confidence: float
    tie: bool
    filled: dict[str, int] = field(default_factory=dict)   # NEW: {col: source pos}
```

(defaulted so existing construction in tests/code is unaffected.)

### 2.4 Confidence

- `allow_fill`: completeness counts filled cells.
  `conf = (winner_populated + len(filled)) / len(columns)`, then `* 0.7`
  if `tie`. A fully back-filled group → 1.0 (×0.7 on a winner tie). When
  `allow_fill` is False, `filled` is empty → formula reduces to today's
  `winner_populated / len(columns)`. **This preserves parity exactly.**
- `anchor`: most_complete-style confidence on the winner
  (`winner_populated / len`, ×0.7 on tie). An all-null-anchor cluster uses
  the most_complete fallback confidence.

---

## Section 3 — `resolve.py`

The group branch (currently builds `rows`, calls `group_winner`, pins
`field_dicts`/`resolved`, builds `GroupProvenance`) changes minimally:
- Pass `allow_fill=g.allow_fill` and `anchor=g.anchor` to `group_winner`.
- Map `res.filled` (positional) to row ids:
  `filled_ids = {c: row_id_array[p] for c, p in res.filled.items()}` when
  `row_id_array` is present, else `{c: p ...}`.
- Pass `filled=filled_ids` into the `GroupProvenance(...)` it builds.
The pinned `res.values` (now possibly containing fills) flow to
`field_dicts`/`resolved` exactly as today — the golden record automatically
gets the filled values.

The per-column `source_row_id` recorded on the group's `field_dicts`
(used for the field-level provenance) should reflect the fill source for a
filled cell: for a filled column use `filled_ids[c]`, else the winner row
id. (Small consistency fix so a filled cell's `source_row_id` points at the
record the value actually came from.) **Scope strictly to the
`if provenance:` branch** in `resolve.py` (where `source_row_id` is set);
the non-provenance path (`row_id_array is None` / `provenance=False`) stays
untouched so its byte-parity holds.

---

## Section 4 — Provenance + NL (surfaces via workstream 1)

### 4.1 `GroupProvenance` (`core/golden.py`)

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
    filled: dict[str, int] = field(default_factory=dict)   # NEW: {col: source row id}
```

`winner_row_id`/`winner_source` stay the PRIMARY winner. `filled` records
which cells came from elsewhere. Defaulted → existing construction is
unaffected and the `asdict` serialization (workstream 1) round-trips it for
free. **Decided (was open):** the serializer omits `filled` when empty
(mirroring the workstream-1 `audit`-key parity fix), so group records for
strict-lock-step groups are unchanged — a small filter in the
`golden_records` serialization (`_serialize_golden_records` /
`_serialize_provenance` consumer) drops an empty `filled` key.

### 4.2 NL audit line (`core/lineage.py::render_group_provenance_line`)

Keep the existing "promoted together" line. When `filled` is non-empty,
append one line per filled cell:
`"{name}: {col} back-filled from record {row_id}"`
e.g. `"mailing_address: zip back-filled from record 12"`.
(Exact string, so the implementer doesn't invent it.) These render in the
lineage `audit`, `explain --cluster`, and the MCP `lineage` tool via the
workstream-1 wiring with no surface change.

Note: `render_group_provenance_line` currently returns a single string;
it widens to return the promotion line plus the fill lines (joined), and
`render_cluster_provenance_nl` already joins per-group output with
newlines, so the change is local.

---

## Section 5 — Parity, testing, module layout

**Load-bearing parity:** `allow_fill=False` + no `anchor` strategy →
byte-identical. Mechanisms: `allow_fill` defaults False (the fill loop is
skipped, `filled` empty, confidence formula reduces to today's); `anchor`
is opt-in (a config that doesn't use it never hits the new ranking branch);
the ranking refactor must produce the SAME winner as today for the three
existing strategies (a refactor-parity test pins this). A parity gate
asserts identical `GroupResult` for the existing strategies before/after.

**Tests** (extend `tests/survivorship/test_winner.py` + a new
`test_allow_fill_anchor.py`):
- **Refactor parity:** `_ranking` + `group_winner` produce the SAME
  winner/values/confidence/tie as the pre-refactor function for
  most_complete/source_priority/most_recent on crafted clusters.
- **anchor:** picks the anchor-bearing most-complete row; most-complete
  tiebreak among anchor-bearers; fallback to most_complete when no row has
  the anchor; tie penalty; config validation (anchor required + in columns;
  anchor with non-anchor strategy rejected).
- **allow_fill:** a winner with a null cell gets it filled from the
  strategy-best other row; `filled` records the right source id; multiple
  null cells each filled independently; nothing to fill → `filled` empty;
  `allow_fill=False` still pins the winner's null (strict); confidence
  counts filled cells.
- **allow_fill + anchor together:** anchor picks the winner, allow_fill
  fills its nulls.
- **Provenance/NL:** `GroupProvenance.filled` populated; the back-fill NL
  line appears in `render_group_provenance_line` and round-trips in the
  lineage `golden_records` audit; a filled cell's `source_row_id` points at
  the fill record.
- **Parity gate:** a no-levers config (no allow_fill, no anchor) →
  byte-identical golden output + provenance vs a baseline.

**Module layout:** all logic stays in `winner.py` (ranking + fill),
`config/schemas.py` (two fields + validation), `resolve.py` (threading),
`core/golden.py` (`GroupProvenance.filled`), `core/lineage.py` (NL line).
No new files. Docs sweep at the end (configuration.mdx field-groups
section: `anchor` + `allow_fill`; the surfacing note already covers the
audit line).

## Section 6 — Rollout / flags

No env flags. Both features are per-`GoldenGroupRule` opt-ins
(`strategy: anchor` / `allow_fill: true`). A config using neither is
byte-identical to today. Auto-detection (workstream 1's heuristic /
infermap path) does NOT propose `anchor`/`allow_fill` — they require
explicit user intent.

## Section 7 — Open questions for spec review

- **anchor + non-anchor-strategy config:** reject (chosen) vs.
  ignore-with-warning. Leaning reject (no silent misconfig).
- **Per-cell fill ranking for `source_priority`/`most_recent` groups:**
  confirm "best other row by the group's own strategy" is the intended
  fill order for non-most_complete strategies (vs. always most_complete
  for fills). Leaning strategy-consistent (use the same ranking).
