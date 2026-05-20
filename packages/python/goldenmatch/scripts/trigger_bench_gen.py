"""Laptop-side CLI for the goldenmatch bench-gen Railway service.

Kicks off a dataset generation on Railway, polls until done, downloads
the resulting parquet locally, and (optionally) uploads it as a
GitHub Release asset on ``bench-dataset-v1``.

Env:
    GOLDENMATCH_BENCH_JOB_URL    base URL of the Railway service
                                 (e.g. https://goldenmatch-bench-gen-production.up.railway.app)
    GOLDENMATCH_BENCH_JOB_TOKEN  bearer token (matches the env on the service)

Usage:
    python scripts/trigger_bench_gen.py --rows 50000000 --workers 16

    # Generate + upload to release in one shot:
    python scripts/trigger_bench_gen.py \\
        --rows 50000000 --workers 16 \\
        --upload-to-release bench-dataset-v1
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import urllib.request
import urllib.error
import json


def _env(name: str, required: bool = True) -> str:
    val = os.environ.get(name, "")
    if required and not val:
        print(f"ERROR: {name} not set", file=sys.stderr)
        sys.exit(2)
    return val


def _request(
    method: str,
    url: str,
    token: str,
    params: dict | None = None,
    stream_to: Path | None = None,
) -> dict | bytes:
    """Tiny stdlib-only HTTP client. Avoids dragging requests/httpx
    into the trigger script -- this is meant to be runnable from any
    fresh checkout without `uv sync`."""
    full = url
    if params:
        from urllib.parse import urlencode
        full = f"{url}?{urlencode(params)}"
    req = urllib.request.Request(full, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            if stream_to is not None:
                stream_to.parent.mkdir(parents=True, exist_ok=True)
                with stream_to.open("wb") as fh:
                    while True:
                        chunk = resp.read(1 << 20)  # 1 MiB
                        if not chunk:
                            break
                        fh.write(chunk)
                return b""
            body = resp.read()
            return json.loads(body.decode("utf-8")) if body else {}
    except urllib.error.HTTPError as e:
        print(f"ERROR: {method} {full}: HTTP {e.code} {e.read().decode('utf-8', errors='replace')}",
              file=sys.stderr)
        sys.exit(1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--download-to",
        type=Path,
        default=None,
        help="Local path to stream the result into. Defaults to "
             "bench-dataset-v1/bench_{rows}.parquet under cwd.",
    )
    ap.add_argument(
        "--upload-to-release",
        type=str,
        default=None,
        help="Release tag to gh-upload the resulting parquet to "
             "(e.g. bench-dataset-v1). Requires gh CLI authed.",
    )
    ap.add_argument(
        "--poll-interval-sec",
        type=int,
        default=30,
        help="How often to poll /status while the job runs.",
    )
    args = ap.parse_args()

    base = _env("GOLDENMATCH_BENCH_JOB_URL").rstrip("/")
    token = _env("GOLDENMATCH_BENCH_JOB_TOKEN")

    # 1. Kick off the job.
    print(f"POST {base}/generate rows={args.rows} workers={args.workers}", flush=True)
    resp = _request(
        "POST", f"{base}/generate", token,
        params={
            "rows": args.rows,
            "workers": args.workers,
            "seed": args.seed,
        },
    )
    assert isinstance(resp, dict)
    job_id = resp["job_id"]
    out_name = Path(resp["output"]).name
    print(f"queued job_id={job_id} output={out_name}", flush=True)

    # 2. Poll /status until completed/failed.
    t0 = time.time()
    while True:
        state = _request("GET", f"{base}/status", token)
        assert isinstance(state, dict)
        job = state.get("jobs", {}).get(job_id, {})
        status = job.get("status", "?")
        wall = int(time.time() - t0)
        print(f"  [{wall:>4}s] status={status}", flush=True)
        if status == "completed":
            print(
                f"  generation wall={job.get('wall_seconds')}s",
                flush=True,
            )
            break
        if status == "failed":
            print(f"ERROR: job failed: {job.get('error')}", file=sys.stderr)
            print(f"  log: GET {base}/logs?job_id={job_id}", file=sys.stderr)
            sys.exit(1)
        time.sleep(args.poll_interval_sec)

    # 3. Download.
    local = args.download_to or (
        Path("bench-dataset-v1") / f"bench_{args.rows}.parquet"
    )
    print(f"GET {base}/download?file={out_name} -> {local}", flush=True)
    _request(
        "GET", f"{base}/download", token,
        params={"file": out_name},
        stream_to=local,
    )
    size = local.stat().st_size
    print(f"  wrote {local} ({size / 1024 / 1024:.1f} MiB)", flush=True)

    # 4. Optional: upload to GitHub Release.
    if args.upload_to_release:
        tag = args.upload_to_release
        print(f"gh release upload {tag} {local}", flush=True)
        # `gh release upload` overwrites an existing asset of the
        # same name with --clobber. Without it the second invocation
        # for the same row count fails.
        proc = subprocess.run(
            ["gh", "release", "upload", tag, str(local), "--clobber"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode != 0:
            print(f"ERROR: gh upload failed: {proc.stderr}", file=sys.stderr)
            return 1
        print(f"  uploaded as asset on release {tag}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
