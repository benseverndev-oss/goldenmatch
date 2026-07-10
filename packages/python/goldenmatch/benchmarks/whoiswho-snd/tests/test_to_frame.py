"""Frame builder: schema, self-author drop, and cluster->pid round-trip."""
from normalize import decode_set
from to_frame import (
    BLOCK_COL,
    PAPER_ID_COL,
    build_name_frame,
    clusters_to_pid_lists,
)

# a tiny 3-paper name-block for "wei_wang" -- two by person A (share co-author
# "ning zeng"), one by a different person B.
_PUB = {
    "p1": {"id": "p1", "title": "Deep nets", "abstract": "", "keywords": ["ml"],
           "authors": [{"name": "Wei Wang", "org": "MIT"}, {"name": "Ning Zeng", "org": "MIT"}],
           "venue": "NeurIPS", "year": 2019},
    "p2": {"id": "p2", "title": "More nets", "abstract": "", "keywords": [],
           "authors": [{"name": "Ning Zeng", "org": ""}, {"name": "Wang Wei", "org": "MIT"}],
           "venue": "ICML", "year": 2020},
    "p3": {"id": "p3", "title": "Soil carbon", "abstract": "", "keywords": [],
           "authors": [{"name": "Wei Wang", "org": "CAS"}, {"name": "Bo Li", "org": "CAS"}],
           "venue": "Nature", "year": 2015},
}


def test_frame_schema_and_row_ids():
    df = build_name_frame("wei_wang", ["p1", "p2", "p3"], _PUB)
    assert df.height == 3
    assert df["__row_id__"].to_list() == [0, 1, 2]
    assert set(df.columns) >= {"__row_id__", PAPER_ID_COL, BLOCK_COL,
                               "coauthors", "orgs", "venue", "text", "year"}
    # constant block -> all one goldenmatch block
    assert df[BLOCK_COL].unique().to_list() == ["0"]


def test_self_author_dropped_from_coauthors():
    df = build_name_frame("wei_wang", ["p1", "p2", "p3"], _PUB)
    co = dict(zip(df[PAPER_ID_COL].to_list(), df["coauthors"].to_list()))
    # "Wei Wang"/"Wang Wei" (the block name, order-insensitive) must NOT appear
    assert "wang wei" not in decode_set(co["p1"])
    assert decode_set(co["p1"]) == {"ning zeng"}
    assert decode_set(co["p2"]) == {"ning zeng"}   # order variant of self still dropped
    assert decode_set(co["p3"]) == {"bo li"}


def test_orgs_and_text_populated():
    df = build_name_frame("wei_wang", ["p1", "p2", "p3"], _PUB)
    row0 = df.filter(df[PAPER_ID_COL] == "p1").to_dicts()[0]
    assert "mit" in decode_set(row0["orgs"])
    assert "deep nets" in row0["text"]


def test_missing_pid_is_skipped():
    df = build_name_frame("wei_wang", ["p1", "ghost", "p3"], _PUB)
    assert df.height == 2
    assert df["__row_id__"].to_list() == [0, 1]  # reindexed contiguously


def test_clusters_to_pid_lists_covers_all_with_singletons():
    df = build_name_frame("wei_wang", ["p1", "p2", "p3"], _PUB)
    # cluster {p1,p2} together (rows 0,1); p3 (row 2) uncovered
    clusters = {0: {"members": [0, 1]}}
    out = clusters_to_pid_lists(clusters, df)
    assert sorted(map(sorted, out)) == [["p1", "p2"], ["p3"]]
    # every paper appears exactly once
    flat = [p for c in out for p in c]
    assert sorted(flat) == ["p1", "p2", "p3"]
