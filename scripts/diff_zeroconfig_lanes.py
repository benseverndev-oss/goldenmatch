"""Arrow-lane vs classic-polars-lane equivalence for ZERO-CONFIG runs.

`_frame_lane_eligible` declined the arrow lane whenever `config._preflight_report`
was set -- which auto_configure_df ALWAYS sets. Zero-config, the one path with no
polars to fall back on, was therefore the one path pinned to the polars lane.

The decline dated from when postflight's signals read a polars frame directly;
they route through `to_frame` now (`autoconfig_verify._signal_blocking_recall`,
`_signal_block_size_percentiles`), so the decline outlived its cause. This script
is the evidence for removing it: run each fixture shape ZERO-CONFIG down both
lanes and compare the written outputs byte-for-byte.

    python scripts/diff_zeroconfig_lanes.py

Exits non-zero on any divergence. Requires polars (it compares AGAINST the polars
lane); the point of the change is that the arrow lane no longer needs it.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


def _fixtures(root: Path) -> dict[str, list[tuple[str, str]]]:
    """Dataset shapes that exercise different auto-config decisions."""
    out: dict[str, list[tuple[str, str]]] = {}

    def _w(name: str, header: str, rows: list[str]) -> Path:
        p = root / name
        p.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
        return p

    # 1. fuzzy-only: no exact-eligible identifier survives the guards
    rows = []
    for i in range(240):
        f = ["ann", "ann", "bob", "bobby", "cara", "dan"][i % 6]
        last = ["smith", "smith", "jones", "jones", "lee", "poe"][i % 6]
        rows.append(f"{f},{last},{f}.{last}@x.com,{10000 + (i % 30)}")
    out["fuzzy_only"] = [(str(_w("fuzzy.csv", "first,last,email,zip", rows)), "a")]

    # 2. exact-eligible identifier present (drives a different matchkey set)
    rows = []
    for i in range(300):
        f = ["ann", "bob", "cara", "dan", "eve"][i % 5]
        last = ["smith", "jones", "lee", "poe", "ray"][(i // 5) % 5]
        rows.append(f"ACC{i % 150:04d},{f},{last},{10000 + (i % 40)}")
    out["exact_ident"] = [(str(_w("exact.csv", "account_no,first,last,zip", rows)), "a")]

    # 3. nulls + empty strings (reader null-spelling divergence bait)
    rows = []
    for i in range(200):
        f = ["ann", "bob", "cara", ""][i % 4]
        last = ["smith", "", "lee", "poe"][i % 4]
        mid = "" if i % 3 else "q"
        rows.append(f"{f},{last},{mid},{10000 + (i % 25)}")
    out["sparse_nulls"] = [(str(_w("sparse.csv", "first,last,middle,zip", rows)), "a")]

    # 4. dates + numerics (dtype-spelling contract at the classify boundary)
    rows = []
    for i in range(200):
        f = ["ann", "ann", "bob", "bobby"][i % 4]
        last = ["smith", "smith", "jones", "jones"][i % 4]
        rows.append(f"{f},{last},19{60 + (i % 40)}-0{1 + (i % 9)}-1{i % 9},{i % 97}")
    out["dates_numeric"] = [(str(_w("dates.csv", "first,last,dob,score", rows)), "a")]

    # 5. two files, shared schema (the multi-file concat + __source__ path).
    #    NOTE disjoint column sets across files are NOT covered: run_dedupe's
    #    arrow ingest calls concat_frames() without relaxed=True, so a schema
    #    mismatch raises ArrowInvalid before the lane choice is even made. That
    #    is a pre-existing bug on BOTH lanes, independent of this comparison.
    rows_a = [f"{['ann','bob','cara'][i%3]},{['smith','jones','lee'][i%3]},{10000+i%20}"
              for i in range(150)]
    rows_b = [f"{['ann','bob','dan'][i%3]},{['smith','jones','poe'][i%3]},{10000+i%35}"
              for i in range(150)]
    out["two_files"] = [
        (str(_w("m_a.csv", "first,last,zip", rows_a)), "a"),
        (str(_w("m_b.csv", "first,last,zip", rows_b)), "b"),
    ]
    return out


def _canonical_lineage(raw: bytes) -> bytes:
    """Lineage normalised for comparison.

    Two fields are expected to differ and are NOT part of the equivalence
    contract:

      * ``generated_at`` -- wall-clock, different on any two runs.
      * the ORDER of ``pairs`` -- the two lanes emit the same pair set in a
        different sequence (measured: same length, same members, same
        per-pair content on all fixtures below). Cluster membership, which is
        what the pairs feed, is byte-identical in ``*_clusters.csv``.

    Everything else -- pair contents, counts, and all lineage metadata -- must
    match exactly, so sorting rather than dropping keeps them under test.
    """
    doc = json.loads(raw)
    doc.pop("generated_at", None)
    if isinstance(doc.get("pairs"), list):
        doc["pairs"] = sorted(
            doc["pairs"], key=lambda p: json.dumps(p, sort_keys=True)
        )
    return json.dumps(doc, sort_keys=True).encode()


def _hash_outputs(directory: Path) -> dict[str, str]:
    got: dict[str, str] = {}
    for f in sorted(os.listdir(directory)):
        p = directory / f
        if not p.is_file():
            continue
        raw = p.read_bytes()
        if p.name.endswith("_lineage.json"):
            raw = _canonical_lineage(raw)
        got[f] = hashlib.sha256(raw).hexdigest()[:16]
    return got


def _run(files, force_arrow: bool, outdir: Path) -> dict[str, str]:
    import goldenmatch.core.pipeline as pipeline
    from goldenmatch.core.autoconfig import auto_configure

    shutil.rmtree(outdir, ignore_errors=True)
    outdir.mkdir(parents=True, exist_ok=True)

    original = pipeline.__dict__.setdefault("_orig_fle", pipeline._frame_lane_eligible)
    pipeline._frame_lane_eligible = (
        (lambda config, matchkeys, *, writes_outputs: True) if force_arrow else original
    )
    try:
        cfg = auto_configure(files)
        cfg.output.directory = str(outdir)
        cfg.output.run_name = "r"
        cfg.output.format = "csv"
        pipeline.run_dedupe(
            files=files, config=cfg,
            output_clusters=True, output_golden=True, output_dupes=True,
            output_unique=True, output_report=False,
        )
    finally:
        pipeline._frame_lane_eligible = original
    return _hash_outputs(outdir)


def main() -> int:
    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    failures = 0
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for name, files in _fixtures(root).items():
            arrow = _run(files, True, root / f"{name}_arrow")
            classic = _run(files, False, root / f"{name}_classic")
            same = arrow == classic
            print(f"{'OK  ' if same else 'DIFF'}  {name:20s} files={len(arrow)}")
            if not same:
                failures += 1
                for k in sorted(set(arrow) | set(classic)):
                    if arrow.get(k) != classic.get(k):
                        print(f"        {k}: arrow={arrow.get(k)} classic={classic.get(k)}")
    print("\nRESULT:", "ALL EQUIVALENT" if not failures else f"{failures} DIVERGENT")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
