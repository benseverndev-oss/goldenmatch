# Zero-config dedupe: finish the arrow-lane polars eviction

> **Status:** MILESTONE ACHIEVED 2026-07-14. Continues the 3.x engine-descent
> eviction (`project_goldenmatch_polars_eviction`).
>
> **DONE + MERGED:** zero-config `dedupe_df(pa.Table, config=None)` runs
> polars-free. W1S1 flag default-on (#1765), W1S3 quality scan-only degrade
> (#1766), W1S4 multi_pass blocking on the seam (#1767), bucket default (#1761).
> The endgame gate `test_zero_config_dedupe_df_is_polars_free` is now a REAL
> native-gated PASS (was xfail). Leak-catalog finding: the transform-prep + golden
> fallback the `pl.*` catalog flagged have WORKING arrow fallbacks (they don't fire
> when polars is blocked) -- so Wave 2 Stage 6 (golden fallback port) is NOT needed.
>
> **REMAINING (scope-corrected):**
> 1. **Drop the `#1747 [polars]` stopgap** -- NOT a ci.yml edit. The rust `bridge`
>    (`packages/rust/extensions/bridge/src/convert.rs`) builds a POLARS DataFrame
>    from JSON (`json_to_polars_df`) + imports polars for the result seam, so those
>    lanes genuinely need polars until the bridge convert is arrow-ported
>    (json->arrow). Same for the duckdb/dbt call sites if they build polars.
>    Separate Rust effort; can't build/verify the bridge locally.
> 2. **D6 deletion** -- move polars to a `goldenmatch[polars]` optional extra + drop
>    from core deps (a 3.x minor, coordinated with goldencheck/goldenflow floors).
> 3. **Release** goldenmatch + golden-suite lockstep + docs.

## Corrected premise (from the leak catalog, 2026-07-14)

A traced zero-config `dedupe_df(pa.Table, config=None)` on the arrow lane with
`GOLDENMATCH_AUTOCONFIG_ARROW_NATIVE=1` + native on shows the **controller and
scoring spine are already polars-free** (score_buckets runs arrow-native; zero
`blocker.py`/`scorer.py`/`indicators.py` leaks). The stale Tier-3 xfail comment
naming `build_blocks(combined_lf)` + indicator guards was overtaken by the
arrow-native autoconfig work. The 26 remaining `pl.*` access sites are:

| Cluster | Sites | Fires when |
|---|---|---|
| **Golden fallback builder** | `golden.py` `_stable_value_expr`, `build_golden_records_df`, `build_golden_records_from_frames` (~150 accesses) | native `golden_fused` ABSENT (falls back to polars builder). Present in bridge/CI → arrow-native, no leak. |
| **Golden bridge** | `pipeline.py:990 _as_polars_df`, `frame.py:1959 to_frame(pl.DataFrame)`, `frame.py:569 select_eligible_clusters` | feeds/wraps the polars golden builder; gone once golden fallback is arrow-native |
| **Quality prep bridge** | `quality.py:323/325 _scan_and_fix` (`pl.from_arrow`→goldencheck `apply_fixes`→`.to_arrow`) | ONLY when goldencheck returns findings to fix (clean arrow data skips polars) |
| **Transform prep bridge** | `transform.py:80/113 run_transform` (`pl.from_arrow`→apply→arrow) | whenever a transform runs on the arrow lane |
| **Autoconfig scalar-spelling** | `autoconfig.py:314 _polars_scalar_spelling` / `_polars_string_spelling` (1× `pl.Utf8`) | the only autoconfig-layer leak left |
| **Flag default** | `GOLDENMATCH_AUTOCONFIG_ARROW_NATIVE` defaults OFF → `auto_configure_df` coerces arrow→polars at the boundary | production default; the biggest single leak |

**The fork:** in the bridge/CI env native `golden_fused` is present, so the golden
leaks don't fire — Wave 1 (bridge-stopgap-drop) does NOT need the golden port. The
full D6 "polars uninstalled" gate DOES need the golden fallback ported — Wave 2.

## Invariants (every stage)

- **Byte-identical on the polars path.** Each stage keeps a parity gate proving the
  polars lane output is unchanged (config-equivalence for autoconfig; cluster
  membership + golden rows for pipeline stages).
- **Arrow-lane tripwire.** Each stage that closes a leak adds/extends a subprocess
  test with `import polars` BLOCKED (the `_zero_polars_probe.py` mechanism) proving
  the closed path stays polars-free.
- Whole-package `ruff check packages/python/goldenmatch` before every push.
- Feature branch off fresh `origin/main`; squash-merge via the queue; arm auto-merge
  and stop. `benzsevern` auth for `benseverndev-oss`.
- Box note: this dev box lacks native `match_fused`/`golden_fused` (golden falls to
  the polars builder locally) — the fused-present paths (bridge/CI) can only be
  verified in CI. Local runs use the pure/fallback paths + `GOLDENMATCH_NATIVE=0`.

---

## WAVE 1 — Unblock the `#1747` bridge stopgap (native present)

Goal: the bridge `run_dedupe`/`auto_configure` entrypoints run polars-free WITH
native present, so the `rust`/`rust_pgrx`/coverage lanes can drop the `[polars]`
extra (`packages/rust` path-filtered lanes; `GOLDENMATCH_BRIDGE_REQUIRE_PY=1`).

### Stage 1 — Flip `GOLDENMATCH_AUTOCONFIG_ARROW_NATIVE` default-on (removes the boundary coercion)

- **Prereq — broaden the parity gate.** `test_autoconfig_arrow_native_parity.py`
  today covers exact-id-plus-names / multi-source / shared-email-switchboard. Add
  shapes that exercise the whole config surface: NE-promotion, date/dob fields,
  probabilistic-eligible, high-null blocking, learned-blocking (≥50k gate),
  all-fuzzy no-anchor. Assert config-equivalence (matchkeys + blocking + backend)
  AND cluster-membership equivalence arrow-vs-polars for each.
- Flip `_autoconfig_arrow_native_enabled()` default true (env `=0` restores the
  coerce path). Keep the boundary coercion as a **guarded fallback**: only for
  shapes the arrow-native path declines (log a one-time WARNING so declines are
  visible, never silent).
- Update `test_zero_config_no_polars.py` Tier-3 xfail reason (the coercion is no
  longer the default blocker; golden fallback + quality/transform remain).
- **Gate:** the broadened parity gate + the full autoconfig suite green.

### Stage 2 — Port the autoconfig scalar-spelling helper (tiny)

- `autoconfig.py:298-314` `_polars_scalar_spelling` / `_polars_string_spelling`
  build a `pl.Series`/`pl.Utf8` to normalize a scalar's spelling. Replace with a
  pure-Python / pyarrow-compute equivalent (single-value normalization — no frame).
- **Gate:** existing autoconfig tests unchanged + the leak drops from the catalog.

### Stage 3 — De-bridge quality prep (`quality._scan_and_fix`)

- Route `apply_fixes` through goldencheck's arrow-native fixer surface instead of
  `pl.from_arrow(df)`→polars→`.to_arrow()`. goldencheck 3.0 is the Arrow Flip
  (`project_goldencheck_arrow_fused_scan`) — confirm `engine.fixer.apply_fixes`
  has an arrow entry (or add a thin arrow adapter there); if not, this stage forks
  to a goldencheck PR first (lockstep floor bump).
- Preserve the clean-data fast path (no findings → no polars, already true).
- **Gate:** quality parity (same fixes applied) on both lanes + arrow tripwire over
  a dirty fixture (forces the fixer path).

### Stage 4 — De-bridge transform prep (`transform.run_transform`)

- Route the transform application through goldenflow's arrow transform surface
  (`project_goldenflow_fused_columnar_apply` — fused apply is arrow-native) instead
  of `pl.from_arrow`. Keep the polars path for the `_is_pl_in` (polars-lane) caller
  byte-identically.
- **Gate:** transform parity on both lanes + arrow tripwire over a fixture that
  runs a real transform chain.

### Stage 5 — Prove the bridge path polars-free + drop the stopgap

- Add a bridge-oriented tripwire mirroring the `rust` lane: `pip`-installed
  goldenmatch (native present, `golden_fused` available) runs `run_dedupe` +
  `auto_configure` with `import polars` blocked → completes.
- Drop the `[polars]` extra from the `rust`/`rust_pgrx`/coverage lanes in
  `.github/workflows/ci.yml` (revert the `#1747` stopgap). Confirm the bridge lanes
  green without it.
- **Gate:** the rust bridge lanes green with no `[polars]`. Update the tracker's
  "Bridge dedupe/autoconfig path still needs polars" item → resolved.

---

## WAVE 2 — Full D6 "polars not installed" gate (fallback paths too)

Goal: `test_zero_config_dedupe_df_is_polars_free` flips xfail→green (strict) — a
whole zero-config dedupe with polars uninstallable.

### Stage 6 — Port the golden FALLBACK builder to arrow-native

- The hard one. `build_golden_records_df` + `_stable_value_expr` evaluate
  survivorship (most_frequent / longest / first / struct tie-breaks) via polars
  expressions. When native `golden_fused` declines, this is the path. Options:
  (a) a pyarrow-compute survivorship builder mirroring the polars expressions
  byte-for-byte; (b) make the pure-Python golden builder the decline path (row
  loop over arrow) — slower but polars-free and only hit when the kernel declines.
  Choose (b) for correctness-first (matches the "pure = lossy-but-correct fallback"
  thesis), measure, then (a) if the fallback is a real hot path.
- Remove `_as_polars_df` (pipeline.py:990) + `build_golden_records_from_frames`
  polars construction + `frame.py:569 select_eligible_clusters` polars once the
  builders no longer need a PolarsFrame.
- **Gate:** golden byte-parity (rows + per-cell survivorship) vs the polars builder
  on a corpus incl. correlated survivorship + field_rules + quality-weighting; arrow
  tripwire running golden with `golden_fused` forced-absent + polars blocked.

### Stage 7 — Quality/transform with zero polars even when uninstalled

- Stages 3-4 de-bridge WHEN goldencheck/goldenflow have arrow surfaces, but those
  packages may still `import polars` internally. For the D6 "uninstalled" gate they
  must be polars-optional on the paths goldenmatch calls. goldencheck 3.0 is
  polars-optional; confirm goldenflow's arrow transform path is too (`[polars]`
  extra, not a hard import). Bump floors as needed (lockstep releases).
- **Gate:** the arrow tripwire from Stages 3-4 run with polars BLOCKED (not just
  "not-on-the-lane") — dirty fixture + transform chain both complete.

### Stage 8 — Flip the endgame gate + D6 deletion prep

- `test_zero_config_dedupe_df_is_polars_free`: xfail→green, `strict=True`. Add an
  import-hook assertion (`polars not in sys.modules`) over the full zero-config run.
- D6 deletion prep (tracked, needs the ~8-PR train + goldencheck/goldenflow floors):
  move polars to a `goldenmatch[polars]` optional extra, drop it from core deps,
  add the subprocess import-gate to `ci-required`. This is the 3.x minor cut —
  coordinate with `feedback_golden_suite_lockstep_release`.
- **Gate:** the D6 gate green in CI (native on) + the zero-polars-installed gate.

---

## Execution notes

- Wave 1 ships value at Stage 5 (stopgap drop) independent of Wave 2.
- Stages 3/4/7 may spawn goldencheck/goldenflow PRs (arrow fixer / arrow transform
  surface + polars-optional) — sequence those FIRST with a floor bump, per the
  lockstep rule, so goldenmatch's floor is satisfiable on PyPI before it tags.
- Each stage is one PR. Parity gate + arrow tripwire + ruff, then arm auto-merge.
- Golden fallback (Stage 6) is the critical-path risk — scope a byte-parity harness
  BEFORE touching `build_golden_records_df` (mirror the ClusterFrames Tier A
  discipline: prove equivalence on a corpus, then port).
