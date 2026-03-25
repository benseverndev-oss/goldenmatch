"""Scheduled scans — run goldencheck on a cron schedule."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["run_schedule"]

# Simple cron-like intervals
_INTERVALS = {
    "hourly": 3600,
    "daily": 86400,
    "weekly": 604800,
    "5min": 300,
    "15min": 900,
    "30min": 1800,
}


def run_schedule(
    files: list[Path],
    interval: str = "daily",
    domain: str | None = None,
    webhook: str | None = None,
    notify_on: str = "grade-drop",
    json_output: bool = False,
) -> None:
    """Run scheduled scans at a fixed interval.

    Args:
        files: Files to scan
        interval: "hourly", "daily", "weekly", "5min", "15min", "30min", or seconds as int
        domain: Domain pack
        webhook: Webhook URL for notifications
        notify_on: Notification trigger
        json_output: Output as JSON
    """
    import signal
    import sys
    from goldencheck.engine.scanner import scan_file
    from goldencheck.engine.confidence import apply_confidence_downgrade
    from goldencheck.engine.history import record_scan
    from goldencheck.models.finding import Severity
    from goldencheck.reporters.json_reporter import report_json as report_json_fn

    if isinstance(interval, str) and interval in _INTERVALS:
        interval_secs = _INTERVALS[interval]
    else:
        try:
            interval_secs = int(interval)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid interval: {interval}. Use: {', '.join(_INTERVALS.keys())} or seconds.")

    shutdown = False

    def _handle_signal(signum, frame):
        nonlocal shutdown
        shutdown = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    def _ts():
        return datetime.now().strftime("[%H:%M:%S]")

    print(f"{_ts()} GoldenCheck scheduler started — scanning {len(files)} file(s) every {interval}")

    run_count = 0
    while not shutdown:
        run_count += 1
        print(f"{_ts()} Run #{run_count}")

        for file in files:
            if shutdown:
                break
            try:
                findings, profile = scan_file(file, domain=domain)
                findings = apply_confidence_downgrade(findings, llm_boost=False)

                errors = sum(1 for f in findings if f.severity == Severity.ERROR)
                warnings = sum(1 for f in findings if f.severity == Severity.WARNING)
                grade, _ = profile.health_score()

                record_scan(file, profile, findings)

                if json_output:
                    report_json_fn(findings, profile, sys.stdout)
                else:
                    print(f"  {file.name}: {errors} errors, {warnings} warnings ({grade})")

                # Webhook
                if webhook:
                    from goldencheck.engine.history import get_previous_scan
                    from goldencheck.engine.notifier import should_notify, send_webhook

                    by_col: dict[str, dict[str, int]] = {}
                    for f in findings:
                        if f.severity >= Severity.WARNING:
                            by_col.setdefault(f.column, {"errors": 0, "warnings": 0})
                            key = "errors" if f.severity == Severity.ERROR else "warnings"
                            by_col[f.column][key] = by_col[f.column].get(key, 0) + 1
                    g, s = profile.health_score(findings_by_column=by_col)

                    prev = get_previous_scan(file)
                    if should_notify(g, findings, prev, notify_on):
                        send_webhook(webhook, str(file), g, s, findings, notify_on,
                                     previous_grade=prev.grade if prev else None)

            except Exception as e:
                logger.warning("Failed to scan %s: %s", file, e)
                print(f"  {file.name}: ERROR — {e}")

        # Wait for next run
        for _ in range(interval_secs):
            if shutdown:
                break
            time.sleep(1)

    print(f"\n{_ts()} Scheduler stopped after {run_count} run(s).")
