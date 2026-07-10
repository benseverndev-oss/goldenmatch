#!/usr/bin/env python
"""Frame-backend differential harness (Polars-eviction Wave 1, Task 6).

Runs the file-based ``run_dedupe`` entry point under BOTH the ``polars`` and
``arrow`` frame backends (``GOLDENMATCH_FRAME``) on two small, frozen,
deterministic fixture datasets, canonicalizes the output (clusters / golden
records / scored pairs), and diffs arrow-mode against polars-mode.

Why a subprocess per run: the frame backend is selected at import time via
``GOLDENMATCH_FRAME`` (read by ``core/frame.py::resolve_frame_backend`` at
call sites, not cached at process start, but running both backends in one
interpreter risks import-order / module-cache leakage between runs). A
subprocess per (dataset, backend) pair gives a clean, honestly-isolated
environment and lets us measure wall time + peak RSS independently.

Modes
-----
- (no flags): differential mode. Runs polars + arrow for both datasets in
  this run, diffs arrow vs polars, prints a report, exits 1 on any diff.
- ``--freeze DIR``: runs the POLARS backend only and writes the canonical
  JSON for each dataset to ``DIR/<dataset>.json``. Used once (and whenever
  the fixture datasets/config intentionally change) to (re)create the
  committed anchors under
  ``packages/python/goldenmatch/tests/fixtures/frame_diff/``.
- ``--single NAME``: internal. Runs ONE dataset under whatever
  ``GOLDENMATCH_FRAME`` is set in the current environment, prints the
  canonical JSON to stdout. This is the subprocess entry point; not meant
  to be invoked directly by a human.

Determinism contract (load-bearing): every matchkey is exact or weighted
(rapidfuzz scorers), blocking is ``static``, ``rerank=False`` everywhere,
GoldenCheck/GoldenFlow prep steps are explicitly disabled, and there is no
EM/probabilistic matchkey and no sample-based auto-config -- so re-running
the same dataset+backend must reproduce byte-identical canonical JSON. If
this harness ever proves flaky, that non-determinism is a real bug in a
"deterministic" config, not a fixture problem to paper over.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Make this script runnable from ANY checkout (CI clones fresh; a human runs
# it from the worktree root) by anchoring the package import path to the
# script's own location, not the caller's CWD or PYTHONPATH.
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parent.parent
_PKG_DIR = _REPO_ROOT / "packages" / "python" / "goldenmatch"
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

BACKENDS = ("polars", "arrow")

FIXTURES_DIR = _PKG_DIR / "tests" / "fixtures" / "frame_diff"


# ---------------------------------------------------------------------------
# Dataset (a): person-shaped, clean ASCII, planted duplicates. Surnames are
# deliberately spread across distinct soundex codes -- a documented repo
# lesson (feedback_synthetic_surname_fixtures): clustered surnames collapse
# blocking into one giant block and hang scoring, even at small N.
# ---------------------------------------------------------------------------


def _emit_group(
    rows: list[dict[str, str]],
    idx_holder: list[int],
    last: str,
    zip_: str,
    members: list[tuple[str, str]],
) -> None:
    """Append one identity group (2+ members that should cluster together,
    or a single unlinked record) as CSV-ready row dicts.

    ``members`` is a list of ``(first_name, external_id)``; ``external_id``
    may be ``""`` (not present in this row -- excluded from the exact
    matchkey, never a false "both blank" match).
    """
    for first, ext_id in members:
        rows.append(
            {
                "row_key": f"r{idx_holder[0]:03d}",
                "external_id": ext_id,
                "first_name": first,
                "last_name": last,
                "zip": zip_,
            }
        )
        idx_holder[0] += 1


def _rows_clean_person() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    idx = [0]

    # Pairs: (last_name, zip, [(first, ext_id), (first_typo, ext_id)]).
    # Surnames are spread across distinct soundex codes on purpose (see
    # module docstring) so blocking never collapses into one giant block.
    _emit_group(rows, idx, "Smith", "30301", [("Michael", "IDA1001"), ("Micheal", "IDA1001")])
    _emit_group(rows, idx, "Johnson", "60601", [("Patricia", ""), ("Patrica", "")])
    _emit_group(rows, idx, "Brown", "10001", [("Robert", "IDA1002"), ("Robrt", "IDA1002")])
    _emit_group(rows, idx, "Garcia", "85001", [("Maria", ""), ("Marya", "")])
    # Lee/Rhee: deliberately different soundex codes -- only the shared
    # external_id (exact matchkey) links this pair; fuzzy name+zip alone
    # would never compare them (different blocking key).
    _emit_group(rows, idx, "Lee", "94101", [("David", "IDA1003")])
    _emit_group(rows, idx, "Rhee", "94101", [("David", "IDA1003")])
    _emit_group(rows, idx, "Nguyen", "77001", [("Linda", ""), ("Lynda", "")])
    _emit_group(rows, idx, "Patel", "20001", [("James", ""), ("Jame", "")])
    _emit_group(rows, idx, "Kim", "33101", [("Susan", "IDA1004"), ("Susann", "IDA1004")])
    _emit_group(rows, idx, "Rossi", "02101", [("William", ""), ("Willam", "")])
    _emit_group(rows, idx, "Andersen", "55401", [("Karen", ""), ("Karn", "")])
    _emit_group(rows, idx, "Muller", "97201", [("Thomas", ""), ("Tomas", "")])
    _emit_group(rows, idx, "Fernandez", "78701", [("Nancy", ""), ("Nancey", "")])
    _emit_group(rows, idx, "Petrov", "19102", [("Daniel", ""), ("Danial", "")])

    # Triples: 3-member clusters.
    _emit_group(
        rows, idx, "Silva", "27601",
        [("Ana", ""), ("Anna", ""), ("Anah", "")],
    )
    _emit_group(
        rows, idx, "Yamamoto", "96813",
        [("Kenji", "IDA1006"), ("Kenjy", "IDA1006"), ("Kenji", "")],
    )

    # Singletons: unique surnames, no planted duplicate.
    for last, first, zip_ in [
        ("Haddad", "Youssef", "48201"),
        ("Kowalski", "Anna", "60007"),
        ("Nakamura", "Hiro", "94043"),
        ("Connor", "Sean", "02138"),
        ("Zimmerman", "Rachel", "10011"),
        ("Okafor", "Chidi", "30303"),
        ("Thompson", "Emily", "63101"),
    ]:
        _emit_group(rows, idx, last, zip_, [(first, "")])

    return rows


# ---------------------------------------------------------------------------
# Dataset (b): dirty variant. Accented names written to a latin-1 encoded
# file (NOT valid UTF-8 -- forces the cp1252-fallback decode path shared by
# both the polars ingest path and the pyarrow reader), plus leading-zero
# ZIP codes (the arrow-reader "dirty" corpus case).
# ---------------------------------------------------------------------------


def _rows_dirty_variant() -> list[dict[str, str]]:
    """Accented names (written latin-1, not valid UTF-8) + leading-zero ZIP
    codes -- exercises the arrow reader's cp1252-fallback + dirty-value
    paths end to end through the full dedupe pipeline, not just the reader
    in isolation."""
    rows: list[dict[str, str]] = []
    idx = [0]

    # Pairs: accented original vs an ASCII-folded "typo" -- same person, a
    # different source normalized the accents away. Some share an
    # external_id (possibly on a spelling divergent enough that only the
    # exact matchkey would catch it), most are fuzzy-only.
    _emit_group(rows, idx, "Garcia", "00501", [("José", "IDB2001"), ("Jose", "IDB2001")])
    _emit_group(rows, idx, "Muller", "01234", [("François", ""), ("Francois", "")])
    _emit_group(rows, idx, "Dubois", "00950", [("Renée", ""), ("Renee", "")])
    _emit_group(rows, idx, "Sorensen", "02134", [("Björn", "IDB2002"), ("Bjorn", "IDB2002")])
    _emit_group(rows, idx, "Petrova", "00601", [("Zoë", ""), ("Zoe", "")])
    # Different soundex on purpose (Fernandez vs Fernandes) -- only the
    # shared external_id links this pair.
    _emit_group(rows, idx, "Fernandez", "00602", [("Iñigo", "IDB2003")])
    _emit_group(rows, idx, "Fernandes", "00602", [("Inigo", "IDB2003")])
    _emit_group(rows, idx, "Silva", "01950", [("André", ""), ("Andre", "")])
    _emit_group(rows, idx, "Moreau", "02445", [("Céline", ""), ("Celine", "")])
    _emit_group(rows, idx, "Alvarez", "00925", [("Sofía", ""), ("Sofia", "")])
    _emit_group(rows, idx, "Backer", "01749", [("Günther", ""), ("Gunther", "")])

    # Triples.
    _emit_group(
        rows, idx, "Lovenskiold", "00603",
        [("Håkon", ""), ("Hakon", ""), ("Haakon", "")],
    )
    _emit_group(
        rows, idx, "Ostergaard", "01890",
        [("Åse", ""), ("Ase", ""), ("Aase", "")],
    )

    # Singletons.
    for last, first, zip_ in [
        ("Bernard", "Isabelle", "00680"),
        ("Schaefer", "Ludwig", "02455"),
        ("Almeida", "Beatriz", "00921"),
        ("Nystrom", "Ingrid", "01960"),
        ("Duarte", "Ricardo", "00791"),
        ("Weber", "Angelika", "02155"),
    ]:
        _emit_group(rows, idx, last, zip_, [(first, "")])

    return rows


DATASETS: dict[str, Any] = {
    "clean_person": {"rows": _rows_clean_person, "latin1": False},
    "dirty_variant": {"rows": _rows_dirty_variant, "latin1": True},
}

_COLUMNS = ["row_key", "external_id", "first_name", "last_name", "zip"]


def _write_dataset(name: str, out_dir: Path) -> Path:
    spec = DATASETS[name]
    rows: list[dict[str, str]] = spec["rows"]()
    buf = io.StringIO()
    buf.write(",".join(_COLUMNS) + "\n")
    for row in rows:
        buf.write(",".join(row[c] for c in _COLUMNS) + "\n")
    text = buf.getvalue()

    csv_path = out_dir / f"{name}.csv"
    if spec["latin1"]:
        csv_path.write_bytes(text.encode("latin-1"))
    else:
        csv_path.write_bytes(text.encode("utf-8"))
    return csv_path


# ---------------------------------------------------------------------------
# Config: exact + weighted matchkeys, static blocking, no EM/rerank.
# ---------------------------------------------------------------------------


def _build_config(out_dir: Path):
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenFieldRule,
        GoldenMatchConfig,
        GoldenRulesConfig,
        MatchkeyConfig,
        MatchkeyField,
        OutputConfig,
        QualityConfig,
        TransformConfig,
    )

    return GoldenMatchConfig(
        # Scope this harness to frame-backend (ingest) differences only --
        # explicitly disable the GoldenCheck/GoldenFlow prep steps so the
        # comparison isn't coupled to those packages' own behavior.
        quality=QualityConfig(mode="disabled"),
        transform=TransformConfig(mode="disabled"),
        matchkeys=[
            MatchkeyConfig(
                name="exact_id",
                type="exact",
                fields=[
                    MatchkeyField(column="external_id", transforms=["strip"]),
                ],
            ),
            MatchkeyConfig(
                name="fuzzy_name_zip",
                type="weighted",
                threshold=0.8,
                rerank=False,
                fields=[
                    MatchkeyField(
                        column="first_name",
                        scorer="jaro_winkler",
                        weight=0.35,
                        transforms=["lowercase", "strip"],
                    ),
                    MatchkeyField(
                        column="last_name",
                        scorer="jaro_winkler",
                        weight=0.45,
                        transforms=["lowercase", "strip"],
                    ),
                    MatchkeyField(
                        column="zip",
                        scorer="exact",
                        weight=0.20,
                        transforms=["strip"],
                    ),
                ],
            ),
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[
                BlockingKeyConfig(fields=["last_name"], transforms=["soundex"]),
            ],
        ),
        golden_rules=GoldenRulesConfig(
            default=GoldenFieldRule(strategy="most_complete"),
        ),
        output=OutputConfig(
            format="csv", directory=str(out_dir / "out"), run_name="diff"
        ),
    )


# ---------------------------------------------------------------------------
# Canonicalization
# ---------------------------------------------------------------------------


def canonicalize_result(result: dict, row_keys: list[str]) -> dict:
    """Turn a raw ``run_dedupe`` result dict into a JSON-safe, order-stable
    shape keyed on ``row_key`` (NOT the internal ``__row_id__``, which is
    just a positional index -- stable here only because both backends read
    the same CSV row-for-row, but ``row_key`` is the honest anchor)."""
    clusters_dict = result["clusters"]

    cid_to_sorted_keys: dict[int, list[str]] = {}
    multi_member: list[list[str]] = []
    for cid, info in clusters_dict.items():
        members = info["members"]
        if len(members) > 1:
            keys = sorted(row_keys[m] for m in members)
            cid_to_sorted_keys[cid] = keys
            multi_member.append(keys)
    multi_member.sort()

    golden: dict[str, dict[str, str | None]] = {}
    golden_df = result.get("golden")
    if golden_df is not None:
        user_cols = [
            c
            for c in golden_df.columns
            if not c.startswith("__") and c != "row_key"
        ]
        for row in golden_df.iter_rows(named=True):
            cid = row["__cluster_id__"]
            keys = cid_to_sorted_keys.get(cid)
            if keys is None:
                # Golden emitted a row for a cluster we don't consider
                # "multi-member" (shouldn't happen; be conservative and skip
                # rather than silently keying on __cluster_id__).
                continue
            golden_key = ",".join(keys)
            golden[golden_key] = {
                c: (None if row[c] is None else str(row[c])) for c in user_cols
            }

    pairs: list[list] = []
    for a, b, score in result.get("scored_pairs", []) or []:
        ka, kb = row_keys[a], row_keys[b]
        if ka > kb:
            ka, kb = kb, ka
        pairs.append([ka, kb, round(float(score), 12)])
    pairs.sort(key=lambda p: (p[0], p[1]))

    return {"clusters": multi_member, "golden": golden, "pairs": pairs}


def _row_keys_in_order(name: str) -> list[str]:
    """The row_key column IS the ingest order (single file, offset=0), so
    row_id == positional index into this list."""
    return [row["row_key"] for row in DATASETS[name]["rows"]()]


# ---------------------------------------------------------------------------
# Subprocess entry point (--single)
# ---------------------------------------------------------------------------


def run_single(name: str) -> dict:
    from goldenmatch.core.pipeline import run_dedupe

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        csv_path = _write_dataset(name, td_path)
        config = _build_config(td_path)
        result = run_dedupe(
            files=[(str(csv_path), "source_a")],
            config=config,
            output_golden=True,
            output_clusters=True,
        )
        row_keys = _row_keys_in_order(name)
        return canonicalize_result(result, row_keys)


# ---------------------------------------------------------------------------
# Orchestration (main process): spawn a subprocess per (dataset, backend)
# ---------------------------------------------------------------------------


def run_one(name: str, backend: str) -> tuple[dict, float, int]:
    """Run one dataset under one frame backend in a fresh subprocess.

    Returns (canonical_result, wall_seconds, peak_rss_bytes).
    """
    import psutil

    env = os.environ.copy()
    env["GOLDENMATCH_FRAME"] = backend
    env["GOLDENMATCH_NATIVE"] = "0"
    env["POLARS_SKIP_CPU_CHECK"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    cmd = [sys.executable, str(_THIS_FILE), "--single", name]
    start = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        encoding="utf-8",
    )
    peak_rss = 0
    try:
        ps_proc = psutil.Process(proc.pid)
    except psutil.Error:
        ps_proc = None

    def _sample_rss() -> int:
        # On this Windows venv, ``sys.executable`` is a tiny redirector stub
        # (~240 KB) that immediately re-execs the real interpreter as a
        # CHILD process under a DIFFERENT pid -- the parent's own RSS stays
        # flat at a few MiB for the whole run while all the real work (and
        # RSS growth) happens in the child. Sum the tracked process plus
        # every descendant so the redirector indirection doesn't make every
        # measurement read as "~4 MiB, always."
        if ps_proc is None:
            return 0
        total = 0
        try:
            total += ps_proc.memory_info().rss
            for child in ps_proc.children(recursive=True):
                try:
                    total += child.memory_info().rss
                except psutil.Error:
                    pass
        except psutil.Error:
            pass
        return total

    while proc.poll() is None:
        peak_rss = max(peak_rss, _sample_rss())
        time.sleep(0.005)

    stdout, stderr = proc.communicate()
    wall = time.perf_counter() - start

    if proc.returncode != 0:
        raise RuntimeError(
            f"diff_frame_backends subprocess failed "
            f"(dataset={name!r} backend={backend!r}, rc={proc.returncode}):\n"
            f"{stderr}"
        )

    canonical = json.loads(stdout)
    return canonical, wall, peak_rss


def _fmt_rss(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MiB"


def _diff_report(name: str, polars_canon: dict, arrow_canon: dict) -> list[str]:
    problems: list[str] = []
    if polars_canon["clusters"] != arrow_canon["clusters"]:
        problems.append(
            f"[{name}] clusters differ: polars={polars_canon['clusters']!r} "
            f"arrow={arrow_canon['clusters']!r}"
        )
    if polars_canon["golden"] != arrow_canon["golden"]:
        problems.append(
            f"[{name}] golden differs: polars={polars_canon['golden']!r} "
            f"arrow={arrow_canon['golden']!r}"
        )
    if polars_canon["pairs"] != arrow_canon["pairs"]:
        problems.append(
            f"[{name}] pairs differ: polars={polars_canon['pairs']!r} "
            f"arrow={arrow_canon['pairs']!r}"
        )
    return problems


def run_diff() -> int:
    problems: list[str] = []
    print("Frame-backend differential harness")
    print("=" * 70)
    for name in DATASETS:
        results: dict[str, dict] = {}
        for backend in BACKENDS:
            canon, wall, rss = run_one(name, backend)
            results[backend] = canon
            n_clusters = len(canon["clusters"])
            n_pairs = len(canon["pairs"])
            print(
                f"[{name}] backend={backend:<7} wall={wall:6.2f}s "
                f"peak_rss={_fmt_rss(rss):>10} "
                f"multi_member_clusters={n_clusters} pairs={n_pairs}"
            )
        dataset_problems = _diff_report(name, results["polars"], results["arrow"])
        if dataset_problems:
            problems.extend(dataset_problems)
            print(f"[{name}] DIFF FOUND ({len(dataset_problems)} mismatch(es))")
        else:
            print(f"[{name}] OK -- arrow matches polars")
    print("=" * 70)
    if problems:
        print(f"FAILED: {len(problems)} mismatch(es) across datasets")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("PASSED: arrow backend reproduces polars backend on every dataset")
    return 0


def freeze(dir_path: Path) -> int:
    dir_path.mkdir(parents=True, exist_ok=True)
    for name in DATASETS:
        canon, wall, rss = run_one(name, "polars")
        out_path = dir_path / f"{name}.json"
        out_path.write_text(
            json.dumps(canon, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"[{name}] froze polars-mode canonical JSON -> {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--freeze",
        metavar="DIR",
        nargs="?",
        const=str(FIXTURES_DIR),
        default=None,
        help=(
            "Write polars-mode canonical JSON fixtures to DIR and exit "
            f"(defaults to {FIXTURES_DIR} when passed with no value)."
        ),
    )
    parser.add_argument(
        "--single",
        metavar="NAME",
        help=argparse.SUPPRESS,  # internal subprocess entry point
    )
    args = parser.parse_args()

    if args.single:
        result = run_single(args.single)
        print(json.dumps(result))
        return 0

    if args.freeze:
        return freeze(Path(args.freeze))

    return run_diff()


if __name__ == "__main__":
    sys.exit(main())
