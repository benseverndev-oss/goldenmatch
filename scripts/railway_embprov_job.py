#!/usr/bin/env python3
"""Railway one-shot entrypoint for the embedding-provider comparison (#506).

Runs on Railway's box so the `vertex` arm and the `larger` synthetic dedupe run
off the dev machine. Reads its GCP creds from env (injected from Infisical):

    GCP_SA_KEY_B64     base64 of the Vertex service-account JSON key
    VERTEX_GCP_PROJECT GCP project for Vertex (e.g. golden-490919)
    EMB_DATASETS       default "febrl3,dblp-acm,synthetic"
    EMB_PROVIDERS      default "none,inhouse,vertex"
    EMB_EPOCHS         in-house training epochs (default 200)
    EMB_SYNTH_ROWS     synthetic dataset size (default 50000)

Writes the Vertex SA key to a file, sets GOOGLE_APPLICATION_CREDENTIALS, fetches
the Leipzig DBLP-ACM dataset, then shells the harness. Results go to stdout (the
Railway deploy logs).
"""
from __future__ import annotations

import base64
import io
import os
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

DATA = Path(os.environ.get("EMB_DATA_DIR", "/data"))
DSDIR = DATA / "bench-datasets"


def _setup_vertex_creds() -> bool:
    b64 = os.environ.get("GCP_SA_KEY_B64")
    if not b64:
        print("GCP_SA_KEY_B64 not set — vertex arm will be skipped", flush=True)
        return False
    keyfile = DATA / "vertex-sa.json"
    keyfile.write_bytes(base64.b64decode(b64))
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(keyfile)
    proj = os.environ.get("VERTEX_GCP_PROJECT", "")
    if proj:
        os.environ["GOOGLE_CLOUD_PROJECT"] = proj
    os.environ["GOLDENMATCH_GPU_MODE"] = "vertex"
    print(f"vertex creds ready (project={proj or '?'})", flush=True)
    return True


def _fetch_dblp_acm() -> None:
    out = DSDIR / "DBLP-ACM"
    if (out / "DBLP2.csv").exists():
        return
    out.mkdir(parents=True, exist_ok=True)
    print("fetching DBLP-ACM from Leipzig...", flush=True)
    raw = urllib.request.urlopen(
        "https://dbs.uni-leipzig.de/file/DBLP-ACM.zip", timeout=180
    ).read()
    zipfile.ZipFile(io.BytesIO(raw)).extractall(out)
    # The zip may nest files under a folder; flatten so DBLP2.csv sits in out/.
    if not (out / "DBLP2.csv").exists():
        for p in out.rglob("DBLP2.csv"):
            for f in p.parent.iterdir():
                f.rename(out / f.name)
            break


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    have_vertex = _setup_vertex_creds()

    providers = os.environ.get("EMB_PROVIDERS", "none,inhouse,vertex")
    if not have_vertex:
        providers = ",".join(p for p in providers.split(",") if p.strip() != "vertex")
    datasets = os.environ.get("EMB_DATASETS", "febrl3,dblp-acm,synthetic")

    if "dblp-acm" in datasets:
        _fetch_dblp_acm()

    cmd = [
        sys.executable, "scripts/bench_embedding_providers.py",
        "--datasets", datasets,
        "--providers", providers,
        "--datasets-dir", str(DSDIR),
        "--epochs", os.environ.get("EMB_EPOCHS", "200"),
        "--synthetic-rows", os.environ.get("EMB_SYNTH_ROWS", "50000"),
    ]
    print("running:", " ".join(cmd), flush=True)
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
