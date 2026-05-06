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


def _fetch_json(url: str) -> dict | None:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "goldenmatch-badge-updater (+https://github.com/benzsevern/goldenmatch)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        # 404 = package not yet indexed (e.g. just published). Treat as 0.
        if e.code == 404:
            return None
        raise


def pypi_last_month(pkg: str) -> int:
    data = _fetch_json(f"https://pypistats.org/api/packages/{pkg}/recent")
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("badges"),
                    help="Output directory for JSON files")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    pypi_total = sum(pypi_last_month(p) for p in PYPI_PACKAGES)
    npm_total = sum(npm_last_month(p) for p in NPM_PACKAGES)

    pypi_json = shield("pypi dl/mo", humanize(pypi_total), "d4a017", "pypi")
    npm_json = shield("npm dl/mo", humanize(npm_total), "cb3837", "npm")

    (args.out / "pypi-downloads.json").write_text(json.dumps(pypi_json, indent=2) + "\n")
    (args.out / "npm-downloads.json").write_text(json.dumps(npm_json, indent=2) + "\n")

    print(f"PyPI total (last 30d): {pypi_total} ({humanize(pypi_total)})")
    print(f"npm  total (last 30d): {npm_total} ({humanize(npm_total)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
