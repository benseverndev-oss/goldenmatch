"""No NEW shared config decision may appear untriaged, and none may diverge.

Phase B3. Same contract as `KNOWN_DEAD` in scripts/test_no_new_dead_code.py:
these sets are floors to work DOWN, never buckets to top up. Both directions
are asserted -- a new entry fails, and an entry that no longer reproduces must
be removed so the ratchet keeps its value.

Every shared field is covered by exactly one of three places:

  parity/shared_decisions.allow   65  agreed; readers supply no conflicting default
  KNOWN_ACTIONABLE                 3  a signal fires on a SINGLE-class field
  KNOWN_AMBIGUOUS                 12  a signal fires, but the name is declared on
                                      several classes, so the readers grouped
                                      under it may not share a field at all
  HELD_BY_HAND                     1  no signal fires, held out on judgement

The sharp gate is `test_no_allowlisted_field_starts_diverging`. That is the
1c843c8a5 recurrence: a field everyone agreed on gains a second, different
fallback. Nothing caught that in 2026-08, which is why the incident shipped.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from shared_decisions.allowlist import load_allowlist  # noqa: E402
from shared_decisions.readers import shared_fields  # noqa: E402
from shared_decisions.report import DEFAULT_ROOT  # noqa: E402
from shared_decisions.shapes import (  # noqa: E402
    access_shapes,
    fallback_divergence,
    nullable_fields,
    split_by_ambiguity,
    unguarded_optional,
)

# A signal fires AND the field name is declared on exactly one config class, so
# every reader grouped under it really is reading the same field. Actionable.
# Triaged in B1 -- docs/superpowers/specs/2026-09-02-shared-decision-triage.md
# carries the finding behind each. Shrinks as B2 lands remediations; never
# grows without a triage.
KNOWN_ACTIONABLE: set[str] = {
    "golden_rules",  # F1  Ray lane's fallback could not construct (fix in #2844)
    "passes",  # F2  distributed/scoring.py resolves it with no strategy branch
    "weight",  # F9  autoconfig defaults to 0; scorer.py and Spark read it bare
}

# A signal fires, but the name is declared on SEVERAL config classes. An access
# does not say which class the object is, so a "divergence" here may be two
# different fields that happen to share a name: `transforms` looked divergent
# only because a `MatchkeyField` fallback was compared against ten
# `BlockingKeyConfig` readers. These need class resolution before they mean
# anything, and NONE is claimed as a defect.
KNOWN_AMBIGUOUS: set[str] = {
    "blocking",
    "column",
    "columns",
    "field",
    "keys",
    "matchkeys",
    "mode",
    "model",
    "path",
    "scorer",
    "source_priority",
    "transforms",
}

# Held out of the allowlist on judgement, with NO signal firing. `strategy` is
# the discriminator F2 and F3 fail to consult -- it decides which of
# keys/passes is correct -- and B0a's ranking test names it alongside them.
# Allowlisting it would suppress an explicit earlier judgement on a syntactic
# signal, which is the "wrong merge" the phase-B spec warns about.
HELD_BY_HAND: set[str] = {"strategy"}


@lru_cache(maxsize=1)
def _signals() -> frozenset[str]:
    """Cached: five tests need this, and each uncached call re-parses ~493 files.

    Uncached, this module took over two minutes and timed out locally.
    """
    accesses = access_shapes(DEFAULT_ROOT)
    nullable = nullable_fields(DEFAULT_ROOT / "config" / "schemas.py")
    return frozenset(
        set(fallback_divergence(accesses)) | set(unguarded_optional(accesses, nullable))
    )


@lru_cache(maxsize=1)
def _shared() -> frozenset[str]:
    """Cached for the same reason as `_signals`."""
    return frozenset(shared_fields(DEFAULT_ROOT))


def test_every_shared_field_is_triaged():
    """A field newly read by a second module is a new shared decision.

    It must be classified before it can ride along: recorded as agreed in the
    allowlist, or recorded as a finding here.
    """
    untriaged = set(_shared()) - load_allowlist()
    untriaged -= KNOWN_ACTIONABLE | KNOWN_AMBIGUOUS | HELD_BY_HAND
    assert not untriaged, (
        f"NEW shared config field(s): {sorted(untriaged)}. A second module now "
        f"reads each of these, so its readers have to agree about something. "
        f"Triage them: add to parity/shared_decisions.allow with a reason if "
        f"the readers agree, or to KNOWN_ACTIONABLE / KNOWN_AMBIGUOUS here if "
        f"they do not."
    )


def test_no_allowlisted_field_starts_diverging():
    """THE 1c843c8a5 GATE.

    An allowlisted field is one whose readers were checked and agreed. If a
    signal starts firing on it, that agreement has broken -- a module has
    added a second, different fallback for a value the field does not carry.
    That is exactly the shape that shipped 0 pairs where legacy produced 242,
    and nothing in the repo could see it at the time.
    """
    broke = set(_signals()) & load_allowlist()
    assert not broke, (
        f"{sorted(broke)} were recorded as AGREED and now trip a divergence "
        f"signal: some module supplies a fallback the other readers do not. "
        f"Reconcile the readers, or move the field to KNOWN_ACTIONABLE / "
        f"KNOWN_AMBIGUOUS with the finding written down. Do NOT widen the "
        f"allowlist reason."
    )


def test_findings_that_no_longer_reproduce_are_removed():
    """A floor to work DOWN. Keeping a fixed finding rots the ratchet.

    `HELD_BY_HAND` is exempt by construction -- no signal fires on it, that is
    why it is a separate set.
    """
    fixed = (KNOWN_ACTIONABLE | KNOWN_AMBIGUOUS) - set(_signals())
    assert not fixed, (
        f"{sorted(fixed)} no longer trip any signal. Remove them from "
        f"KNOWN_ACTIONABLE / KNOWN_AMBIGUOUS and add them to "
        f"parity/shared_decisions.allow with the reason they are now agreed, "
        f"so the ratchet keeps its value."
    )


def test_the_ambiguity_split_is_the_real_one():
    """Guard the split: the two finding sets must match what the code computes.

    Hand-maintained sets that drift from `split_by_ambiguity` would let an
    ACTIONABLE finding be filed as ambiguous -- which is how a real defect gets
    excused as a name collision.
    """
    actionable, ambiguous = split_by_ambiguity(set(_signals()))
    assert actionable == KNOWN_ACTIONABLE, (
        f"actionable drifted: computed {sorted(actionable)}, recorded {sorted(KNOWN_ACTIONABLE)}"
    )
    assert ambiguous == KNOWN_AMBIGUOUS, (
        f"ambiguous drifted: computed {sorted(ambiguous)}, recorded {sorted(KNOWN_AMBIGUOUS)}"
    )


def test_the_three_sets_partition_the_shared_fields():
    """Guard the guards: the floors must cover the inventory exactly.

    Without this, a field could sit in two sets (double-counted, so a
    regression in one is masked by the other) or the sets could drift into
    naming fields that are no longer shared at all -- either way the three
    tests above would still pass while measuring less than they claim.
    """
    shared = set(_shared())
    allow = load_allowlist()

    sets = {
        "allowlist": allow,
        "KNOWN_ACTIONABLE": KNOWN_ACTIONABLE,
        "KNOWN_AMBIGUOUS": KNOWN_AMBIGUOUS,
        "HELD_BY_HAND": HELD_BY_HAND,
    }
    overlap = set()
    names = list(sets)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            overlap |= sets[a] & sets[b]
    assert not overlap, f"a field is in two sets at once: {sorted(overlap)}"

    missing = shared - set().union(*sets.values())
    assert not missing, f"shared but in no set: {sorted(missing)}"

    phantom = set().union(*sets.values()) - shared
    assert not phantom, (
        f"{sorted(phantom)} are recorded but no longer shared fields -- drop "
        f"them so the floors describe the real inventory."
    )
