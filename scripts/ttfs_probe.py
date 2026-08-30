#!/usr/bin/env python3
"""Time-to-first-success probe -- the North Star scoreboard's friction row.

`context-network/planning/north-star-roadmap.md` lists four adoption proxies.
Three (stars, downloads, inbound issues) measure whether anyone ARRIVED. This
one measures whether the surface area is in their way once they do: how long a
stranger takes to get from `pip install` to a CORRECT dedupe, on the path the
README actually advertises (README.md:283).

    pip install goldenmatch && goldenmatch dedupe customers.csv

Measured in a clean `python:3.12-slim` container with no pip cache and no repo
checkout, installing from **PyPI** -- the stranger's real path. That means the
number reflects the LAST RELEASE, not today's `main`. Deliberate: this is an
adoption metric, not a PR gate.

Install and run are timed SEPARATELY because they price different things.
Install time prices dependency weight (and moves with GitHub's network); run
time prices the engine. A single blended number hides both.

Usage:
    python scripts/ttfs_probe.py                  # run it, print the JSON row
    python scripts/ttfs_probe.py --out ttfs.json  # ...and save it

`scripts/scoreboard.py` calls `probe()` and merges the row into the nightly
snapshot. The pure halves (parsing, P/R/F1, row encoding) are unit-tested in
`scripts/test_ttfs_probe.py` -- no Docker needed.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import subprocess
import tempfile
import time
from itertools import combinations
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_DIR = _ROOT / "scripts" / "ttfs"
_INPUT_CSV = _FIXTURE_DIR / "customers.csv"
_GT_CSV = _FIXTURE_DIR / "ground_truth.csv"

_IMAGE = "python:3.12-slim"
_FLOOR = 0.90
# Generous but finite: a hung install must become a recorded failure, never a
# workflow that burns its whole budget and lands no data at all.
_INSTALL_TIMEOUT_S = 900
_RUN_TIMEOUT_S = 600
# A traceback tail has to fit. The old 400 was set when the note was a
# curiosity; it is the only diagnostic the nightly keeps, so size it for the
# job -- an unreadable note is why a FAILED row sat unexplained.
_NOTE_CHARS = 1500

# Every row shape carries every key -- the scoreboard renders them positionally,
# so a missing key would KeyError the nightly rather than degrade to "—".
ROW_KEYS = (
    "ttfs_install_s",
    "ttfs_run_s",
    "ttfs_total_s",
    "ttfs_f1",
    "ttfs_ok",
    "ttfs_fail",
    "ttfs_note",
)


# ---------------------------------------------------------------------------
# Pure: cluster output -> predicted pairs
# ---------------------------------------------------------------------------


def parse_clusters_csv(text: str) -> list[tuple[int, int]]:
    """Read `<run>_clusters.csv` into (cluster_id, row_id) tuples.

    The writer emits `__cluster_id__, __row_id__, __cluster_size__,
    __oversized__` (core/pipeline.py). Only the first two matter here.
    """
    if not text.strip():
        return []
    reader = csv.DictReader(io.StringIO(text))
    out: list[tuple[int, int]] = []
    for rec in reader:
        out.append((int(rec["__cluster_id__"]), int(rec["__row_id__"])))
    return out


def pairs_from_cluster_rows(rows: list[tuple[int, int]]) -> set[tuple[int, int]]:
    """Expand cluster membership to canonical (lo, hi) pairs.

    Singletons contribute nothing -- a cluster of one asserts no match, so it
    is neither a true nor a false positive.
    """
    members: dict[int, list[int]] = {}
    for cid, rid in rows:
        members.setdefault(cid, []).append(rid)
    out: set[tuple[int, int]] = set()
    for ids in members.values():
        if len(ids) < 2:
            continue
        for a, b in combinations(sorted(ids), 2):
            out.add((a, b))
    return out


def load_ground_truth(text: str) -> set[tuple[int, int]]:
    """Read the labelled duplicate pairs, keyed on INPUT ROW INDEX."""
    reader = csv.DictReader(io.StringIO(text))
    field_names = reader.fieldnames or []
    if "id_a" not in field_names or "id_b" not in field_names:
        raise ValueError(f"ground truth needs id_a and id_b columns, got {field_names}")
    out: set[tuple[int, int]] = set()
    for rec in reader:
        a, b = int(rec["id_a"]), int(rec["id_b"])
        out.add((a, b) if a < b else (b, a))
    return out


def check_row_ids(rows: list[tuple[int, int]], n_input_rows: int) -> None:
    """Assert `__row_id__` really is the input row index.

    The probe keys ground truth on input row position and trusts the cluster
    output to use the same numbering. If a pipeline change ever renumbers or
    filters rows, F1 would silently collapse and read as a QUALITY regression
    when it is actually an alignment bug. Fail loudly and by name instead.
    """
    for _cid, rid in rows:
        if rid < 0 or rid >= n_input_rows:
            raise ValueError(
                f"row_id {rid} outside input range [0, {n_input_rows}) -- the "
                "cluster output is no longer keyed on input row index, so the "
                "ground-truth alignment this probe assumes is broken"
            )


# ---------------------------------------------------------------------------
# Pure: scoring
# ---------------------------------------------------------------------------


def prf1(predicted: set[tuple[int, int]], truth: set[tuple[int, int]]) -> dict:
    """Pairwise precision / recall / F1.

    Both degenerate cases return 0.0 rather than raising or fabricating a 1.0:
    predicting nothing is not precision 1, and an empty label set cannot
    certify anything.
    """
    tp = len(predicted & truth)
    fp = len(predicted - truth)
    fn = len(truth - predicted)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


# ---------------------------------------------------------------------------
# Pure: the honest-failure row contract
# ---------------------------------------------------------------------------


def diagnostic_note(
    *,
    phase: str | None,
    install_log: str,
    run_log: str,
    run_rc: int | None = None,
    fallback: str = "",
) -> str | None:
    """Pick the log that explains THIS failure and return its tail.

    The probe used to record ``(stdout + stderr)[-400:]`` of the whole
    container. Two things made that useless. The halves are concatenated, not
    interleaved, so the tail was always the END OF STDERR -- and pip writes its
    "a new release of pip is available" notice there, after everything. And the
    dedupe ran under ``--quiet``, which suppressed the error message entirely.
    So a real failure ("Auto-config error: No module named 'polars'") was
    recorded as a pip upgrade notice, and the scoreboard could report THAT the
    first run broke but never WHY.

    Now each phase writes its own log inside the container and this picks the
    one that failed: install failures explain themselves from pip's log, run
    failures from the CLI's. Prefixed with the exit code when there is one,
    because "exited 1 silently" is itself the finding.
    """
    chosen = install_log if phase == "install" else run_log
    text = (chosen or "").strip()
    if not text:
        # An empty log IS informative -- say so rather than recording nothing.
        text = fallback.strip() or f"{phase or 'run'} produced no output"
    if run_rc is not None and phase != "install":
        text = f"[exit {run_rc}] {text}"
    return text[-_NOTE_CHARS:] or None


def build_row(
    *,
    install_s: float | None,
    run_s: float | None,
    f1: float | None,
    floor: float,
    fail: str | None = None,
    note: str | None = None,
) -> dict:
    """Encode one probe outcome.

    `fail` is passed for a mechanical failure (install / run). A measured F1
    below the floor is derived here rather than passed, so a bad result can
    never be recorded as a missing one -- the number and the timings both
    survive alongside the verdict.
    """
    if fail is None and f1 is not None and f1 < floor:
        fail = "f1_below_floor"
    total = (install_s + run_s) if (install_s is not None and run_s is not None) else None
    return {
        "ttfs_install_s": install_s,
        "ttfs_run_s": run_s,
        "ttfs_total_s": total,
        "ttfs_f1": f1,
        "ttfs_ok": fail is None,
        "ttfs_fail": fail,
        "ttfs_note": note,
    }


def unavailable_row(note: str) -> dict:
    """The probe itself could not run (no Docker, no image).

    Distinct from a failure: `ttfs_ok` is None, not False. Nothing was learned
    about the product, so nothing should be reported about it.
    """
    return {
        "ttfs_install_s": None,
        "ttfs_run_s": None,
        "ttfs_total_s": None,
        "ttfs_f1": None,
        "ttfs_ok": None,
        "ttfs_fail": None,
        "ttfs_note": note,
    }


# ---------------------------------------------------------------------------
# Impure: drive the container
# ---------------------------------------------------------------------------


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=60, check=True)
    except (subprocess.SubprocessError, OSError):
        return False
    return True


def _timed(cmd: list[str], timeout: int) -> tuple[float, int, str]:
    """Run a command, return (elapsed, returncode, combined output)."""
    start = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return time.monotonic() - start, 124, f"timed out after {timeout}s"
    return (time.monotonic() - start, proc.returncode, (proc.stdout or "") + (proc.stderr or ""))


def probe(*, image: str = _IMAGE, floor: float = _FLOOR, keep: bool = False) -> dict:
    """Run the full clean-container probe. Always returns a ROW_KEYS dict."""
    if not _INPUT_CSV.exists() or not _GT_CSV.exists():
        return unavailable_row(f"fixture missing under {_FIXTURE_DIR}")
    if not _docker_available():
        return unavailable_row("docker not available")

    n_input_rows = sum(1 for _ in _INPUT_CSV.open(encoding="utf-8")) - 1
    workdir = Path(tempfile.mkdtemp(prefix="ttfs-"))
    try:
        shutil.copy(_INPUT_CSV, workdir / "customers.csv")
        out_dir = workdir / "out"
        out_dir.mkdir()

        # Two `docker run`s against one named container would need a commit
        # between them; instead run one container and split the clock inside
        # it, writing the install duration to a file the host reads back.
        #
        # The clock is taken with `python`, not `date +%s.%N` piped through
        # `awk`. This is a python image, so the interpreter is the ONE tool
        # guaranteed present -- whereas GNU `date`'s %N and an `awk` binary are
        # both assumptions about the base image, and this whole path cannot be
        # exercised without a Docker daemon. Fewer unverifiable dependencies on
        # the branch that is hardest to test.
        now = "python -c 'import time; print(time.time())'"
        # Each phase writes its OWN log. Without that split the host can only
        # see one concatenated blob whose tail is pip's stderr, which is how a
        # broken first run got recorded as a pip upgrade notice.
        #
        # The dedupe deliberately does NOT pass --quiet: this probe exists to
        # find out why the first run fails, and --quiet suppresses the very
        # message that says so. Its output goes to a file, so the extra
        # verbosity costs nothing.
        script = (
            "set -e; "
            f"S=$({now}); "
            "pip install --quiet --no-cache-dir goldenmatch > /work/install_log 2>&1; "
            'python -c "import time,sys; '
            "open('/work/install_s','w').write('%.3f' % (time.time()-float(sys.argv[1])))\" \"$S\"; "
            "cd /work; "
            "set +e; "
            "goldenmatch dedupe customers.csv --output-clusters "
            "--output-dir /work/out --run-name ttfs > /work/run_log 2>&1; "
            # Keep the run's own status as the container's status -- writing
            # run_rc must not turn a failed dedupe into a successful script.
            "RC=$?; echo $RC > /work/run_rc; exit $RC"
        )
        cmd = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{workdir}:/work",
            "-w",
            "/work",
            image,
            # `sh -c`, not `bash -lc`: the script is POSIX, so it does not need
            # bash, and a login shell would source profile files for no reason.
            "sh",
            "-c",
            script,
        ]
        elapsed, code, output = _timed(cmd, _INSTALL_TIMEOUT_S + _RUN_TIMEOUT_S)

        def _read(name: str) -> str:
            f = workdir / name
            try:
                return f.read_text(encoding="utf-8", errors="replace") if f.exists() else ""
            except OSError:
                return ""

        install_file = workdir / "install_s"
        install_s = None
        if install_file.exists():
            try:
                install_s = round(float(install_file.read_text().strip()), 3)
            except ValueError:
                install_s = None

        install_log = _read("install_log")
        run_log = _read("run_log")
        run_rc = None
        rc_text = _read("run_rc").strip()
        if rc_text:
            try:
                run_rc = int(rc_text)
            except ValueError:
                run_rc = None

        if code != 0:
            # If the install duration never landed, pip is what failed.
            phase = "run" if install_s is not None else "install"
            run_s = (
                round(elapsed - install_s, 3)
                if (phase == "run" and install_s is not None)
                else None
            )
            return build_row(
                install_s=install_s if install_s is not None else round(elapsed, 3),
                run_s=run_s,
                f1=None,
                floor=floor,
                fail=phase,
                note=diagnostic_note(
                    phase=phase,
                    install_log=install_log,
                    run_log=run_log,
                    run_rc=run_rc if phase == "run" else None,
                    # `output` is the docker client's own stream: it explains a
                    # daemon-level failure, where neither in-container log exists.
                    fallback=output,
                ),
            )

        run_s = round(elapsed - install_s, 3) if install_s is not None else None

        clusters = next(out_dir.glob("*_clusters.csv"), None)
        if clusters is None:
            return build_row(
                install_s=install_s,
                run_s=run_s,
                f1=None,
                floor=floor,
                fail="run",
                note=diagnostic_note(
                    phase="run",
                    install_log=install_log,
                    run_log=run_log,
                    run_rc=run_rc,
                    fallback="run exited 0 but wrote no clusters file",
                ),
            )

        rows = parse_clusters_csv(clusters.read_text(encoding="utf-8"))
        check_row_ids(rows, n_input_rows)
        scores = prf1(
            pairs_from_cluster_rows(rows), load_ground_truth(_GT_CSV.read_text(encoding="utf-8"))
        )
        return build_row(install_s=install_s, run_s=run_s, f1=round(scores["f1"], 4), floor=floor)
    finally:
        if not keep:
            shutil.rmtree(workdir, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", help="write the JSON row here as well as stdout")
    ap.add_argument("--image", default=_IMAGE)
    ap.add_argument("--floor", type=float, default=_FLOOR)
    ap.add_argument("--keep", action="store_true", help="keep the temp workdir for debugging")
    args = ap.parse_args()

    row = probe(image=args.image, floor=args.floor, keep=args.keep)
    text = json.dumps(row, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    # Always exit 0: this is a measurement, not a gate. A red exit here would
    # suppress the nightly snapshot PR and throw away the rest of the data.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
