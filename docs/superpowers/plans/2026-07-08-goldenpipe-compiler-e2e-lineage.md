# GoldenPipe Compiler SP3 — End-to-End Field Lineage — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface goldenmatch's existing golden-provenance as a GoldenPipe artifact + stitch it with SP2's field-lineage into an end-to-end per-golden-field journey (pre-match Flow-clean × post-match survivorship).

**Architecture:** Host-only Python (no kernel/Rust — needs the Match execution output). Part A: the match adapter calls goldenmatch's `golden_provenance_for_run(result.dupes, clusters, result.config.golden_rules)` and attaches `ctx.artifacts["golden_provenance"]`. Part B: `end_to_end_lineage(compiled, golden_provenance)` joins SP2 `field_lineage` with the provenance on column name. Additive, byte-identical (None when survivorship inactive).

**Tech Stack:** Python (goldenpipe compiler host + goldenmatch lineage). Reuses goldenmatch's provenance engine wholesale.

---

## Box / environment constraints

- **Python is box-runnable** (real red→green). No Rust, no cross-surface, no kernel.
  ```bash
  INTERP="D:/show_case/goldenmatch/.venv/Scripts/python.exe"
  export PYTHONPATH="packages/python/goldenpipe;packages/python/goldencheck;packages/python/infermap;packages/python/goldencheck-types;packages/python/goldenflow;packages/python/goldenmatch"
  export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 GOLDENMATCH_NATIVE=0 GOLDENMATCH_AUTOCONFIG_MEMORY=0
  ```
  `cd "D:/show_case/gg-local-llm"` each Bash call. `ruff check` touched Python.
- SP2 is merged into `main`; this branch is off fresh `main` so `goldenpipe/compiler/{lineage,provenance,ir,capture}.py` are present.
- Spec: `docs/superpowers/specs/2026-07-08-goldenpipe-compiler-e2e-lineage-design.md`.

## Verified shapes (from the code)

- SP2 `field_lineage(compiled) -> {"fields":[{column, checks, transforms, blocking_key, scorer_input, ...}], "unmapped":...}` (importable from `goldenpipe.compiler.lineage`).
- goldenmatch `golden_provenance_for_run(data_df, clusters, rules) -> list[ClusterProvenance] | None` (fail-open; None for non-survivorship / no multi-member clusters). `ClusterProvenance` dataclass: `cluster_id`, `fields: dict[str, FieldProvenance]`. `FieldProvenance`: `value, source_row_id, strategy, confidence, ...`.
- Match adapter `DedupeStage.run` (`adapters/match.py`): sets `ctx.artifacts["clusters"]`/`["dupes"]` (lines ~71-78) before `return StageResult(SUCCESS)` (line ~94). `result.dupes` carries `__row_id__` (Int64); `result.config.golden_rules` is the `GoldenRulesConfig | None`.
- `_survivorship_active` (True only for `field_groups` / list-form `field_rules` / `when`/`validate_with`) — default & auto-config pipelines → `None`.

## File structure

- Create `packages/python/goldenpipe/goldenpipe/compiler/e2e.py` — `end_to_end_lineage`, `format_end_to_end`, `surface_golden_provenance`.
- Modify `packages/python/goldenpipe/goldenpipe/adapters/match.py` — call `surface_golden_provenance` in `DedupeStage.run`.
- Tests: `tests/compiler/test_e2e.py`, `test_e2e_surface.py`, `test_e2e_integration.py`.

---

## Task 1: The stitch — `end_to_end_lineage` + `format_end_to_end` (box TDD)

**Files:** Create `goldenpipe/compiler/e2e.py`; Test `tests/compiler/test_e2e.py`.

- [ ] **Step 1: Write the failing test** `tests/compiler/test_e2e.py`:

```python
from types import SimpleNamespace

from goldenpipe.compiler.e2e import end_to_end_lineage, format_end_to_end


def _cp(nodes):
    return {"nodes": nodes, "edges": []}


def _n(kind, nid, **rest):
    return {"kind": kind, "id": nid, "origin_stage": "s", "resolved": False, **rest}


def test_stitch_combines_survivorship_and_plan():
    compiled = _cp([
        _n("Scan", 0, column="email", ops=["pattern_consistency"]),
        _n("Map", 1, column="email", op="email_normalize"),
        _n("Partition", 2, keys=["email"]),
    ])
    fp = SimpleNamespace(value="j@x.com", source_row_id=24, strategy="conditional", confidence=1.0)
    cp = SimpleNamespace(cluster_id=1, fields={"email": fp})
    out = end_to_end_lineage(compiled, [cp])
    assert len(out["entries"]) == 1
    e = out["entries"][0]
    assert e["source_row_id"] == 24 and e["strategy"] == "conditional"          # goldenmatch
    assert e["transforms"] == ["email_normalize"] and e["blocking_key"] is True  # SP2 (email is a Partition key)
    assert e["checks"] == ["pattern_consistency"]


def test_none_provenance_degrades_with_note():
    out = end_to_end_lineage({"nodes": [], "edges": []}, None)
    assert out["entries"] == []
    assert "survivorship inactive" in out["notes"][0]


def test_column_without_sp2_lineage_gets_empty_plan():
    fp = SimpleNamespace(value="x", source_row_id=3, strategy="conditional", confidence=0.9)
    cp = SimpleNamespace(cluster_id=1, fields={"phone": fp})
    out = end_to_end_lineage({"nodes": [], "edges": []}, [cp])
    e = out["entries"][0]
    assert e["source_row_id"] == 3 and e["transforms"] == [] and e["blocking_key"] is False


def test_format_end_to_end():
    out = {"entries": [{
        "cluster_id": 1, "column": "email", "value": "j@x.com", "source_row_id": 24,
        "strategy": "conditional", "survivor_confidence": 1.0, "checks": [],
        "transforms": ["email_normalize"], "blocking_key": False, "scorer_input": True,
    }], "notes": []}
    assert format_end_to_end(out) == (
        "cluster 1 email = 'j@x.com' (row 24 via conditional); pre-match transforms[email_normalize], scorer-input"
    )
```

- [ ] **Step 2: Run to verify FAIL.**

- [ ] **Step 3: Implement `e2e.py`** (the stitch + format; `surface_golden_provenance` is added in Task 2 — you may stub it or add it now):

```python
"""End-to-end field lineage (SP3): stitch SP2 field-lineage (pre-match Flow-clean +
matching role, from the IR) with goldenmatch's golden-provenance (post-match
survivorship) into one per-golden-field journey. Host-only — needs the Match output."""
from __future__ import annotations

from goldenpipe.compiler.lineage import field_lineage


def end_to_end_lineage(compiled: dict, golden_provenance: list | None) -> dict:
    """Join SP2 field-lineage with goldenmatch ClusterProvenance on column name.
    Returns {entries, notes}. None/empty provenance -> [] + a note (plan-only view)."""
    if not golden_provenance:
        return {"entries": [], "notes": ["survivorship inactive — use field_lineage(compiled) for the plan-only view"]}
    by_col = {f["column"]: f for f in field_lineage(compiled).get("fields", [])}
    entries = []
    for cp in golden_provenance:
        cid = getattr(cp, "cluster_id", None)
        for col, fp in (getattr(cp, "fields", None) or {}).items():
            plan = by_col.get(col, {})
            entries.append({
                "cluster_id": cid,
                "column": col,
                "value": getattr(fp, "value", None),
                "source_row_id": getattr(fp, "source_row_id", None),
                "strategy": getattr(fp, "strategy", None),
                "survivor_confidence": getattr(fp, "confidence", None),
                "checks": list(plan.get("checks", [])),
                "transforms": list(plan.get("transforms", [])),
                "blocking_key": bool(plan.get("blocking_key", False)),
                "scorer_input": bool(plan.get("scorer_input", False)),
            })
    return {"entries": entries, "notes": []}


def format_end_to_end(result: dict) -> str:
    lines = []
    for e in result.get("entries", []):
        pre = []
        if e["transforms"]:
            pre.append(f"transforms[{','.join(e['transforms'])}]")
        pre.extend(r for r, on in (("blocking-key", e["blocking_key"]), ("scorer-input", e["scorer_input"])) if on)
        pre_s = ("; pre-match " + ", ".join(pre)) if pre else ""
        lines.append(f"cluster {e['cluster_id']} {e['column']} = {e['value']!r} (row {e['source_row_id']} via {e['strategy']}){pre_s}")
    for n in result.get("notes", []):
        lines.append(f"# {n}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run to verify PASS (4 tests).**
- [ ] **Step 5: ruff + commit** (`feat(goldenpipe): end-to-end field lineage stitch (SP3)`).

---

## Task 2: Surface goldenmatch provenance in the Match adapter (box TDD)

**Files:** Modify `goldenpipe/compiler/e2e.py` (add `surface_golden_provenance`) + `goldenpipe/adapters/match.py`; Test `tests/compiler/test_e2e_surface.py`.

- [ ] **Step 1: Write the failing test** `tests/compiler/test_e2e_surface.py` (monkeypatch goldenmatch's entry so the WIRING is tested deterministically — no full dedupe needed):

```python
from types import SimpleNamespace

from goldenpipe.compiler.e2e import surface_golden_provenance


def test_surface_passes_dupes_clusters_rules_and_returns_provenance(monkeypatch):
    calls = {}
    def fake(data_df, clusters, rules):
        calls["args"] = (data_df, clusters, rules)
        return ["PROV"]
    monkeypatch.setattr("goldenmatch.core.lineage.golden_provenance_for_run", fake)
    result = SimpleNamespace(dupes="DUPES_DF", config=SimpleNamespace(golden_rules="RULES"))
    out = surface_golden_provenance(result, {1: {"members": [0, 1], "size": 2}})
    assert out == ["PROV"]
    assert calls["args"] == ("DUPES_DF", {1: {"members": [0, 1], "size": 2}}, "RULES")


def test_surface_none_when_no_rules():
    result = SimpleNamespace(dupes="DUPES_DF", config=SimpleNamespace(golden_rules=None))
    assert surface_golden_provenance(result, {1: {"members": [0, 1]}}) is None


def test_surface_none_when_no_dupes_or_clusters():
    result = SimpleNamespace(dupes=None, config=SimpleNamespace(golden_rules="RULES"))
    assert surface_golden_provenance(result, {1: {}}) is None
    result2 = SimpleNamespace(dupes="DF", config=SimpleNamespace(golden_rules="RULES"))
    assert surface_golden_provenance(result2, None) is None


def test_surface_fail_open_on_error(monkeypatch):
    def boom(*a): raise RuntimeError("x")
    monkeypatch.setattr("goldenmatch.core.lineage.golden_provenance_for_run", boom)
    result = SimpleNamespace(dupes="DF", config=SimpleNamespace(golden_rules="RULES"))
    assert surface_golden_provenance(result, {1: {"members": [0, 1]}}) is None
```

- [ ] **Step 2: Run to verify FAIL.**

- [ ] **Step 3: Add `surface_golden_provenance` to `e2e.py`:**

```python
def surface_golden_provenance(result, clusters):
    """Reuse goldenmatch's golden_provenance_for_run to rebuild ClusterProvenance from a
    finished DedupeResult. Returns list|None (None when survivorship inactive, no dupes/
    clusters/rules, or any error — fail-open). data_df=result.dupes (carries __row_id__);
    rules=result.config.golden_rules."""
    try:
        from goldenmatch.core.lineage import golden_provenance_for_run
        cfg = getattr(result, "config", None)
        rules = getattr(cfg, "golden_rules", None) if cfg is not None else None
        dupes = getattr(result, "dupes", None)
        if dupes is None or not clusters or rules is None:
            return None
        return golden_provenance_for_run(dupes, clusters, rules)
    except Exception:
        return None
```

- [ ] **Step 4: Wire into `DedupeStage.run` in `adapters/match.py`.** Immediately BEFORE the final `return StageResult(status=StageStatus.SUCCESS)` (after the `dupes`/`clusters`/`scored_pairs` artifact assignments, ~line 93), add:
```python
        # SP3: surface goldenmatch's golden-record provenance (survivorship audit) as an
        # advisory pipeline artifact. None (byte-identical) unless survivorship is active.
        from goldenpipe.compiler.e2e import surface_golden_provenance
        ctx.artifacts["golden_provenance"] = surface_golden_provenance(result, ctx.artifacts.get("clusters"))
```
`surface_golden_provenance` is itself fail-open, so no extra try/except is needed here — it returns None on any problem. Confirm `result` is in scope at that point (it is — the artifact assignments above use it).

- [ ] **Step 5: Run to verify PASS + no regression.**
```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/compiler/test_e2e_surface.py packages/python/goldenpipe/tests/test_adapters.py -q
```
Expected: surface tests pass; `test_adapters` unchanged (the new artifact is additive; a default config yields None).

- [ ] **Step 6: ruff + commit** (`feat(goldenpipe): surface goldenmatch golden-provenance in match adapter (SP3)`).

---

## Task 3: Real-pipeline end-to-end proof (box)

**Files:** Test `tests/compiler/test_e2e_integration.py`.

- [ ] **Step 1: Write the integration test.** Build a tiny fixture + a **survivorship-ACTIVE** explicit match config so `golden_provenance` populates.

  **CRITICAL (from review): the stitch reads only `ClusterProvenance.fields` (the SCALAR survivorship branch), NOT `.groups`.** Group-member columns land in `cp.groups`, which SP3 does not read. So the Flow-transformed column you assert on (`email`) MUST be a **scalar** column (not a group member) — then `build_resolution_order` adds it as a scalar unit and it appears in `cp.fields` with a `source_row_id`. Trip `_survivorship_active` via `field_groups` on *other* columns.

  **Concrete minimal config** (verified to trip `_survivorship_active` via the `field_groups` branch, no `when:`-predicate risk):
  ```python
  from goldenmatch.config.schemas import GoldenRulesConfig, GoldenGroupRule  # confirm exact names
  golden_rules = GoldenRulesConfig(
      default_strategy="most_complete",
      field_groups=[GoldenGroupRule(name="loc", columns=["city", "state"], strategy="most_complete")],
  )
  # pass on the match StageSpec: config=GoldenMatchConfig(matchkeys=..., blocking=..., golden_rules=golden_rules)
  ```
  **Fixture columns:** `email` (dirty → Flow-transformed, SCALAR — the column you assert), `city`+`state` (the ≥2-column group), a name column for blocking (soundex-spread surnames), and real duplicates (so there's a multi-member cluster). Confirm `GoldenGroupRule`/`GoldenRulesConfig` exact names + required fields against `goldenmatch/config/schemas.py`.

  Run the full `load→check→flow→match` with that config, then:
  - Assert `ctx.artifacts["golden_provenance"]` is not None (Part A surfaced it).
  - `compile_and_run` the same plan to get `compiled`; `end_to_end_lineage(compiled, ctx.artifacts["golden_provenance"])` → assert at least one entry for the **scalar `email` column** carries **both** `source_row_id` (from goldenmatch `cp.fields`) **and** non-empty `transforms` (from SP2).

  **Fallback (if the active config is still fiddly):** keep the assertion to "golden_provenance is not None AND end_to_end_lineage yields ≥1 entry with a source_row_id" — the Task-1 unit test carries the join fidelity; this test's job is proving Part-A surfacing produces REAL provenance end-to-end. Do NOT weaken to a monkeypatched provenance here (Task 2 covers wiring; this must exercise the REAL goldenmatch lineage path). If you genuinely cannot get `_survivorship_active` true after a real effort, report BLOCKED with the exact config + why `golden_provenance` stayed None — do not fake it.

  Env: `GOLDENMATCH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_AUTOCONFIG_MEMORY=0`; tiny fixture, soundex-spread surnames.

- [ ] **Step 2: Run to verify PASS.** Report the printed end-to-end lineage for the survivorship-active field (source_row_id + transforms) — the SP3 headline artifact.

- [ ] **Step 3: ruff + commit** (`test(goldenpipe): real-pipeline end-to-end lineage proof (SP3)`).

---

## Task 4: Ship

- [ ] **Step 1:** SP2 (#1597) is already merged; this branch is off fresh `main`. `git fetch origin && git rebase origin/main` (resolve any conflict in `adapters/match.py`).
- [ ] **Step 2:** Re-run the box suite:
```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/compiler/ packages/python/goldenpipe/tests/test_adapters.py -q
```
Expected: green (SP1/SP2 compiler tests + the 3 SP3 test files + adapters no-regression).
- [ ] **Step 3:** Push + PR + arm auto-merge, then **STOP** (no CI polling):
```bash
unset GH_TOKEN; gh auth switch --user benzsevern; export GH_TOKEN=$(gh auth token --user benzsevern)
git push -u origin feat/goldenpipe-compiler-e2e-lineage
gh pr create --base main --title "feat(goldenpipe): compiler SP3 — end-to-end field lineage" --body "<summary: surface goldenmatch golden-provenance in the match adapter + stitch with SP2 field-lineage into a per-golden-field journey (pre-match Flow-clean x post-match survivorship). Host-only, reuses goldenmatch's lineage engine wholesale; additive/byte-identical (None when survivorship inactive). Links spec+plan. Note SP3 of the compiler program.>"
gh pr merge <N> --auto --squash   # merge-queue: NO --delete-branch
```
Watch CI: python (goldenpipe) — the SP3 tests + adapters.

---

## Verification summary
- Box-green: `test_e2e` (stitch join, None-degrade, format), `test_e2e_surface` (adapter wiring, fail-open, None cases), `test_e2e_integration` (real goldenmatch provenance + stitch), `test_adapters` (no regression — additive artifact).
- Additive/byte-identical: `golden_provenance` is a new artifact key, `None` for default/auto pipelines (survivorship gate narrow); the stitch + format are opt-in host functions. No kernel, no cross-surface, no execution change.
