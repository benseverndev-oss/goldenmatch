"""Shared helpers for the per-module coverage BASELINE.

`check_coverage_floors.py` holds a curated table of absolute minimums for the
modules we have opinions about. It covered 38 of 436 measured modules -- the
other ~91% could regress to zero and only the global `fail_under` would notice,
and that has only a few points of slack to spend.

This is the other half: a GENERATED snapshot of every module's line rate, so any
module regressing below where it already is fails, whether or not anyone thought
to write a floor for it.

The two mechanisms answer different questions and both are worth having:

* floors    -- "this module MUST be at least X" (curated intent, absolute)
* baseline  -- "this module must not get WORSE than it is" (generated, relative)

Regenerate with `python scripts/regen_coverage_baseline.py <coverage.xml>`.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

BASELINE_PATH = Path(__file__).parent / "coverage_baseline.json"

# A module may drop this far below its baseline without failing. Coverage is not
# perfectly stable across shards (env-gated skips, native-vs-fallback lanes), and
# a gate that fires on noise gets muted, which is the failure mode that matters
# most here.
TOLERANCE_PP = 0.02

# Small modules need more slack in PERCENTAGE terms: on a 20-statement file one
# line is 5pp. Allow roughly one-and-a-half statements for small files, so the
# effective tolerance is the larger of the two.
SLACK_STATEMENTS = 1.5


def normalize(filename: str) -> str:
    """Coverage filenames differ by how the report was generated.

    CI runs from the repo root and emits ``goldenmatch/core/scorer.py``; a report
    generated inside the package directory (or through a ``[paths]`` remap) emits
    ``core/scorer.py``. Normalizing here means a baseline generated either way
    compares correctly, instead of every module reading as "new".
    """
    filename = filename.replace("\\", "/").lstrip("./")
    if not filename.startswith("goldenmatch/"):
        filename = "goldenmatch/" + filename
    return filename


def parse_report(xml_path: Path) -> dict[str, dict]:
    """Return ``{module: {"rate": float, "statements": int}}``."""
    root = ET.parse(xml_path).getroot()
    out: dict[str, dict] = {}
    for cls in root.iter("class"):
        name = normalize(cls.get("filename") or "")
        lines = cls.find("lines")
        n = len(lines.findall("line")) if lines is not None else 0
        out[name] = {"rate": round(float(cls.get("line-rate") or 0), 4), "statements": n}
    return out


def overall_rate(xml_path: Path) -> float:
    return float(ET.parse(xml_path).getroot().get("line-rate") or 0)


def tolerance_for(statements: int) -> float:
    """Allowed drop below baseline, as a fraction."""
    if statements <= 0:
        return TOLERANCE_PP
    return max(TOLERANCE_PP, SLACK_STATEMENTS / statements)


def load_baseline() -> dict:
    if not BASELINE_PATH.exists():
        raise SystemExit(
            f"{BASELINE_PATH.name} is missing. Generate it with:\n"
            f"  python scripts/regen_coverage_baseline.py <coverage.xml>"
        )
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
