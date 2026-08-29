"""North Star scoreboard — the falsifiable adoption metric the roadmap gates on.

`context-network/planning/north-star-roadmap.md` says the scoreboard must be
stood up FIRST, "without it 'de facto' is unfalsifiable". This records a dated
snapshot of the four proxies (weekly downloads, stars, forks, non-maintainer
inbound issues) to a time-series and regenerates a human-facing scoreboard with
WEEK-OVER-WEEK deltas — so we track a trend as a GOAL, not a rolling badge
snapshot.

Usage:
    python scripts/scoreboard.py            # fetch live, append a row, rewrite the doc
    python scripts/scoreboard.py --check     # CI: fail if the doc is stale vs the data
    python scripts/scoreboard.py --no-fetch  # rewrite the doc from existing data only

The weekly `.github/workflows/scoreboard.yml` runs the fetch form and commits.
Downloads are last-30-day sums (pypistats/npm); a throttled fetch records `null`
(the prior week carries forward in the trend, never a fake 0).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from suite_download_badges import (
    NPM_PACKAGES,
    PYPI_PACKAGES,
    _Throttled,
    humanize,
    npm_last_month,
    pypi_last_month,
)

_REPO = "benseverndev-oss/goldenmatch"
_ROOT = Path(__file__).resolve().parent.parent
_DATA = _ROOT / "context-network" / "planning" / "scoreboard.jsonl"
_DOC = _ROOT / "context-network" / "planning" / "scoreboard.md"
# Accounts that are the maintainer, not "someone who reached for it".
_MAINTAINERS = {"benzsevern", "benzsevern-mjh"}


def _gh(url: str) -> dict | list | None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {"User-Agent": "gm-scoreboard", "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError:
        return None


def _github_metrics() -> dict:
    repo = _gh(f"https://api.github.com/repos/{_REPO}") or {}
    # Open issues authored by non-maintainer, non-bot accounts (a raw proxy for
    # "a stranger reached for it" -- still needs human triage to exclude
    # badge-marketing bots; see the doc's caveat).
    search = _gh(
        f"https://api.github.com/search/issues?q=repo:{_REPO}+type:issue+state:open&per_page=100"
    )
    ext = 0
    if isinstance(search, dict):
        for it in search.get("items", []):
            login = (it.get("user") or {}).get("login", "")
            if login and login not in _MAINTAINERS and not login.endswith("[bot]"):
                ext += 1
    return {
        "stars": repo.get("stargazers_count"),
        "forks": repo.get("forks_count"),
        "open_issues_nonmaintainer": ext,
    }


def _download_total(pkgs: list[str], fetch) -> int | None:
    """Sum last-30-day downloads. Returns None if the API throttled us (so the
    prior week's number carries forward -- never a fabricated 0)."""
    total = 0
    try:
        for pkg in pkgs:
            total += fetch(pkg)
    except _Throttled:
        return None
    return total


def _load_ttfs(path: str | None) -> dict:
    """Read the time-to-first-success row produced by `scripts/ttfs_probe.py`.

    The probe runs as its OWN workflow step and writes JSON, rather than being
    called inline here: it drives a container for minutes, and a crash in it
    must not take down the cheap API snapshot alongside it. A missing or
    unreadable file is `ttfs_ok: None` -- "we did not measure", which the doc
    renders differently from "we measured and it failed".
    """
    import ttfs_probe

    if not path:
        return ttfs_probe.unavailable_row("probe not run")
    p = Path(path)
    if not p.exists():
        return ttfs_probe.unavailable_row(f"{p.name} not written by the probe step")
    try:
        row = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ttfs_probe.unavailable_row(f"unreadable probe output: {exc}")
    missing = set(ttfs_probe.ROW_KEYS) - set(row)
    if missing:
        return ttfs_probe.unavailable_row(f"probe output missing keys: {sorted(missing)}")
    return {k: row[k] for k in ttfs_probe.ROW_KEYS}


def _collect(ttfs_json: str | None = None) -> dict:
    row: dict = {"date": datetime.date.today().isoformat()}
    row.update(_github_metrics())
    row["pypi_30d"] = _download_total(PYPI_PACKAGES, pypi_last_month)
    row["npm_30d"] = _download_total(NPM_PACKAGES, npm_last_month)
    row.update(_load_ttfs(ttfs_json))
    return row


def _load_rows() -> list[dict]:
    if not _DATA.exists():
        return []
    return [
        json.loads(line) for line in _DATA.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _append(row: dict, rows: list[dict]) -> list[dict]:
    # One row per date: replace a same-date entry rather than duplicating.
    rows = [r for r in rows if r.get("date") != row["date"]]
    rows.append(row)
    rows.sort(key=lambda r: r["date"])
    _DATA.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return rows


def _delta(cur, prev) -> str:
    if cur is None:
        return "—"
    if prev is None:
        return humanize(cur) if not isinstance(cur, int) or cur >= 1000 else str(cur)
    d = cur - prev
    arrow = "▲" if d > 0 else ("▼" if d < 0 else "▬")
    return f"{cur} ({arrow}{'+' if d > 0 else ''}{d})"


def _ttfs_cell(row: dict) -> str:
    """One TTFS reading, rendered so the three states stay distinguishable.

    A failed probe must NOT look like an unmeasured one -- "—" for a bad first
    run would read as "we didn't check" and quietly retire the signal.
    """
    ok = row.get("ttfs_ok")
    if ok is None:
        return "—"
    if ok is False:
        return f"**FAILED** ({row.get('ttfs_fail') or 'unknown'})"
    total, f1 = row.get("ttfs_total_s"), row.get("ttfs_f1")
    if total is None:
        return "—"
    return f"{total:.1f}s · F1 {f1:.2f}" if f1 is not None else f"{total:.1f}s"


def _ttfs_delta(cur: dict, prev: dict) -> str:
    """Day-over-day on total seconds, but only between two SUCCESSFUL probes --
    differencing a success against a failure is a meaningless number."""
    if cur.get("ttfs_ok") is not True or prev.get("ttfs_ok") is not True:
        return "—"
    c, p = cur.get("ttfs_total_s"), prev.get("ttfs_total_s")
    if c is None or p is None:
        return "—"
    d = c - p
    arrow = "▲" if d > 0 else ("▼" if d < 0 else "▬")
    return f"{arrow}{'+' if d > 0 else ''}{d:.1f}s"


def _ttfs_history_cell(row: dict) -> str:
    ok = row.get("ttfs_ok")
    if ok is None:
        return "—"
    if ok is False:
        return f"fail:{row.get('ttfs_fail') or '?'}"
    total = row.get("ttfs_total_s")
    return f"{total:.1f}s" if total is not None else "—"


def _render(rows: list[dict]) -> str:
    if not rows:
        return "# North Star scoreboard\n\n_No data yet — run `python scripts/scoreboard.py`._\n"
    cur = rows[-1]
    prev = rows[-2] if len(rows) > 1 else {}

    def dl(v):
        return "—" if v is None else humanize(v)

    def dl_delta(key):
        c, p = cur.get(key), prev.get(key)
        if c is None:
            return "—"
        if p is None:
            return dl(c)
        d = c - p
        arrow = "▲" if d > 0 else ("▼" if d < 0 else "▬")
        return f"{dl(c)} ({arrow}{'+' if d > 0 else ''}{d})"

    lines = [
        "# North Star scoreboard",
        "",
        "**GENERATED — do not hand-edit.** `python scripts/scoreboard.py` (weekly via",
        "`.github/workflows/scoreboard.yml`). The falsifiable adoption metric behind",
        "[north-star-roadmap.md](./north-star-roadmap.md): *is GoldenMatch becoming the",
        "tool developers reach for by default?* Trend > snapshot.",
        "",
        f"**Latest: {cur['date']}** (vs previous snapshot)",
        "",
        "| Signal | Now | WoW | North Star reading |",
        "|---|---|---|---|",
        f"| GitHub stars | {cur.get('stars', '—')} | {_delta(cur.get('stars'), prev.get('stars'))} | discovery momentum |",
        f"| Forks | {cur.get('forks', '—')} | {_delta(cur.get('forks'), prev.get('forks'))} | intent-to-use |",
        f"| PyPI downloads (30d, suite) | {dl(cur.get('pypi_30d'))} | {dl_delta('pypi_30d')} | actual reach |",
        f"| npm downloads (30d, suite) | {dl(cur.get('npm_30d'))} | {dl_delta('npm_30d')} | actual reach (TS) |",
        f'| Open issues, non-maintainer | {cur.get("open_issues_nonmaintainer", "—")} | {_delta(cur.get("open_issues_nonmaintainer"), prev.get("open_issues_nonmaintainer"))} | "someone reached for it"† |',
        f"| Time-to-first-success | {_ttfs_cell(cur)} | {_ttfs_delta(cur, prev)} | zero-config friction‡ |",
        "",
        "† Raw count — still needs human triage to exclude badge-marketing bots",
        '(e.g. MCP-marketplace "live badge" issues). The roadmap\'s true gate is **≥1',
        "GENUINE inbound issue from a stranger**; a bot filing a promo badge does not count.",
        "",
        "‡ `pip install goldenmatch && goldenmatch dedupe customers.csv` in a clean",
        "container, from **PyPI** — so it tracks the last RELEASE, not `main`. Install",
        "and run are timed separately (`scripts/ttfs_probe.py`); the headline is their",
        "sum, and F1 is measured against a labelled fixture so a fast wrong answer",
        "cannot pass. **FAILED** means the probe ran and the product did not; `—` means",
        "the probe itself did not run. The two are never merged.",
        "",
        "## History",
        "",
        "| Date | Stars | Forks | PyPI 30d | npm 30d | Ext. issues | TTFS |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows[-16:][::-1]:
        lines.append(
            f"| {r['date']} | {r.get('stars', '—')} | {r.get('forks', '—')} | "
            f"{dl(r.get('pypi_30d'))} | {dl(r.get('npm_30d'))} | "
            f"{r.get('open_issues_nonmaintainer', '—')} | {_ttfs_history_cell(r)} |"
        )
    lines += [
        "",
        "## The gates (from the roadmap)",
        "",
        "- **Stars velocity + weekly downloads trend UP over a rolling 4-week window.**",
        "- **≥1 genuine inbound issue/PR from a stranger** (not a badge bot).",
        "- **Time-to-first-success trends DOWN**, and never records a FAILED probe on a",
        "  released version — a stranger's first run has to work before anything else",
        "  on this board can matter.",
        "",
        "_Classification: planning/active — regenerated by `scripts/scoreboard.py`._",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="fail if the doc is stale vs the data")
    ap.add_argument(
        "--no-fetch", action="store_true", help="rewrite the doc from existing data only"
    )
    ap.add_argument(
        "--ttfs-json",
        help="JSON row from scripts/ttfs_probe.py (omitted => ttfs_ok null, "
        "i.e. 'not measured', never 'failed')",
    )
    args = ap.parse_args()

    if args.check:
        rows = _load_rows()
        expected = _render(rows)
        actual = _DOC.read_text(encoding="utf-8") if _DOC.exists() else ""
        if expected != actual:
            print(
                f"{_DOC} is stale vs {_DATA}. Run: python scripts/scoreboard.py --no-fetch",
                flush=True,
            )
            return 1
        return 0

    rows = _load_rows()
    if not args.no_fetch:
        rows = _append(_collect(args.ttfs_json), rows)
    _DOC.write_text(_render(rows), encoding="utf-8")
    print(f"wrote {_DOC} ({len(rows)} snapshots)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
