"""Cross-module config-field ACCESSORS (readers and writers).

The load-bearing test is test_the_incident_pair_is_surfaced: two modules read
BOTH blocking_config.passes and .keys and must agree on precedence. Nothing
checked that they did, and they did not.

The scan counts WRITES as well as reads -- see `field_accessors` and
test_a_write_only_module_counts_as_an_accessor. "Accessor", not "reader", is
what these tests say, because that is what is measured.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from shared_decisions.readers import (  # noqa: E402
    field_accessors,
    shared_fields,
    unparseable_modules,
)

FIXTURES = Path(__file__).parent / "fixtures" / "incident_1c843c8a5"
REPO = Path(__file__).resolve().parent.parent
GM = REPO / "packages" / "python" / "goldenmatch" / "goldenmatch"


def test_a_bom_prefixed_source_file_is_parsed_not_skipped(tmp_path):
    """core/autoconfig_planner.py and core/execution_plan.py both carry a
    UTF-8 BOM (`\\ufeff`) in the committed blob. A plain `encoding="utf-8"`
    decode leaves the BOM character in the source string, `ast.parse` raises
    a SyntaxError on it, and the module falls into the silent `except
    SyntaxError: continue` -- invisible to every field this scan reports,
    on Linux CI too, not just locally. `encoding="utf-8-sig"` strips it."""
    src = textwrap.dedent(
        """
        def look(config):
            return config.blocking.passes
        """
    ).lstrip("\n")
    # "utf-8-sig" PREPENDS the BOM bytes on encode -- writing `src` (no
    # leading "\ufeff") through it reproduces exactly the byte layout
    # `autoconfig_planner.py`/`execution_plan.py` carry, without double-BOM
    # bytes that manually prepending "\ufeff" first would have produced.
    (tmp_path / "bommed.py").write_bytes(src.encode("utf-8-sig"))
    accessors = field_accessors(tmp_path)
    assert "bommed.py" in accessors.get("passes", set()), accessors.get("passes")
    assert unparseable_modules(tmp_path) == []


def test_unparseable_modules_names_the_genuinely_broken_file_only(tmp_path):
    """`unparseable_modules` exists so a silent ast.parse SyntaxError is
    COUNTED rather than swallowed -- the same silence
    `modules_without_coverage_data` exists to surface in the companion
    parity_coverage.py tool. Drive the REAL function against a genuinely
    unparseable file (unbalanced parens, not a BOM) alongside a valid one,
    and pin that it names the broken file and ONLY the broken file --
    non-emptiness alone wouldn't tell a future regression that returns
    every module, or a hardcoded module name, apart from a correct one."""
    (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
    (tmp_path / "fine.py").write_text("def g():\n    return 1\n", encoding="utf-8")
    assert unparseable_modules(tmp_path) == ["broken.py"]


def test_the_incident_pair_is_surfaced():
    """EXIT CRITERION. Both fixture modules read `passes` and `keys`; the scan
    must report both fields as read by more than one module."""
    shared = shared_fields(FIXTURES)
    for field in ("passes", "keys"):
        assert field in shared, f"{field} not reported as shared: {sorted(shared)}"
        assert len(shared[field]) >= 2, f"{field} readers: {shared[field]}"
    both = {m for m in shared["passes"] if m in shared["keys"]}
    assert len(both) >= 2, f"expected both fixture modules to read both fields, got {both}"


def test_a_field_accessed_by_one_module_is_not_shared():
    accessors = field_accessors(FIXTURES)
    shared = shared_fields(FIXTURES)
    single = {f for f, mods in accessors.items() if len(mods) == 1}
    assert single, "fixture has no single-accessor field; test cannot witness the filter"
    assert not (single & set(shared)), (
        f"single-accessor fields leaked into shared: {single & set(shared)}"
    )


def test_the_real_package_scan_is_not_empty():
    """A wrong root or a parse failure yields an empty dict that reads as clean."""
    shared = shared_fields(GM)
    assert len(shared) >= 5, f"only {len(shared)} shared fields found in goldenmatch"


def test_scan_reports_module_paths_not_absolute():
    shared = shared_fields(FIXTURES)
    for mods in shared.values():
        for m in mods:
            assert not Path(m).is_absolute(), m


def test_non_config_named_bases_are_not_missed():
    """The "config"/"cfg"-substring rule alone misses readers whose base is
    named after the config *type* it holds rather than containing "config" --
    e.g. `blocking.passes` (base `blocking`, a CamelCase segment of
    `BlockingConfig`). These modules were silently absent from the
    real-package scan until the base-name rule was widened past the literal
    substring; naming them explicitly means a future narrowing says which one
    vanished, not just that the count dropped.

    Originally this named three modules. `distributed/scoring.py` and
    `identity/block_index.py` were dropped when PR #2845 routed them through
    `BlockingConfig.resolved_keys()` -- they no longer read `.passes`/`.keys`
    at all, so they can no longer witness anything about the base-name rule.
    The two below still use a type-named base and still exercise it."""
    accessors = field_accessors(GM)
    both = {m for m in accessors.get("passes", set()) if m in accessors.get("keys", set())}
    for expected in (
        "core/autoconfig_verify.py",
        "core/autoconfig.py",
    ):
        assert expected in both, f"{expected} missing from passes+keys accessors: {sorted(both)}"


# Every module under packages/python/goldenmatch/goldenmatch that ACCESSES
# both incident fields (`blocking.passes` AND `blocking.keys`). Pinned as a
# SET, not a count. A count floor is satisfiable by accident: drop a real
# accessor, gain an unrelated false positive somewhere else, and the number
# holds while the inventory has quietly stopped watching a module. Each entry
# was verified by reading its access site.
# FOUR MODULES LEFT THIS SET WHEN THE DECISION WAS SINGLE-SOURCED.
#
# `backends/score_buckets.py`, `backends/fs_out_of_core.py`,
# `distributed/scoring.py` and `identity/block_index.py` used to resolve
# keys-vs-passes by hand. PR #2845 moved that rule onto
# `BlockingConfig.resolved_keys()` and routed all ten call sites through it, so
# those modules no longer read `.passes`/`.keys` directly and are no longer
# shared-decision accessors at all.
#
# That is the remediation working, observed by the detector that found it: this
# inventory exists to surface duplicated decisions, and the decision it
# surfaced has been de-duplicated. The set shrinks; it must never grow without
# a triage.
EXPECTED_PASSES_AND_KEYS_ACCESSORS = {
    # bare `blocking_config.passes` -- the original "config"/"cfg" rule
    "core/blocker.py",  # half of the 1c843c8a5 incident pair; still reads both
    "core/autoconfig.py",
    # base named after the config TYPE (`blocking.passes`), no "config" in it
    "core/autoconfig_verify.py",
    "core/autoconfig_rules.py",
    # attribute-chain base (`config.blocking.passes`)
    "core/fused_match.py",
    # module-local alias (`b = config.blocking`, then `b.passes`)
    "core/config_critique.py",
    "core/perceptual_autoconfig.py",  # `blk = config.blocking`
}


def test_the_exact_set_of_passes_and_keys_accessors():
    """The incident field pair is accessed by every blocking-strategy consumer.
    Pin the WHOLE SET, naming what went missing and what turned up unexpected.

    This replaced a `>= 10` count floor that named only 5 of the 11 modules.
    Under that floor a regression could drop `core/blocker.py` -- literally one
    half of the incident pair -- while an unrelated false positive appeared
    elsewhere, and the suite stayed green at 10. A count cannot tell those two
    events apart; a set comparison names both."""
    accessors = field_accessors(GM)
    both = {m for m in accessors.get("passes", set()) if m in accessors.get("keys", set())}
    missing = EXPECTED_PASSES_AND_KEYS_ACCESSORS - both
    unexpected = both - EXPECTED_PASSES_AND_KEYS_ACCESSORS
    assert not missing, f"expected accessors that vanished from the scan: {sorted(missing)}"
    assert not unexpected, (
        f"new accessors the scan found but nobody has triaged: {sorted(unexpected)} "
        "-- read each access site, then either add it above or fix the rule"
    )
    # Belt-and-braces floor, secondary to the set above.
    assert len(both) >= len(EXPECTED_PASSES_AND_KEYS_ACCESSORS), sorted(both)


def test_config_critique_is_an_accessor_of_both_passes_and_keys():
    """core/config_critique.py aliases `b = config.blocking` and then reads
    `b.strategy`/`b.passes`/`b.keys` in the SAME multi_pass precedence branch
    the 1c843c8a5 incident fix added to score_buckets -- a module making the
    identical precedence-shaped decision on the identical fields. It was
    silently absent from the scan (a bare `b` doesn't word-boundary-match any
    config class) until module-local alias tracking was added; named
    explicitly so a future narrowing says this exact module vanished."""
    accessors = field_accessors(GM)
    both = {m for m in accessors.get("passes", set()) if m in accessors.get("keys", set())}
    assert "core/config_critique.py" in both, f"config_critique.py missing from: {sorted(both)}"


def test_fused_match_is_an_accessor_of_both_passes_and_keys():
    """core/fused_match.py is a shipping scoring backend that reads BOTH
    incident fields via `config.blocking.keys`/`config.blocking.passes` --
    an attribute-chain base (`config.blocking`), not a bare Name. It was
    silently absent from the scan until attribute chains were walked; named
    explicitly so a future narrowing says this exact module vanished."""
    accessors = field_accessors(GM)
    both = {m for m in accessors.get("passes", set()) if m in accessors.get("keys", set())}
    assert "core/fused_match.py" in both, f"fused_match.py missing from: {sorted(both)}"


def test_single_letter_bases_do_not_falsely_match_a_config_class_prefix():
    """A base name that merely PREFIXES a config class name (`c` prefixes
    `CanopyConfig`, `f` prefixes `FieldTransform`) must not count as a config
    read -- `c` in cli/memory.py:253 (`for c in corrections:`) is a
    Correction record, not a CanopyConfig, and its `.trust` access is
    unrelated to any config field. The word-boundary rule (equality, not
    prefix) rejects it; a looser prefix rule previously let it through."""
    accessors = field_accessors(GM)
    assert "cli/memory.py" not in accessors.get("trust", set()), (
        f"cli/memory.py falsely counted as a 'trust' accessor: {accessors.get('trust', set())}"
    )


def test_a_write_only_module_counts_as_an_accessor(tmp_path):
    """WRITES COUNT, deliberately -- so the name has to say "accessor".

    `core/pipeline.py` does `config.blocking.keys = new_keys` (an ast.Store).
    For a shared-decision inventory the mutator matters MORE than a reader: it
    is the module every reader has to agree with. The function was called
    `field_readers` for three fix rounds while measuring this, which is the
    same overstated-name defect the inventory exists to surface.

    Uses a temp tree, not the real package, so the assertion is about the
    STORE context itself and cannot be satisfied by an unrelated read in the
    same file."""
    (tmp_path / "writer.py").write_text(
        textwrap.dedent("""
            def bump(config):
                config.blocking.keys = []
        """),
        encoding="utf-8",
    )
    (tmp_path / "reader.py").write_text(
        textwrap.dedent("""
            def look(config):
                return config.blocking.keys
        """),
        encoding="utf-8",
    )
    accessors = field_accessors(tmp_path)
    assert accessors.get("keys") == {"writer.py", "reader.py"}, accessors.get("keys")
    assert "keys" in shared_fields(tmp_path), "write + read is a shared decision, not one-sided"


def test_the_alias_rule_and_the_access_rule_are_the_same_rule(tmp_path):
    """One config-look rule, applied identically to an access base and to an
    alias assignment's value.

    `profile.blocking` carries no literal "config"/"cfg" substring, but its
    `blocking` segment word-boundary-matches `BlockingConfig`. The access scan
    always accepted it; the alias pass tested only the WHOLE dotted string, so
    `b = profile.blocking` did not register `b` -- meaning the scan's answer
    depended on whether the author used a local variable. Two implementations
    of one rule, disagreeing, inside the detector built to find exactly that.
    Both now go through `_chain_looks_like_config`."""
    (tmp_path / "direct.py").write_text(
        textwrap.dedent("""
            def look(profile):
                return profile.blocking.passes
        """),
        encoding="utf-8",
    )
    (tmp_path / "aliased.py").write_text(
        textwrap.dedent("""
            def look(profile):
                b = profile.blocking
                return b.passes
        """),
        encoding="utf-8",
    )
    accessors = field_accessors(tmp_path)
    assert accessors.get("passes") == {"direct.py", "aliased.py"}, (
        f"the aliased form must be seen exactly like the direct one; got {accessors.get('passes')}"
    )
