"""08 -- Identity Graph: durable identities across runs.

The other examples produce one-shot dedupe output. This one shows the
v1.15 Identity Graph: stable ``entity_id``s that survive re-runs, an
audit trail of how identities formed, and the steward operations
(merge / split) that close the loop.

Walkthrough (six acts):

  1. Run 1 -- two customers, two clusters, two identities.
  2. Run 2 -- one new record joins an existing identity (``absorbed``).
  3. Cross-source -- ERP records arrive; one matches an existing CRM
     identity, the other is brand new.
  4. Conflict -- a contradictory edge is recorded for steward review.
  5. Manual merge -- a steward decides two identities are actually the
     same person and merges them.
  6. Manual split -- a wrongly-absorbed record is split into its own
     identity.

Run:
    pip install goldenmatch
    python 08_identity_graph.py
"""
from __future__ import annotations

import shutil
from pathlib import Path

import polars as pl

import goldenmatch as gm
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    IdentityConfig,
    MatchkeyConfig,
    MatchkeyField,
    OutputConfig,
)
from goldenmatch.core.pipeline import run_dedupe_df
from goldenmatch.identity.model import EdgeKind, EvidenceEdge


DB = ".goldenmatch/identity_demo.db"


def _config(run_name: str) -> GoldenMatchConfig:
    return GoldenMatchConfig(
        output=OutputConfig(run_name=run_name),
        matchkeys=[MatchkeyConfig(
            name="people_fuzzy", type="weighted", threshold=0.85,
            fields=[
                MatchkeyField(field="name",  scorer="jaro_winkler", weight=0.7),
                MatchkeyField(field="email", scorer="exact",        weight=0.3),
            ],
        )],
        blocking=BlockingConfig(strategy="static", keys=[
            BlockingKeyConfig(fields=["zip"]),
        ]),
        identity=IdentityConfig(
            enabled=True, path=DB,
            source_pk_column="id", dataset="demo",
        ),
    )


def header(title: str) -> None:
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


def show_identity(record_id: str) -> None:
    """Print the resolved identity for one record."""
    with gm.IdentityStore(path=DB) as store:
        view = gm.find_by_record(store, record_id)
    if view is None:
        print(f"  {record_id} -> (no identity)")
        return
    conf = view.node.confidence
    conf_s = f"{conf:.3f}" if conf is not None else "-"
    print(f"  {record_id} -> entity {view.node.entity_id[:8]}...  "
          f"({view.node.status}, conf={conf_s})")
    print(f"     members: {[r.record_id for r in view.records]}")
    print(f"     events:  {[(e.kind, e.run_name) for e in view.events]}")


def main() -> None:
    # Clean slate for a deterministic demo.
    Path(DB).unlink(missing_ok=True)
    Path(DB + "-wal").unlink(missing_ok=True)
    Path(DB + "-shm").unlink(missing_ok=True)

    # ── Act 1: first run ────────────────────────────────────────────────
    header("Act 1 -- First run: two customers, two identities")
    df_run1 = pl.DataFrame({
        "id":    ["1", "2", "3"],
        "name":  ["Alice Smith", "Alyce Smith", "Bob Jones"],
        "email": ["a@x.com", "a@x.com", "b@y.com"],
        "zip":   ["12345", "12345", "67890"],
    })
    r1 = run_dedupe_df(df_run1, _config("demo-r1"), source_name="crm")
    print("\n  identity_summary:", r1["identity_summary"])
    show_identity("crm:1")
    show_identity("crm:3")

    # Capture Alice's entity_id for stability checks below.
    with gm.IdentityStore(path=DB) as store:
        alice = gm.find_by_record(store, "crm:1").node.entity_id

    # ── Act 2: rerun + new record absorbed ──────────────────────────────
    header("Act 2 -- Rerun: 1 new record joins Alice's identity")
    df_run2 = pl.concat([df_run1, pl.DataFrame({
        "id":    ["4"], "name":  ["Alise Smith"],
        "email": ["a@x.com"], "zip":   ["12345"],
    })])
    r2 = run_dedupe_df(df_run2, _config("demo-r2"), source_name="crm")
    print("\n  identity_summary:", r2["identity_summary"])
    show_identity("crm:1")
    show_identity("crm:4")

    with gm.IdentityStore(path=DB) as store:
        same = gm.find_by_record(store, "crm:1").node.entity_id == alice
    print(f"\n  entity_id stable across runs: {same}")

    # ── Act 3: cross-source from ERP ────────────────────────────────────
    header("Act 3 -- ERP records arrive; cross-source matching")
    erp_df = pl.DataFrame({
        "id":    ["erp-001", "erp-002"],
        "name":  ["A. Smith", "Charlie Wong"],   # erp-001 should match Alice
        "email": ["a@x.com", "charlie@z.com"],
        "zip":   ["12345", "11111"],
    })
    # Stack ERP onto the CRM rows so the within-block scoring can find
    # the cross-source match.
    combined = pl.concat([df_run2, erp_df], how="diagonal")
    r3 = run_dedupe_df(combined, _config("demo-r3"), source_name="crm")
    # The erp-* rows hash-collide on email+name with Alice so they fall in
    # her cluster; ``source_name`` here is the *default* for unsourced
    # rows -- in production you'd `run_dedupe()` a multi-file config.
    print("\n  identity_summary:", r3["identity_summary"])
    show_identity("crm:erp-001")
    show_identity("crm:erp-002")

    # ── Act 4: record a conflict for steward review ─────────────────────
    header("Act 4 -- A contradictory edge is recorded for review")
    with gm.IdentityStore(path=DB) as store:
        store.add_edge(EvidenceEdge(
            entity_id=alice,
            record_a_id="crm:1",
            record_b_id="manual:susan-different-person",
            kind=EdgeKind.CONFLICTS_WITH.value,
            score=0.12,
            matchkey_name="manual_review",
            run_name="steward",
            dataset="demo",
        ))
        conflicts = gm.find_conflicts(store, dataset="demo")
    print(f"\n  conflicts found: {len(conflicts)}")
    for c in conflicts:
        print(f"     {c['record_a_id']} <!> {c['record_b_id']}  score={c['score']}")

    # ── Act 5: manual merge ─────────────────────────────────────────────
    header("Act 5 -- Steward manually merges two identities")
    # Create a fake duplicate identity to merge.
    with gm.IdentityStore(path=DB) as store:
        dup_eid = gm.new_entity_id()
        store.upsert_identity(gm.IdentityNode(
            entity_id=dup_eid, dataset="demo", confidence=0.6,
        ))
        store.upsert_record(gm.SourceRecord(
            record_id="legacy:alice-old",
            source="legacy", source_pk="alice-old",
            record_hash="legacy-hash",
            entity_id=dup_eid, dataset="demo",
            payload={"name": "ALICE SMITH (legacy)"},
        ))
        out = gm.manual_merge(
            store, keep_entity_id=alice, absorb_entity_id=dup_eid,
            reason="confirmed duplicate from legacy CRM import",
        )
    print(f"\n  Merged {out['absorbed'][:8]}... -> {out['keep'][:8]}...")
    show_identity("legacy:alice-old")  # now points at Alice

    # ── Act 6: manual split ─────────────────────────────────────────────
    header("Act 6 -- Steward splits a wrongly-absorbed record")
    with gm.IdentityStore(path=DB) as store:
        # Pretend Alise (crm:4) actually belonged on her own identity.
        out = gm.manual_split(
            store, entity_id=alice, record_ids=["crm:4"],
            reason="Alise Smith is a different person despite matching email",
        )
    print(f"\n  Moved {len(out['moved'])} record(s) -> "
          f"new identity {out['new_entity_id'][:8]}...")
    show_identity("crm:1")
    show_identity("crm:4")

    # ── Audit: full event log for Alice ─────────────────────────────────
    header("Audit -- full event log for Alice's identity")
    with gm.IdentityStore(path=DB) as store:
        log = gm.identity_history(store, alice)
    for ev in log:
        run = ev["run_name"] or "-"
        print(f"  [{ev['recorded_at']}]  {ev['kind']:18s}  run={run}")

    print()
    print("Done. Inspect the graph any time:")
    print(f"    goldenmatch identity show {alice[:8]}...")
    print(f"    goldenmatch identity history {alice[:8]}...")
    print(f"    goldenmatch identity conflicts --dataset demo")


if __name__ == "__main__":
    main()
