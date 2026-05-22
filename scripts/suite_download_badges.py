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
        headers={"User-Agent": "goldenmatch-badge-updater (+https://github.com/benseverndev-oss/goldenmatch)"},
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


def _parse_prior(prior_path: Path) -> int | None:
    """Reverse humanize() on a prior badge JSON file. Returns None when
    the file is absent / malformed / explicitly 0 (so callers can decide
    whether 0 is real or a stuck fallback)."""
    if not prior_path.exists():
        return None
    try:
        prior = json.loads(prior_path.read_text())
        msg = str(prior.get("message", "")).replace("/month", "").strip()
        if not msg:
            return None
        if msg.endswith("M"):
            return int(float(msg[:-1]) * 1_000_000)
        if msg.endswith("k"):
            return int(float(msg[:-1]) * 1_000)
        value = int(msg)
        # A prior value of 0 likely means a previous run got stuck on the
        # all-throttled fallback path. Don't propagate it forward — return
        # None so we fall through to "best effort partial sum".
        return value if value > 0 else None
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def _safe_total(packages: list[str], fetcher, kind: str, prior_path: Path) -> tuple[int, bool]:
    """Sum downloads across packages with per-package graceful degradation.

    Returns (total, used_fallback). Previous behaviour was all-or-nothing:
    if any package's fetch threw _Throttled the whole sum was abandoned in
    favour of the prior badge value. That's brittle — one throttled package
    out of six shouldn't zero the suite total. Now each package tries
    independently; throttled packages contribute their prior-individual
    share (or 0 if we have no per-package prior).
    """
    prior_total = _parse_prior(prior_path)

    total = 0
    any_throttled = False
    for pkg in packages:
        try:
            total += fetcher(pkg)
        except _Throttled as exc:
            print(
                f"warn: {kind}:{pkg} fetch throttled ({exc}); contributing 0",
                file=sys.stderr,
            )
            any_throttled = True
            continue

    # If everything throttled and we have a sane prior, preserve it.
    if total == 0 and any_throttled and prior_total is not None:
        print(
            f"warn: {kind} all-throttled; preserving prior total {prior_total}",
            file=sys.stderr,
        )
        return prior_total, True

    return total, any_throttled


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
