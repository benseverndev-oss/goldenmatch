"""Watch engine — poll a directory for data file changes and re-scan."""
from __future__ import annotations

import logging
import os
import signal
import time
from datetime import datetime
from pathlib import Path

from goldencheck.models.finding import Severity

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".csv", ".parquet", ".xlsx", ".xls"}


def watch_directory(
    path: Path,
    interval: int = 60,
    pattern: str | None = None,
    exit_on: str | None = None,
    json_output: bool = False,
) -> int:
    """Poll a directory for changes and re-scan modified files.

    Returns exit code: 0 if clean, 1 if findings at exit_on threshold.
    """
    from goldencheck.engine.scanner import scan_file
    from goldencheck.engine.confidence import apply_confidence_downgrade
    from goldencheck.reporters.json_reporter import report_json
    import sys

    path = Path(path)
    if not path.is_dir():
        raise ValueError(f"Not a directory: {path}")

    # Graceful shutdown
    shutdown = False
    last_exit_code = 0

    def _handle_signal(signum, frame):
        nonlocal shutdown
        shutdown = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # Track file modification times
    mtimes: dict[str, float] = {}
    globs = [pattern] if pattern else [f"*{ext}" for ext in SUPPORTED_EXTENSIONS]

    def _ts():
        return datetime.now().strftime("[%H:%M:%S]")

    def _find_files():
        files = []
        for g in globs:
            files.extend(path.glob(g))
        return sorted(set(files))

    print(f"{_ts()} Watching {path}/ ({', '.join(globs)}) — polling every {interval}s")

    first_run = True

    while not shutdown:
        files = _find_files()
        scanned = 0

        for f in files:
            if shutdown:
                break

            try:
                mtime = os.stat(f).st_mtime
            except OSError:
                continue

            str_path = str(f)
            if not first_run and mtimes.get(str_path) == mtime:
                continue

            if not first_run:
                print(f"{_ts()} {f.name} changed — re-scanning...")

            mtimes[str_path] = mtime
            scanned += 1

            try:
                findings, profile = scan_file(f)
                findings = apply_confidence_downgrade(findings, llm_boost=False)

                errors = sum(1 for x in findings if x.severity == Severity.ERROR)
                warnings = sum(1 for x in findings if x.severity == Severity.WARNING)
                grade, score = profile.health_score()

                if json_output:
                    report_json(findings, profile, sys.stdout)
                else:
                    print(f"{_ts()} Scanned {f.name} — {errors} errors, {warnings} warnings ({grade})")

                if errors > 0 or warnings > 0:
                    last_exit_code = 1
                else:
                    last_exit_code = 0

                # Check exit_on threshold
                if exit_on == "error" and errors > 0:
                    print(f"{_ts()} Exit: errors found (--exit-on error)")
                    return 1
                elif exit_on == "warning" and (errors > 0 or warnings > 0):
                    print(f"{_ts()} Exit: warnings found (--exit-on warning)")
                    return 1

            except Exception as e:
                logger.warning("Failed to scan %s: %s", f, e)
                print(f"{_ts()} Error scanning {f.name}: {e}")

        first_run = False

        # Wait for next poll
        for _ in range(interval):
            if shutdown:
                break
            time.sleep(1)

    print(f"\n{_ts()} Stopped.")
    return last_exit_code
