# Rust-is-the-reference — goldenmatch pilot Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the native kernel the DEFAULT for every goldenmatch component whose native symbol exists, so `GOLDENMATCH_NATIVE=auto` (the default) runs native wherever a kernel exists — Python becomes the lossy fallback, not the gated exception. This includes making the FS (Fellegi-Sunter) native kernel authoritative (its output IS the spec), gated on a measured F1 non-regression.

**Architecture:** Flip the `auto` gate in `core/_native_loader.py` from an explicit allowlist (`component in _GATED_ON`) to symbol-presence (`_has_symbol(component)`), mirroring goldencheck/goldenanalysis. This subsumes the two already-byte-exact-but-not-yet-gated components (`pprl_bloom`, `perceptual`) automatically. Separately, flip the FS kernel default (`_fs_native_enabled`) from opt-in to ON (native where the mk is eligible; numpy fallback for TF-adjust / non-native-scorer fields or a missing wheel). The FS flip is a BREAKING output change (rapidfuzz-rs decides comparison levels, not rapidfuzz-py) → major version bump, gated on the probabilistic bench panel showing F1 holds/improves (if it regresses, the kernel's level logic is fixed before shipping — per the roadmap's "is Rust's behavior the desired spec?" rule). No Rust changes to the byte-exact components; no output change on the 7 already-gated ones.

**Tech Stack:** Python 3.11-3.13, `goldenmatch.core._native_loader`, pytest. Reference: `docs/design/2026-07-01-rust-is-the-reference-roadmap.md`, audit `docs/design/2026-07-01-native-gate-audit-and-goldenflow-parity-design.md`.

---

## Context the worker needs

- The loader (`packages/python/goldenmatch/goldenmatch/core/_native_loader.py`) currently: `native_enabled(component)` returns, under `auto`, `_native is not None and component in _GATED_ON`. `_GATED_ON` = {clustering, block_scoring, pairs, featurize, hashing, field_scoring, autoconfig, sketch}. `pprl_bloom` and `perceptual` are byte-exact but deliberately NOT in `_GATED_ON` (they were held back for a published-wheel + bench precondition under the OLD allowlist discipline).
- Call sites already guard the actual symbol with `hasattr(native_module(), "<symbol>")` / `try…except AttributeError` and fall back to Python, so a wheel missing a symbol degrades safely regardless of the gate.
- Under Rust-reference, byte-exact ⇒ native is correct-by-definition, so the published-wheel+bench precondition is no longer a *correctness* gate; the `_has_symbol` check is exactly the right mechanism (native when the symbol is present, honest fallback when not). This INTENTIONALLY supersedes the old loader-comment precondition for `pprl_bloom`/`perceptual` ("a wall-clock bench confirms the lift" before gating) — not forgotten: the one hard prerequisite it named, the `bloom_clk_batch` rayon guard, already shipped (`GOLDENMATCH_NATIVE_RAYON_MIN_BLOOM_ROWS`, per root CLAUDE.md). Note this waiver explicitly when rewriting the loader docstring so the rationale is on record.
- FS native requires BOTH its own env flag `GOLDENMATCH_FS_NATIVE` AND `native_enabled("block_scoring")` (`_fs_native_enabled`, probabilistic.py:1771-1775). The env flag defaults off today, so the `_has_symbol` flip alone does NOT turn FS on — FS is handled explicitly by Task 2b (which flips its default to authoritative). `block_scoring` stays native after the flip (`_has_symbol("block_scoring")` = True), so FS's second condition is a verified no-op.
- Dispatch telemetry (`_record_dispatch` / `native_dispatch_report` / `NativeDispatchSummary` / `warn_if_slow_path`) reads the gate decision. Keeping the gate honest per-component (via `_has_symbol`) keeps that telemetry accurate — do NOT make `auto` return a blanket `True`.

## File Structure

- Modify: `packages/python/goldenmatch/goldenmatch/core/_native_loader.py`
  - Add `_COMPONENT_SYMBOLS` (component → representative native symbol) + `_has_symbol`.
  - Change the `auto` branch to `_native is not None and _has_symbol(component)`.
  - Repurpose `_GATED_ON` → keep as a documentation constant / retained name for back-compat, but it no longer drives `auto`. Add `_FALLBACK_ONLY` (empty deny-set) as the seam for any future known-divergence.
  - Rewrite the module docstring + the `auto`-hint log to the Rust-reference framing.
- Modify: tests asserting the old semantics (found in Task 3).
- Modify: `packages/python/goldenmatch/CLAUDE.md` (loader posture note), root roadmap doc cross-ref.
- CI (Task 5, may be a separate PR): `.github/workflows/ci.yml` native lane — build the wheel as a REQUIRED artifact and run the native path as the default lane.

---

### Task 1: component → symbol map + `_has_symbol`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/_native_loader.py`
- Test: `packages/python/goldenmatch/tests/test_native_loader_reference.py` (new)

**CRITICAL (from plan review):** there are **12** component strings, not 10 — `native_enabled("simhash")` (`core/sketch.py:389`) and `native_enabled("sail_scoring")` (`sail/scorers.py:13,72`) were missed. And each component must key on the **floor** symbol its auto call site actually invokes, NOT the arrow-era symbol — keying `clustering` on `build_clusters_arrow`/`pairs` on `dedup_pairs_arrow` would regress those to Python on any published wheel that has the working Phase-1 kernels (`connected_components`, `canonicalize_pairs`) but not the arrow ones. So: map each component to a TUPLE of acceptable symbols and treat it native-capable if ANY is present (wheel-skew safe). `sail_scoring` is f32-vs-f64 divergent → it goes in `_FALLBACK_ONLY` (Task 2), NOT the capability map.

- [ ] **Step 0: Enumerate the real component set** — `grep -rn 'native_enabled("' packages/python/goldenmatch/goldenmatch` and record every distinct component string. Expect: clustering, block_scoring, pairs, featurize, hashing, field_scoring, autoconfig, sketch, simhash, pprl_bloom, perceptual, sail_scoring (12). Verify each mapped symbol against the auto CALL SITE (not just the crate export) — the call site names the floor symbol.

- [ ] **Step 1: Write the failing test** (derive the component set; assert every one is disposed — mapped OR in `_FALLBACK_ONLY`):

```python
# tests/test_native_loader_reference.py
import re, pathlib
from goldenmatch.core import _native_loader as nl

def _components_used_in_source() -> set[str]:
    root = pathlib.Path(nl.__file__).parent.parent  # goldenmatch/
    found = set()
    for p in root.rglob("*.py"):
        found |= set(re.findall(r'native_enabled\(\s*["\']([a-z_]+)["\']', p.read_text(encoding="utf-8")))
    return found

def test_every_component_has_a_disposition():
    # every component string passed to native_enabled() must be either capable
    # (in _COMPONENT_SYMBOLS) or explicitly fallback-only — no silent omissions.
    for comp in _components_used_in_source():
        assert comp in nl._COMPONENT_SYMBOLS or comp in nl._FALLBACK_ONLY, comp

def test_has_symbol_false_for_unknown_component():
    assert nl._has_symbol("does_not_exist") is False
```

- [ ] **Step 2: Run it, verify it fails.**
  Run: `.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_native_loader_reference.py -q`

- [ ] **Step 3: Implement** — add to `_native_loader.py` (tuple-of-symbols, any-present; floor symbol first):

```python
# component -> the native symbol(s) whose presence means "this component can run
# native on this wheel". A component is native-capable if ANY listed symbol is
# present (floor symbol first) so an OLDER published wheel that carries the
# Phase-1 kernel but not the arrow one still runs native (wheel-skew safe). Call
# sites still guard each specific symbol; this is the per-component gate for auto
# + the honest dispatch telemetry.
_COMPONENT_SYMBOLS: dict[str, tuple[str, ...]] = {
    "clustering": ("connected_components", "build_clusters_arrow"),
    "block_scoring": ("score_block_pairs", "score_block_pairs_arrow"),
    "pairs": ("canonicalize_pairs", "dedup_pairs_arrow"),
    "featurize": ("char_ngram_features",),
    "hashing": ("record_fingerprint", "record_fingerprints_batch"),
    "field_scoring": ("score_field_matrix",),
    "autoconfig": ("autoconfig_decide_plan",),
    "sketch": ("sketch_simhash_band_hashes_batch",),
    "simhash": ("sketch_simhash_band_hashes_batch",),  # same byte-exact kernel as sketch
    "pprl_bloom": ("bloom_clk_batch",),
    "perceptual": ("perceptual_phash_image",),
    # NOTE: "sail_scoring" is intentionally absent here — it is f32-vs-f64
    # divergent and lives in _FALLBACK_ONLY (Task 2), so it never auto-runs native.
}

def _has_symbol(component: str) -> bool:
    syms = _COMPONENT_SYMBOLS.get(component)
    if not syms or _native is None:
        return False
    return any(hasattr(_native, s) for s in syms)
```

Verify each first-listed (floor) symbol against the actual auto call site during Step 0 (e.g. `clustering` -> `core/cluster.py` calls `connected_components`/`mst_split_components`; `pairs` -> `core/pairs.py` calls `canonicalize_pairs`/`dedup_pairs_max_score`; `block_scoring` floor = whatever `backends/score_buckets.py` invokes before the arrow kernel). Adjust the tuple if the real floor differs.

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `feat(native): component->symbol capability map (wheel-skew-safe) for reference-mode gate`

---

### Task 2: flip the `auto` gate to symbol-presence

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/_native_loader.py` (`native_enabled`, docstring, hint log)
- Test: `packages/python/goldenmatch/tests/test_native_loader_reference.py`

- [ ] **Step 1: Write the failing test** (use monkeypatch to simulate a wheel with symbols)

```python
def test_auto_runs_native_for_byte_exact_ungated_components(monkeypatch):
    # pprl_bloom + perceptual are byte-exact; under reference-mode auto they run
    # native whenever the symbol is present (no longer held behind _GATED_ON).
    class FakeNative:
        bloom_clk_batch = staticmethod(lambda *a, **k: None)
        perceptual_phash_image = staticmethod(lambda *a, **k: None)
    monkeypatch.setattr(nl, "_native", FakeNative)
    monkeypatch.delenv("GOLDENMATCH_NATIVE", raising=False)
    assert nl.native_enabled("pprl_bloom") is True
    assert nl.native_enabled("perceptual") is True

def test_auto_falls_back_when_symbol_absent(monkeypatch):
    class FakeNativeNoBloom:  # wheel predates bloom_clk_batch
        score_block_pairs_arrow = staticmethod(lambda *a, **k: None)
    monkeypatch.setattr(nl, "_native", FakeNativeNoBloom)
    monkeypatch.delenv("GOLDENMATCH_NATIVE", raising=False)
    assert nl.native_enabled("pprl_bloom") is False     # honest fallback
    assert nl.native_enabled("block_scoring") is True

def test_env_zero_still_forces_python(monkeypatch):
    class FakeNative:
        bloom_clk_batch = staticmethod(lambda *a, **k: None)
    monkeypatch.setattr(nl, "_native", FakeNative)
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    assert nl.native_enabled("pprl_bloom") is False
```

- [ ] **Step 2: Run, verify fail** (`pprl_bloom`/`perceptual` currently False under auto because they're not in `_GATED_ON`).

- [ ] **Step 3: Implement** — change the `auto` branch of `native_enabled`:

```python
    # auto / unset: Rust is the reference — run native wherever the component's
    # kernel symbol is present on this wheel; the pure-Python path is the lossy
    # fallback for a missing wheel/symbol. (_FALLBACK_ONLY names any component
    # with a KNOWN divergence that must stay Python even when the symbol exists.)
    result = (
        _native is not None
        and component not in _FALLBACK_ONLY
        and _has_symbol(component)
    )
    _record_dispatch(component, result)
    return result
```

Add near `_GATED_ON`:

```python
# Components with a native symbol that is KNOWN to diverge from the Python
# reference and must NOT auto-run native even under reference-mode.
#   - sail_scoring: score_field_pairwise returns f32 vs the pure f64 floor
#     (boundary-nondeterminism, same class as FS); stays Python under auto until
#     its parity battery is green on the PUBLISHED wheel. Its own docstring
#     (sail/scorers.py:13-21) documents this; it was previously "off by accident"
#     (unmapped) — make it off ON PURPOSE.
# (field_scoring is 1e-4-near-exact and was already gated pre-flip, so it is NOT
# here. FS block scoring is gated separately via GOLDENMATCH_FS_NATIVE — Task 2b.)
_FALLBACK_ONLY: frozenset[str] = frozenset({"sail_scoring"})
```

Update the module docstring (lines 1-17) and the `auto`-hint log (currently says "gated set only … set =1 for full acceleration") to the reference-mode framing: native runs wherever a kernel symbol exists; `=1` additionally *requires* it (raise if the wheel is missing); `=0` forces the fallback. `_GATED_ON` is retained as a documentation constant (byte-exact-signed-off history) but no longer drives `auto`.

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `feat(native): reference-mode auto gate (native wherever the symbol exists)`

---

### Task 2b: make FS native authoritative (default ON), gated on measured F1

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/probabilistic.py` (`_fs_native_enabled`, ~line 1756)
- Test: `packages/python/goldenmatch/tests/test_probabilistic.py` (the `TestNativeFSParity` block)
- Bench (gate): `.github/workflows/bench-probabilistic.yml` panel (`scripts/bench_er_headtohead`)

**Rationale:** Under Rust-reference the FS native kernel's output IS the spec. It is NOT an end-to-end perf win (the per-block Python loop dominates, not the scoring math), so the justification is single-source-of-truth (same as `autoconfig`/`sketch`). Determinism confirmed safe: native FS is reproducible run-to-run; the docstring's reproducibility concern is native-vs-numpy divergence, which disappears when native is the sole path.

- [ ] **Step 1: MEASURE FIRST (the product decision gate).** Run the probabilistic bench panel both ways and diff F1 per dataset:
  - Baseline (numpy authoritative): panel with `GOLDENMATCH_FS_NATIVE` unset.
  - Treatment (native authoritative): panel with `GOLDENMATCH_FS_NATIVE=1`.
  - Run in CI via `bench-probabilistic.yml` (do NOT run the panel locally — heavy). Record the F1 delta per dataset (historical_50k, febrl3, synthetic, dblp_acm).
  - **DECISION:** if F1 holds or improves on every dataset (within noise) → proceed. If any dataset REGRESSES materially → STOP: the native kernel's level decisions are wrong, not "the new reference"; fix the Rust kernel (rapidfuzz-rs level thresholds) to match the desired spec first, then re-measure. Do not ship a regression under the banner of "Rust is authoritative."

- [ ] **Step 2: Write the failing test** — default-on assertion:

```python
def test_fs_native_authoritative_by_default(monkeypatch):
    # Reference-mode: FS native is ON by default (no GOLDENMATCH_FS_NATIVE needed)
    # when the ext + symbol are present. numpy is the fallback, not the default.
    monkeypatch.delenv("GOLDENMATCH_FS_NATIVE", raising=False)
    from goldenmatch.core import probabilistic as p
    from goldenmatch.core._native_loader import native_available, native_module
    if native_available() and hasattr(native_module(), "score_block_pairs_fs"):
        assert p._fs_native_enabled() is True

def test_fs_native_force_off_still_works(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "0")
    from goldenmatch.core import probabilistic as p
    assert p._fs_native_enabled() is False
```

- [ ] **Step 3: Run, verify fail** (today `_fs_native_enabled()` returns False when the env is unset).

- [ ] **Step 4: Implement** — change `_fs_native_enabled` (probabilistic.py:1771-1775) so the default (env unset) resolves to native-when-available, and `GOLDENMATCH_FS_NATIVE=0/false/...` is the explicit opt-OUT to numpy:

```python
    val = os.environ.get("GOLDENMATCH_FS_NATIVE")
    if val is not None and val.strip().lower() in ("0", "false", "no", "off", "disabled"):
        return False  # explicit opt-out to the numpy fallback
    from goldenmatch.core._native_loader import native_enabled
    return native_enabled("block_scoring")
```

  Rewrite the docstring: FS native is now the DEFAULT authoritative path (reference-mode); numpy is the reproducible fallback (`GOLDENMATCH_FS_NATIVE=0`) and the automatic fallback for TF-adjust / non-native-scorer fields (`_fs_native_eligible`) or a missing wheel.

- [ ] **Step 5: Invert the FS parity test.** In `TestNativeFSParity`, reframe native as the oracle: the numpy path is asserted acceptably-close to native on the clean/non-boundary corpus (documented lossy at level boundaries), rather than native-must-match-numpy.

- [ ] **Step 6: Run the loader/probabilistic unit tests locally; push for the bench panel + full suite.**
  `.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_probabilistic.py -q -k "native or fs"`

- [ ] **Step 7: Commit** — `feat(fs)!: native FS kernel authoritative by default (breaking: rapidfuzz-rs decides levels)`

**Version:** this is the change that forces the **major version bump** for the pilot PR (breaking output change on the FS path). Bump `pyproject.toml` + `__init__.py` + `CHANGELOG.md` with a migration note (`GOLDENMATCH_FS_NATIVE=0` restores the prior numpy operating point).

---

### Task 3: reconcile tests that assert the old allowlist semantics

**Review finding:** there are NO tests asserting `native_enabled("pprl_bloom"/"perceptual") is False` under auto — so there is little to "invert"; this task is mostly (a) confirming the `_GATED_ON`-membership pins still pass (the constant is retained) and (b) any test asserting `_GATED_ON` GOVERNS auto.

**Files (audit + modify):**
- `packages/python/goldenmatch/tests/test_auto_semantic_blocking.py:177` — asserts `"sketch" in nl._GATED_ON` (still passes; `_GATED_ON` retained as a doc constant — add a comment it no longer governs `auto`).
- `packages/python/goldenmatch/tests/test_native_surface.py`, `test_native_parity.py` — verify they don't assert an ungated component is Python-under-auto; adjust only if they do.
- `test_native_bloom_parity.py` — importskip + `=1` vs `=0` parity; nothing to invert (confirm green).

- [ ] **Step 1** — `grep -rn "_GATED_ON\|native_enabled(" packages/python/goldenmatch/tests` to enumerate every assertion touching gate semantics. Classify each: (a) still-true doc pin, (b) asserts old auto behavior → update, (c) unaffected.
- [ ] **Step 2** — For any (b): update to the reference-mode expectation (native under auto when the symbol is present). For `_GATED_ON` pins (a): keep + add a comment that the constant is documentation now, not the `auto` gate.
- [ ] **Step 3** — Run the loader + native test files locally (native wheel is installed in `.venv`, so these exercise the real gate):
  `.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_native_loader_reference.py packages/python/goldenmatch/tests/test_native_surface.py packages/python/goldenmatch/tests/test_auto_semantic_blocking.py -q`
- [ ] **Step 4: Commit** — `test(native): reconcile gate-semantics assertions for reference-mode`

---

### Task 4: docs — loader posture + package CLAUDE.md

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/_native_loader.py` (docstring — done in Task 2)
- Modify: `packages/python/goldenmatch/CLAUDE.md` (the `goldenmatch-native` / gate note)

- [ ] **Step 1** — In `CLAUDE.md`, add: under reference-mode, `GOLDENMATCH_NATIVE=auto` runs native wherever the component symbol is present (not an allowlist); Python is the lossy fallback; `=1` requires native; `=0` forces fallback; FS native stays opt-in via `GOLDENMATCH_FS_NATIVE`; `_FALLBACK_ONLY` is the seam for a future known-divergence.
- [ ] **Step 2: Commit** — `docs(native): document reference-mode gate semantics`

---

### Task 5: CI inversion — native wheel REQUIRED + native as the default lane (may be a separate PR)

**Files:**
- Modify: `.github/workflows/ci.yml` (native lane)

- [ ] **Step 1** — Locate the goldenmatch native/parity lane. Confirm today it builds the wheel and runs `@native_only` tests as an OPT-IN lane that skips without the wheel.
- [ ] **Step 2** — Make the wheel build a REQUIRED step for the goldenmatch python lane (so the default test run exercises the native path), and keep a `GOLDENMATCH_NATIVE=0` fallback sub-lane that asserts the pure-Python path still passes (the fallback is tested, not authoritative). Wire it into `ci-required` per the `changes`-filter pattern in the repo CLAUDE.md.
- [ ] **Step 3** — Do NOT run the full suite locally (OOM per repo CLAUDE.md). Push and let CI validate; arm `gh pr merge --auto --squash` once the required lanes are green (per feedback_dont_poll_ci_arm_automerge).
- [ ] **Step 4: Commit** — `ci(native): build the wheel as a required artifact; native is the default lane`

---

## Decisions (resolved)

- **FS native: AUTHORITATIVE** (decided 2026-07-01). Task 2b flips it default-ON, numpy becomes the fallback. Breaking output change → major bump. Ship ONLY if the bench panel shows F1 holds/improves (Task 2b Step 1); a regression means fix the kernel first, not ship it. `GOLDENMATCH_FS_NATIVE=0` remains as the reproducible-numpy escape hatch.

## Out of scope (later waves)
- goldenanalysis + goldencheck flips (near-zero risk, same `_has_symbol` pattern — they already have it).
- goldenflow `phone_validate` / `phone_national` Rust fixes + product decisions.
- Deleting/relabeling the pure-Python kernels as "lossy fallback" in docs across the suite (rollout-docs-sweep).
