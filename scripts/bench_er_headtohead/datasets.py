#!/usr/bin/env python
"""Dataset loaders for the probabilistic accuracy panel.

Each loader returns (records, truth):
  records: pyarrow.Table with a 'record_id' column + matchable fields
  truth:   pyarrow.Table with columns {record_id, cluster_id}

historical_50k is Splink's home-turf biographical dataset (Wikidata historical
people, with a ground-truth cluster label). Loaded via splink_datasets when
splink is installed, else from a vendored parquet under the gitignored
tests/benchmarks/datasets/.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import tempfile
from collections.abc import Hashable
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


def _rename_columns(table: pa.Table, mapping: dict[str, str]) -> pa.Table:
    """Rename columns via a {old: new} mapping (pyarrow renames by full list)."""
    return table.rename_columns([mapping.get(name, name) for name in table.column_names])


def _cast_column_to_string(table: pa.Table, name: str) -> pa.Table:
    """Return a copy of ``table`` with column ``name`` cast to pa.string()."""
    idx = table.schema.get_field_index(name)
    return table.set_column(idx, name, pc.cast(table.column(name), pa.string()))


def _read_csv_lossy(path: Path) -> pa.Table:
    """Read a CSV with every column a string and empty fields kept as ``""``.

    The Leipzig CSVs carry invalid UTF-8 bytes, which are replaced rather than
    refused (a leading BOM is dropped). Quoted fields may span lines.
    """
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    header = next(csv.reader(io.StringIO(text)), [])
    return pacsv.read_csv(
        io.BytesIO(text.encode("utf-8")),
        parse_options=pacsv.ParseOptions(newlines_in_values=True),
        convert_options=pacsv.ConvertOptions(
            column_types={name: pa.string() for name in header},
            null_values=[],
            strings_can_be_null=False,
            quoted_strings_can_be_null=False,
        ),
    )


def _with_source_ids(table: pa.Table, src: str, rename: dict[str, str] | None = None) -> pa.Table:
    """Rename columns, then replace ``id`` with a trailing ``record_id`` of ``<src>:<id>``."""
    if rename:
        table = _rename_columns(table, rename)
    ids = pa.array([f"{src}:{v}" for v in table.column("id").to_pylist()], pa.string())
    return table.append_column("record_id", ids).drop_columns(["id"])


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


def _historical_50k() -> tuple[pa.Table, pa.Table]:
    df = None
    try:
        from splink import splink_datasets  # type: ignore
    except ImportError:
        splink_datasets = None  # type: ignore
    if splink_datasets is not None:
        try:
            df = pa.Table.from_pandas(
                splink_datasets.historical_50k,
                preserve_index=False,  # type: ignore
            )
        except Exception as e:  # splink present but dataset unusable -> try vendored
            logger.warning("splink_datasets.historical_50k failed (%s); trying vendored parquet", e)
            df = None
    if df is None:
        vendored = DATASETS_DIR / "historical_50k.parquet"
        if not vendored.exists():
            raise DatasetUnavailable(
                f"install `goldenmatch[bench]` (for splink_datasets) or vendor {vendored}"
            )
        df = pq.read_table(vendored)

    # historical_50k columns: unique_id, cluster, first_name, surname, dob,
    # birth_place, postcode_fake, occupation, ...
    df = _rename_columns(df, {"unique_id": "record_id", "cluster": "cluster_id"})
    truth = df.select(["record_id", "cluster_id"])
    records = df.drop_columns(["cluster_id"])
    return records, truth


def _dblp_acm() -> tuple[pa.Table, pa.Table]:
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

    records = pa.concat_tables(
        [
            _with_source_ids(_read_csv_lossy(dblp_path), "dblp"),
            _with_source_ids(_read_csv_lossy(acm_path), "acm"),
        ],
        promote_options="default",
    )

    gt = _read_csv_lossy(gt_path)
    pairs: list[tuple[Hashable, Hashable]] = [
        (f"dblp:{d}", f"acm:{a}")
        for d, a in zip(gt.column("idDBLP").to_pylist(), gt.column("idACM").to_pylist())
    ]

    all_ids = records.column("record_id").to_pylist()
    cmap = _cluster_ids_from_pairs(all_ids, pairs)
    truth = pa.table(
        {
            "record_id": all_ids,
            "cluster_id": [cmap[r] for r in all_ids],
        }
    )
    return records, truth


def _febrl3() -> tuple[pa.Table, pa.Table]:
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
    id_col = str(pdf.columns[0])
    records = pa.Table.from_pandas(pdf, preserve_index=False)
    records = _rename_columns(records, {id_col: "record_id"})
    records = _cast_column_to_string(records, "record_id")

    all_ids = records.column("record_id").to_pylist()
    pairs: list[tuple[Hashable, Hashable]] = [(str(a), str(b)) for a, b in links]
    cmap = _cluster_ids_from_pairs(all_ids, pairs)
    truth = pa.table(
        {
            "record_id": all_ids,
            "cluster_id": [cmap[r] for r in all_ids],
        }
    )
    return records, truth


def _ncvr() -> tuple[pa.Table, pa.Table]:
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


def _synthetic(
    shape: str,
    rows: int,
    *,
    dupe_rate: float = 0.20,
    seed: int = 42,
    corruption: float = 1.0,
) -> tuple[pa.Table, pa.Table]:
    """Synthetic rows of one head-to-head SHAPE, as a (records, truth) dataset.

    Reuses ``generate_fixture.generate`` (writes records + truth parquet to a
    temp dir), then reads both back into the (records, truth) contract shape.

    Exposing the head-to-head shapes as datasets is what lets the score-histogram
    probe run on them. That probe answers whether a shape's matches and
    non-matches are SEPARABLE, and separability is the property that decides
    whether the calibration choice matters at all: linear and posterior are both
    monotone in match weight, so they rank pairs identically and can only differ
    where the cut lands. The head-to-head panel shows exactly that split --
    biblio's linear and posterior lanes are byte-identical at both scales, while
    person's differ by 0.078 F1 -- and the probe is the instrument that can say
    whether an empty band is the reason.

    ``rows`` is a parameter because the effect is scale-dependent: the person
    shape's over-merge is mild at 100k and catastrophic at 1M, so a probe fixed
    at one small size could report "separable" for a shape that stops being so.
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

    with tempfile.TemporaryDirectory(prefix=f"gm_synth_{shape}_") as td:
        out = Path(td) / "records.parquet"
        truth_path = Path(td) / "truth.parquet"
        generate(
            rows=rows,
            dupe_rate=dupe_rate,
            out=out,
            truth=truth_path,
            seed=seed,
            batch=1_000_000,
            shape=shape,
            corruption=corruption,
        )
        records = pq.read_table(out)
        truth = pq.read_table(truth_path)
    return records, truth


def _synthetic_rows(default: int = 5_000) -> int:
    """Row count for the synthetic shapes, overridable per run.

    Env rather than a loader argument because `load_dataset(name)` is a
    single-string contract shared with the real benchmark datasets, and widening
    it for two synthetic entries would touch every caller.
    """
    raw = os.environ.get("GOLDENMATCH_BENCH_SYNTHETIC_ROWS", "").strip()
    if not raw:
        return default
    try:
        n = int(raw)
    except ValueError:
        return default
    return n if n > 0 else default


def _synthetic_person() -> tuple[pa.Table, pa.Table]:
    """Synthetic person rows. See :func:`_synthetic`."""
    return _synthetic("person", _synthetic_rows())


def _synthetic_biblio() -> tuple[pa.Table, pa.Table]:
    """Synthetic biblio rows. See :func:`_synthetic`."""
    return _synthetic("biblio", _synthetic_rows())


def _two_source_leipzig(
    subdir: str,
    file_a: str,
    file_b: str,
    gt_file: str,
    gt_cols: tuple[str, str],
    src_a: str,
    src_b: str,
    rename: dict[str, str] | None = None,
) -> tuple[pa.Table, pa.Table]:
    """Generic Leipzig two-source ER loader (same shape as ``_dblp_acm``).

    Unions ``file_a`` + ``file_b`` into one records frame with source-prefixed
    record ids (``<src_a>:<id>`` / ``<src_b>:<id>``) and derives ``cluster_id``
    from the perfect-mapping pair list via connected components. ``rename``
    harmonises differently-named columns across the two sources (e.g. Google's
    ``name`` -> ``title``) so the FS comparison set has shared fields."""
    base = DATASETS_DIR / subdir
    a_path, b_path, gt_path = base / file_a, base / file_b, base / gt_file
    missing = [p for p in (a_path, b_path, gt_path) if not p.exists()]
    if missing:
        raise DatasetUnavailable(
            f"{subdir} source CSVs not found (vendor under {base}); "
            f"missing: {[p.name for p in missing]}"
        )

    records = pa.concat_tables(
        [
            _with_source_ids(_read_csv_lossy(a_path), src_a, rename),
            _with_source_ids(_read_csv_lossy(b_path), src_b, rename),
        ],
        promote_options="default",
    )

    gt = _read_csv_lossy(gt_path)
    ca, cb = gt_cols
    pairs: list[tuple[Hashable, Hashable]] = [
        (f"{src_a}:{x}", f"{src_b}:{y}")
        for x, y in zip(gt.column(ca).to_pylist(), gt.column(cb).to_pylist())
    ]
    all_ids = records.column("record_id").to_pylist()
    cmap = _cluster_ids_from_pairs(all_ids, pairs)
    truth = pa.table({"record_id": all_ids, "cluster_id": [cmap[r] for r in all_ids]})
    return records, truth


def _dblp_scholar() -> tuple[pa.Table, pa.Table]:
    """Leipzig DBLP-Scholar bibliographic ER. HELD OUT from the tuning panel
    (dblp_acm is in-panel; this is a distinct, larger, noisier bibliographic
    linkage — Scholar side is web-scraped) — so it tests whether an FS lever
    tuned on dblp_acm generalises to unseen bibliographic data."""
    return _two_source_leipzig(
        "DBLP-Scholar",
        "DBLP1.csv",
        "Scholar.csv",
        "DBLP-Scholar_perfectMapping.csv",
        ("idDBLP", "idScholar"),
        "dblp",
        "scholar",
    )


def _amazon_google() -> tuple[pa.Table, pa.Table]:
    """Leipzig Amazon-GoogleProducts ER. HELD OUT and a DIFFERENT DOMAIN
    (product, not person/bibliographic) — the hardest generalisation test for a
    threshold lever tuned on PII + bibliographic panels. Google's ``name`` is
    renamed to ``title`` so the two sources share a comparable field."""
    return _two_source_leipzig(
        "Amazon-Google",
        "Amazon.csv",
        "GoogleProducts.csv",
        "Amzon_GoogleProducts_perfectMapping.csv",
        ("idAmazon", "idGoogleBase"),
        "amazon",
        "google",
        rename={"name": "title"},
    )


def _febrl4() -> tuple[pa.Table, pa.Table]:
    """recordlinkage's Febrl4 synthetic person LINKAGE dataset (two sources:
    5k originals + 5k duplicates, 1:1 true links). HELD OUT from the tuning
    panel (febrl3 is single-source dedup with up to 5 dups/record) — a
    structurally different PII task, so it tests generalisation of an FS lever
    tuned on febrl3-shape data."""
    try:
        from recordlinkage.datasets import load_febrl4  # type: ignore
    except ImportError as e:
        raise DatasetUnavailable(
            "recordlinkage not installed (pip install recordlinkage) for febrl4"
        ) from e

    dfa, dfb, links = load_febrl4(return_links=True)

    # Indices are globally unique across the two frames ('rec-N-org' vs
    # 'rec-N-dup-0'), so a plain concat keeps record ids distinct. recordlinkage
    # hands back its own frames: convert each once here and stay in arrow after.
    def _prep(df) -> pa.Table:
        table = pa.Table.from_pandas(df.reset_index(), preserve_index=False)
        return _rename_columns(table, {table.column_names[0]: "record_id"})

    records = pa.concat_tables([_prep(dfa), _prep(dfb)], promote_options="default")
    records = _cast_column_to_string(records, "record_id")

    all_ids = records.column("record_id").to_pylist()
    pairs: list[tuple[Hashable, Hashable]] = [(str(a), str(b)) for a, b in links]
    cmap = _cluster_ids_from_pairs(all_ids, pairs)
    truth = pa.table({"record_id": all_ids, "cluster_id": [cmap[r] for r in all_ids]})
    return records, truth


# ── Held-out corpus for FS link-cut routing (spec 2026-09-11, P3) ─────────────
# Never used to write or tune a routing row. Data is fetched at run time (see
# .github/workflows/fs-cut-rule-matrix.yml) and never committed.


def _abt_buy() -> tuple[pa.Table, pa.Table]:
    """Leipzig Abt-Buy product ER (CC-BY: credit the Database Group Leipzig and
    Köpcke, Thor & Rahm, VLDB 2010). Buy's extra ``manufacturer`` column is kept,
    null on Abt rows."""
    return _two_source_leipzig(
        "Abt-Buy",
        "Abt.csv",
        "Buy.csv",
        "abt_buy_perfectMapping.csv",
        ("idAbt", "idBuy"),
        "abt",
        "buy",
    )


_MAGELLAN_SPLITS = ("train.csv", "valid.csv", "test.csv")

#: Corpus names of the DeepMatcher/Magellan benchmarks, by their directory under ``Magellan/``.
MAGELLAN_SUBDIRS: dict[str, str] = {
    "walmart_amazon": "Walmart-Amazon",
    "itunes_amazon": "iTunes-Amazon",
    "fodors_zagats": "Fodors-Zagats",
}


def magellan_labels(subdir: str) -> list[tuple[str, str, bool]]:
    """Every labelled candidate pair of the train/valid/test splits as
    ``(a:<id>, b:<id>, is_match)``, in file order. Raises ``DatasetUnavailable`` when a split
    is missing."""
    base = DATASETS_DIR / "Magellan" / subdir
    splits = [base / s for s in _MAGELLAN_SPLITS]
    missing = [p.name for p in splits if not p.exists()]
    if missing:
        raise DatasetUnavailable(
            f"{subdir} Magellan splits not found under {base}; missing: {missing}"
        )
    labels: list[tuple[str, str, bool]] = []
    for split in splits:
        table = _read_csv_lossy(split)
        for left, right, label in zip(
            table.column("ltable_id").to_pylist(),
            table.column("rtable_id").to_pylist(),
            table.column("label").to_pylist(),
        ):
            labels.append((f"a:{left}", f"b:{right}", label.strip() == "1"))
    return labels


def _magellan(subdir: str) -> tuple[pa.Table, pa.Table]:
    """A DeepMatcher/Magellan structured benchmark (cite-only: fetched at run time,
    never committed; Konda et al. 2016, Mudgal et al. 2018).

    Records are tableA + tableB with ``a:<id>`` / ``b:<id>`` ids. Truth links the
    ``label == 1`` pairs of the train/valid/test candidate splits: it covers only
    DeepMatcher's labelled candidate positives. An unlabelled true match that a
    rule links is scored as a false positive, so lower cuts are penalised; recall
    a higher cut loses on unlabelled matches is not counted. The bias therefore
    favours high-cut rules such as ``evidence_12`` and ``posterior_099``."""
    base = DATASETS_DIR / "Magellan" / subdir
    paths = [base / "tableA.csv", base / "tableB.csv", *(base / s for s in _MAGELLAN_SPLITS)]
    missing = [p for p in paths if not p.exists()]
    if missing:
        raise DatasetUnavailable(
            f"{subdir} Magellan CSVs not found under {base}; missing: {[p.name for p in missing]}"
        )
    records = pa.concat_tables(
        [
            _with_source_ids(_read_csv_lossy(paths[0]), "a"),
            _with_source_ids(_read_csv_lossy(paths[1]), "b"),
        ],
        promote_options="default",
    )
    pairs: list[tuple[Hashable, Hashable]] = [
        (left, right) for left, right, is_match in magellan_labels(subdir) if is_match
    ]
    all_ids = records.column("record_id").to_pylist()
    cmap = _cluster_ids_from_pairs(all_ids, pairs)
    truth = pa.table({"record_id": all_ids, "cluster_id": [cmap[r] for r in all_ids]})
    return records, truth


def _walmart_amazon() -> tuple[pa.Table, pa.Table]:
    """Magellan Walmart-Amazon: electronics products, ~24.6k records."""
    return _magellan("Walmart-Amazon")


def _itunes_amazon() -> tuple[pa.Table, pa.Table]:
    """Magellan iTunes-Amazon: music tracks, ~62.8k records, not saturated."""
    return _magellan("iTunes-Amazon")


def _fodors_zagats() -> tuple[pa.Table, pa.Table]:
    """Magellan Fodors-Zagats: restaurants, 946 records, near-saturated (F1 ~1.0)."""
    return _magellan("Fodors-Zagats")


def _febrl_raw(name: str) -> tuple[pa.Table, pa.Table]:
    """FEBRL ``dataset1`` / ``dataset2`` from the raw CSVs recordlinkage ships (ANU
    open-source licence, MPL-1.1 derived; Christen 2008), read without pandas.

    Headers and values carry a space after each comma, so both are trimmed.
    ``rec-<N>-org`` and ``rec-<N>-dup-<k>`` are the same entity ``N``."""
    path = DATASETS_DIR / "FEBRL" / f"{name}.csv"
    if not path.exists():
        raise DatasetUnavailable(f"FEBRL {name} not found at {path}")
    raw = _read_csv_lossy(path)
    records = _rename_columns(
        pa.table(
            {col.strip(): pc.utf8_trim_whitespace(raw.column(col)) for col in raw.column_names}
        ),
        {"rec_id": "record_id"},
    )
    ids = records.column("record_id").to_pylist()
    entity: list[int] = []
    for rid in ids:
        parts = rid.split("-")
        if len(parts) < 3 or parts[0] != "rec" or not parts[1].isdigit():
            raise ValueError(f"FEBRL {name}: unexpected rec_id {rid!r}")
        entity.append(int(parts[1]))
    truth = pa.table({"record_id": ids, "cluster_id": entity})
    return records, truth


def _febrl1() -> tuple[pa.Table, pa.Table]:
    """FEBRL dataset1: 1,000 records, 500 originals with one duplicate each."""
    return _febrl_raw("dataset1")


def _febrl2() -> tuple[pa.Table, pa.Table]:
    """FEBRL dataset2: 5,000 records, 4,000 originals and 1,000 duplicates."""
    return _febrl_raw("dataset2")


# ── DESIGN corpus widening (spec 2026-09-11-fs-cut-rule-routing-design, P3) ───
# Leipzig clustering trio (many-source, not two-source like _two_source_leipzig)
# plus synthetic design variants. Approved so the candidate rule
# "lambda < 0.004 with the prior binding -> posterior_099" does not rest on
# dblp_acm alone. DESIGN, never HOLDOUT: see corpus.py.


def _musicbrainz_20k() -> tuple[pa.Table, pa.Table]:
    """Leipzig MusicBrainz 20K clustering benchmark (CC-BY: credit the Database
    Group Leipzig and Saeedi, Peukert & Rahm, ADBIS 2017). 5 sources of the same
    ~10K tracks; ``CID`` is the ground-truth cluster id, never empty."""
    path = DATASETS_DIR / "Leipzig-Clustering" / "MusicBrainz" / "musicbrainz-20-A01.csv"
    if not path.exists():
        raise DatasetUnavailable(f"MusicBrainz 20K not found at {path}")
    raw = _read_csv_lossy(path)
    record_ids = pa.array([f"mb:{v}" for v in raw.column("TID").to_pylist()], pa.string())
    cluster_ids = pa.array([int(v) for v in raw.column("CID").to_pylist()], pa.int64())
    keep = ["number", "title", "length", "artist", "album", "year", "language"]
    records = raw.select(keep).append_column("record_id", record_ids)
    truth = pa.table({"record_id": record_ids, "cluster_id": cluster_ids})
    return records, truth


def _geo_settlements() -> tuple[pa.Table, pa.Table]:
    """Leipzig geographic settlements clustering benchmark (CC-BY: credit the
    Database Group Leipzig and Saeedi, Peukert & Rahm, ADBIS 2017). Settlement
    labels from 5 sources; ``combinedSettlements(PerfectMatch).json``'s
    ``clusteredVertices`` give the ground-truth clusters (one record can appear
    in two clusters, which then merge under union)."""
    import json

    base = DATASETS_DIR / "Leipzig-Clustering" / "GeoSettlements"
    records_path = base / "settlements.json"
    clusters_path = base / "combinedSettlements(PerfectMatch).json"
    missing = [p for p in (records_path, clusters_path) if not p.exists()]
    if missing:
        raise DatasetUnavailable(
            f"GeoSettlements source JSON not found (vendor under {base}); "
            f"missing: {[p.name for p in missing]}"
        )

    record_ids: list[str] = []
    labels: list[str | None] = []
    lats: list[str | None] = []
    lons: list[str | None] = []
    types: list[str | None] = []
    for line in records_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        data = obj.get("data", {})
        record_ids.append(f"geo:{obj['id']}")
        labels.append(data.get("label"))
        lat = data.get("lat")
        lon = data.get("lon")
        lats.append(str(lat) if lat is not None else None)
        lons.append(str(lon) if lon is not None else None)
        t = data.get("type")
        if isinstance(t, list):
            t = "|".join(t)
        types.append(t)
    records = pa.table(
        {
            "record_id": pa.array(record_ids, pa.string()),
            "label": pa.array(labels, pa.string()),
            "lat": pa.array(lats, pa.string()),
            "lon": pa.array(lons, pa.string()),
            "type": pa.array(types, pa.string()),
        }
    )

    pairs: list[tuple[Hashable, Hashable]] = []
    for line in clusters_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        vertices = obj.get("data", {}).get("clusteredVertices") or []
        ids = [f"geo:{v}" for v in vertices]
        pairs.extend(zip(ids, ids[1:]))

    all_ids = records.column("record_id").to_pylist()
    cmap = _cluster_ids_from_pairs(all_ids, pairs)
    truth = pa.table({"record_id": all_ids, "cluster_id": [cmap[r] for r in all_ids]})
    return records, truth


def _affiliations() -> tuple[pa.Table, pa.Table]:
    """Leipzig affiliation-strings clustering benchmark (CC-BY: credit the
    Database Group Leipzig and Saeedi, Peukert & Rahm, ADBIS 2017). Free-text
    affiliation strings; the headerless mapping CSV's id pairs give the
    ground-truth clusters."""
    import csv as _csv

    base = DATASETS_DIR / "Leipzig-Clustering" / "Affiliations"
    ids_path = base / "affiliationstrings_ids.csv"
    mapping_path = base / "affiliationstrings_mapping.csv"
    missing = [p for p in (ids_path, mapping_path) if not p.exists()]
    if missing:
        raise DatasetUnavailable(
            f"Affiliations source CSVs not found (vendor under {base}); "
            f"missing: {[p.name for p in missing]}"
        )

    ids_table = _read_csv_lossy(ids_path)
    record_ids = pa.array([f"aff:{v}" for v in ids_table.column("id1").to_pylist()], pa.string())
    records = pa.table({"record_id": record_ids, "affiliation": ids_table.column("affil1")})

    text = mapping_path.read_bytes().decode("utf-8-sig", errors="replace")
    pairs: list[tuple[Hashable, Hashable]] = [
        (f"aff:{row[0]}", f"aff:{row[1]}") for row in _csv.reader(io.StringIO(text)) if row
    ]

    all_ids = records.column("record_id").to_pylist()
    cmap = _cluster_ids_from_pairs(all_ids, pairs)
    truth = pa.table({"record_id": all_ids, "cluster_id": [cmap[r] for r in all_ids]})
    return records, truth


#: Seed for the design-set synthetic variants: distinct from the held-out 1009 and
#: the design panel's 42.
_DESIGN_SYNTH_SEED = 7

#: name -> (shape, corruption, dupe_rate). d02 = dupe_rate 0.02 (tiny lambda), d20 = 0.20.
_SYNTH_DESIGN: dict[str, tuple[str, float, float]] = {
    "synth_biblio_d02": ("biblio", 1.0, 0.02),
    "synth_person_d02": ("person", 1.0, 0.02),
    "synth_product_d02": ("product", 1.0, 0.02),
    "synth_product_d20": ("product", 1.0, 0.20),
}


def _synthetic_design(shape: str, corruption: float, dupe_rate: float) -> tuple[pa.Table, pa.Table]:
    """A design synthetic variant: the design panel's dedicated seed (distinct
    from both the held-out seed and the head-to-head panel's default 42), at
    ``dupe_rate`` and ``corruption``."""
    return _synthetic(
        shape,
        _synthetic_rows(),
        dupe_rate=dupe_rate,
        seed=_DESIGN_SYNTH_SEED,
        corruption=corruption,
    )


#: Seed for the held-out synthetic variants; the design panel's synthetic sets use 42.
_HELDOUT_SYNTH_SEED = 1009

#: name -> (shape, corruption, dupe_rate). c05 = corruption 0.5, c20 = 2.0;
#: d10 = dupe_rate 0.10, d40 = 0.40.
_SYNTH_HELDOUT: dict[str, tuple[str, float, float]] = {
    f"synth_{shape}_c{int(round(c * 10)):02d}_d{int(round(d * 100)):02d}": (shape, c, d)
    for shape in ("person", "biblio")
    for c in (0.5, 2.0)
    for d in (0.10, 0.40)
}


def _synthetic_heldout(
    shape: str, corruption: float, dupe_rate: float
) -> tuple[pa.Table, pa.Table]:
    """A held-out synthetic variant: a seed the design panel never used, duplicate
    noise scaled by ``corruption``, and ``dupe_rate`` duplicates."""
    return _synthetic(
        shape,
        _synthetic_rows(),
        dupe_rate=dupe_rate,
        seed=_HELDOUT_SYNTH_SEED,
        corruption=corruption,
    )


_LOADERS = {
    "historical_50k": _historical_50k,
    "dblp_acm": _dblp_acm,
    "febrl3": _febrl3,
    "ncvr": _ncvr,
    "synthetic_person": _synthetic_person,
    # The head-to-head panel's OTHER shape. Present so the score-histogram probe
    # can compare the two: the panel shows biblio's linear and posterior lanes
    # agreeing byte-for-byte while person's diverge, and only a labelled score
    # distribution can say whether an empty band is why.
    "synthetic_biblio": _synthetic_biblio,
    # Not in the FS threshold-refit tuning panel (these are DESIGN datasets for
    # link-cut routing).
    "febrl4": _febrl4,
    "dblp_scholar": _dblp_scholar,
    "amazon_google": _amazon_google,
    # Held-out corpus for FS link-cut routing (P3): never used to write a row.
    "abt_buy": _abt_buy,
    "walmart_amazon": _walmart_amazon,
    "itunes_amazon": _itunes_amazon,
    "fodors_zagats": _fodors_zagats,
    "febrl1": _febrl1,
    "febrl2": _febrl2,
    **{
        name: (lambda spec=spec: _synthetic_heldout(*spec)) for name, spec in _SYNTH_HELDOUT.items()
    },
    # DESIGN corpus widening (P3): Leipzig clustering trio + synthetic variants.
    "musicbrainz_20k": _musicbrainz_20k,
    "geo_settlements": _geo_settlements,
    "affiliations": _affiliations,
    **{name: (lambda spec=spec: _synthetic_design(*spec)) for name, spec in _SYNTH_DESIGN.items()},
}


def load_dataset(name: str) -> tuple[pa.Table, pa.Table]:
    if name not in _LOADERS:
        raise KeyError(f"unknown dataset {name!r}; have {sorted(_LOADERS)}")
    return _LOADERS[name]()
