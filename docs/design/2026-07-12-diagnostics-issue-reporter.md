# Diagnostics + prefilled GitHub issue prompts

**Status:** Implemented (goldenmatch wiring). `golden-diagnostics` package + four
goldenmatch anomaly wires shipped; other packages adopt the shared util as
follow-ups.

## Goal

When the software hits a state that is *probably its own bug* — a slow
non-optimized path taken silently, a broken native install, an unexpected crash —
tell the user plainly and hand them a **prefilled GitHub issue** to file. Turn a
silent degradation into an actionable report.

## The governing discipline: fire only on ANOMALIES

The naive version of this feature ("any error → tell them to file an issue")
would be noise. Most fallbacks in this codebase are **by design** — pure-Python
when the native wheel isn't installed is normal and must never nag. A prompt that
fires on expected fallbacks trains users to ignore it, which destroys the signal
on the one that matters.

So the helper fires **only on anomalies**, never on expected fallbacks or
user-input errors:

| Condition | Bug? | Response |
|---|---|---|
| Native **not installed** → pure Python | No, expected | existing "install `[native]`" hint (unchanged) |
| Native **installed but kernel symbol missing** (wheel skew, #688 class) | Anomaly | **prompt an issue** |
| Native **installed but failed to load** (broken `.so` / ABI mismatch) | Anomaly | **prompt an issue** |
| Unexpected crash at `dedupe_df` / `match_df` | Likely a bug | **re-raise + prompt with traceback + env** |
| The config **linter itself** crashes | Anomaly | **prompt an issue** (linting skipped) |
| `ControllerNotConfidentError` / bad config / `FileNotFound` / `ValueError` | No — user situation / by-design refuse | recovery guidance elsewhere, **no prompt** |

## Architecture

### Shared package: `golden-diagnostics` (`packages/python/golden-diagnostics`)

Pure-Python, zero runtime deps. **Sends nothing anywhere** — it is a better error
message, not telemetry — so unlike opt-in analytics it is safe on by default.
Kill switch `GOLDEN_DIAGNOSTICS=0`.

- `environment_report(package, version, extra)` — PII-safe env dict (python /
  platform / arch / package+version; `extra` scalars scrubbed of path-like or
  >200-char values, mirroring the analytics allow-list discipline).
- `issue_url(title, body, *, repo, labels)` — prefilled GitHub new-issue URL,
  length-capped to stay under browser URL limits.
- `report_anomaly(category, summary, *, detail, exc, once_key, ...)` — emits an
  actionable message + prefilled URL, **warn-once per `(category, once_key)` per
  process**; honors the kill switch; **never raises** (diagnostics is never
  load-bearing).
- `report_exception(exc, *, category, summary, expected, ...)` — reports only if
  `exc` is not one of `expected`, so a caller can `report_exception(...); raise`
  unconditionally.

### GoldenMatch binding (`goldenmatch/core/diagnostics.py`)

Guarded thin adapter that pins package/version/repo and is a **silent no-op if
`golden-diagnostics` is unavailable**. Exposes `report_anomaly`,
`report_unexpected`, and the `guard_entrypoint(category, summary)` decorator.
`_expected_exceptions()` resolves the by-design set lazily (controller refuse,
config-lint error, FS mismatch, throughput/slow-path refusals, path-guard) plus
the user-input builtins.

## The four wires (goldenmatch)

1. **Native wheel-skew slow path** — `_native_loader.warn_if_slow_path` gained a
   prompt for the specific anomalous subclass: a hot-path component fell back
   **while its kernel symbol is missing from the loaded wheel** (`not
   _has_symbol(c)`). Symbol-present-but-fell-back (a scorer with no native kernel)
   is the legit case and is **not** flagged.
2. **Unexpected crash at entry points** — `dedupe_df` / `match_df` are decorated
   with `@guard_entrypoint`; an exception not in the expected set is reported
   (traceback + env) then re-raised unchanged.
3. **Config-lint internal crash** — `_run_config_lint`'s fail-open `except` now
   reports the linter *itself* crashing (distinct from the user's config being
   bad, which surfaces as findings).
4. **Broken optional-dep install** — `_native_loader` distinguishes
   `ModuleNotFoundError` (not installed → expected) from a real load failure
   (installed but broken → anomaly), captured at import and reported lazily on
   first `native_enabled()` (avoids an import-time cycle).

## Backward compatibility / safety

- The `guard_entrypoint` decorator uses `functools.wraps`, so signature
  introspection and attribute patching of `dedupe_df`/`match_df` are unchanged
  (86 api/native/lint tests pass).
- Every wire is wrapped so diagnostics **never** breaks a run.
- Default-on is safe because nothing is transmitted.

## Release sequencing (publish first, then depend)

`golden-diagnostics` is a **workspace member but deliberately NOT a runtime
dependency** of goldenmatch yet. It is not on PyPI, and a hard requirement on an
unpublished dist breaks every plain `pip install goldenmatch` lane — the
`web_ui_e2e` lane and the bench/eval lanes install with plain pip, which does not
read the uv workspace and so tries (and fails) to resolve `golden-diagnostics`
from PyPI. (The first push of this feature declared the hard dep and reddened
`web_ui_e2e` for exactly this reason.)

So the sequencing is: the feature ships **active where `golden-diagnostics` is
installed** — dev checkouts and the `uv sync --all-packages` CI lanes, which
install every workspace member — and **gracefully dormant** everywhere else (the
`goldenmatch.core.diagnostics` import is guarded → no prompts, never a crash).
The follow-up is to **publish `golden-diagnostics` to PyPI, then promote it to a
runtime dependency** (or a `[diagnostics]` extra) so end users on `pip install
goldenmatch` get the prompts too. Publish first, then depend — never the reverse.

## Follow-ups (not in this slice)

- Adopt `golden-diagnostics` in goldencheck / goldenflow / goldenpipe / infermap
  (each passing its own `package`/`repo`), wiring their own anomaly points.
- CLI-surface polish: render the prompt via `rich` at the CLI boundary (today it
  is a `logger.warning`, which the CLI already surfaces).
- A `goldenmatch doctor` command that runs the environment report + known-anomaly
  checks on demand.
