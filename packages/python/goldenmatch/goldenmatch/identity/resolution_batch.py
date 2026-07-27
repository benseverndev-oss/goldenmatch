"""The versioned compute->control seam contract (Wave C, milestone C1).

The governing frame (decision 0047) and the control-plane manifesto
(`context-network/architecture/identity-control-plane-manifesto.md` §3) call for the
compute->control handoff to be an EXPLICIT, VERSIONED batch rather than the loose
in-memory args `resolve_clusters` took (`controller_snapshot: dict`, `run_name: str`,
… plus an env-only residency knob). This module introduces that contract object.

Scope of C1 (this file): the **metadata + config** half of the seam -- the fields
that were loose args -- plus the frame-residency budget (`flush_rows`) as a first-class
CONTRACT TERM instead of a bare env read. The bulk DATA (records / clusters /
pair-evidence frames) still flows to `resolve_clusters` as frames for now; folding those
arrays into the batch, and extracting an `apply_batch(store, batch)` body, is the C1
follow-on (kept separate to keep this change byte-identical -- `resolve_clusters` builds
a batch from its kwargs when none is passed, so every current caller is unchanged).

Bumping `CONTRACT_VERSION` is required for any field change; the version travels on the
object so a persisted or logged batch is self-describing.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, ClassVar


def _default_flush_rows() -> int:
    """The residency-budget default, single-sourced from resolve's env parse.

    Lazy import avoids a circular dependency (resolve imports this module)."""
    from goldenmatch.identity.resolve import _bulk_flush_rows
    return _bulk_flush_rows()


@dataclass(frozen=True)
class ResolutionBatch:
    """Versioned metadata/config contract for one compute->control resolution.

    Immutable so a batch can be logged / persisted / replayed without a caller
    mutating it mid-apply. Idempotency across the seam is keyed on
    ``(run_id, entity_id, kind)`` (matches the store's ``has_run_event`` +
    ``evidence_edges`` UNIQUE) -- ``run_id`` is the caller-facing half of that key.
    """

    CONTRACT_VERSION: ClassVar[int] = 1

    run_id: str = ""
    dataset: str | None = None
    matchkey_name: str | None = None
    source_pk_col: str | None = None
    controller_snapshot: dict[str, Any] | None = None
    actor: str = "pipeline"
    emit_singletons: bool = True
    weak_confidence_threshold: float = 0.6
    # Frame-residency budget: the write side flushes every ``flush_rows`` records so
    # it never stacks a second O(N) term on the compute prep floor (manifesto §3).
    # A contract term now, not just ``GOLDENMATCH_IDENTITY_BULK_FLUSH_ROWS``.
    flush_rows: int = 250_000
    contract_version: int = field(default=CONTRACT_VERSION)

    # --- bulk DATA parts (C1 follow-on) ---------------------------------------
    # The compute-side payload the control plane applies: the cluster partition
    # (dict OR SP-A frames), the record frame, the scored-pair stream, and the
    # per-cluster pair-score view. These are NOT part of the VERSIONED metadata
    # contract above (the manifesto's "Arrow may be one representation of its bulk
    # parts" -- they are the payload, not the config), so adding them does not bump
    # CONTRACT_VERSION; they are carried so the whole compute->control handoff is
    # ONE object and ``apply_batch(store, batch)`` takes no side args. ``None`` on a
    # metadata-only batch (``from_args``); ``resolve_clusters`` folds them in via
    # ``with_data`` before calling ``apply_batch``. Typed ``Any`` to keep this module
    # dependency-free (the data shapes live in resolve.py / core.frame).
    clusters: Any = None
    cluster_frames: Any = None
    df: Any = None
    scored_pairs: Any = None
    pair_score_view: Any = None

    def with_data(
        self,
        *,
        clusters: Any = None,
        cluster_frames: Any = None,
        df: Any = None,
        scored_pairs: Any = None,
        pair_score_view: Any = None,
    ) -> ResolutionBatch:
        """Return a copy carrying the bulk DATA parts (the compute payload).

        ``resolve_clusters`` calls this to fold its data args into the batch before
        ``apply_batch``; the metadata/config fields are unchanged. Frozen-safe
        (``dataclasses.replace``)."""
        import dataclasses

        return dataclasses.replace(
            self,
            clusters=clusters,
            cluster_frames=cluster_frames,
            df=df,
            scored_pairs=scored_pairs,
            pair_score_view=pair_score_view,
        )

    @classmethod
    def from_args(
        cls,
        *,
        run_id: str = "",
        dataset: str | None = None,
        matchkey_name: str | None = None,
        source_pk_col: str | None = None,
        controller_snapshot: dict[str, Any] | None = None,
        actor: str = "pipeline",
        emit_singletons: bool = True,
        weak_confidence_threshold: float = 0.6,
        flush_rows: int | None = None,
    ) -> ResolutionBatch:
        """Build a batch from the loose resolve args, filling the residency budget
        from the env default when unspecified. This is the adapter seam:
        ``resolve_clusters`` calls it when no batch was supplied."""
        return cls(
            run_id=run_id,
            dataset=dataset,
            matchkey_name=matchkey_name,
            source_pk_col=source_pk_col,
            controller_snapshot=controller_snapshot,
            actor=actor,
            emit_singletons=emit_singletons,
            weak_confidence_threshold=weak_confidence_threshold,
            flush_rows=_default_flush_rows() if flush_rows is None else flush_rows,
        )


def bulk_fast_path_enabled() -> bool:
    """Kill-switch mirror (``GOLDENMATCH_IDENTITY_BULK=0`` disables the bulk path).

    Exposed here so the seam's config surface lives in one place; the resolve body
    still owns the runtime read to stay byte-identical this milestone."""
    return os.environ.get("GOLDENMATCH_IDENTITY_BULK", "1").strip() != "0"
