"""SND matching configs -- the headline relational config + honest baselines.

Design note (the modeling crux): two papers by the same real person are linked
mainly by *sharing a specific co-author*, and only sometimes by org+topic. That's
OR logic, not a single weighted average -- so the headline config uses SEPARATE
matchkeys (goldenmatch OR's them: a pair matches if ANY matchkey accepts). A lone
strong co-author signal fires the co-author matchkey directly instead of being
diluted to below-threshold by near-empty text/venue fields. goldenmatch's
union-find clustering then transitively chains a person's papers together even
when two of them share no co-author directly (A-B, B-C => {A,B,C}).

Every weighted matchkey needs a blocking config; the name IS the block, so we
block on a constant column (all of a name's papers in one goldenmatch block).
``set_jaccard`` must be registered (``scorers.register()``) before these build.
"""
from __future__ import annotations

from to_frame import BLOCK_COL


def _const_blocking():
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig

    return BlockingConfig(keys=[BlockingKeyConfig(fields=[BLOCK_COL], transforms=[])])


def _mk(name, threshold, fields):
    from goldenmatch.config.schemas import MatchkeyConfig

    return MatchkeyConfig(name=name, type="weighted", threshold=threshold,
                          rerank=False, fields=fields)


def _field(field, scorer, weight, transforms=None):
    from goldenmatch.config.schemas import MatchkeyField

    return MatchkeyField(field=field, scorer=scorer, weight=weight,
                         transforms=transforms or [])


# --- Headline: co-author-overlap-driven relational config -------------------

def relational_config(
    *,
    coauthor_threshold: float = 0.15,
    orgtext_threshold: float = 0.55,
):
    """The headline SND config: co-author OR org+topic.

    - MK ``coauthor``: single-field ``set_jaccard`` on the co-author set. A
      single-field weighted matchkey means the threshold applies directly to the
      Jaccard, so ``coauthor_threshold=0.15`` ~ "share >=1 specific co-author
      among a handful". This is the make-or-break relational signal.
    - MK ``orgtext``: org set-overlap + title/abstract topical similarity +
      venue. Catches same-person papers with disjoint co-authors (solo-ish or
      cross-era papers) via a stable affiliation + topic.
    """
    from goldenmatch.config.schemas import GoldenMatchConfig

    coauthor = _mk("coauthor", coauthor_threshold, [
        _field("coauthors", "set_jaccard", 1.0),
    ])
    orgtext = _mk("orgtext", orgtext_threshold, [
        _field("orgs", "set_jaccard", 2.0),
        _field("text", "token_sort", 1.0),
        _field("venue", "token_sort", 0.5),
    ])
    return GoldenMatchConfig(matchkeys=[coauthor, orgtext], blocking=_const_blocking())


def fusion_config(
    *,
    coauthor_threshold: float = 0.15,
    orgtext_threshold: float = 0.55,
    topic_threshold: float = 0.5,
):
    """Embedding fusion: co-author OR org+topic OR a TF-IDF TOPICAL bridge.

    Adds a third OR'd matchkey ``topic`` -- TF-IDF cosine over title/abstract --
    so two same-author papers that share NO co-author (the connectivity ceiling
    the adaptive threshold couldn't break) still link when they are topically
    close. ``topic_threshold`` is the cosine bar: high enough that only genuinely
    same-subfield papers link (guarding against two same-name people in one
    field), tuned empirically. The co-author matchkey stays the precise primary
    signal; topic only ADDS edges (recall), never removes.
    """
    from goldenmatch.config.schemas import GoldenMatchConfig

    coauthor = _mk("coauthor", coauthor_threshold, [
        _field("coauthors", "set_jaccard", 1.0),
    ])
    orgtext = _mk("orgtext", orgtext_threshold, [
        _field("orgs", "set_jaccard", 2.0),
        _field("text", "token_sort", 1.0),
        _field("venue", "token_sort", 0.5),
    ])
    topic = _mk("topic", topic_threshold, [
        _field("text", "tfidf_cosine", 1.0),
    ])
    return GoldenMatchConfig(matchkeys=[coauthor, orgtext, topic], blocking=_const_blocking())


def coauthor_only_config(*, coauthor_threshold: float = 0.15):
    """Ablation: the relational signal ALONE (no org/text). Isolates how much of
    the score co-author overlap earns by itself."""
    from goldenmatch.config.schemas import GoldenMatchConfig

    coauthor = _mk("coauthor", coauthor_threshold, [
        _field("coauthors", "set_jaccard", 1.0),
    ])
    return GoldenMatchConfig(matchkeys=[coauthor], blocking=_const_blocking())


# --- Baselines --------------------------------------------------------------

def text_only_config(*, threshold: float = 0.7):
    """The "unresolved" straw baseline: topical similarity only, NO relational
    signal. Shows what you get without the co-author graph -- the number the
    substrate has to beat decisively."""
    from goldenmatch.config.schemas import GoldenMatchConfig

    text = _mk("text_only", threshold, [
        _field("text", "token_sort", 1.0),
    ])
    return GoldenMatchConfig(matchkeys=[text], blocking=_const_blocking())


# CONFIGS registry consumed by run_snd.py (--config). graph_er and the trivial
# floors (all-one / all-singletons) are handled directly in run_snd.
CONFIGS = {
    "relational": relational_config,
    "coauthor_only": coauthor_only_config,
    "text_only": text_only_config,
}
