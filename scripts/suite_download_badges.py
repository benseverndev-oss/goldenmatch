"""Compute suite-wide PyPI + npm download counts and emit shields.io endpoint JSON.

Sums last-30-day downloads across every Golden Suite package and writes two
JSON files in shields.io endpoint format. A GitHub Action commits these to the
`badges` orphan branch; the README references them via
`img.shields.io/endpoint?url=...`.

Single source of truth for which packages count toward the suite totals --
update PYPI_PACKAGES / NPM_PACKAGES when adding a new package.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PYPI_PACKAGES = [
    "goldenmatch",
    "goldencheck",
    "goldenpipe",
    "goldenflow",
    "infermap",
    "goldencheck-types",
]
NPM_PACKAGES = [
    "goldenmatch",
    "goldencheck",
    "goldenflow",
    "infermap",
    "goldencheck-types",
]

# pypistats.org rate-limits unauthenticated callers; a short sleep between
# calls keeps us under the per-second cap so the workflow doesn't 429.
_PYPI_INTER_REQUEST_SLEEP_S = 1.5


class _Throttled(Exception):
    """Raised after retries exhausted on 429/5xx — caller decides what to do."""


def _fetch_json(url: str, *, retries: int = 4) -> dict | None:
    """Fetch JSON with backoff on 429 / 5xx. Returns None on 404."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "goldenmatch-badge-updater (+https://github.com/benzsevern/goldenmatch)"},
    )
    backoff_s = 2.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # Package not yet indexed (e.g. just published). Treat as 0.
                return None
            if e.code == 429 or 500 <= e.code < 600:
                if attempt == retries - 1:
                    raise _Throttled(f"{url} → HTTP {e.code} after {retries} tries") from e
                time.sleep(backoff_s)
                backoff_s *= 2
                continue
            raise
    return None  # unreachable, satisfies type checker


def pypi_last_month(pkg: str) -> int:
    data = _fetch_json(f"https://pypistats.org/api/packages/{pkg}/recent")
    # Space subsequent pypistats calls — the API throttles aggressively.
    time.sleep(_PYPI_INTER_REQUEST_SLEEP_S)
    if not data:
        return 0
    return int(data.get("data", {}).get("last_month", 0))


def npm_last_month(pkg: str) -> int:
    data = _fetch_json(f"https://api.npmjs.org/downloads/point/last-month/{pkg}")
    if not data:
        return 0
    return int(data.get("downloads", 0))


def humanize(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def shield(label: str, message: str, color: str, logo: str) -> dict:
    return {
        "schemaVersion": 1,
        "label": label,
        "message": f"{message}/month",
        "color": color,
        "namedLogo": logo,
        "logoColor": "white",
    }


def _safe_total(packages: list[str], fetcher, kind: str, prior_path: Path) -> tuple[int, bool]:
    """Sum downloads across packages with graceful degradation.

    Returns (total, used_fallback). If the upstream registry throttles or
    errors during the sum, falls back to the prior badge file's message and
    parses its numeric value out so the badge stays current-ish rather than
    breaking the run entirely.
    """
    try:
        return sum(fetcher(p) for p in packages), False
    except _Throttled as exc:
        print(f"warn: {kind} fetch throttled ({exc}); preserving prior badge value", file=sys.stderr)
        if not prior_path.exists():
            # First-ever run got throttled — emit 0 rather than crash.
            return 0, True
        try:
            prior = json.loads(prior_path.read_text())
            msg = str(prior.get("message", "0/month"))
            # Reverse humanize(): "1.2k/month" → 1200, "5/month" → 5, "1.0M/month" → 1_000_000
            stripped = msg.replace("/month", "").strip()
            if stripped.endswith("M"):
                return int(float(stripped[:-1]) * 1_000_000), True
            if stripped.endswith("k"):
                return int(float(stripped[:-1]) * 1_000), True
            return int(stripped), True
        except (ValueError, KeyError, json.JSONDecodeError):
            return 0, True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("badges"),
                    help="Output directory for JSON files")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    pypi_path = args.out / "pypi-downloads.json"
    npm_path = args.out / "npm-downloads.json"

    pypi_total, pypi_stale = _safe_total(PYPI_PACKAGES, pypi_last_month, "pypi", pypi_path)
    npm_total, npm_stale = _safe_total(NPM_PACKAGES, npm_last_month, "npm", npm_path)

    pypi_json = shield("pypi dl/mo", humanize(pypi_total), "d4a017", "pypi")
    npm_json = shield("npm dl/mo", humanize(npm_total), "cb3837", "npm")

    pypi_path.write_text(json.dumps(pypi_json, indent=2) + "\n")
    npm_path.write_text(json.dumps(npm_json, indent=2) + "\n")

    pypi_tag = " (stale, throttled)" if pypi_stale else ""
    npm_tag = " (stale, throttled)" if npm_stale else ""
    print(f"PyPI total (last 30d): {pypi_total} ({humanize(pypi_total)}){pypi_tag}")
    print(f"npm  total (last 30d): {npm_total} ({humanize(npm_total)}){npm_tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
