"""FastAPI control plane for generating goldenmatch bench datasets on Railway.

Modeled on the shellnet-job pattern at
``goldenmatch-shell-company-network/src/shellnet/job_server.py``: a small
FastAPI app that exposes a few authenticated endpoints, runs the heavy
compute as a background task, and persists state + result files to a
volume mount at ``/data`` so a redeploy doesn't lose progress or the
generated parquet.

Why a dedicated Railway service: at 50M rows the dataset generator
takes ~3-5 minutes on a 16-core box with the vectorized + threaded
implementation. Generating it inside the bench job itself would blow
the 60-min job cap. Generating it on a developer laptop OOMs (the
intermediate Polars frame is ~3-5 GB resident). Railway gives us a
beefy box and a volume mount; we drop the resulting parquet there and
either upload to a GitHub Release asset or stream it back via
``/download``.

Endpoints (all require Bearer token via ``GOLDENMATCH_BENCH_JOB_TOKEN``):

- ``GET  /healthz`` -> ``{ok: true}`` (no auth -- Railway healthcheck)
- ``POST /generate?rows=N&workers=W&seed=S`` -> kicks off generation
- ``GET  /status`` -> per-job dict ({status, started_at, finished_at, ...})
- ``GET  /download?file=NAME`` -> streams the file from ``/data``
- ``GET  /list`` -> ls of ``/data`` so caller can see what's available

The same pattern can host future bench generators (chain dataset for
Phase 5.5, identity bench fixtures, etc.) -- ``_ALLOWED_SCRIPTS`` is
the extension point.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    status,
)
from fastapi.responses import FileResponse, JSONResponse

log = logging.getLogger("goldenmatch.bench-gen")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

DATA_DIR = Path(os.environ.get("GOLDENMATCH_BENCH_DATA_DIR", "/data"))
STATE_PATH = DATA_DIR / "state.json"
LOGS_DIR = DATA_DIR / "logs"


# Scripts the operator can trigger. Each entry maps a short job name
# to the script path + arg template; the dispatch endpoint fills in
# the per-call args (rows, workers, seed, output filename).
_ALLOWED_JOBS = {
    "phase5_dataset": "scripts/generate_phase5_dataset.py",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("state.json corrupt; resetting")
    return {"jobs": {}}


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _mark(job_id: str, **fields: Any) -> dict[str, Any]:
    state = _load_state()
    state["jobs"].setdefault(job_id, {})
    state["jobs"][job_id].update(fields)
    state["updated_at"] = _now()
    _save_state(state)
    return state["jobs"][job_id]


def _auth(authorization: str | None = Header(default=None)) -> None:
    expected = os.environ.get("GOLDENMATCH_BENCH_JOB_TOKEN")
    if not expected:
        raise HTTPException(500, "GOLDENMATCH_BENCH_JOB_TOKEN not configured on server")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    if authorization.removeprefix("Bearer ").strip() != expected:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")


_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _safe_child(base: Path, name: str) -> Path:
    """Resolve ``name`` strictly inside ``base`` and return the resolved path.

    Caller-supplied filenames (``?file=``, ``?job_id=``, ``output=``) must not
    escape ``DATA_DIR``. Two layers:

    1. An allowlist regexp barrier: ``name`` must be a single segment of safe
       characters with no parent ref. This rejects separators (``/`` and the
       Windows ``\\``), leading dots, and ``..`` before the value is ever used
       to build a path.
    2. A realpath containment check: ``os.path.realpath`` collapses any symlink,
       so a symlinked entry inside ``base`` that points elsewhere is caught.

    Raises ``HTTPException(400)`` on any traversal attempt.
    """
    if not _SAFE_NAME_RE.fullmatch(name) or ".." in name:
        raise HTTPException(400, "name must be a simple filename (no path)")
    base_real = os.path.realpath(base)
    candidate = os.path.realpath(os.path.join(base_real, name))
    if candidate != base_real and not candidate.startswith(base_real + os.sep):
        raise HTTPException(400, "resolved path escapes the data directory")
    return Path(candidate)


def _run_subprocess(job_id: str, cmd: list[str]) -> None:
    """Run ``cmd`` synchronously inside a background task; mark state."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{job_id}.log"
    started = _now()
    _mark(
        job_id,
        status="running",
        started_at=started,
        log=str(log_path),
        cmd=cmd,
    )
    log.info("[%s] $ %s", job_id, " ".join(cmd))
    t0 = time.perf_counter()
    try:
        with log_path.open("wb") as fh:
            proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT)
        wall = time.perf_counter() - t0
        if proc.returncode != 0:
            _mark(
                job_id,
                status="failed",
                finished_at=_now(),
                wall_seconds=round(wall, 2),
                returncode=proc.returncode,
                error=f"exit {proc.returncode}",
            )
            log.error("[%s] FAILED exit=%d wall=%.1fs", job_id, proc.returncode, wall)
            return
        _mark(
            job_id,
            status="completed",
            finished_at=_now(),
            wall_seconds=round(wall, 2),
            returncode=0,
        )
        log.info("[%s] OK wall=%.1fs", job_id, wall)
    except Exception as e:  # pragma: no cover - defensive
        _mark(
            job_id,
            status="failed",
            finished_at=_now(),
            wall_seconds=round(time.perf_counter() - t0, 2),
            error=str(e),
        )
        log.exception("[%s] crashed", job_id)


app = FastAPI(title="goldenmatch bench-gen job server")


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "data_dir": str(DATA_DIR), "data_dir_exists": DATA_DIR.exists()}


@app.get("/status", dependencies=[Depends(_auth)])
def status_endpoint() -> dict[str, Any]:
    return _load_state()


@app.post("/generate", dependencies=[Depends(_auth)])
def trigger_generate(
    bg: BackgroundTasks,
    rows: int,
    workers: int = 8,
    seed: int = 42,
    output: str | None = None,
    job: str = "phase5_dataset",
) -> dict[str, Any]:
    """Kick off a dataset generation job in a background task.

    The output filename defaults to ``bench_{rows}.parquet`` under
    ``DATA_DIR`` so the operator can retrieve it via ``/download?file=...``.
    """
    if job not in _ALLOWED_JOBS:
        raise HTTPException(400, f"job must be one of {sorted(_ALLOWED_JOBS)}")
    if rows <= 0:
        raise HTTPException(400, "rows must be positive")
    if workers <= 0:
        raise HTTPException(400, "workers must be positive")

    out_name = output or f"bench_{rows}.parquet"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _safe_child(DATA_DIR, out_name)

    job_id = f"{job}-{rows}-{int(time.time())}"
    cmd = [
        "python", _ALLOWED_JOBS[job],
        "--rows", str(rows),
        "--workers", str(workers),
        "--seed", str(seed),
        "--output", str(out_path),
    ]
    bg.add_task(_run_subprocess, job_id, cmd)
    return {
        "ok": True,
        "job_id": job_id,
        "output": str(out_path),
        "cmd": cmd,
    }


@app.get("/download", dependencies=[Depends(_auth)])
def download(file: str) -> FileResponse:
    """Stream a file from ``DATA_DIR`` by exact name.

    The path handed to ``FileResponse`` comes from a directory listing, never
    from the request value: we validate ``file`` is a simple name, then look
    for a real entry whose ``.name`` matches. A traversal string therefore
    can't reach the filesystem sink (the served path is `entry`, not a path
    built from user input)."""
    if not _SAFE_NAME_RE.fullmatch(file) or ".." in file:
        raise HTTPException(400, "file must be a simple name (no path)")
    listing = DATA_DIR.iterdir() if DATA_DIR.exists() else []
    for entry in listing:
        if entry.is_file() and entry.name == file:
            return FileResponse(
                str(entry),
                media_type="application/octet-stream",
                filename=entry.name,
            )
    raise HTTPException(404, f"{file} not found")


@app.get("/list", dependencies=[Depends(_auth)])
def list_files() -> JSONResponse:
    """List files in ``DATA_DIR`` with size + mtime so the operator
    can see which datasets are available."""
    if not DATA_DIR.exists():
        return JSONResponse({"files": [], "data_dir": str(DATA_DIR)})
    entries = []
    for p in sorted(DATA_DIR.iterdir()):
        if p.is_file():
            stat = p.stat()
            entries.append({
                "name": p.name,
                "size_bytes": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            })
    return JSONResponse({"files": entries, "data_dir": str(DATA_DIR)})


@app.get("/logs", dependencies=[Depends(_auth)])
def get_log(job_id: str) -> FileResponse:
    """Stream a job's log file by exact name from a directory listing
    (same no-user-derived-path-at-the-sink pattern as ``/download``)."""
    if not _SAFE_NAME_RE.fullmatch(job_id) or ".." in job_id:
        raise HTTPException(400, "job_id must be a simple identifier")
    target = f"{job_id}.log"
    listing = LOGS_DIR.iterdir() if LOGS_DIR.exists() else []
    for entry in listing:
        if entry.is_file() and entry.name == target:
            return FileResponse(str(entry), media_type="text/plain", filename=entry.name)
    raise HTTPException(404, f"log for {job_id} not found")
