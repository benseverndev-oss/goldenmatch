#!/usr/bin/env python3
"""Fetch the public benchmark datasets the goldenmatch benchmark tests read.

The datasets are deliberately NOT committed -- `.gitignore` carries
"Benchmark datasets downloaded at runtime (DBLP-ACM, etc.) -- not committed",
`ci.yml` declares the `benchmarks` lane as a skip needing
"DBLP-ACM/Febrl3/NCVR datasets (gitignored)", and real-benchmark runs live in
the separate `benchmarks.yml` workflow. This script is the "downloaded at
runtime" half that comment promised and nothing implemented: until now the
fetch was hand-inlined as a curl+unzip block in ten places across the bench
workflows (`bench-probabilistic.yml` alone repeats it nine times), and a
developer running `pytest -m benchmark` locally had no documented way to get
the data at all.

Usage
-----
    python scripts/fetch_benchmark_datasets.py              # all fetchable sets
    python scripts/fetch_benchmark_datasets.py DBLP-ACM     # just one
    python scripts/fetch_benchmark_datasets.py --list       # what is on disk
    python scripts/fetch_benchmark_datasets.py --force      # re-download

Exit codes: 0 all requested datasets present, 1 at least one failed, 2 usage.

Stdlib only, on purpose -- this has to run before the workspace is necessarily
installed, and in bench workflows that have not `pip install`ed anything yet.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

# Anchored to __file__, never to the CWD: local runs sit in the package
# directory and CI runs sit at the repo root, and a bare relative path silently
# resolves to a different place in each.
REPO_ROOT = Path(__file__).resolve().parent.parent
DATASETS_DIR = REPO_ROOT / "packages/python/goldenmatch/tests/benchmarks/datasets"

_LEIPZIG = "https://dbs.uni-leipzig.de/file"


@dataclass(frozen=True)
class Dataset:
    name: str
    url: str
    files: tuple[str, ...]
    #: Env var overriding `url` -- a mirror, or a local file:// copy on a box
    #: with no route to Leipzig. `GOLDENMATCH_DBLP_ACM_URL` already exists in
    #: bench-abbrevalign.yml; the others follow its naming.
    env: str
    note: str = ""


# File names are the archive's OWN names, verified against the published
# archives, and are what the tests/benchmarks already read -- including the
# upstream typo in `Amzon_GoogleProducts_perfectMapping.csv`. Do not "fix" it:
# run_amazon_google_bench.py reads that exact string.
FETCHABLE: tuple[Dataset, ...] = (
    Dataset(
        name="DBLP-ACM",
        url=f"{_LEIPZIG}/DBLP-ACM.zip",
        files=("DBLP2.csv", "ACM.csv", "DBLP-ACM_perfectMapping.csv"),
        env="GOLDENMATCH_DBLP_ACM_URL",
        note="bibliographic; test_autoconfig_benchmarks.py + run_leipzig.py",
    ),
    Dataset(
        name="Abt-Buy",
        url=f"{_LEIPZIG}/Abt-Buy.zip",
        files=("Abt.csv", "Buy.csv", "abt_buy_perfectMapping.csv"),
        env="GOLDENMATCH_ABT_BUY_URL",
        note="product (electronics); run_domain_bench.py",
    ),
    Dataset(
        name="Amazon-GoogleProducts",
        url=f"{_LEIPZIG}/Amazon-GoogleProducts.zip",
        files=("Amazon.csv", "GoogleProducts.csv",
               "Amzon_GoogleProducts_perfectMapping.csv"),
        env="GOLDENMATCH_AMAZON_GOOGLE_URL",
        note="product (software); run_amazon_google_bench.py",
    ),
)

#: Listed rather than omitted, so "why did my benchmark still skip?" has an
#: answer here instead of requiring a hunt through .gitignore.
UNFETCHABLE: dict[str, str] = {
    "NCVR": (
        "NC voter roll extract (ncvoter_sample_10k.txt). Not redistributable "
        "from a public URL -- obtain from the NC State Board of Elections and "
        "place the sample at datasets/NCVR/ncvoter_sample_10k.txt."
    ),
    "Febrl3": (
        "Generated, not downloaded -- produced by the Febrl data generator. "
        "See tests/benchmarks/run_leipzig.py for the expected layout."
    ),
}


@dataclass
class Result:
    name: str
    ok: bool
    detail: str
    missing: list[str] = field(default_factory=list)


def dataset_dir(ds: Dataset) -> Path:
    return DATASETS_DIR / ds.name


def present(ds: Dataset) -> list[str]:
    """Expected files that are NOT on disk (empty list == fully present)."""
    d = dataset_dir(ds)
    return [f for f in ds.files if not (d / f).is_file()]


def resolve_url(ds: Dataset) -> str:
    return os.environ.get(ds.env) or ds.url


def _download(url: str, dest: Path) -> None:
    """Fetch `url` to `dest`. Honours the standard proxy env vars.

    `urlopen` reads `https_proxy`/`no_proxy` through `getproxies()`, which is
    what the sandboxed agent proxy sets, so no explicit handler is needed.
    """
    with urllib.request.urlopen(url, timeout=120) as resp, dest.open("wb") as fh:
        shutil.copyfileobj(resp, fh)


def fetch(ds: Dataset, *, force: bool = False) -> Result:
    missing = present(ds)
    if not missing and not force:
        return Result(ds.name, True, f"already present in {dataset_dir(ds)}")

    url = resolve_url(ds)
    out = dataset_dir(ds)
    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / f"{ds.name}.zip"
        try:
            _download(url, archive)
        except (urllib.error.URLError, OSError) as exc:
            return Result(ds.name, False, f"download failed from {url}: {exc}", missing)

        extract = Path(tmp) / "x"
        try:
            with zipfile.ZipFile(archive) as zf:
                # Reject absolute / parent-traversing members before writing
                # anything: this unpacks a remote archive into the source tree.
                for member in zf.namelist():
                    target = (extract / member).resolve()
                    if not str(target).startswith(str(extract.resolve())):
                        return Result(ds.name, False,
                                      f"archive member escapes the extract dir: {member!r}",
                                      missing)
                zf.extractall(extract)
        except zipfile.BadZipFile as exc:
            return Result(ds.name, False, f"not a zip archive ({url}): {exc}", missing)

        # Search the whole tree rather than assuming a flat archive -- the
        # published layouts are flat today, but a mirror set via the env
        # override may nest them under a directory.
        still_missing = []
        for want in ds.files:
            found = next((p for p in extract.rglob(want) if p.is_file()), None)
            if found is None:
                still_missing.append(want)
            else:
                shutil.copyfile(found, out / want)

    if still_missing:
        return Result(ds.name, False,
                      f"archive from {url} is missing {still_missing}", still_missing)
    return Result(ds.name, True, f"downloaded to {out}")


def do_list() -> int:
    print(f"datasets dir: {DATASETS_DIR}\n")
    for ds in FETCHABLE:
        missing = present(ds)
        state = "OK      " if not missing else "MISSING "
        print(f"  {state}{ds.name:24} {ds.note}")
        if missing:
            print(f"           missing: {', '.join(missing)}")
    print("\n  not auto-fetchable:")
    for name, why in UNFETCHABLE.items():
        have = (DATASETS_DIR / name).is_dir()
        print(f"  {'OK      ' if have else 'ABSENT  '}{name:24} {why}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("datasets", nargs="*", metavar="NAME",
                    help=f"one or more of: {', '.join(d.name for d in FETCHABLE)} "
                         f"(default: all)")
    ap.add_argument("--list", action="store_true", help="show what is on disk and exit")
    ap.add_argument("--force", action="store_true", help="re-download even if present")
    args = ap.parse_args(argv)

    if args.list:
        return do_list()

    by_name = {d.name: d for d in FETCHABLE}
    if args.datasets:
        unknown = [n for n in args.datasets if n not in by_name]
        if unknown:
            for n in unknown:
                hint = UNFETCHABLE.get(n)
                print(f"error: {n!r} is not auto-fetchable."
                      + (f" {hint}" if hint else
                         f" Known: {', '.join(by_name)}"), file=sys.stderr)
            return 2
        wanted = [by_name[n] for n in args.datasets]
    else:
        wanted = list(FETCHABLE)

    results = [fetch(ds, force=args.force) for ds in wanted]
    for r in results:
        print(f"{'ok  ' if r.ok else 'FAIL'}  {r.name}: {r.detail}")

    failed = [r for r in results if not r.ok]
    if failed:
        print(f"\n{len(failed)}/{len(results)} dataset(s) unavailable. Benchmark tests "
              f"that need them will SKIP, not fail.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
