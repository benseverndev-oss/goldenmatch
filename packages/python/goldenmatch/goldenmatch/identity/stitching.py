"""Cross-device / channel stitching model (#1110, epic #1108).

Identity Graph v2 resolves *records* into entities from PII similarity alone. A
real CDP/MDM identity spine also has to stitch the SAME person across the
channels they show up on -- a CRM row, an email open, a web cookie, an offline
purchase -- where the strongest link is often a shared *device / channel
identifier* (a cookie id, a login id, a hashed email) rather than fuzzy PII.

This module is the stitching primitive that sits in front of identity
resolution. It is deliberately a pure, dependency-light function over a frame +
the probabilistic pairs a normal dedupe already produces -- it does NOT
re-implement the scorer, and it adds no DB migration. Three pieces, mapping the
issue's scope bullets:

1. **Channel classification** -- label each record's channel (``classify_channel``)
   and attach a *trust* weight (``channel_trust``): verified channels (CRM,
   offline) are trusted; ambient ones (web cookie, social) are not.
2. **Deterministic + probabilistic stitching** -- records that share a non-null
   *device key* are the same person with near-certainty (``deterministic_stitch_pairs``);
   records that don't fall back to the probabilistic PII pairs. ``stitch_frame``
   unions both into channel-aware groups.
3. **Cross-channel scoring adjustment** -- a probabilistic match *across* two
   low-trust channels is weaker than one within trusted channels, so its score
   is scaled by the channels' trust (``cross_channel_factor`` / ``adjust_score``).
   A deterministic device-key link is NOT downweighted (a shared cookie is a hard
   identifier regardless of channel).

The output (``StitchResult``) gives, per stitched group, the channels present
and a **channel-aware confidence** -- so a person stitched across CRM/email/web
resolves to one entity, and a steward can see whether that entity rests on a
hard device link or a downweighted cross-channel guess.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from goldenmatch._polars_lazy import pl

if TYPE_CHECKING:
    from goldenmatch.config.schemas import ChannelStitchConfig

# ── Channel taxonomy + trust ────────────────────────────────────────────────
#
# Trust weight in (0, 1]: how reliably a channel's records carry *verified*
# identity. CRM / offline are operator-verified (1.0 / 0.95); email is
# self-asserted but addressable (0.8); app/web/social are ambient device signals
# (0.7 / 0.5 / 0.4). ``unknown`` sits in the middle (0.6) so an unclassified
# source is neither trusted nor punished.

CHANNEL_CRM = "crm"
CHANNEL_OFFLINE = "offline"
CHANNEL_LOYALTY = "loyalty"
CHANNEL_EMAIL = "email"
CHANNEL_APP = "app"
CHANNEL_WEB = "web"
CHANNEL_SOCIAL = "social"
CHANNEL_UNKNOWN = "unknown"

DEFAULT_CHANNEL_TRUST: dict[str, float] = {
    CHANNEL_CRM: 1.0,
    CHANNEL_OFFLINE: 0.95,
    CHANNEL_LOYALTY: 0.9,
    CHANNEL_EMAIL: 0.8,
    CHANNEL_APP: 0.7,
    CHANNEL_WEB: 0.5,
    CHANNEL_SOCIAL: 0.4,
    CHANNEL_UNKNOWN: 0.6,
}

# Source-name substrings -> channel, checked in order (most specific first).
# Used only when a record carries no explicit channel value.
_SOURCE_CHANNEL_HINTS: list[tuple[str, str]] = [
    ("salesforce", CHANNEL_CRM),
    ("hubspot", CHANNEL_CRM),
    ("dynamics", CHANNEL_CRM),
    ("crm", CHANNEL_CRM),
    ("pos", CHANNEL_OFFLINE),
    ("store", CHANNEL_OFFLINE),
    ("instore", CHANNEL_OFFLINE),
    ("offline", CHANNEL_OFFLINE),
    ("loyalty", CHANNEL_LOYALTY),
    ("rewards", CHANNEL_LOYALTY),
    ("esp", CHANNEL_EMAIL),
    ("mailchimp", CHANNEL_EMAIL),
    ("email", CHANNEL_EMAIL),
    ("newsletter", CHANNEL_EMAIL),
    ("ios", CHANNEL_APP),
    ("android", CHANNEL_APP),
    ("mobile", CHANNEL_APP),
    ("app", CHANNEL_APP),
    ("cookie", CHANNEL_WEB),
    ("clickstream", CHANNEL_WEB),
    ("web", CHANNEL_WEB),
    ("site", CHANNEL_WEB),
    ("facebook", CHANNEL_SOCIAL),
    ("twitter", CHANNEL_SOCIAL),
    ("tiktok", CHANNEL_SOCIAL),
    ("social", CHANNEL_SOCIAL),
]

# Columns whose shared non-null value is a near-certain same-person signal.
# These are deterministic identifiers, not PII to fuzzy-match.
DEFAULT_DEVICE_KEYS: list[str] = [
    "device_id",
    "cookie_id",
    "advertising_id",
    "idfa",
    "gaid",
    "login_id",
    "user_id",
    "customer_id",
    "loyalty_id",
    "hashed_email",
    "email_hash",
]


def channel_trust(
    channel: str | None,
    trust_map: dict[str, float] | None = None,
) -> float:
    """Trust weight in (0, 1] for a channel. Unknown / unmapped -> the
    ``unknown`` weight (0.6 by default), never 0 (a record is never worthless)."""
    table = trust_map or DEFAULT_CHANNEL_TRUST
    if channel and channel in table:
        return float(table[channel])
    return float(table.get(CHANNEL_UNKNOWN, 0.6))


def classify_channel(
    row: dict[str, Any],
    *,
    channel_column: str | None = "channel",
    channel_map: dict[str, str] | None = None,
    default: str = CHANNEL_UNKNOWN,
) -> str:
    """Classify one record into a channel.

    Resolution order (first hit wins):

    1. An explicit value in ``channel_column`` (e.g. a ``channel`` column).
    2. ``channel_map``: an exact ``__source__`` -> channel override.
    3. A substring hint on ``__source__`` (``_SOURCE_CHANNEL_HINTS``).
    4. ``default`` (``"unknown"``).

    Always returns a lower-cased label; an explicit channel value is taken
    verbatim (lower-cased) so callers can use custom channel names.
    """
    if channel_column and channel_column in row:
        val = row.get(channel_column)
        if val is not None and str(val).strip():
            return str(val).strip().lower()
    source = str(row.get("__source__", "") or "").lower()
    if channel_map:
        # Exact source match (case-insensitive).
        for src, chan in channel_map.items():
            if source == str(src).strip().lower():
                return str(chan).strip().lower()
    if source:
        for needle, chan in _SOURCE_CHANNEL_HINTS:
            if needle in source:
                return chan
    return default


def cross_channel_factor(
    channel_a: str | None,
    channel_b: str | None,
    trust_map: dict[str, float] | None = None,
) -> float:
    """Trust factor in (0, 1] for a probabilistic match between two channels.

    The geometric mean of the two channels' trust weights: a match within
    trusted channels (crm/crm -> 1.0) is unscaled; a cross-channel match into a
    low-trust channel (crm/web -> ~0.71) is downweighted; a low-trust/low-trust
    match (web/web -> 0.5) most of all. Geometric mean (vs ``min``) keeps the
    penalty smooth and symmetric.
    """
    ta = channel_trust(channel_a, trust_map)
    tb = channel_trust(channel_b, trust_map)
    return (ta * tb) ** 0.5


def adjust_score(
    score: float,
    channel_a: str | None,
    channel_b: str | None,
    trust_map: dict[str, float] | None = None,
) -> float:
    """Scale a probabilistic match ``score`` by the channels' trust factor.

    Deterministic device-key links should bypass this (a shared cookie is a hard
    identifier regardless of channel); ``stitch_frame`` only applies it to the
    probabilistic layer.
    """
    return float(score) * cross_channel_factor(channel_a, channel_b, trust_map)


# ── Stitch result types ─────────────────────────────────────────────────────


@dataclass
class StitchGroup:
    """One stitched group of records (members are ``__row_id__`` ints)."""

    members: list[int]
    channels: list[str]
    confidence: float
    deterministic: bool  # joined (in part) via a shared device key
    cross_channel: bool  # members span >= 2 distinct channels
    # device key columns that contributed a deterministic link, e.g. {"cookie_id"}
    device_keys: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.members)


@dataclass
class StitchResult:
    groups: list[StitchGroup]
    n_deterministic_pairs: int = 0
    n_probabilistic_pairs: int = 0

    @property
    def multi_member_groups(self) -> list[StitchGroup]:
        return [g for g in self.groups if g.size > 1]

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_groups": len(self.groups),
            "n_multi_member": len(self.multi_member_groups),
            "n_deterministic_pairs": self.n_deterministic_pairs,
            "n_probabilistic_pairs": self.n_probabilistic_pairs,
            "n_cross_channel": sum(1 for g in self.groups if g.cross_channel),
            "n_device_stitched": sum(1 for g in self.groups if g.deterministic),
        }


# ── Deterministic stitching ─────────────────────────────────────────────────


def deterministic_stitch_pairs(
    df: pl.DataFrame,
    device_keys: list[str] | None = None,
) -> list[tuple[int, int, str]]:
    """Star-link records that share a non-null value on any device key.

    Returns ``(row_id_a, row_id_b, key)`` edges. Per (key, value) group we emit
    a STAR to the group's first member -- a spanning set, not all O(n^2) pairs --
    which is all Union-Find needs to merge the group and keeps this O(N) per key
    even when a value is shared by thousands of rows. Keys absent from ``df`` are
    skipped; null / empty values never link.
    """
    keys = device_keys if device_keys is not None else DEFAULT_DEVICE_KEYS
    present = [k for k in keys if k in df.columns]
    if not present or "__row_id__" not in df.columns or df.is_empty():
        return []

    edges: list[tuple[int, int, str]] = []
    for key in present:
        sub = df.select(["__row_id__", key]).drop_nulls()
        if sub.is_empty():
            continue
        # Drop empty-string values (a blank cookie is not a link).
        sub = sub.filter(
            pl.col(key).cast(pl.Utf8).str.strip_chars().str.len_chars() > 0
        )
        if sub.is_empty():
            continue
        grouped = sub.group_by(key).agg(pl.col("__row_id__"))
        for mids in grouped["__row_id__"].to_list():
            ids = [int(m) for m in mids]
            if len(ids) < 2:
                continue
            anchor = ids[0]
            for other in ids[1:]:
                edges.append((anchor, other, key))
    return edges


# ── Top-level stitching ─────────────────────────────────────────────────────


def stitch_frame(
    df: pl.DataFrame,
    *,
    scored_pairs: list[tuple[int, int, float]] | None = None,
    device_keys: list[str] | None = None,
    channel_column: str | None = "channel",
    channel_map: dict[str, str] | None = None,
    trust_map: dict[str, float] | None = None,
    adjust_cross_channel: bool = True,
    prob_threshold: float = 0.0,
    config: ChannelStitchConfig | None = None,
) -> StitchResult:
    """Stitch a multi-channel frame into channel-aware identity groups.

    Combines two evidence layers:

    * **Deterministic** -- records sharing a device key (``device_keys``) are
      linked with certainty (edge weight 1.0, never downweighted).
    * **Probabilistic** -- the ``scored_pairs`` a normal dedupe produced
      (``(row_a, row_b, score)``). When ``adjust_cross_channel`` is on, each
      score is scaled by ``cross_channel_factor`` of the two records' channels,
      then dropped if it falls below ``prob_threshold``. This module does NOT
      score pairs itself -- pass the pipeline's pairs in.

    Both layers are unioned (Union-Find). Each group's **confidence** is its
    weakest holding edge (min over contributing edge weights; deterministic
    edges contribute 1.0), so a group resting only on a downweighted
    cross-channel guess reads as low-confidence while a device-stitched group
    reads as 1.0. Singletons (no edges) get confidence 1.0.

    ``config`` (a ``ChannelStitchConfig``) supplies defaults for every knob; an
    explicit keyword argument always wins over the config field.
    """
    from goldenmatch.core.cluster import UnionFind

    if config is not None:
        if device_keys is None:
            device_keys = list(config.device_keys) if config.device_keys else None
        if channel_column == "channel":
            channel_column = config.channel_column
        if channel_map is None:
            channel_map = config.channel_map or None
        if trust_map is None:
            trust_map = config.channel_trust or None
        # adjust_cross_channel / prob_threshold: only override the *defaults*.
        if adjust_cross_channel is True:
            adjust_cross_channel = config.adjust_cross_channel
        if prob_threshold == 0.0:
            prob_threshold = config.prob_threshold

    if "__row_id__" not in df.columns or df.is_empty():
        return StitchResult(groups=[])

    # Per-row channel (computed once).
    channel_of: dict[int, str] = {}
    for row in df.to_dicts():
        rid = row.get("__row_id__")
        if rid is None:
            continue
        channel_of[int(rid)] = classify_channel(
            row, channel_column=channel_column, channel_map=channel_map,
        )

    uf = UnionFind()
    uf.add_many(sorted(channel_of.keys()))

    # Track the best (max) contributing edge weight per directed-into-group is
    # not what we want -- we want each group's WEAKEST holding edge. Accumulate
    # every edge's weight against both endpoints' eventual root after unioning.
    det_pairs = deterministic_stitch_pairs(df, device_keys)
    # device keys that linked each row (for reporting).
    keys_for_root: dict[int, set[str]] = {}

    edge_list: list[tuple[int, int, float, bool]] = []  # (a, b, weight, is_det)
    for a, b, key in det_pairs:
        if a not in channel_of or b not in channel_of:
            continue
        uf.add(a)
        uf.add(b)
        uf.union(a, b)
        edge_list.append((a, b, 1.0, True))
        keys_for_root.setdefault(a, set()).add(key)
        keys_for_root.setdefault(b, set()).add(key)
    n_det = len(edge_list)

    n_prob = 0
    for a, b, score in scored_pairs or []:
        ia, ib = int(a), int(b)
        if ia not in channel_of or ib not in channel_of:
            continue
        weight = float(score)
        if adjust_cross_channel:
            weight = adjust_score(
                weight, channel_of[ia], channel_of[ib], trust_map,
            )
        if weight < prob_threshold:
            continue
        uf.add(ia)
        uf.add(ib)
        uf.union(ia, ib)
        edge_list.append((ia, ib, weight, False))
        n_prob += 1

    # Group membership.
    root_members: dict[int, list[int]] = {}
    for rid in channel_of:
        root_members.setdefault(uf.find(rid), []).append(rid)

    # Weakest holding edge per group + whether any edge was deterministic.
    min_edge: dict[int, float] = {}
    det_root: set[int] = set()
    det_keys_root: dict[int, set[str]] = {}
    for a, _b, w, is_det in edge_list:
        r = uf.find(a)
        if w < min_edge.get(r, float("inf")):
            min_edge[r] = w
        if is_det:
            det_root.add(r)
    for rid, ks in keys_for_root.items():
        det_keys_root.setdefault(uf.find(rid), set()).update(ks)

    groups: list[StitchGroup] = []
    for root, members in root_members.items():
        members_sorted = sorted(members)
        chans = sorted({channel_of[m] for m in members_sorted})
        if len(members_sorted) == 1:
            conf = 1.0
        else:
            conf = float(min_edge.get(root, 1.0))
        groups.append(StitchGroup(
            members=members_sorted,
            channels=chans,
            confidence=round(conf, 6),
            deterministic=root in det_root,
            cross_channel=len(chans) > 1,
            device_keys=sorted(det_keys_root.get(root, set())),
        ))
    # Stable order: largest groups first, then by smallest member id.
    groups.sort(key=lambda g: (-g.size, g.members[0]))
    return StitchResult(
        groups=groups,
        n_deterministic_pairs=n_det,
        n_probabilistic_pairs=n_prob,
    )
