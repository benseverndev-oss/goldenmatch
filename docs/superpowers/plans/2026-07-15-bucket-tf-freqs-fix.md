# Bucket tf_freqs Fix (#1781) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thread `MatchkeyField.tf_freqs` through the bucket fast path so #1318's TF name
downweight actually applies on the default scoring path, with the parity-matrix case that should
have caught the gap.

**Spec:** `docs/superpowers/specs/2026-07-15-bucket-tf-freqs-fix-design.md` — READ FIRST. Pins:
Approach A (resolver gains the kwarg; plugin branch only; TypeError-fallback wrapper as the
score_pair-side twin of `_fuzzy_score_matrix`'s score_matrix posture), the NE site untouched,
the float32-vs-float64 fixture caveat, and the #1319 Leg-A success bar.

**Architecture:** One production file (`backends/score_buckets.py`: `_resolve_score_pair_callable`
+ its weighted-field call site), three test additions.

**Tech Stack:** Python 3.12, pytest. No Rust/TS.

---

## Environment / repo mechanics

- NEW worktree `D:\show_case\gm-1781`, branch `fix/1781-bucket-tf-freqs` off freshly-fetched
  `origin/main`. **NEVER `git stash`.**
- Tests via main venv + worktree PYTHONPATH (Git Bash):
  `cd /d/show_case/gm-1781/packages/python/goldenmatch && PYTHONPATH="D:/show_case/gm-1781/packages/python/goldenmatch" POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 GOLDENMATCH_NATIVE=0 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest <tests> -q`
- Ruff: `D:/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check <files>`.
- `docs/superpowers/` gitignored → `git add -f` spec + plan. Commit trailers:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01R8MSaGwsjdxzf6Z7Bt3BXs`
- Push/PR: `unset GH_TOKEN`; push via
  `git push "https://x-access-token:$(gh auth token --user benzsevern)@github.com/benseverndev-oss/goldenmatch.git" <branch>`;
  `GH_TOKEN=$(gh auth token --user benzsevern) gh pr create/merge --auto`; STOP after arming.

**Key code (verified on origin/main):**
- `packages/python/goldenmatch/goldenmatch/backends/score_buckets.py` —
  `_resolve_score_pair_callable(scorer_name)` at ~204 (plugin branch at the END, ~260-266:
  `plugin = PluginRegistry.instance().get_scorer(...)`, `fn = getattr(plugin, "score_pair", None)`,
  `return fn`); weighted-field call site ~427 (`fn = _resolve_score_pair_callable(scorer)` inside
  `for f in mk.fields` — `f` in scope); NE-spec call site ~342 (gated on
  `_SCORE_FIELD_DIRECT_SCORERS`, builtins only — the plugin branch never fires there; UNTOUCHED);
  per-pair use in `_score_one_bucket_fast` ~964 (`score_fns[f_idx](va, vb)`); the float32/float64
  borderline note at ~92-97; `_NATIVE_SCORER_IDS` at ~188.
- `packages/python/goldenmatch/goldenmatch/refdata/scorer.py:86-125` —
  `NameFreqWeightedJW.score_pair(val_a, val_b, *, tf_freqs=None)`: with a table → whole-range
  data-driven downweight; without → static census fallback.
- `packages/python/goldenmatch/goldenmatch/core/scorer.py:594-597` — the legacy
  try/except-TypeError posture being mirrored; `:1236` — the legacy per-field threading
  (`tf_freqs=getattr(f, "tf_freqs", None)`).
- `packages/python/goldenmatch/tests/test_bucket_legacy_parity_matrix.py` — scorer matrix
  parameterized at ~108 (`["jaro_winkler", "token_sort", "levenshtein", "soundex_match",
  "ensemble"]`); READ the file to see how a case builds data+config and asserts byte-identical
  multi-member clustering across bucket/legacy.
- The #1319 measurement harness (success bar): session scratchpad
  `C:/Users/bsevern/AppData/Local/Temp/claude/D--show-case-goldenmatch/2af6f77e-d6b2-41c0-ad88-08d7fd3f306e/scratchpad/1319/measure_1319.py`
  (args `a on|off`; builds the 2600-row fixture, runs auto_configure_df + dedupe_df, emits JSON
  with precision/mass). Baseline numbers to beat: bucket ON == OFF byte-identical (P 0.0091);
  legacy ON P 0.0305 at committed threshold 0.8.

## File structure

- Modify: `packages/python/goldenmatch/goldenmatch/backends/score_buckets.py`
- Modify: `packages/python/goldenmatch/tests/test_bucket_legacy_parity_matrix.py`
- Modify or create (implementer's call by where bucket unit tests live — locate with
  `grep -rln "_resolve_score_pair_callable" tests/`): the applied-table regression + back-compat
  wrapper tests.

---

### Task B0: Worktree + baseline

- [ ] **Step 1:** From `D:\show_case\goldenmatch`:
  `git fetch origin main -q && git worktree add /d/show_case/gm-1781 -b fix/1781-bucket-tf-freqs origin/main`
- [ ] **Step 2:** Copy spec + this plan into the worktree `docs/superpowers/{specs,plans}/`;
  `git add -f` both; commit `docs: spec + plan for the #1781 bucket tf_freqs fix` (+ trailers).
- [ ] **Step 3:** Baseline: `pytest tests/test_bucket_legacy_parity_matrix.py -q` → green (note
  the count). If red, STOP.

### Task B1: The fix + all three test layers (TDD)

**Files:** as in File structure.

- [ ] **Step 1: Failing tests.** EVERY new test that constructs a
  `MatchkeyField(scorer="name_freq_weighted_jw")` must `import goldenmatch.refdata` INSIDE the
  test first (the scorer registers only on that import — `refdata/__init__.py:96`; without it
  config construction raises ValueError at schemas.py:158-166 and the test ERRORS instead of
  failing in the predicted mode; xdist self-containment rule).
  - **Parity-matrix TF case** (`test_bucket_legacy_parity_matrix.py`): extend the harness with a
    case whose weighted matchkey has a `name_freq_weighted_jw` field carrying a hand-built
    `tf_freqs` table (e.g. `{"smith": 0.4, "jones": 0.3, "quixote": 0.01, ...}` over the
    fixture's post-transform surname values — READ how the harness builds fields; set
    `f.tf_freqs = {...}` after construction or via the field kwargs if supported). Fixture rules:
    pair scores must sit AWAY from the matchkey threshold (the spec's float32-vs-float64
    caveat) — build names whose downweighted scores are clearly above/below. Assert the harness's
    standard byte-identical bucket-vs-legacy clustering.
  - **Applied-table regression test** (bucket-path unit test): a small common-name fixture (~40
    rows, shared common full names on distinct people + strong unique emails; miniaturized
    #1319 shape) scored through the BUCKET path twice — once with `tf_freqs` populated on the
    name field, once with the table stripped — assert the outputs DIFFER (the table demonstrably
    applied; ON==OFF byte-identical was the bug's signature). Force the bucket path the way the
    parity harness does (read it; `backend="bucket"` or the default-envelope conditions).
  - **Back-compat wrapper unit test:** register a fake plugin scorer whose `score_pair(a, b)`
    LACKS the `tf_freqs` keyword (register inside the test — xdist self-containment rule), put it
    on a field WITH `tf_freqs` set, resolve via `_resolve_score_pair_callable(name, tf_freqs=...)`
    and call it → returns the plugin's score (TypeError fallback engaged, no crash), and a second
    call keeps working (the "permanently degrades" contract).
- [ ] **Step 2:** Run → parity TF case FAILS (bucket ignores the table → clustering diverges from
  legacy) or the regression test FAILS (ON == OFF); back-compat test fails on the missing kwarg.
  (If the parity case unexpectedly PASSES, the fixture's downweight isn't biting — strengthen
  the table skew until legacy's clustering visibly depends on it, THEN confirm bucket diverges.)
- [ ] **Step 3: Implement** in `score_buckets.py`:
  - `_resolve_score_pair_callable(scorer_name: str, tf_freqs: dict[str, float] | None = None)`.
    Built-in branches untouched. Plugin branch:
    ```python
    fn = getattr(plugin, "score_pair", None)
    if fn is None or not tf_freqs:
        return fn
    # #1781: bind the field's TF table so the fast path matches the legacy
    # matrix path (core/scorer.py:1236). TypeError fallback = the
    # score_pair-side twin of _fuzzy_score_matrix's score_matrix posture
    # (core/scorer.py:594-597): a legacy plugin without the keyword degrades
    # permanently to the bare call.
    def _with_tf(a, b, _fn=fn, _tf=tf_freqs):
        try:
            return _fn(a, b, tf_freqs=_tf)
        except TypeError:
            return _fn(a, b)

    return _with_tf
    ```
    (A once-latched flag instead of per-call try is fine too — implementer's call; per-call
    try on the happy path costs nothing when no exception is raised. Keep whichever reads best
    with a comment.) Update the resolver docstring: #1781, the sample-telemetry-vs-final-dedupe
    skew, and that only the plugin branch consumes the kwarg.
  - Weighted call site (~427): `fn = _resolve_score_pair_callable(scorer, getattr(f, "tf_freqs", None))`.
  - NE call site (~342): UNTOUCHED (add no kwarg; the spec documents why).
- [ ] **Step 4:** All three test layers PASS; whole parity file green; ruff clean.
- [ ] **Step 5:** Commit `fix(goldenmatch): bucket fast path threads MatchkeyField.tf_freqs (#1781)` (+ trailers).

### Task B2: Success bar + PR

- [ ] **Step 1: #1319 Leg-A re-measure** on the fix branch: run the scratchpad harness (path in
  Key code) with PYTHONPATH pointed at THIS worktree, `a on` and `a off`:
  expected — bucket ON now DIVERGES from bucket OFF, and bucket ON's precision matches legacy
  ON (~0.0305 at the committed 0.8 threshold; NOT the ~0.99 full recovery — that lands with
  PR2b). Record the numbers.
- [ ] **Step 2:** Targeted sweep: the parity file + the bucket unit-test file(s) +
  `tests/test_tf_name_weighting_1207.py` + `pytest tests/test_scorer.py -q` (adjacent) → green.
- [ ] **Step 3:** Push (auth dance); PR titled
  `fix(goldenmatch): bucket fast path threads tf_freqs -- #1318 downweight was a no-op on the default path (#1781)`,
  body: the bug (telemetry-yes/output-no skew), the fix shape, the three test layers, the
  re-measure numbers (bucket ON == legacy ON), `Closes #1781`, and a note that the redesigned
  PR2b (#1319) builds on this. Arm `gh pr merge --auto`, STOP.
- [ ] **Step 4:** After merge: update memory (`project_1207_autoconfig_blocking_union`) +
  work tracker; comment on #1319 that the prerequisite landed.
