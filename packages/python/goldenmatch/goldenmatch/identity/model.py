"""Identity Graph data classes."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class IdentityStatus(StrEnum):
    ACTIVE = "active"
    MERGED_INTO = "merged_into"
    SPLIT = "split"
    RETIRED = "retired"


class EdgeKind(StrEnum):
    SAME_AS = "same_as"
    POSSIBLE_SAME_AS = "possible_same_as"
    CONFLICTS_WITH = "conflicts_with"
    DERIVED_FROM = "derived_from"
    # v3 (#1113): a steward's mediation verdict on a conflict. The resolution
    # (same / distinct / defer) rides in ``negative_evidence``.
    MEDIATION_VERDICT = "mediation_verdict"


class EventKind(StrEnum):
    CREATED = "created"
    ABSORBED_RECORD = "absorbed_record"
    MERGED_WITH = "merged_with"
    SPLIT_FROM = "split_from"
    RETIRED = "retired"
    MANUAL_MERGE = "manual_merge"
    MANUAL_SPLIT = "manual_split"
    # v3 (#1112): auto-consolidation of persistently-overlapping entities
    # across runs. Distinct from MANUAL_MERGE -- no human in the loop.
    CONSOLIDATED = "consolidated"
    # v3 (#1113): a steward mediated a conflict (same / distinct / defer).
    CONFLICT_MEDIATED = "conflict_mediated"
    # Agent Memory (#1075): a record was manually claimed into an entity (moved
    # from any prior entity). Distinct from ABSORBED_RECORD (pipeline-driven).
    CLAIMED = "claimed"
    # Claim lifecycle (#1256): explicit transitions of a claim's authority.
    # ``PROMOTE`` raises a claim's tier (e.g. inference -> verified) -- makes an
    # agent inference becoming durable shared truth an auditable event rather
    # than invisible drift. ``AMEND`` supersedes a prior claim's content;
    # ``REVOKE`` retracts it. Distinct from the structural ops above.
    PROMOTE = "promote"
    AMEND = "amend"
    REVOKE = "revoke"


class ClaimType(StrEnum):
    """Categorical authority of a claim (#1256), orthogonal to numeric ``trust``.

    ``trust`` stays the confidence *within* a tier; ``claim_type`` is the tier
    itself, so a reviewer can tell "an agent inferred this at 0.8" from "a tool
    verified this at 0.8".
    """

    OBSERVATION = "observation"  # agent saw this in a session
    INFERENCE = "inference"      # agent concluded this; needs revalidation
    VERIFIED = "verified"        # backed by tool output / source / test / user
    DIRECTIVE = "directive"      # human-authorized rule or constraint


class EvidenceRef(StrEnum):
    """What backs a claim (#1256) -- the typed provenance of the evidence."""

    TOOL_CALL = "tool-call"
    SOURCE = "source"
    USER_CONFIRMATION = "user-confirmation"
    TEST_RUN = "test-run"


@dataclass
class IdentityNode:
    entity_id: str
    status: str = IdentityStatus.ACTIVE.value
    merged_into: str | None = None
    golden_record: dict[str, Any] | None = None
    confidence: float | None = None
    dataset: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class SourceRecord:
    """A single record observation. ``record_id`` is ``{source}:{source_pk}``."""

    record_id: str
    source: str
    source_pk: str
    record_hash: str
    entity_id: str | None = None
    payload: dict[str, Any] | None = None
    dataset: str | None = None
    first_seen_at: datetime = field(default_factory=datetime.now)
    last_seen_at: datetime = field(default_factory=datetime.now)


@dataclass
class EvidenceEdge:
    entity_id: str
    record_a_id: str
    record_b_id: str
    kind: str = EdgeKind.SAME_AS.value
    score: float | None = None
    matchkey_name: str | None = None
    field_scores: dict[str, Any] | None = None
    negative_evidence: dict[str, Any] | None = None
    controller_snapshot: dict[str, Any] | None = None
    run_name: str | None = None
    dataset: str | None = None
    # Provenance spine (#1075/#1078): WHO created this write and their trust.
    # ``actor`` is a free-form principal id ("pipeline", "agent:<name>",
    # "steward:<user>"); ``trust`` in [0, 1]. Both nullable -- pre-provenance rows
    # and callers that don't supply them read back as None.
    actor: str | None = None
    trust: float | None = None
    recorded_at: datetime = field(default_factory=datetime.now)
    edge_id: int | None = None


@dataclass
class IdentityEvent:
    entity_id: str
    kind: str
    payload: dict[str, Any] | None = None
    run_name: str | None = None
    dataset: str | None = None
    # Provenance spine (#1075/#1078): WHO made this change and their trust.
    # See EvidenceEdge for the contract. The "why" rides in ``payload['reason']``.
    actor: str | None = None
    trust: float | None = None
    # Claim-authority tier (#1256): categorical authority of the claim this event
    # writes, ORTHOGONAL to ``trust`` (numeric confidence within a tier). See
    # ``ClaimType``. ``evidence_ref`` (``EvidenceRef``) is what backs the claim;
    # ``previous_claim_id`` chains a claim's lifecycle (promote/amend/revoke ->
    # the event_id it supersedes). All nullable/additive: pre-#1256 rows and
    # callers that don't supply them read back as None, exactly like actor/trust.
    claim_type: str | None = None
    evidence_ref: str | None = None
    previous_claim_id: int | None = None
    # Tamper-evidence (#1078): per-event content hash (sha256 over the event's
    # own immutable fields), computed at insert by ``audit.event_content_hash``.
    # A pure function -- no prior-state read, so it imposes no insert-time
    # serialization point and works uniformly with the Postgres bulk-COPY path.
    # The chain/seal anchor lives in ``audit_seals`` (see ``AuditSeal``). Nullable:
    # pre-hash-chain rows read back as None and are hashed on the fly at seal/verify.
    entry_hash: str | None = None
    recorded_at: datetime = field(default_factory=datetime.now)
    event_id: int | None = None


@dataclass
class AuditSeal:
    """A periodic tamper-evidence anchor over the append-only event log (#1078).

    Each seal records the chained root hash of every event (in ``event_id``
    order) up to ``last_event_id`` for a given ``dataset`` scope (``None`` =
    global chain). Seals chain to their predecessor via ``prev_seal_id`` /
    ``prev_root`` so the whole history is one verifiable ledger -- detecting
    deletion, reordering, insertion, and content edits of any sealed event.
    Created on demand by ``audit.seal_audit_log``; checked by
    ``audit.verify_audit_chain``.
    """

    root_hash: str
    event_count: int
    last_event_id: int | None = None
    dataset: str | None = None
    prev_seal_id: int | None = None
    prev_root: str | None = None
    actor: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    seal_id: int | None = None


@dataclass
class IdentityAlias:
    alias: str
    entity_id: str
    kind: str = "external_id"
    dataset: str | None = None
    recorded_at: datetime = field(default_factory=datetime.now)


def canon_record_pair(a: str, b: str) -> tuple[str, str]:
    """Canonicalize record pair ordering to (min, max) lexicographically."""
    return (a, b) if a <= b else (b, a)
