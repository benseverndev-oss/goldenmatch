"""Batched payload prefetch must be byte-identical to retaining every payload.

`resolve_clusters`' prep used to keep one Python payload dict per prepped record
for the whole resolve (`_payloads`, read later through `rowid_to_payload`). At
14M records that is ~10 GB of dict + value objects on top of the pipeline's own
resident set, which is what OOMs a 32 GB box in `identity_prep_record_ids`.

Every read of `rowid_to_payload` happens in the write loop and is for a member
of the cluster being processed, so the payloads can instead be materialized per
BATCH of clusters from the prepped frame -- through the same `select_dicts` path
the prep uses -- and released after. `GOLDENMATCH_IDENTITY_PAYLOAD_PREFETCH=0`
restores the retained list (kill-switch).

Payloads feed three outputs, so all three are compared on the deterministic
surface (entity ids are uuid4): each source_record's stored payload + derived
values, each entity's golden record keyed by its member set, and the cluster
structure. The fixture drives every read site: the bulk path and the per-row
path (GOLDENMATCH_IDENTITY_BULK), and a second resolve over the same store that
UPDATES one entity and MERGES two. Cluster members carry DIFFERENT values so
golden survivorship has real choices to make, and a duplicated __row_id__ pins
the last-occurrence-wins semantics the retained list has.
"""

from __future__ import annotations

import json

import polars as pl
import pytest
from goldenmatch.identity import IdentityStore
from goldenmatch.identity import resolve as resolve_mod
from goldenmatch.identity.resolve import resolve_clusters

_N_CLUSTERS = 60
_PER_CLUSTER = 3
_BIG = 40  # one cluster larger than the small batch sizes below


def _phase1_rows() -> list[dict]:
    rows: list[dict] = []
    rid = 0
    for c in range(_N_CLUSTERS):
        for m in range(_PER_CLUSTER):
            rows.append({
                "__row_id__": rid,
                "pk": f"p{c}_{m}",
                # members differ, and the most complete one is not always first
                "name": f"Name{c}" if m != 1 else None,
                "city": f"City{c % 7}" if m == 2 else "",
                "zip": str(10000 + c) if m != 0 else None,
            })
            rid += 1
    for m in range(_BIG):
        rows.append({"__row_id__": rid, "pk": f"big_{m}", "name": f"Big{m % 5}",
                     "city": None if m % 3 else "Metro", "zip": str(90000 + m)})
        rid += 1
    # Duplicated __row_id__: the retained list keeps the LAST occurrence's
    # payload (ordinal fixed by the first, value overwritten in place).
    rows.append({"__row_id__": 0, "pk": "p0_0", "name": "Overwritten",
                 "city": "LastWins", "zip": "00000"})
    return rows


def _phase1_clusters() -> dict[int, dict]:
    out = {c: {"members": [c * _PER_CLUSTER + m for m in range(_PER_CLUSTER)]}
           for c in range(_N_CLUSTERS)}
    base = _N_CLUSTERS * _PER_CLUSTER
    out[_N_CLUSTERS] = {"members": list(range(base, base + _BIG))}
    return out


def _phase2() -> tuple[list[dict], dict[int, dict]]:
    """Same PKs (so records map to phase-1 entities) with CHANGED values.

    cluster 0 = phase-1 cluster 0 + a new record  -> UPDATE one entity
    cluster 1 = phase-1 clusters 1 and 2 joined   -> MERGE two entities
    cluster 2 = brand-new records                 -> new entity
    """
    rows = [
        {"__row_id__": 0, "pk": "p0_0", "name": None, "city": "NewCity", "zip": "11111"},
        {"__row_id__": 1, "pk": "p0_1", "name": "Renamed0", "city": "", "zip": None},
        {"__row_id__": 2, "pk": "p0_2", "name": "Name0", "city": "City0", "zip": "10000"},
        {"__row_id__": 3, "pk": "fresh_a", "name": "Added", "city": None, "zip": "22222"},
        {"__row_id__": 4, "pk": "p1_0", "name": "Name1", "city": "", "zip": None},
        {"__row_id__": 5, "pk": "p1_1", "name": None, "city": "Mid", "zip": "10001"},
        {"__row_id__": 6, "pk": "p2_0", "name": "Name2", "city": "Far", "zip": None},
        {"__row_id__": 7, "pk": "p2_2", "name": "Name2b", "city": "", "zip": "10002"},
        {"__row_id__": 8, "pk": "new_x", "name": "X", "city": "Xc", "zip": "33333"},
        {"__row_id__": 9, "pk": "new_y", "name": None, "city": "Yc", "zip": "44444"},
    ]
    clusters = {0: {"members": [0, 1, 2, 3]}, 1: {"members": [4, 5, 6, 7]},
                2: {"members": [8, 9]}}
    return rows, clusters


def _frame(rows: list[dict], lane: str):
    df = pl.DataFrame(rows)
    return df.to_arrow() if lane == "arrow" else df


def _snapshot(store: IdentityStore) -> tuple[list, dict, set]:
    recs = store._fetchall(
        "SELECT record_id, source, source_pk, record_hash, payload, entity_id "
        "FROM source_records ORDER BY record_id", (),
    )
    derived = [(r["record_id"], r["source"], r["source_pk"], r["record_hash"], r["payload"])
               for r in recs]
    members: dict[str, set] = {}
    for r in recs:
        members.setdefault(r["entity_id"], set()).add(r["record_id"])
    nodes = store._fetchall(
        "SELECT entity_id, golden_record FROM identity_nodes WHERE status = 'active'", ())
    golden = {frozenset(members[n["entity_id"]]): n["golden_record"]
              for n in nodes if n["entity_id"] in members}
    return derived, golden, {frozenset(v) for v in members.values()}


def _run(tmp_path, tag, monkeypatch, *, prefetch: str, batch: str, bulk: str, lane: str):
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_PAYLOAD_PREFETCH", prefetch)
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_PAYLOAD_PREFETCH_ROWS", batch)
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_BULK", bulk)
    with IdentityStore(backend="sqlite", path=str(tmp_path / f"{tag}.db")) as store:
        s1 = resolve_clusters(clusters=_phase1_clusters(), df=_frame(_phase1_rows(), lane),
                              store=store, run_name="r1", dataset="pf", source_pk_col="pk",
                              emit_singletons=False)
        snap1 = _snapshot(store)
        rows2, clusters2 = _phase2()
        s2 = resolve_clusters(clusters=clusters2, df=_frame(rows2, lane), store=store,
                              run_name="r2", dataset="pf", source_pk_col="pk",
                              emit_singletons=False)
        return (vars(s1).copy(), vars(s2).copy()), snap1, _snapshot(store)


@pytest.mark.parametrize("lane", ["polars", "arrow"])
@pytest.mark.parametrize("bulk", ["1", "0"], ids=["bulk", "per_row"])
@pytest.mark.parametrize("batch", ["1", "7", "128", "100000"], ids=lambda b: f"batch{b}")
def test_prefetch_matches_retained(tmp_path, monkeypatch, lane, bulk, batch):
    """Batch 1 (a boundary after every cluster), 7 (boundaries mid-way and a
    cluster bigger than the batch), 128, and one batch for everything."""
    base = _run(tmp_path, f"ret_{lane}{bulk}{batch}", monkeypatch,
                prefetch="0", batch=batch, bulk=bulk, lane=lane)
    pref = _run(tmp_path, f"pf_{lane}{bulk}{batch}", monkeypatch,
                prefetch="1", batch=batch, bulk=bulk, lane=lane)
    (bsum, bsnap1, bsnap2), (psum, psnap1, psnap2) = base, pref

    b_derived, b_golden, b_struct = bsnap2
    assert b_derived and b_golden, "fixture resolved nothing -- the test would be vacuous"
    # The phase-2 resolve must actually take the merge, update and new-entity
    # paths, or those payload read sites were never exercised.
    s2 = bsum[1]
    assert s2["merged"] >= 1, s2              # clusters 1+2 joined -> merge path
    assert s2["absorbed_records"] >= 1, s2    # fresh_a joins entity 0 -> update path
    assert s2["created"] >= 1, s2             # new_x / new_y -> new entity
    assert psnap1 == bsnap1
    assert psnap2 == bsnap2
    assert psum == bsum
    # Duplicated __row_id__ 0: last occurrence's values won in the stored payload
    p00 = next(json.loads(d[4]) for d in bsnap1[0] if d[0].endswith(":p0_0"))
    assert p00["city"] == "LastWins"


def test_prefetch_engages_and_never_misses(tmp_path, monkeypatch):
    """Guard against the equivalence test passing because prefetch silently
    no-ops (then it would compare the retained path with itself). With a
    7-row batch the view must prefetch many times, serve every read from a
    batch (no per-row fallback), and never hold more than one batch."""
    views: list = []

    class _Spy(resolve_mod._PrefetchedPayloadView):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.peak = 0
            self.calls = 0
            views.append(self)

        def prefetch(self, row_ids):
            super().prefetch(row_ids)
            self.calls += 1
            self.peak = max(self.peak, len(self._batch))

    monkeypatch.setattr(resolve_mod, "_PrefetchedPayloadView", _Spy)
    _run(tmp_path, "spy", monkeypatch, prefetch="1", batch="7", bulk="1", lane="polars")
    assert views, "prefetch view was never constructed"
    v = views[0]
    assert v.calls > 5, v.calls
    assert v._misses == 0
    # one batch at most: <= batch target + the largest single cluster
    assert v.peak <= 7 + _BIG, v.peak


def test_prefetch_knobs(monkeypatch):
    from goldenmatch.identity.resolve import _payload_prefetch_enabled, _payload_prefetch_rows

    monkeypatch.delenv("GOLDENMATCH_IDENTITY_PAYLOAD_PREFETCH", raising=False)
    assert _payload_prefetch_enabled() is True          # default ON
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_PAYLOAD_PREFETCH", "0")
    assert _payload_prefetch_enabled() is False         # kill-switch
    monkeypatch.delenv("GOLDENMATCH_IDENTITY_PAYLOAD_PREFETCH_ROWS", raising=False)
    assert _payload_prefetch_rows() == 250_000
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_PAYLOAD_PREFETCH_ROWS", "500")
    assert _payload_prefetch_rows() == 500
    for bad in ("0", "-3", "abc"):
        monkeypatch.setenv("GOLDENMATCH_IDENTITY_PAYLOAD_PREFETCH_ROWS", bad)
        assert _payload_prefetch_rows() == 250_000
