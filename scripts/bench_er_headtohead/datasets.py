#!/usr/bin/env python
"""Dataset loaders for the probabilistic accuracy panel.

Each loader returns (records, truth):
  records: polars.DataFrame with a 'record_id' column + matchable fields
  truth:   polars.DataFrame with columns {record_id, cluster_id}

historical_50k is Splink's home-turf biographical dataset (Wikidata historical
people, with a ground-truth cluster label). Loaded via splink_datasets when
splink is installed, else from a vendored parquet under the gitignored
tests/benchmarks/datasets/.
"""
from __future__ import annotations

import logging
import tempfile
from collections.abc import Hashable
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]
DATASETS_DIR = REPO / "packages" / "python" / "goldenmatch" / "tests" / "benchmarks" / "datasets"


class DatasetUnavailable(RuntimeError):
    """Raised when a dataset's data or its loader dependency is missing."""


def _cluster_ids_from_pairs(
    all_ids: list[Hashable], pairs: list[tuple[Hashable, Hashable]]
) -> dict[Hashable, int]:
    """Connected-components labelling of record ids from matching pairs.

    Uses goldenmatch's ``UnionFind`` (path compression + union by rank). Its
    internal dicts are keyed on whatever hashables we hand it, but the type
    hints say ``int`` and ``union`` ranks ints, so we map every id to a dense
    integer index, run UF over indices, then translate the components back to
    a ``{record_id: cluster_id}`` mapping. Singletons get their own cluster id.
    """
    from goldenmatch.core.cluster import UnionFind

    idx_of: dict[Hashable, int] = {}
    for rid in all_ids:
        if rid not in idx_of:
            idx_of[rid] = len(idx_of)

    uf = UnionFind()
    uf.add_many(list(idx_of.values()))
    for a, b in pairs:
        # Ids referenced only by the truth pairs (not in all_ids) still need a node.
        if a not in idx_of:
            idx_of[a] = len(idx_of)
            uf.add(idx_of[a])
        if b not in idx_of:
            idx_of[b] = len(idx_of)
            uf.add(idx_of[b])
        uf.union(idx_of[a], idx_of[b])

    idx_to_id = {idx: rid for rid, idx in idx_of.items()}
    cluster_id: dict[Hashable, int] = {}
    for component in uf.get_clusters():
        # Stable cluster id = smallest member index in the component.
        cid = min(component)
        for member_idx in component:
            cluster_id[idx_to_id[member_idx]] = cid
    return cluster_id


def _historical_50k() -> tuple[pl.DataFrame, pl.DataFrame]:
    df = None
    try:
        from splink import splink_datasets  # type: ignore
    except ImportError:
        splink_datasets = None  # type: ignore
    if splink_datasets is not None:
        try:
            df = pl.from_pandas(splink_datasets.historical_50k)  # type: ignore
        except Exception as e:  # splink present but dataset unusable -> try vendored
            logger.warning(
                "splink_datasets.historical_50k failed (%s); trying vendored parquet", e
            )
            df = None
    if df is None:
        vendored = DATASETS_DIR / "historical_50k.parquet"
        if not vendored.exists():
            raise DatasetUnavailable(
                "install `goldenmatch[bench]` (for splink_datasets) or vendor "
                f"{vendored}"
            )
        df = pl.read_parquet(vendored)

    # historical_50k columns: unique_id, cluster, first_name, surname, dob,
    # birth_place, postcode_fake, occupation, ...
    df = df.rename({"unique_id": "record_id", "cluster": "cluster_id"})
    truth = df.select(["record_id", "cluster_id"])
    records = df.drop("cluster_id")
    return records, truth


def _dblp_acm() -> tuple[pl.DataFrame, pl.DataFrame]:
    """Leipzig DBLP-ACM bibliographic ER (cross-source dedupe).

    Unions DBLP2.csv + ACM.csv into one records frame with source-prefixed
    record ids (``dblp:<id>`` / ``acm:<id>``), and derives cluster_id from the
    perfect-mapping pair list (idDBLP, idACM) via connected components.
    """
    base = DATASETS_DIR / "DBLP-ACM"
    dblp_path = base / "DBLP2.csv"
    acm_path = base / "ACM.csv"
    gt_path = base / "DBLP-ACM_perfectMapping.csv"
    missing = [p for p in (dblp_path, acm_path, gt_path) if not p.exists()]
    if missing:
        raise DatasetUnavailable(
            "DBLP-ACM source CSVs not found (vendor under "
            f"{base}); missing: {[p.name for p in missing]}"
        )

    # latin-1 / utf8-lossy: the Leipzig CSVs carry invalid UTF-8 bytes.
    dblp = pl.read_csv(dblp_path, encoding="utf8-lossy", infer_schema_length=0)
    acm = pl.read_csv(acm_path, encoding="utf8-lossy", infer_schema_length=0)

    def _prefix(df: pl.DataFrame, src: str) -> pl.DataFrame:
        return df.with_columns(
            (pl.lit(f"{src}:") + pl.col("id").cast(pl.Utf8)).alias("record_id")
        ).drop("id")

    records = pl.concat(
        [_prefix(dblp, "dblp"), _prefix(acm, "acm")], how="diagonal"
    )

    gt = pl.read_csv(gt_path, encoding="utf8-lossy", infer_schema_length=0)
    pairs: list[tuple[Hashable, Hashable]] = [
        (f"dblp:{row['idDBLP']}", f"acm:{row['idACM']}")
        for row in gt.iter_rows(named=True)
    ]

    all_ids = records["record_id"].to_list()
    cmap = _cluster_ids_from_pairs(all_ids, pairs)
    truth = pl.DataFrame(
        {
            "record_id": all_ids,
            "cluster_id": [cmap[r] for r in all_ids],
        }
    )
    return records, truth


def _febrl3() -> tuple[pl.DataFrame, pl.DataFrame]:
    """recordlinkage's Febrl3 synthetic person dataset (with duplicates).

    Records come from the dataframe (index reset into ``record_id``);
    cluster_id is derived from the ground-truth ``links`` MultiIndex via
    connected components (preferred over parsing the rec-id string, since the
    links are what the eval scores against).
    """
    try:
        from recordlinkage.datasets import load_febrl3  # type: ignore
    except ImportError as e:
        raise DatasetUnavailable(
            "recordlinkage not installed (pip install recordlinkage) for febrl3"
        ) from e

    df, links = load_febrl3(return_links=True)

    pdf = df.reset_index()
    # The reset index column is the febrl record id (e.g. 'rec-123-org').
    id_col = pdf.columns[0]
    records = pl.from_pandas(pdf).rename({id_col: "record_id"})
    records = records.with_columns(pl.col("record_id").cast(pl.Utf8))

    all_ids = records["record_id"].to_list()
    pairs: list[tuple[Hashable, Hashable]] = [
        (str(a), str(b)) for a, b in links
    ]
    cmap = _cluster_ids_from_pairs(all_ids, pairs)
    truth = pl.DataFrame(
        {
            "record_id": all_ids,
            "cluster_id": [cmap[r] for r in all_ids],
        }
    )
    return records, truth


def _ncvr() -> tuple[pl.DataFrame, pl.DataFrame]:
    """NC Voter Registration 10K sample.

    The raw NCVR sample is one row per voter (``ncid`` is unique across all
    10K rows) -- it carries NO true-entity grouping that would make every
    record a non-singleton cluster. Every other NCVR benchmark in this repo
    SYNTHESIZES corrupted duplicates at runtime and tracks ground-truth pairs
    itself; the file on disk cannot supply a ``cluster_id`` without guessing.
    Per the panel contract, refuse rather than fabricate an all-singletons
    (or wrongly-grouped) truth.
    """
    sample = DATASETS_DIR / "NCVR" / "ncvoter_sample_10k.txt"
    if not sample.exists():
        raise DatasetUnavailable(f"NCVR sample not found at {sample}")
    raise DatasetUnavailable(
        "NCVR raw sample has no true-entity grouping (ncid is unique per row); "
        "a meaningful cluster_id requires synthesized corrupted duplicates "
        "(see tests/benchmarks/run_ncvr_*.py), not the file alone. Refusing "
        "to fabricate truth. Provide a paired/corrupted NCVR variant to enable "
        "this adapter."
    )


def _synthetic_person() -> tuple[pl.DataFrame, pl.DataFrame]:
    """Synthetic person rows from the bench fixture generator.

    Reuses ``generate_fixture.generate`` (writes records + truth parquet to a
    temp dir), then reads both back into the (records, truth) contract shape.
    """
    try:
        from generate_fixture import generate  # type: ignore
    except ImportError:
        import importlib.util

        gen_path = Path(__file__).resolve().parent / "generate_fixture.py"
        if not gen_path.exists():
            raise DatasetUnavailable(f"generate_fixture.py not found at {gen_path}")
        spec = importlib.util.spec_from_file_location("generate_fixture", gen_path)
        if spec is None or spec.loader is None:
            raise DatasetUnavailable(f"could not load generate_fixture from {gen_path}")
        gen_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen_mod)
        generate = gen_mod.generate  # type: ignore

    with tempfile.TemporaryDirectory(prefix="gm_synth_person_") as td:
        out = Path(td) / "records.parquet"
        truth_path = Path(td) / "truth.parquet"
        generate(
            rows=5_000,
            dupe_rate=0.20,
            out=out,
            truth=truth_path,
            seed=42,
            batch=1_000_000,
        )
        records = pl.read_parquet(out)
        truth = pl.read_parquet(truth_path)
    return records, truth


_LOADERS = {
    "historical_50k": _historical_50k,
    "dblp_acm": _dblp_acm,
    "febrl3": _febrl3,
    "ncvr": _ncvr,
    "synthetic_person": _synthetic_person,
}


def load_dataset(name: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    if name not in _LOADERS:
        raise KeyError(f"unknown dataset {name!r}; have {sorted(_LOADERS)}")
    return _LOADERS[name]()
