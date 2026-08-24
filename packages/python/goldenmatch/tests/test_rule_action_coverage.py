"""Every RED verdict the controller can reach must have a rule that answers it.

Seven sub-profiles compute a health verdict; the 17 rules in `DEFAULT_RULES` act
on three config surfaces. That asymmetry is how a run reaches RED with nothing to
do about it. `DataProfile.health`'s own docstring records the shape:

    v23 telemetry (#577) showed this signal stayed YELLOW for all 5 controller
    iterations with no rule addressing it because the verdict isn't actionable

and #2717 hit it three times in one issue -- a runtime warning about
concatenated sources with no lever, a RED cluster verdict with no cluster action
at all, and a rule walking a threshold that provably could not move the metric
it was reacting to.

This gate makes that fail CI instead of surfacing as a bad benchmark months
later. There are two ways for a RED condition to be legitimately unanswered, and
they are NOT the same thing:

  * `_UNACTIONABLE` -- no config change can fix it. Permanent and documented.
  * `_UNCOVERED` -- a real hole. Shrinks only, never grows.
"""

from __future__ import annotations

from goldenmatch.core.autoconfig_rules import DEFAULT_RULES
from goldenmatch.core.complexity_profile import RED_REASONS

#: RED conditions no config change can fix. Adding one here is a claim that the
#: controller should REPORT the condition and stop, not that someone owes work.
#:
#: `DataProfile` already made this argument once and acted on it: it dropped a
#: uniform-types YELLOW clause precisely because "there's no config change that
#: fixes 'your data is all strings'". An empty frame is the same -- there is no
#: blocking key, threshold or matchkey that makes zero rows matchable.
_UNACTIONABLE: frozenset[str] = frozenset({
    "data_empty",
})

#: RED conditions with no rule yet. SHRINKS ONLY -- adding an entry to turn a red
#: gate green is the failure this exists to prevent. Each entry is a MEASURED
#: hole with its evidence, not a hypothetical.
_UNCOVERED: frozenset[str] = frozenset({
    # `rule_low_transitivity` is the ONLY rule reading profile.cluster and it
    # returns None unless transitivity < 0.85, so a run where one cluster
    # swallowed 10%+ of the data with healthy transitivity produces no proposal.
    "cluster_giant",
    # Both rules reading profile.matchkey.per_field (rule_unimodal_scoring,
    # rule_matchkey_demote_high_cardinality_field) sort by HIGHEST cardinality.
    # Neither handles a field collapsing to a single value.
    "matchkey_collapsed_field",
})


def _claimed() -> set[str]:
    return {r for rule in DEFAULT_RULES for r in getattr(rule, "targets", ())}


def test_every_red_reason_has_a_rule():
    missing = RED_REASONS - _claimed() - _UNCOVERED - _UNACTIONABLE
    assert not missing, (
        f"RED conditions no rule can answer: {sorted(missing)}. Either add a rule "
        f"that targets it, or -- if no config change can fix it -- record it in "
        f"_UNACTIONABLE with the reason, as DataProfile did when it removed its "
        f"uniform-types clause."
    )


def test_the_uncovered_allowlist_only_shrinks():
    """An entry that a rule now answers must be deleted, not left to rot."""
    stale = _UNCOVERED & _claimed()
    assert not stale, (
        f"{sorted(stale)} are answered by a rule now -- delete them from _UNCOVERED"
    )


def test_unactionable_conditions_have_no_rule():
    """If someone writes a rule for one of these, the classification was wrong
    and the entry should move out of _UNACTIONABLE rather than sit contradicted."""
    contradicted = _UNACTIONABLE & _claimed()
    assert not contradicted, (
        f"{sorted(contradicted)} are marked unactionable but a rule targets them"
    )


def test_the_two_lists_are_disjoint():
    """A condition is either impossible to act on or owed an action, not both."""
    assert not (_UNACTIONABLE & _UNCOVERED)


def test_both_lists_name_real_reasons():
    """A slug that no profile can emit is dead weight hiding a real gap."""
    assert _UNCOVERED <= RED_REASONS, sorted(_UNCOVERED - RED_REASONS)
    assert _UNACTIONABLE <= RED_REASONS, sorted(_UNACTIONABLE - RED_REASONS)


def test_the_gate_would_catch_a_new_unanswered_condition():
    """Negative control: the assertion is capable of failing.

    A gate that passes because its inputs are empty is worse than no gate --
    this proves the comparison is live before anyone trusts a green run.
    """
    invented = RED_REASONS | {"totally_new_red_condition"}
    missing = invented - _claimed() - _UNCOVERED - _UNACTIONABLE
    assert missing == {"totally_new_red_condition"}
