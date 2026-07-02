"""WhoIsWho paper records -> a per-name goldenmatch DataFrame.

SND is a clustering-*within-a-blocking-group* problem: the block key (the name)
is given, and we cluster that name's papers into one cluster per real person.
So we build ONE frame per name -- rows are papers, columns carry the signals
that discriminate two people who share a name.

The signal hierarchy (name string is constant -> zero signal):
    coauthors  -- set of the OTHER authors on the paper (PRIMARY, relational)
    orgs       -- set of affiliations on the paper (strong)
    venue      -- publication venue (medium)
    text       -- title + abstract + keywords (embedding / token signal)
    year       -- weak tie-breaker

Set-valued columns (coauthors, orgs) are encoded as sorted "|"-delimited strings
via ``normalize.encode_set`` so they ride goldenmatch's string scorer surface;
the co-author Jaccard plugin scorer decodes them back to sets.
"""
from __future__ import annotations

import polars as pl
from normalize import encode_set, name_key, norm_token

# A constant blocking column: every paper in a name-block is a candidate for
# every other, so we force them all into ONE goldenmatch block. (goldenmatch
# requires a blocking config for weighted matchkeys; the name IS the block.)
BLOCK_COL = "__block__"
PAPER_ID_COL = "__paper_id__"


def _paper_row(pid: str, paper: dict, block_name_key: str) -> dict:
    authors = paper.get("authors") or []
    coauthor_names = []
    all_orgs = []
    for a in authors:
        nm = a.get("name") or ""
        org = a.get("org") or ""
        if org:
            all_orgs.append(org)
        # drop the self-author (the name that defines this block) -- its token
        # signature is constant across the block and carries no signal.
        if name_key(nm) != block_name_key:
            coauthor_names.append(nm)

    title = paper.get("title") or ""
    abstract = paper.get("abstract") or ""
    keywords = paper.get("keywords") or []
    text = norm_token(" ".join([title, abstract, " ".join(keywords)]))

    year = paper.get("year")
    try:
        year_i = int(year) if year not in (None, "") else 0
    except (TypeError, ValueError):
        year_i = 0

    return {
        "__row_id__": 0,  # filled by build_name_frame (explicit, deterministic)
        PAPER_ID_COL: pid,
        BLOCK_COL: "0",
        "coauthors": encode_set(coauthor_names),
        "orgs": encode_set(all_orgs),
        "venue": norm_token(paper.get("venue") or ""),
        "text": text,
        "year": year_i,
    }


def build_name_frame(name: str, pids: list[str], pub: dict[str, dict]) -> pl.DataFrame:
    """Build the goldenmatch frame for one ambiguous ``name``.

    ``pids`` are that name's papers (from ``sna_valid_raw.json`` /
    ``train_author.json``); ``pub`` is the pid -> record map. Rows are ordered
    exactly as ``pids``, and ``__row_id__`` is a fresh 0-based index -- so a
    cluster's ``members`` (row ids) map back to ``__paper_id__`` positionally.
    """
    bkey = name_key(name.replace("_", " "))
    rows = []
    rid = 0
    for pid in pids:
        paper = pub.get(pid)
        if paper is None:
            continue
        row = _paper_row(pid, paper, bkey)
        row["__row_id__"] = rid
        rows.append(row)
        rid += 1
    schema = {
        "__row_id__": pl.Int64,
        PAPER_ID_COL: pl.Utf8,
        BLOCK_COL: pl.Utf8,
        "coauthors": pl.Utf8,
        "orgs": pl.Utf8,
        "venue": pl.Utf8,
        "text": pl.Utf8,
        "year": pl.Int64,
    }
    return pl.DataFrame(rows, schema=schema)


def clusters_to_pid_lists(clusters: dict[int, dict], df: pl.DataFrame) -> list[list[str]]:
    """Map goldenmatch ``result.clusters`` back to WhoIsWho prediction format.

    Returns a list of paper-id lists (one per predicted real author). Papers not
    covered by any multi-member cluster become singletons -- WhoIsWho scores
    every paper, so a dropped singleton would silently deflate recall's
    denominator differently than intended.
    """
    row_to_pid = dict(zip(df["__row_id__"].to_list(), df[PAPER_ID_COL].to_list()))
    covered: set[int] = set()
    out: list[list[str]] = []
    for info in clusters.values():
        members = info.get("members", [])
        if len(members) < 2:
            continue
        pids = [row_to_pid[m] for m in members if m in row_to_pid]
        if pids:
            out.append(pids)
            covered.update(members)
    # singletons: every uncovered row is its own cluster
    for rid, pid in row_to_pid.items():
        if rid not in covered:
            out.append([pid])
    return out
