# GoldenPipe Repair-Plan Intelligence Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Phase-1 advisory `build_repair_plan` to the GoldenPipe brain that maps GoldenCheck findings to specific GoldenFlow transforms, keyed by `(check × value-level column type)`, emitted as a `repair_plan` artifact with zero change to executed stages.

**Architecture:** A new pure kernel function `build_repair_plan(findings, columns)` lives in `goldenpipe-core` (Rust, source-of-truth-by-convention) and is mirrored byte-identically by a pure-Python module and a pure-TS module, all replaying one hand-authored golden-vector file. An in-kernel value-level fine-typer classifies each column from ≤20 sampled string values using **hand-rolled ASCII char-class matchers** (no regex crate — dependency-free and byte-identical across engines) plus a Luhn check for `credit_card`. A static `(check, type_tag) → [transforms]` table does the lookup. The pipeline host samples values, calls the function, and attaches the artifact + reasoning.

**Tech Stack:** Rust (serde/serde_json, no regex), pure-Python mirror, pure-TS mirror, existing goldenpipe-core golden-vector parity harness.

---

## Box / environment constraints (read before executing)

- **The box CANNOT `cargo build` (Rust) or run vitest/tsc (TS OOMs).** Both are CI-only. Rust and TS tasks are **write-against-spec, verify by grep/eye + CI**. Only the **pure-Python path is box-runnable** and is where real red→green TDD happens.
- Because Rust can't run on the box, **the golden-vector file `build_repair_plan.json` is hand-authored** (its expected outputs are deterministic table lookups + ASCII classification, computed by hand from the spec). Python (box, Leg A) and Rust (CI) both assert against it — that IS the parity proof.
- **rustfmt runs on the box** (formats without building): `rustfmt --edition 2021 <file>` on every touched `.rs` before commit.
- Python invocation (native Windows, `;` PYTHONPATH separator):
  ```bash
  INTERP="D:/show_case/goldenmatch/.venv/Scripts/python.exe"
  export PYTHONPATH="packages/python/goldenpipe;packages/python/goldencheck;packages/python/infermap;packages/python/goldencheck-types"
  export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
  ```
  Run pytest as: `"$INTERP" -m pytest <path> -v`
- `ruff check` every touched Python file before commit.
- Spec: `docs/superpowers/specs/2026-07-07-goldenpipe-repair-plan-intelligence-design.md`.
- Reference the cross-surface skills where relevant: @superpowers:test-driven-development.

---

## File structure (decomposition)

**Rust core (`packages/rust/extensions/goldenpipe-core/`):**
- Create `src/repair.rs` — `TypeTag`, `ColumnInput`, `Finding`, `RepairItem`, `RepairPlan`, the fine-typer (`fine_type`), the mapping table (`lookup`), and `build_repair_plan`. One file, one responsibility (repair planning).
- Modify `src/lib.rs` — add `pub mod repair;`.
- Modify `src/json.rs` — add `build_repair_plan_json`.
- Modify `tests/golden_vectors.rs` — add `vec_build_repair_plan`.
- Create `tests/vectors/build_repair_plan.json` — the hand-authored parity contract.

**Rust shims (CI-verified):**
- Modify `packages/rust/extensions/goldenpipe-wasm/src/lib.rs` — export `build_repair_plan_json`.
- Modify `packages/rust/extensions/goldenpipe-native/src/lib.rs` — export `build_repair_plan_json`.

**Python (`packages/python/goldenpipe/goldenpipe/`):**
- Create `repair.py` — the real pure-Python impl (fine-typer + table + `build_repair_plan`), mirroring `repair.rs` line-for-line in behavior.
- Modify `core/_planner_json.py` — add `build_repair_plan_json`.
- Modify `tests/core/test_planner_parity.py` — add `build_repair_plan` to Leg A + Leg B case lists.
- Create `repair_host.py` — the sampling helper + `attach_repair_plan(ctx, findings, contexts, df)` host glue (kept separate from the pure kernel mirror so the pure module stays data-only).
- Create `tests/test_repair_host.py` — box tests for sampling + artifact attachment.
- Modify the engine call site that already builds ColumnContexts (identified in Task 5) to call `attach_repair_plan`.

**TS (`packages/typescript/goldenpipe/`, CI-verified):**
- Create `src/core/repair.ts` — mirror of `repair.rs`.
- Modify the TS json-face module + `tests/parity/planner-parity.test.ts` to replay `build_repair_plan.json`.

---

## Canonical behavior reference (shared by all three surfaces)

**Type tags.** Coarse (from host `ColumnContext`, lowercase string): `date`, `email`, `name`, `phone`, `zip`. Fine (from fine-typer): `iban`, `isin`, `swift`, `credit_card`, `cusip`, `npi`, `imei`, `ean`, `isbn`, `aba_routing`. Wildcard `*` used only in the table (never a resolved tag).

**Fine-typer detector order (first firing wins).** Name-hint-gated group FIRST (they only fire on intentionally-named columns, so low false-positive), then value-distinctive group as fallback:
1. `cusip` (name has `cusip`; value `^[0-9A-Z]{9}$`)
2. `npi` (name has `npi`; value `^[0-9]{10}$`)
3. `imei` (name has `imei` or `imsi`; value `^[0-9]{15}$`)
4. `ean` (name has `ean`, `gtin`, or `barcode`; value `^[0-9]{8}$` or `^[0-9]{13}$`)
5. `isbn` (name has `isbn`; value `^[0-9]{9}[0-9Xx]$` or `^[0-9]{13}$`)
6. `aba_routing` (name has `routing` or `aba`; value `^[0-9]{9}$`)
7. `iban` (value `^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$`, total length 15–34)
8. `isin` (value `^[A-Z]{2}[0-9A-Z]{9}[0-9]$`, length 12)
9. `swift` (value `^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$`, length 8 or 11)
10. `credit_card` (value `^[0-9]{13,19}$` after stripping spaces/dashes, AND Luhn passes)

Rationale for gated-first: a 13-digit `barcode` column resolves to `ean`, not `credit_card`; a `card_number` column (no gated hint matches) falls through to `credit_card`.

**Firing rule.** For a detector, count non-empty samples whose value matches its value-predicate. It fires iff (name-hint satisfied when required) AND (matches × 2 > count of non-empty samples) — strict majority. Empty/whitespace samples are skipped in both numerator and denominator. Name-hint match is ASCII-lowercase substring of `name`.

**Resolution.** `fine_type(name, samples)` returns the first firing fine tag, else `None`. `resolve_tag` = fine tag if present, else the coarse tag if it is one of the 5 known coarse tags (else `None` → column omitted / only `*` checks apply).

**Mapping table (exact `(check, tag)` first, then `(check, "*")`, first-match).**
Coarse:
- `(encoding_detection, *)` → `["fix_mojibake", "normalize_unicode"]`
- `(future_dated, date)`, `(temporal_order, date)`, `(stale_data, date)` → `["date_validate"]`
- `(format_detection, date)` → `["date_parse"]`
- `(format_detection, email)` → `["email_normalize"]`
- `(pattern_consistency, email)` → `["email_canonical"]`
- `(pattern_consistency, name)` → `["name_proper"]`
- `(format_detection, phone)` → `["phone_validate"]`
- `(pattern_consistency, phone)` → `["phone_national"]`
- `(format_detection, zip)` → `["zip_normalize"]`

Fine — BOTH `format_detection` and `pattern_consistency` map to the type's validator:
- `iban`→`iban_validate`, `isin`→`isin_validate`, `swift`→`swift_validate`, `cusip`→`cusip_validate`, `npi`→`npi_validate`, `imei`→`imei_validate`, `ean`→`ean_validate`, `isbn`→`isbn_validate`, `credit_card`→`luhn_validate`, `aba_routing`→`aba_validate`.

**build_repair_plan.** For each finding: resolve the finding's column tag from `columns`; look up `(check, tag)`; if a transform list exists, emit `RepairItem{column, check, type_tag, suggested_transforms, reason}`. `reason` = the finding's `message` truncated to 80 chars (Python `str(message)[:80]`). Findings whose column is absent from `columns`, whose tag is `None`, or whose `(check, tag)` misses → no item. Deterministic order = input findings order. Empty result → `RepairPlan{repairs: []}`.

**JSON wire shape** (what `build_repair_plan_json` consumes/emits):
- Input: `{"findings": [{"column","check","message","severity"}], "columns": [{"name","coarse_type","samples":[str]}]}`
- Output: `{"repairs": [{"column","check","type_tag","suggested_transforms":[str],"reason"}]}`
- Key order in each object is the struct field order above (serde preserve_order + Python dict insertion order + TS object literal order must agree).

---

## Task 1: Python pure kernel — fine-typer + table + build_repair_plan (box TDD, the correctness heart)

**Files:**
- Create: `packages/python/goldenpipe/goldenpipe/repair.py`
- Test: `packages/python/goldenpipe/tests/test_repair_pure.py`

Do Python FIRST: it is the only surface that runs on the box, so it is where real red→green happens. Rust (Task 4) mirrors it against the same hand-authored vectors.

- [ ] **Step 1: Write failing tests for the fine-typer**

Create `tests/test_repair_pure.py`:

```python
from goldenpipe.repair import fine_type, resolve_tag, build_repair_plan


def test_iban_classifies_iban():
    assert fine_type("account", ["GB82WEST12345698765432", "DE89370400440532013000"]) == "iban"

def test_routing_9digit_is_not_iban_and_needs_name_for_aba():
    # bare 9-digit, no name hint -> no fine tag
    assert fine_type("col1", ["021000021", "011401533"]) is None
    # with routing name hint -> aba_routing
    assert fine_type("routing_number", ["021000021", "011401533"]) == "aba_routing"

def test_credit_card_needs_luhn():
    assert fine_type("card", ["4539578763621486", "4485275742308327"]) == "credit_card"   # valid Luhn
    assert fine_type("card", ["4539578763621487", "1234567812345678"]) is None            # fail Luhn

def test_barcode_resolves_ean_not_credit_card():
    assert fine_type("barcode", ["4006381333931", "0012345678905"]) == "ean"

def test_minority_match_does_not_fire():
    # only 1 of 3 is an IBAN -> no majority
    assert fine_type("account", ["GB82WEST12345698765432", "n/a", "unknown"]) is None

def test_resolve_tag_prefers_fine_then_coarse_then_none():
    assert resolve_tag("email_addr", "email", ["a@b.com"]) == "email"          # coarse, no fine
    assert resolve_tag("iban", "string", ["GB82WEST12345698765432"]) == "iban" # fine wins
    assert resolve_tag("misc", "string", ["hello"]) is None                    # neither
```

- [ ] **Step 2: Run to verify failure**

Run: `"$INTERP" -m pytest packages/python/goldenpipe/tests/test_repair_pure.py -v`
Expected: FAIL — `ModuleNotFoundError: goldenpipe.repair`.

- [ ] **Step 3: Implement `repair.py`**

```python
"""Pure repair-plan kernel — the SP2 mirror of goldenpipe-core/src/repair.rs.

Deterministic, no I/O, no polars. Hand-rolled ASCII matchers (no regex) so the
three surfaces are byte-identical by construction. See the design spec for the
canonical behavior tables.
"""
from __future__ import annotations

# ── coarse tags the host may supply ──────────────────────────────────────
_COARSE = {"date", "email", "name", "phone", "zip"}

# ── ASCII char-class primitives (no regex; \\d would diverge across engines) ─
def _is_digit(c: str) -> bool:
    return "0" <= c <= "9"

def _is_upper(c: str) -> bool:
    return "A" <= c <= "Z"

def _is_alnum_upper(c: str) -> bool:
    return _is_digit(c) or _is_upper(c)

def _all(s: str, pred) -> bool:
    return len(s) > 0 and all(pred(c) for c in s)

# ── value predicates (detection shape, not full validation) ──────────────
def _v_cusip(s: str) -> bool:
    return len(s) == 9 and _all(s, _is_alnum_upper)

def _v_npi(s: str) -> bool:
    return len(s) == 10 and _all(s, _is_digit)

def _v_imei(s: str) -> bool:
    return len(s) == 15 and _all(s, _is_digit)

def _v_ean(s: str) -> bool:
    return len(s) in (8, 13) and _all(s, _is_digit)

def _v_isbn(s: str) -> bool:
    if len(s) == 13 and _all(s, _is_digit):
        return True
    return len(s) == 10 and _all(s[:9], _is_digit) and s[9] in "0123456789Xx"

def _v_aba(s: str) -> bool:
    return len(s) == 9 and _all(s, _is_digit)

def _v_iban(s: str) -> bool:
    if not (15 <= len(s) <= 34):
        return False
    return _is_upper(s[0]) and _is_upper(s[1]) and _is_digit(s[2]) and _is_digit(s[3]) and _all(s[4:], _is_alnum_upper)

def _v_isin(s: str) -> bool:
    return len(s) == 12 and _is_upper(s[0]) and _is_upper(s[1]) and _all(s[2:11], _is_alnum_upper) and _is_digit(s[11])

def _v_swift(s: str) -> bool:
    if len(s) not in (8, 11):
        return False
    return _all(s[:6], _is_upper) and _all(s[6:8], _is_alnum_upper) and (len(s) == 8 or _all(s[8:11], _is_alnum_upper))

def _luhn_ok(s: str) -> bool:
    d = [int(c) for c in s]
    total, alt = 0, False
    for x in reversed(d):
        if alt:
            x *= 2
            if x > 9:
                x -= 9
        total += x
        alt = not alt
    return total % 10 == 0

def _v_credit_card(s: str) -> bool:
    t = s.replace(" ", "").replace("-", "")
    return 13 <= len(t) <= 19 and _all(t, _is_digit) and _luhn_ok(t)

# ── detectors: (tag, name_hints_or_None, value_predicate) in fixed order ──
# name-gated group first (low false-positive), value-distinctive fallback second.
_DETECTORS = [
    ("cusip", ("cusip",), _v_cusip),
    ("npi", ("npi",), _v_npi),
    ("imei", ("imei", "imsi"), _v_imei),
    ("ean", ("ean", "gtin", "barcode"), _v_ean),
    ("isbn", ("isbn",), _v_isbn),
    ("aba_routing", ("routing", "aba"), _v_aba),
    ("iban", None, _v_iban),
    ("isin", None, _v_isin),
    ("swift", None, _v_swift),
    ("credit_card", None, _v_credit_card),
]


def fine_type(name: str, samples: list[str]) -> str | None:
    lname = name.lower()
    nonempty = [s for s in samples if s and s.strip()]
    if not nonempty:
        return None
    for tag, hints, pred in _DETECTORS:
        if hints is not None and not any(h in lname for h in hints):
            continue
        matches = sum(1 for s in nonempty if pred(s))
        if matches * 2 > len(nonempty):
            return tag
    return None


def resolve_tag(name: str, coarse_type: str, samples: list[str]) -> str | None:
    ft = fine_type(name, samples)
    if ft is not None:
        return ft
    return coarse_type if coarse_type in _COARSE else None


# ── mapping table: (check, tag) -> transforms; "*" tag = wildcard ─────────
_VALIDATOR = {
    "iban": "iban_validate", "isin": "isin_validate", "swift": "swift_validate",
    "cusip": "cusip_validate", "npi": "npi_validate", "imei": "imei_validate",
    "ean": "ean_validate", "isbn": "isbn_validate", "credit_card": "luhn_validate",
    "aba_routing": "aba_validate",
}
_TABLE: dict[tuple[str, str], list[str]] = {
    ("encoding_detection", "*"): ["fix_mojibake", "normalize_unicode"],
    ("future_dated", "date"): ["date_validate"],
    ("temporal_order", "date"): ["date_validate"],
    ("stale_data", "date"): ["date_validate"],
    ("format_detection", "date"): ["date_parse"],
    ("format_detection", "email"): ["email_normalize"],
    ("pattern_consistency", "email"): ["email_canonical"],
    ("pattern_consistency", "name"): ["name_proper"],
    ("format_detection", "phone"): ["phone_validate"],
    ("pattern_consistency", "phone"): ["phone_national"],
    ("format_detection", "zip"): ["zip_normalize"],
}
for _t, _v in _VALIDATOR.items():
    _TABLE[("format_detection", _t)] = [_v]
    _TABLE[("pattern_consistency", _t)] = [_v]


def _lookup(check: str, tag: str | None) -> list[str] | None:
    if tag is not None and (check, tag) in _TABLE:
        return _TABLE[(check, tag)]
    if (check, "*") in _TABLE:
        return _TABLE[(check, "*")]
    return None


def build_repair_plan(findings: list[dict], columns: list[dict]) -> dict:
    tags: dict[str, str | None] = {}
    for c in columns:
        tags[c["name"]] = resolve_tag(c["name"], c.get("coarse_type", ""), c.get("samples", []))

    repairs = []
    for f in findings:
        col = f.get("column")
        check = f.get("check", "")
        # encoding wildcard can apply even to an omitted-tag column present in `columns`
        if col not in tags:
            continue
        transforms = _lookup(check, tags[col])
        if not transforms:
            continue
        repairs.append({
            "column": col,
            "check": check,
            "type_tag": tags[col] if tags[col] is not None else "*",
            "suggested_transforms": list(transforms),
            "reason": str(f.get("message", ""))[:80],
        })
    return {"repairs": repairs}
```

- [ ] **Step 4: Run tests to verify pass**

Run: `"$INTERP" -m pytest packages/python/goldenpipe/tests/test_repair_pure.py -v`
Expected: PASS (all 6).

- [ ] **Step 5: ruff + commit**

```bash
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/repair.py packages/python/goldenpipe/tests/test_repair_pure.py
git add packages/python/goldenpipe/goldenpipe/repair.py packages/python/goldenpipe/tests/test_repair_pure.py
git commit -m "feat(goldenpipe): pure-Python repair-plan kernel (fine-typer + table)"
```

---

## Task 2: Hand-author the golden-vector contract + wire the Python parity leg

**Files:**
- Create: `packages/rust/extensions/goldenpipe-core/tests/vectors/build_repair_plan.json`
- Modify: `packages/python/goldenpipe/goldenpipe/core/_planner_json.py`
- Modify: `packages/python/goldenpipe/tests/core/test_planner_parity.py`

- [ ] **Step 1: Author the vector file** (each case `{input, expected}`; expected computed by hand from the canonical tables). Cover: an IBAN `pattern_consistency` → `iban_validate`; a `future_dated` date → `date_validate`; an `encoding_detection` on an omitted-tag column → `fix_mojibake,normalize_unicode`; a routing-number 9-digit `pattern_consistency` → `aba_validate`; a credit_card `format_detection` → `luhn_validate`; a no-map `unique` finding → no item; a finding on a column absent from `columns` → no item; an empty-findings case → `{"repairs":[]}`.

```json
[
  {
    "input": {
      "findings": [{"column": "iban", "check": "pattern_consistency", "message": "3 values fail check-digit", "severity": "warning"}],
      "columns": [{"name": "iban", "coarse_type": "string", "samples": ["GB82WEST12345698765432", "DE89370400440532013000"]}]
    },
    "expected": {"repairs": [{"column": "iban", "check": "pattern_consistency", "type_tag": "iban", "suggested_transforms": ["iban_validate"], "reason": "3 values fail check-digit"}]}
  },
  {
    "input": {
      "findings": [{"column": "signup_date", "check": "future_dated", "message": "12 rows dated after today", "severity": "warning"}],
      "columns": [{"name": "signup_date", "coarse_type": "date", "samples": ["2020-01-01", "2021-05-05"]}]
    },
    "expected": {"repairs": [{"column": "signup_date", "check": "future_dated", "type_tag": "date", "suggested_transforms": ["date_validate"], "reason": "12 rows dated after today"}]}
  },
  {
    "input": {
      "findings": [{"column": "notes", "check": "encoding_detection", "message": "mojibake detected", "severity": "info"}],
      "columns": [{"name": "notes", "coarse_type": "string", "samples": ["cafÃ©", "naÃ¯ve"]}]
    },
    "expected": {"repairs": [{"column": "notes", "check": "encoding_detection", "type_tag": "*", "suggested_transforms": ["fix_mojibake", "normalize_unicode"], "reason": "mojibake detected"}]}
  },
  {
    "input": {
      "findings": [{"column": "routing_number", "check": "pattern_consistency", "message": "bad routing", "severity": "warning"}],
      "columns": [{"name": "routing_number", "coarse_type": "string", "samples": ["021000021", "011401533"]}]
    },
    "expected": {"repairs": [{"column": "routing_number", "check": "pattern_consistency", "type_tag": "aba_routing", "suggested_transforms": ["aba_validate"], "reason": "bad routing"}]}
  },
  {
    "input": {
      "findings": [{"column": "card", "check": "format_detection", "message": "spaced digits", "severity": "info"}],
      "columns": [{"name": "card", "coarse_type": "string", "samples": ["4539578763621486", "4485275742308327"]}]
    },
    "expected": {"repairs": [{"column": "card", "check": "format_detection", "type_tag": "credit_card", "suggested_transforms": ["luhn_validate"], "reason": "spaced digits"}]}
  },
  {
    "input": {
      "findings": [{"column": "id", "check": "unique", "message": "dupes", "severity": "warning"}],
      "columns": [{"name": "id", "coarse_type": "string", "samples": ["a", "b"]}]
    },
    "expected": {"repairs": []}
  },
  {
    "input": {
      "findings": [{"column": "ghost", "check": "future_dated", "message": "x", "severity": "info"}],
      "columns": []
    },
    "expected": {"repairs": []}
  },
  {
    "input": {"findings": [], "columns": []},
    "expected": {"repairs": []}
  }
]
```

Note the mojibake sample uses JSON `Ã©` escapes (the UTF-8 bytes of `é` mis-decoded) — this is finding-driven, the fine-typer never inspects these for classification (coarse `string` → no fine tag → the `*` wildcard applies).

- [ ] **Step 2: Add `build_repair_plan_json` to `_planner_json.py`**

At the end of the module (it must call the real `goldenpipe.repair`):

```python
from goldenpipe import repair as _repair  # add to import block


def build_repair_plan_json(s: str) -> str:
    arg = json.loads(s)
    out = _repair.build_repair_plan(arg.get("findings", []), arg.get("columns", []))
    return json.dumps(out)
```

- [ ] **Step 3: Add the vector to the Leg A + Leg B case lists in `test_planner_parity.py`**

Add `("build_repair_plan", PJ.build_repair_plan_json)` to `_CASES`, and `("build_repair_plan", "build_repair_plan_json")` to the Leg B parametrize list.

- [ ] **Step 4: Run the Leg A parity test (box)**

Run: `"$INTERP" -m pytest packages/python/goldenpipe/tests/core/test_planner_parity.py -k build_repair_plan -v`
Expected: Leg A PASS. (Leg B native-wheel case is skip-guarded locally — confirm it SKIPs, does not fail.)

- [ ] **Step 5: ruff + commit**

```bash
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/core/_planner_json.py packages/python/goldenpipe/tests/core/test_planner_parity.py
git add packages/rust/extensions/goldenpipe-core/tests/vectors/build_repair_plan.json packages/python/goldenpipe/goldenpipe/core/_planner_json.py packages/python/goldenpipe/tests/core/test_planner_parity.py
git commit -m "test(goldenpipe): repair-plan golden vectors + python parity leg"
```

---

## Task 3: Host wiring — sampling helper + artifact attachment (box-testable)

**Files:**
- Create: `packages/python/goldenpipe/goldenpipe/repair_host.py`
- Test: `packages/python/goldenpipe/tests/test_repair_host.py`

Keep the polars-touching sampling glue OUT of the pure `repair.py` (which must stay data-only and box-parity-clean).

- [ ] **Step 1: Write failing host tests**

```python
import polars as pl
from goldenpipe.repair_host import sample_column, build_column_inputs, attach_repair_plan
from goldenpipe.models.context import PipeContext


def test_sample_column_first_n_nonnull_as_str():
    s = pl.Series("c", [None, "a", "b", None, "c"])
    assert sample_column(s, limit=2) == ["a", "b"]

def test_build_column_inputs_from_contexts_and_df():
    df = pl.DataFrame({"iban": ["GB82WEST12345698765432", "DE89370400440532013000"]})
    class Ctx:  # minimal ColumnContext stand-in
        def __init__(self, name, t): self.name, self.inferred_type = name, t
    cols = build_column_inputs([Ctx("iban", "string")], df)
    assert cols[0]["name"] == "iban" and cols[0]["coarse_type"] == "string"
    assert cols[0]["samples"] == ["GB82WEST12345698765432", "DE89370400440532013000"]

def test_attach_repair_plan_sets_artifact_and_reasoning():
    df = pl.DataFrame({"signup_date": ["2020-01-01", "2021-05-05"]})
    findings = [{"column": "signup_date", "check": "future_dated", "message": "12 rows after today", "severity": "warning"}]
    class Ctx:
        def __init__(self, name, t): self.name, self.inferred_type = name, t
    ctx = PipeContext()  # adjust constructor per real signature
    attach_repair_plan(ctx, findings, [Ctx("signup_date", "date")], df)
    plan = ctx.artifacts["repair_plan"]
    assert plan["repairs"][0]["suggested_transforms"] == ["date_validate"]
```

(Adjust `PipeContext` construction to its real signature — inspect `goldenpipe/models/context.py` first.)

- [ ] **Step 2: Run to verify failure**

Run: `"$INTERP" -m pytest packages/python/goldenpipe/tests/test_repair_host.py -v`
Expected: FAIL — `ModuleNotFoundError: goldenpipe.repair_host`.

- [ ] **Step 3: Implement `repair_host.py`**

```python
"""Host glue for repair-plan: sample polars columns, build ColumnInputs, call the
pure kernel, attach the advisory artifact + reasoning. Advisory ONLY — never
mutates the stage list."""
from __future__ import annotations

from goldenpipe.repair import build_repair_plan

_SAMPLE_LIMIT = 20


def sample_column(series, limit: int = _SAMPLE_LIMIT) -> list[str]:
    out: list[str] = []
    for v in series:                       # polars Series iterates values in row order
        if v is None:
            continue
        s = str(v)
        if s.strip() == "":
            continue
        out.append(s)
        if len(out) >= limit:
            break
    return out


def build_column_inputs(contexts, df) -> list[dict]:
    cols = []
    names = set(df.columns)
    for ctx in contexts:
        if ctx.name not in names:
            continue
        cols.append({
            "name": ctx.name,
            "coarse_type": str(ctx.inferred_type),   # ColumnType is a str-enum -> "email" etc.
            "samples": sample_column(df[ctx.name]),
        })
    return cols


def attach_repair_plan(ctx, findings, contexts, df) -> dict:
    columns = build_column_inputs(contexts, df)
    plan = build_repair_plan(findings, columns)
    ctx.artifacts["repair_plan"] = plan
    for item in plan["repairs"]:
        line = (f"repair: {item['column']} ({item['check']}) -> "
                f"{','.join(item['suggested_transforms'])} [{item['reason']}]")
        _record_reasoning(ctx, line)
    return plan


def _record_reasoning(ctx, line: str) -> None:
    # Use whatever reasoning channel the ctx already exposes (inspect models/context.py:
    # e.g. ctx.reasoning.append(line) or ctx.add_reasoning(line)). Kept in one helper
    # so the exact call is easy to correct during implementation.
    getattr(ctx, "reasoning", []).append(line)
```

**Implementation note:** inspect `goldenpipe/models/context.py` for the real reasoning API and `ColumnContext.inferred_type`'s str value (Task 5 confirms), and correct `_record_reasoning` + the `str(ctx.inferred_type)` cast accordingly. `str(ColumnType.EMAIL)` must yield `"email"` — verify (the enum is `str, Enum` with lowercase values, so `.value` is `"email"`; use `ctx.inferred_type.value` if `str()` yields `ColumnType.EMAIL`).

- [ ] **Step 4: Run host tests to verify pass**

Run: `"$INTERP" -m pytest packages/python/goldenpipe/tests/test_repair_host.py -v`
Expected: PASS.

- [ ] **Step 5: ruff + commit**

```bash
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/repair_host.py packages/python/goldenpipe/tests/test_repair_host.py
git add packages/python/goldenpipe/goldenpipe/repair_host.py packages/python/goldenpipe/tests/test_repair_host.py
git commit -m "feat(goldenpipe): repair-plan host glue (sampling + artifact attach)"
```

---

## Task 4: Rust core mirror (write-against-spec, CI-verified)

**Files:**
- Create: `packages/rust/extensions/goldenpipe-core/src/repair.rs`
- Modify: `packages/rust/extensions/goldenpipe-core/src/lib.rs` (add `pub mod repair;`)
- Modify: `packages/rust/extensions/goldenpipe-core/src/json.rs` (add `build_repair_plan_json`)
- Modify: `packages/rust/extensions/goldenpipe-core/tests/golden_vectors.rs` (add `vec_build_repair_plan`)

The box cannot build this — mirror `repair.py` behavior exactly, format with rustfmt, verify in CI.

- [ ] **Step 1: Write `src/repair.rs`** — serde structs + hand-rolled matchers + Luhn + detector table (same order as Python) + `_lookup` + `build_repair_plan`. Structs:

```rust
//! Repair-plan kernel. Mirrors goldenpipe/repair.py EXACTLY (behavior-canonical
//! by convention: Rust is the source of truth; the pure-Python/TS mirrors must
//! reproduce these bytes). Hand-rolled ASCII matchers — NO regex crate — so all
//! three surfaces agree on classification (`\d` would be Unicode in Rust/Python
//! but ASCII in JS).
use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
pub struct Finding {
    pub column: Option<String>,
    #[serde(default)]
    pub check: String,
    #[serde(default)]
    pub message: String,
    #[serde(default)]
    pub severity: String,
}

#[derive(Deserialize)]
pub struct ColumnInput {
    pub name: String,
    #[serde(default)]
    pub coarse_type: String,
    #[serde(default)]
    pub samples: Vec<String>,
}

#[derive(Serialize)]
pub struct RepairItem {
    pub column: String,
    pub check: String,
    pub type_tag: String,
    pub suggested_transforms: Vec<String>,
    pub reason: String,
}

#[derive(Serialize, Default)]
pub struct RepairPlan {
    pub repairs: Vec<RepairItem>,
}
```

Implement `fine_type(name, samples) -> Option<&'static str>`, `resolve_tag`, `lookup(check, tag) -> Option<Vec<String>>`, and `build_repair_plan(findings, columns) -> RepairPlan`. Match Python's field/tag strings and the `reason` truncation: Rust `message.chars().take(80).collect()` (char-based, to match Python `[:80]` slicing on code points — note both slice by Unicode scalar, so use `.chars().take(80)` NOT byte slicing). `type_tag` for a wildcard-matched (omitted-tag) column serializes as `"*"` exactly like Python.

Include `#[cfg(test)] mod tests` mirroring the Python unit cases (iban, routing-not-iban, credit_card Luhn, barcode→ean, minority-no-fire).

- [ ] **Step 2: Add `pub mod repair;` to `lib.rs`** and the `build_repair_plan_json` wrapper to `json.rs`:

```rust
#[derive(Deserialize)]
struct RepairIn {
    #[serde(default)]
    findings: Vec<crate::repair::Finding>,
    #[serde(default)]
    columns: Vec<crate::repair::ColumnInput>,
}

pub fn build_repair_plan_json(input: &str) -> String {
    let arg: RepairIn = match serde_json::from_str(input) {
        Ok(a) => a,
        Err(e) => return parse_err(e),
    };
    serde_json::to_string(&crate::repair::build_repair_plan(&arg.findings, &arg.columns)).unwrap()
}
```

- [ ] **Step 3: Add the golden-vector test entry** to `tests/golden_vectors.rs`:

```rust
#[test]
fn vec_build_repair_plan() {
    run("build_repair_plan", build_repair_plan_json);
}
```

- [ ] **Step 4: rustfmt (box)**

Run: `rustfmt --edition 2021 packages/rust/extensions/goldenpipe-core/src/repair.rs packages/rust/extensions/goldenpipe-core/src/json.rs packages/rust/extensions/goldenpipe-core/src/lib.rs packages/rust/extensions/goldenpipe-core/tests/golden_vectors.rs`
Expected: exit 0, no diff surprises. **Do not** attempt `cargo build`/`cargo test` on the box — CI runs them.

- [ ] **Step 5: Grep-verify wiring + commit**

Verify by eye/grep: `pub mod repair` in lib.rs; `build_repair_plan_json` referenced in json.rs + golden_vectors.rs; struct field order matches the JSON wire shape.
```bash
git add packages/rust/extensions/goldenpipe-core/src/repair.rs packages/rust/extensions/goldenpipe-core/src/lib.rs packages/rust/extensions/goldenpipe-core/src/json.rs packages/rust/extensions/goldenpipe-core/tests/golden_vectors.rs
git commit -m "feat(goldenpipe-core): build_repair_plan kernel + golden-vector test"
```

---

## Task 5: Wire the engine call site (box-testable integration)

**Files:**
- Modify: the engine module that builds ColumnContexts post-Check (find it in Step 1).
- Test: extend `packages/python/goldenpipe/tests/test_repair_host.py` or add an engine-level test.

- [ ] **Step 1: Locate the call site.** Grep for where `build_contexts_from_check` is called and where `findings` + the working DataFrame are both in scope:

Run: `grep -rn "build_contexts_from_check\|artifacts\[.findings.\]\|enrich_contexts_from_flow" packages/python/goldenpipe/goldenpipe --include=*.py`

Confirm the reasoning API on `PipeContext` (`grep -n "reasoning\|def add_reason" packages/python/goldenpipe/goldenpipe/models/context.py`) and fix `repair_host._record_reasoning` to match.

- [ ] **Step 2: Write a failing engine-level assertion** that after a Check stage produces findings on a date column, `ctx.artifacts["repair_plan"]` exists with the expected item. (Use the smallest existing engine-test harness as a template — find one via `grep -rln "PipeContext()" packages/python/goldenpipe/tests`.)

- [ ] **Step 3: Insert the call.** At the located site (after contexts + findings are available, before Match auto-config), add:

```python
from goldenpipe.repair_host import attach_repair_plan
# ... where `findings`, `contexts`, and the working df are in scope:
attach_repair_plan(ctx, findings, contexts, df)
```

Guard it so a missing df or empty findings is a no-op (the pure fn already returns `{"repairs": []}`; ensure no exception if `df is None`).

- [ ] **Step 4: Run the engine test + the full repair test set (box)**

Run: `"$INTERP" -m pytest packages/python/goldenpipe/tests/test_repair_pure.py packages/python/goldenpipe/tests/test_repair_host.py packages/python/goldenpipe/tests/core/test_planner_parity.py -k "repair or build_repair_plan" -v`
Expected: PASS. Also run one broad existing engine test to prove **no executed-stage regression** (byte-identical-when-inactive): pick a pipeline test that asserts stage order/output and confirm it still passes unchanged.

- [ ] **Step 5: ruff + commit**

```bash
"$INTERP" -m ruff check <touched engine file> <touched test>
git add <touched files>
git commit -m "feat(goldenpipe): attach advisory repair_plan artifact in the engine"
```

---

## Task 6: TS mirror + WASM/native shim exports (write-against-spec, CI-verified)

**Files:**
- Create: `packages/typescript/goldenpipe/src/core/repair.ts`
- Modify: the TS json-face + `packages/typescript/goldenpipe/tests/parity/planner-parity.test.ts` (replay `build_repair_plan.json`).
- Modify: `packages/rust/extensions/goldenpipe-wasm/src/lib.rs` and `packages/rust/extensions/goldenpipe-native/src/lib.rs` (export `build_repair_plan_json` following the existing per-fn pattern).

Box cannot run tsc/vitest or build wasm — mirror behavior, verify in CI.

- [ ] **Step 1: Write `repair.ts`** mirroring `repair.py`: same ASCII predicates (use `c >= "0" && c <= "9"` etc., never `\d`), same detector order, same table, same `buildRepairPlan(findings, columns)` returning `{repairs: [...]}` with fields in the same order. Match the JS reason truncation to code points: `[...String(message)].slice(0, 80).join("")` (spread iterates by code point, matching Python `[:80]` on a str and Rust `.chars().take(80)`).

- [ ] **Step 2: Add the TS json-face fn** `buildRepairPlanJson(s)` and register `build_repair_plan` in the parity test's vector list (mirror how `apply_decision`/`evaluate_builtin` are registered in `planner-parity.test.ts`).

- [ ] **Step 3: Export the shim fns.** In `goldenpipe-wasm/src/lib.rs` and `goldenpipe-native/src/lib.rs`, add a `build_repair_plan_json` binding following the exact pattern used for `apply_decision_json` (grep those files for `apply_decision_json` and copy the shape). rustfmt both.

- [ ] **Step 4: rustfmt the shims (box) + grep-verify**

Run: `rustfmt --edition 2021 packages/rust/extensions/goldenpipe-wasm/src/lib.rs packages/rust/extensions/goldenpipe-native/src/lib.rs`
Grep-verify each surface names `build_repair_plan_json`.

- [ ] **Step 5: Commit**

```bash
git add packages/typescript/goldenpipe/src/core/repair.ts <ts json-face> packages/typescript/goldenpipe/tests/parity/planner-parity.test.ts packages/rust/extensions/goldenpipe-wasm/src/lib.rs packages/rust/extensions/goldenpipe-native/src/lib.rs
git commit -m "feat(goldenpipe): TS repair mirror + wasm/native json shims"
```

---

## Task 7: Ship

- [ ] **Step 1: Rebase onto fresh origin/main**

```bash
git fetch origin
git rebase origin/main
```
Resolve any conflicts (most likely in `json.rs` import list, `golden_vectors.rs`, `lib.rs` mod list, `_planner_json.py` imports, and `test_planner_parity.py` case lists — keep BOTH sides' entries).

- [ ] **Step 2: Re-run the box-runnable suite after rebase**

Run: `"$INTERP" -m pytest packages/python/goldenpipe/tests/test_repair_pure.py packages/python/goldenpipe/tests/test_repair_host.py packages/python/goldenpipe/tests/core/test_planner_parity.py -k "repair or build_repair_plan" -v`
Expected: PASS.

- [ ] **Step 3: Push + PR + arm auto-merge, then STOP**

```bash
unset GH_TOKEN; gh auth switch --user benzsevern; export GH_TOKEN=$(gh auth token --user benzsevern)
git push -u origin feat/goldenpipe-repair-plan
gh pr create --base main --title "feat(goldenpipe): advisory repair-plan intelligence (Phase 1)" --body "<summary: kernel build_repair_plan + in-kernel fine-typer + host wiring; advisory only, byte-identical when inactive; spec + plan links>"
# merge-queue repo: NO --delete-branch
gh pr merge <N> --auto --squash
```
Then **STOP** — do not poll CI (merge-queue lands on green). Watch that CI covers: Rust `cargo test` (golden vectors incl. `vec_build_repair_plan`), Python `test_planner_parity` Leg A+B, TS parity, and the goldenpipe-core parity gate.

---

## Verification summary (what "done" means)

- Box-green: `test_repair_pure.py`, `test_repair_host.py`, `test_planner_parity.py -k build_repair_plan` (Leg A), one existing engine test unchanged (no regression).
- CI-green: Rust golden vectors (`vec_build_repair_plan`), Python Leg B (native wheel), TS parity, wasm build.
- Advisory guarantee: no `Decision` emitted; executed-stage list byte-identical whether or not repair-planning runs (proved by the unchanged engine test).
