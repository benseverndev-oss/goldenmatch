# Bucket native default backend (up to 750k) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make bucket+native the default/suggested backend for the average `pip install goldenmatch` user up to 750k rows — by shipping the native kernel as a marker-guarded default dependency and adding a planner rule that selects bucket up to 750k on any box where the pairs fit RAM.

**Architecture:** Three independent pieces. (1) `pyproject.toml`: move `goldenmatch-native` from the `[native]` extra into core `dependencies` behind PEP 508 platform markers. (2) `core/autoconfig_planner_rules.py`: add a NEW `rule_bucket_suggested` (sub-32GB, `n_rows <= 750k`, pair-memory fits RAM) inserted after `rule_fast_box`; `fast_box` stays untouched. (3) Bench-validate bucket vs polars-direct at 200k/500k/750k on the bench box; the 750k constant is gated on that bench.

**Tech Stack:** Python 3.12, the v3 planner (`autoconfig_planner_rules.py` / `autoconfig_planner.py`), `goldenmatch-native` abi3 wheel, uv workspace, pytest.

**Scope:** Per `docs/superpowers/specs/2026-06-01-bucket-native-default-backend-design.md`. Does NOT cap bucket >750k (big boxes keep bucket via fast_box), does NOT resume polars-direct columnar work, does NOT change the `GOLDENMATCH_NATIVE` sign-off model.

---

## File Structure

- **Modify** `packages/python/goldenmatch/goldenmatch/core/autoconfig_planner_rules.py` — add `BUCKET_SUGGESTED_MAX_ROWS`, `_PAIR_SCORE_BYTES`, `_BUCKET_RAM_SAFETY_FRACTION`, `_is_bucket_suggested_eligible`, `_bucket_suggested_plan`, `rule_bucket_suggested`.
- **Modify** `packages/python/goldenmatch/goldenmatch/core/autoconfig_planner.py` (or wherever the rule sequence is assembled) — register `rule_bucket_suggested` immediately AFTER `rule_fast_box`.
- **Modify** `packages/python/goldenmatch/pyproject.toml` — native to marker-guarded core dep; keep `[native]` alias; `[tool.uv.sources]` path/workspace stays.
- **Test** `packages/python/goldenmatch/tests/` — find the existing planner test (e.g. `test_autoconfig_planner.py` / `test_planner_integration.py`); add the new-rule cases there.
- **Modify** docs (scale-envelope / README install) + confirm telemetry `rule_name`.
- **Bench** `scripts/bench_native_bucket.py` (existing) — run at 200k/500k/750k on `large-new-64GB`.

**Wave order:** Task 1 (planner, fully unit-tested, the core) → Task 2 (packaging) → Task 3 (bench validation, gates the 750k constant) → Task 4 (docs/telemetry). Tasks 1-2 are independent; Task 3 informs the final constant; Task 4 last.

---

## Task 1: Planner — `rule_bucket_suggested` (new rule, RAM-safe), TDD

**Files:**
- Modify: `core/autoconfig_planner_rules.py`
- Modify: `core/autoconfig_planner.py` (rule registration order)
- Test: the existing planner test module

### Task 1.1: the eligibility predicate + RAM-fit guard

- [ ] **Step 1: Read** `autoconfig_planner_rules.py:77-157` (`_is_simple_eligible`, `_is_fast_box_eligible`, `_fast_box_plan`, the `ExecutionPlan` fields, `profile.blocking.estimated_pair_count`, `runtime.available_ram_gb`, `runtime.cpu_count`). Read `autoconfig_planner.py` to find the ordered rule list (where `rule_simple_plan`, `rule_fast_box`, `rule_chunked` are sequenced).

- [ ] **Step 2: Write failing tests** in the planner test module. Mock/construct a `ComplexityProfile` (with `blocking.estimated_pair_count`) and `RuntimeProfile` (with `available_ram_gb`, `cpu_count`), and force native ON (`_scoring_backend()` returns "bucket"; monkeypatch `native_enabled` to True or set the env so the kill-switch is off — match how existing planner tests control this):

```python
# names per the existing test module's helpers; adapt to its fixtures.
def test_bucket_suggested_fires_sub32gb_under_750k_when_pairs_fit(monkeypatch):
    # 16GB box, 300k rows, 20M pairs (~1.3GB at 64B/pair) -> fits -> bucket
    monkeypatch.setattr(rules, "native_enabled", lambda c: True)
    prof = make_profile(estimated_pair_count=20_000_000)
    rt = make_runtime(available_ram_gb=16.0, cpu_count=8)
    assert rules._is_bucket_suggested_eligible(prof, rt, n_rows_full=300_000) is True
    plan = rules._bucket_suggested_plan(prof, rt, 300_000)
    assert plan.backend == "bucket"
    assert plan.rule_name == "plan_selected_bucket_suggested"

def test_bucket_suggested_blocked_when_pairs_wont_fit(monkeypatch):
    monkeypatch.setattr(rules, "native_enabled", lambda c: True)
    # 16GB box, 300k rows, 49M pairs (~3.1GB) vs 0.5*16=8GB headroom -> still fits;
    # use a pair count that exceeds the safety budget to force False:
    prof = make_profile(estimated_pair_count=400_000_000)  # ~24GB > 8GB budget
    rt = make_runtime(available_ram_gb=16.0, cpu_count=8)
    assert rules._is_bucket_suggested_eligible(prof, rt, n_rows_full=300_000) is False

def test_bucket_suggested_blocked_over_750k():
    prof = make_profile(estimated_pair_count=1_000_000)
    rt = make_runtime(available_ram_gb=16.0, cpu_count=8)
    assert rules._is_bucket_suggested_eligible(prof, rt, n_rows_full=1_000_000) is False

def test_bucket_suggested_not_needed_on_fat_box():
    # >=32GB is already covered by fast_box; bucket_suggested should NOT also fire
    # (avoid double-coverage) — it requires sub-32GB.
    prof = make_profile(estimated_pair_count=20_000_000)
    rt = make_runtime(available_ram_gb=64.0, cpu_count=16)
    assert rules._is_bucket_suggested_eligible(prof, rt, n_rows_full=300_000) is False

def test_bucket_suggested_polars_when_native_absent(monkeypatch):
    monkeypatch.setattr(rules, "native_enabled", lambda c: False)
    prof = make_profile(estimated_pair_count=20_000_000)
    rt = make_runtime(available_ram_gb=16.0, cpu_count=8)
    plan = rules._bucket_suggested_plan(prof, rt, 300_000)
    assert plan.backend == "polars-direct"  # _scoring_backend() fallback
```

(If the test module has no `make_profile`/`make_runtime` helpers, read how existing planner tests build these objects and reuse that exactly.)

- [ ] **Step 3: Run, verify they fail** (`_is_bucket_suggested_eligible` not defined).

- [ ] **Step 4: Implement** in `autoconfig_planner_rules.py` (after `rule_fast_box`):

```python
# ── Rule 3b: bucket-suggested band (sub-32GB, up to 750k, RAM-safe) ──────────
# Extends bucket+native to the average 16GB user for the 100k-750k band, which
# fast_box's blanket 32GB floor excludes. RAM safety comes from an explicit
# pair-memory-fit check (fast_box has NO per-dataset RAM check today -- its 50M
# pair proxy is density, not bytes). 750k is PROVISIONAL pending the 200k-750k
# bench (Task 3).
BUCKET_SUGGESTED_MAX_ROWS = 750_000
_PAIR_SCORE_BYTES = 64          # conservative: a Python (id_a, id_b, score) tuple
_BUCKET_RAM_SAFETY_FRACTION = 0.5  # leave half of RAM for the rest of the pipeline


def _is_bucket_suggested_eligible(
    profile: ComplexityProfile,
    runtime: RuntimeProfile,
    n_rows_full: int,
) -> bool:
    """Sub-32GB boxes get bucket up to 750k rows IFF the estimated pair memory
    fits within a safety fraction of available RAM. Fat boxes are handled by
    rule_fast_box (which fires first), so this requires available_ram_gb < 32."""
    if n_rows_full < SIMPLE_PLAN_MAX_ROWS or n_rows_full > BUCKET_SUGGESTED_MAX_ROWS:
        return False
    if runtime.available_ram_gb >= FAST_BOX_MIN_RAM_GB:
        return False  # fast_box already covers this
    if profile.blocking.estimated_pair_count >= SIMPLE_PLAN_MAX_PAIRS:
        return False
    est_pair_gb = (profile.blocking.estimated_pair_count * _PAIR_SCORE_BYTES) / (1024 ** 3)
    return est_pair_gb <= runtime.available_ram_gb * _BUCKET_RAM_SAFETY_FRACTION


def _bucket_suggested_plan(
    profile: ComplexityProfile,
    runtime: RuntimeProfile,
    n_rows_full: int,
) -> ExecutionPlan:
    return ExecutionPlan(
        backend=_scoring_backend(),
        max_workers=min(16, runtime.cpu_count),
        clustering_strategy="in_memory",
        rule_name="plan_selected_bucket_suggested",
    )


rule_bucket_suggested = PlannerRule(
    name="plan_selected_bucket_suggested",
    predicate=_is_bucket_suggested_eligible,
    action=_bucket_suggested_plan,
)
```

(Match `_fast_box_plan`'s exact `ExecutionPlan(...)` kwargs — copy its shape so no required field is missed.)

- [ ] **Step 5: Run, verify pass.** Commit (`feat(planner): rule_bucket_suggested — bucket up to 750k on sub-32GB, RAM-safe`).

### Task 1.2: register the rule after fast_box

- [ ] **Step 1: Read** the ordered rule list in `autoconfig_planner.py`.
- [ ] **Step 2: Failing integration test** — a sub-32GB + 300k (fits) profile produces `ExecutionPlan.rule_name == "plan_selected_bucket_suggested"` via the full planner (not just the predicate); a sub-32GB + 300k whose pairs don't fit yields a chunked/other rule (NOT bucket_suggested); a 32GB + 300k still yields `plan_selected_fast_box`.
- [ ] **Step 3: Run, verify fails.**
- [ ] **Step 4: Implement** — insert `rule_bucket_suggested` in the rule sequence immediately AFTER `rule_fast_box` and BEFORE the chunked rule.
- [ ] **Step 5: Run, verify pass. Commit** (`feat(planner): register rule_bucket_suggested after fast_box`).

---

## Task 2: Packaging — native as a marker-guarded default dep

**Files:**
- Modify: `packages/python/goldenmatch/pyproject.toml`
- Test: a small unit/parse test (or a doc check)

- [ ] **Step 1: Read** `pyproject.toml`: the `[project] dependencies`, the `[project.optional-dependencies] native = [...]` block, and `[tool.uv.sources]` (whether `goldenmatch-native` has a `{ workspace = true }` / `{ path = ... }` source).
- [ ] **Step 2:** Add `goldenmatch-native` to core `dependencies` with the marker (note PEP 508 prefers `==`/`or` over substring `in` for machine types):

```toml
"goldenmatch-native>=0.1.0; sys_platform == 'darwin' or (sys_platform == 'win32' and platform_machine == 'AMD64') or (sys_platform == 'linux' and (platform_machine == 'x86_64' or platform_machine == 'aarch64'))",
```

Keep the `[native]` extra as a back-compat alias (leave `native = ["goldenmatch-native>=0.1.0"]`). Keep/confirm the `[tool.uv.sources]` workspace/path source for `goldenmatch-native` so dev/CI resolve the local crate (per the CLAUDE.md `uv sync` gotcha).

- [ ] **Step 3: Verify `uv sync --all-packages` resolves** from the repo root (the monorepo gotcha — an extra/dep pointing at a not-yet-published pkg can break the whole workspace lock). Run: `uv sync --all-packages` and confirm exit 0, no resolution error.

- [ ] **Step 4: Add a guard test** (`tests/test_packaging.py` or extend an existing one): parse `pyproject.toml` and assert `goldenmatch-native` appears in core `dependencies` with a marker containing `sys_platform == 'darwin'` and `platform_machine == 'aarch64'`, and that the `[native]` extra still lists it. (Use `tomllib`.)

- [ ] **Step 5: Run, verify pass. Commit** (`build(native): ship goldenmatch-native as a marker-guarded default dependency`).

**Follow-up note (NOT this PR):** publish a `musllinux` abi3 wheel in the `goldenmatch-native` publish matrix so the `linux` marker is honest on Alpine; until then, Alpine users without a Rust toolchain should `pip install goldenmatch --no-deps`-style workaround or the install may try to compile. The runtime loader already degrades to pure-Python gracefully; the risk is install-time only. Track as a separate issue.

---

## Task 3: Bench validation — bucket vs polars-direct, 200k/500k/750k (GATES the 750k constant)

**Files:** `scripts/bench_native_bucket.py` (existing); record results in the spec.

- [ ] **Step 1: Read** `scripts/bench_native_bucket.py` — confirm it can run bucket+native vs polars-direct at a given N and reports wall + RSS (+ cluster parity if available). If it only does one backend, note how to run both (env `GOLDENMATCH_PLANNER_BUCKET=0` forces polars-direct).
- [ ] **Step 2:** Dispatch the bench on `large-new-64GB` (the bench runner) at **200k, 500k, 750k**, bucket+native vs polars-direct. (Use the existing `bench-native-bucket.yml` workflow if it accepts an N input, or a workflow_dispatch; otherwise run `scripts/bench_native_bucket.py` via the bench harness.) Capture wall, peak RSS, identical-cluster parity.
- [ ] **Step 3: Decide the ceiling.** If bucket+native wins (faster, parity holds) across 200k-750k -> keep `BUCKET_SUGGESTED_MAX_ROWS = 750_000`. If it stops winning at e.g. 500k -> lower the constant to the last winning scale and update the test in Task 1.1. **Do not leave 750k unconfirmed.**
- [ ] **Step 4:** Append a "Bucket-suggested validation (date)" results table to the spec (`git add -f`), recording the per-scale wall/RSS/parity and the chosen ceiling. Commit.

---

## Task 4: Docs + telemetry

**Files:** README / scale-envelope docs; confirm telemetry surfaces the new `rule_name`.

- [ ] **Step 1:** Update the backend-selection / scale-envelope doc: bucket+native is the default-installed suggested backend up to `<confirmed ceiling>` rows; above -> chunked/distributed. Note `pip install goldenmatch` now pulls native on common platforms; opt out with `GOLDENMATCH_NATIVE=0` or `GOLDENMATCH_PLANNER_BUCKET=0`.
- [ ] **Step 2:** Confirm `plan_selected_bucket_suggested` flows through `serialize_telemetry` (it reads `ExecutionPlan.rule_name`; no change needed if so — verify with a telemetry test or by reading `web/controller_telemetry.py`). Add an assertion if a telemetry test enumerates rule names.
- [ ] **Step 3: Commit** (`docs(backend): bucket-native default + suggested-up-to-<N> guidance`).

---

## Done when

- `rule_bucket_suggested` selects bucket up to the confirmed ceiling on sub-32GB boxes when pairs fit RAM, polars-direct when native absent, never OOMs (RAM-fit guard), and fast_box/>750k behavior is unchanged — all unit + integration tested.
- `pip install goldenmatch` pulls native on the 5 covered platforms (marker-guarded); `uv sync --all-packages` resolves; `[native]` still works.
- The 750k (or adjusted) ceiling is backed by the 200k-750k bench, recorded in the spec.
- Docs/telemetry reflect the new default + guidance.
- Full goldenmatch suite green in CI (never run locally — `feedback_avoid_full_suite_oom`).

## Notes / references

- Spec: `docs/superpowers/specs/2026-06-01-bucket-native-default-backend-design.md`.
- `_scoring_backend()` returns "bucket" iff `native_enabled("block_scoring")` else "polars-direct" — the bucket_suggested plan reuses it, so native-absent -> polars-direct automatically.
- `GOLDENMATCH_PLANNER_BUCKET=0` opt-out still forces polars-direct.
- Planner tests control native via the env / monkeypatching `native_enabled` — match the existing module's pattern.
- @superpowers:test-driven-development for the planner tasks.
