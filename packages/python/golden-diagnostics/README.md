# golden-diagnostics

Anomaly diagnostics + prefilled GitHub issue prompts for the Golden Suite.

When a suite package hits a state that is probably *its own bug* — a silent
non-optimized (slow) path, a broken native install, an unexpected crash — this
turns it into an actionable message carrying a **prefilled GitHub issue URL**.

It **sends nothing anywhere** (no network, no telemetry) — it is a better error
message, not analytics — so unlike opt-in usage analytics it is safe to run by
default. Silence every prompt with `GOLDEN_DIAGNOSTICS=0`.

## Discipline: anomalies only

Fires **only on anomalies**, never on expected fallbacks or user-input errors.
"Native not installed → pure Python" is normal and must not nag; "native
installed but the kernel symbol is missing" (wheel skew) is an anomaly worth a
prompt. A bad config or a `FileNotFoundError` is the user's situation, not a suite
bug.

## API

```python
from golden_diagnostics import report_anomaly, report_exception, issue_url

# Report an anomaly (warn-once per (category, once_key) per process):
report_anomaly(
    "native-wheel-skew",
    "the installed native wheel is missing a kernel symbol; running slow",
    once_key="native-wheel-skew:field_scoring",
    package="goldenmatch",
    version="3.0.0",
)

# Report an exception only if it is not a by-design / user-facing one:
try:
    ...
except Exception as exc:
    report_exception(exc, category="dedupe", summary="dedupe crashed",
                     expected=[ValueError, FileNotFoundError])
    raise
```

`report_anomaly` / `report_exception` never raise — diagnostics is never
load-bearing. See `docs/design/2026-07-12-diagnostics-issue-reporter.md` in the
monorepo for the full design and the anomaly taxonomy.
