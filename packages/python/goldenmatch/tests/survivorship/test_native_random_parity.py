"""F2: seeded randomized native-vs-slow survivorship parity gate.

Stress-tests :func:`build_survivorship_native` against the slow oracle across
many random ELIGIBLE configs x random clustered frames. The hand-written
parity tests in ``test_native_parity.py`` pin specific levers; this loop is the
Frankenstein / tie / confidence catcher -- it samples the whole supported
surface (group strategies, scalar strategies, allow_fill, anchor, date_column,
source_priority, list-form ``when:`` conditionals, ``validate:``) over frames
deliberately seeded with NULLs, ties, all-agree clusters, all-null columns, and
size-1 clusters.

Determinism: every iteration is driven by a ``random.Random(seed)`` from a
FIXED seed list (no unseeded randomness), so a failure is reproducible from the
seed printed in the assertion message. The generator only ever emits configs
the eligibility gate accepts, and asserts that BEFORE each parity check -- an
ineligible generated config is a test bug, not a parity finding.

CONSTRAINT: this module is never executed locally (the box OOMs on pytest); the
controller runs the gate and feeds back any MISMATCH (assert_parity prints the
native-vs-oracle diff) for repair.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    GoldenFieldRule,
    GoldenGroupRule,
    GoldenRulesConfig,
)
from goldenmatch.core.survivorship.native import survivorship_native_eligible

from .test_native_parity import assert_parity

# Native-expressible strategy pools. These are the ONLY strategies the
# generator samples from, so every config it builds is eligible by
# construction (asserted per-iteration). Kept in lockstep with
# native._NATIVE_GROUP_STRATEGIES / _NATIVE_SCALAR_STRATEGIES.
_GROUP_STRATEGIES = ("most_complete", "source_priority", "most_recent", "anchor")
_SCALAR_STRATEGIES = (
    "most_complete",
    "first_non_null",
    "most_recent",
    "longest_value",
    "source_priority",
)
# Source pool for source_priority (groups AND scalars). The priority list is a
# RANDOM ordering / subset of this, so unknown-source rows (sources outside the
# chosen priority) and rank ties (same source) both occur.
_SOURCE_POOL = ("crm", "erp", "web", "legacy", "ghost")
_BASE_DATE = date(2020, 1, 1)

# Fixed seed list: deterministic + reproducible. Spread of small ints so the
# RNG streams differ; a failing seed prints in the assertion message.
_SEEDS = list(range(1, 121))  # 120 iterations


# ── value generators ────────────────────────────────────────────────────────


def _maybe_null(rng: random.Random, value, null_p: float = 0.30):
    """Return ``value`` or ``None`` with probability ``null_p`` (deliberate
    NULL injection so winners can be null / allow_fill has donors / scalars go
    all-null)."""
    return None if rng.random() < null_p else value


def _string_cell(rng: random.Random, col: str, row_seq: int):
    """A string cell with controllable length (so most_complete / longest_value
    have a real longest value AND length ties). ~1/4 of the time it draws from a
    tiny shared pool so two rows collide on the SAME value (all-agree / tie)."""
    if rng.random() < 0.25:
        # Shared small pool -> repeated values -> all-agree clusters + value ties.
        return rng.choice(["AA", "BB", "CC"])
    # Variable-length unique-ish value: length drawn so equal-length ties happen.
    length = rng.choice([2, 2, 4, 4, 7])
    body = "".join(rng.choice("xyzpqr") for _ in range(length))
    return f"{col[:1]}{body}{row_seq}"[:max(2, length)]


def _date_cell(rng: random.Random):
    """A date drawn from a small offset pool so equal-date ties occur, with a
    null chance (most_recent excludes null-date rows)."""
    offset = rng.choice([0, 0, 30, 30, 365])
    return _BASE_DATE + timedelta(days=offset)


# ── config (rules) generation ───────────────────────────────────────────────


def _make_group_rule(rng, name, columns, date_col):
    """Build ONE eligible GoldenGroupRule over ``columns`` (>= 2). Picks a random
    native group strategy and the levers that strategy requires; returns
    ``(rule, used_date_col)`` so the frame generator can guarantee the
    date_column / anchor exist."""
    strategy = rng.choice(_GROUP_STRATEGIES)
    allow_fill = rng.random() < 0.5
    if strategy == "most_complete":
        return (
            GoldenGroupRule(name=name, strategy="most_complete",
                            allow_fill=allow_fill, columns=columns),
            None,
        )
    if strategy == "source_priority":
        priority = _random_priority(rng)
        return (
            GoldenGroupRule(name=name, strategy="source_priority",
                            source_priority=priority, allow_fill=allow_fill,
                            columns=columns),
            None,
        )
    if strategy == "most_recent":
        # date_column only needs to be a FRAME column (resolve._dates_for reads
        # it straight from the cluster frame, group membership irrelevant). We
        # do NOT add it as a member -- two most_recent groups would then both
        # claim `updated` and trip the schema's "column in >1 field group"
        # guard. The date col stays a standalone scalar resolved by default.
        return (
            GoldenGroupRule(name=name, strategy="most_recent",
                            date_column=date_col, allow_fill=allow_fill,
                            columns=columns),
            date_col,
        )
    # anchor: anchor must be one of the group's columns.
    anchor = rng.choice(columns)
    return (
        GoldenGroupRule(name=name, strategy="anchor", anchor=anchor,
                        allow_fill=allow_fill, columns=columns),
        None,
    )


def _random_priority(rng: random.Random):
    """A non-empty random subset+ordering of the source pool. Because it is a
    SUBSET, some rows carry sources outside the priority (unknown -> sentinel
    rank), and the ordering randomizes which known source wins."""
    k = rng.randint(1, len(_SOURCE_POOL))
    return rng.sample(list(_SOURCE_POOL), k)


def _make_scalar_rule(rng, date_col):
    """An eligible plain-scalar GoldenFieldRule (random native strategy + its
    required levers). Occasionally carries ``validate='email_validate'`` (the
    same validator the hand-written tests use; goldenflow_filter is fail-open so
    parity holds whether or not the validator is installed)."""
    strategy = rng.choice(_SCALAR_STRATEGIES)
    validate = "email_validate" if rng.random() < 0.25 else None
    if strategy == "most_recent":
        return GoldenFieldRule(strategy="most_recent", date_column=date_col,
                               validate=validate)
    if strategy == "source_priority":
        return GoldenFieldRule(strategy="source_priority",
                               source_priority=_random_priority(rng),
                               validate=validate)
    return GoldenFieldRule(strategy=strategy, validate=validate)


def _make_conditional_rule(rng, date_col, predicate_col):
    """A list-form ``when:`` conditional: 1-2 guarded clauses (each a random
    native strategy) + a mandatory when-less default LAST (schema requires
    exactly one default, last). The predicate reads ``predicate_col`` (a
    resolved group-member or scalar winner), matching the hand-written E1 tests
    (``city == 'NY'`` shape)."""
    clauses: list[GoldenFieldRule] = []
    n_guarded = rng.randint(1, 2)
    literals = ["AA", "BB", "CC", "NY", "SF"]
    for _ in range(n_guarded):
        lit = rng.choice(literals)
        op = rng.choice(["==", "!="])
        when = f"{predicate_col} {op} '{lit}'"
        clauses.append(_clause_with_when(rng, date_col, when))
    # mandatory when-less default, LAST.
    clauses.append(_clause_with_when(rng, date_col, None))
    return clauses


def _clause_with_when(rng, date_col, when):
    """One conditional clause (random native strategy + required levers + the
    given ``when`` predicate, which may be None for the default clause)."""
    strategy = rng.choice(_SCALAR_STRATEGIES)
    validate = "email_validate" if rng.random() < 0.2 else None
    if strategy == "most_recent":
        return GoldenFieldRule(strategy="most_recent", date_column=date_col,
                               when=when, validate=validate)
    if strategy == "source_priority":
        return GoldenFieldRule(strategy="source_priority",
                               source_priority=_random_priority(rng),
                               when=when, validate=validate)
    return GoldenFieldRule(strategy=strategy, when=when, validate=validate)


# ── frame + config assembly ─────────────────────────────────────────────────


def _generate_case(seed: int):
    """Build one (df, rules) pair for ``seed``. Returns a DataFrame with
    ``__cluster_id__`` / ``__row_id__`` / ``__source__`` plus random user
    columns, and an eligible GoldenRulesConfig over them.

    Column plan (drawn once per case):
      * a shared date column ``updated`` (always present, so any most_recent
        rule's date_column resolves) -- also a plain scalar unit.
      * 0-2 field groups, each over 2-3 dedicated columns (no cross-group or
        group/field_rule overlap -- the schema forbids both).
      * the remaining user columns are plain scalars or (occasionally) list-form
        conditionals whose predicate reads an already-resolved column.
    """
    rng = random.Random(seed)

    # --- cluster shape: 1..12 clusters, sizes including 1 ---
    n_clusters = rng.randint(1, 12)
    cluster_sizes = [rng.randint(1, 4) for _ in range(n_clusters)]

    # --- column pool ---
    date_col = "updated"
    # Pool of user column names available for groups + scalars + conditionals.
    pool = [f"c{i}" for i in range(rng.randint(3, 8))]
    rng.shuffle(pool)

    field_groups: list[GoldenGroupRule] = []
    used: set[str] = set()
    # 0..2 groups, each consuming 2-3 fresh columns from the pool.
    n_groups = rng.randint(0, 2)
    available = [c for c in pool]
    for gi in range(n_groups):
        free = [c for c in available if c not in used]
        if len(free) < 2:
            break
        gsize = rng.randint(2, min(3, len(free)))
        cols = free[:gsize]
        rule, _ = _make_group_rule(rng, f"g{gi}", cols, date_col)
        # Reserve this group's columns so no other group / field_rule reuses
        # them (the schema forbids a column in >1 group or in both a group and
        # a field_rule). date_col is never a member, so it stays a free scalar.
        used.update(rule.columns)
        field_groups.append(rule)

    grouped_cols = {c for g in field_groups for c in g.columns}

    # Remaining user columns become scalars or conditionals. Exclude anything a
    # group already owns; the date_col is handled separately as a scalar.
    ungrouped = [c for c in pool if c not in grouped_cols]
    field_rules: dict = {}

    # A predicate target for conditionals: prefer a resolved column that exists
    # (a group member or a scalar we are about to add). Group members are
    # resolved winners the predicate can read.
    predicate_candidates = sorted(grouped_cols - {date_col})

    cond_cols: list[str] = []
    # Columns deliberately left WITHOUT a field_rule so default_strategy governs
    # them. They must still be added to the frame (else they don't exist for
    # either path), so we track them explicitly -- otherwise default_strategy
    # would only ever be exercised on the date column.
    default_scalar_cols: list[str] = []
    for col in ungrouped:
        # ~25% conditional IF we have a column to predicate on; else scalar.
        if predicate_candidates and rng.random() < 0.25:
            pred_col = rng.choice(predicate_candidates)
            field_rules[col] = _make_conditional_rule(rng, date_col, pred_col)
            cond_cols.append(col)
        elif rng.random() < 0.7:
            # Explicit per-field rule.
            field_rules[col] = _make_scalar_rule(rng, date_col)
        else:
            # Unruled -> governed by default_strategy. Still a frame column.
            default_scalar_cols.append(col)
        # Once a scalar resolves to a winner it becomes a valid predicate target
        # for a LATER conditional (toposort handles the dependency). Conditionals
        # are NOT predicate targets (avoids a conditional->conditional cycle).
        if col not in cond_cols:
            predicate_candidates.append(col)

    # default_strategy: a native scalar strategy. most_recent/source_priority as
    # the default would need EVERY unruled column to satisfy that strategy's
    # frame support (date/source), which they do (date col present, __source__
    # present), so any native default is safe.
    default_strategy = rng.choice(["most_complete", "first_non_null", "longest_value"])

    rules = GoldenRulesConfig(
        default_strategy=default_strategy,
        field_groups=field_groups,
        field_rules=field_rules,
    )

    # --- build the frame ---
    # Every user column referenced anywhere (group members, scalar/conditional
    # keys, unruled default-governed scalars, the date column) must exist in the
    # frame.
    all_user_cols: set[str] = (
        set(grouped_cols) | set(field_rules.keys()) | set(default_scalar_cols)
    )
    all_user_cols.add(date_col)
    # Plain string columns = everything except the date column.
    string_cols = sorted(c for c in all_user_cols if c != date_col)

    cluster_ids: list[int] = []
    row_ids: list[int] = []
    sources: list[str] = []
    col_data: dict[str, list] = {c: [] for c in string_cols}
    date_data: list = []

    rid = 10
    for cid, size in enumerate(cluster_sizes, start=1):
        # Per-cluster "mode": occasionally force an all-agree cluster (every row
        # identical) or an all-null cluster, to guarantee those paths fire.
        mode = rng.random()
        agree_vals = {c: _string_cell(rng, c, 0) for c in string_cols}
        agree_date = _date_cell(rng)
        all_null = mode < 0.12
        all_agree = 0.12 <= mode < 0.30
        for seq in range(size):
            cluster_ids.append(cid)
            row_ids.append(rid)
            rid += 1
            sources.append(rng.choice(_SOURCE_POOL))
            for c in string_cols:
                if all_null:
                    col_data[c].append(None)
                elif all_agree:
                    col_data[c].append(agree_vals[c])
                else:
                    col_data[c].append(_maybe_null(rng, _string_cell(rng, c, seq)))
            if all_null:
                date_data.append(None)
            elif all_agree:
                date_data.append(agree_date)
            else:
                date_data.append(_maybe_null(rng, _date_cell(rng)))

    data = {
        "__cluster_id__": cluster_ids,
        "__row_id__": row_ids,
        "__source__": sources,
    }
    schema: dict = {
        "__cluster_id__": pl.Int64,
        "__row_id__": pl.Int64,
        "__source__": pl.Utf8,
    }
    for c in string_cols:
        data[c] = col_data[c]
        schema[c] = pl.Utf8
    data[date_col] = date_data
    schema[date_col] = pl.Date

    df = pl.DataFrame(data, schema=schema)
    return df, rules


# ── the randomized parity loop ──────────────────────────────────────────────


@pytest.mark.parametrize("seed", _SEEDS)
def test_random_native_parity(seed):
    """One randomized eligible config x frame per seed. Asserts eligibility
    FIRST (an ineligible config is a generator bug, not a parity finding), then
    asserts byte-identical native-vs-oracle values AND __golden_confidence__.
    On any failure, re-raise with the seed so the case is reproducible."""
    df, rules = _generate_case(seed)
    # Guard: the generator must only ever emit eligible configs.
    assert survivorship_native_eligible(rules, provenance=False) is True, (
        f"GENERATOR BUG: emitted an ineligible config (seed={seed}): {rules!r}"
    )
    try:
        assert_parity(df, rules)
    except AssertionError as exc:
        raise AssertionError(f"seed={seed}\n{exc}") from exc


# ── explicit edge cases (not random) ────────────────────────────────────────


def _mixed_rules():
    """A group + a scalar + a conditional, used by the edge-case frames so each
    edge exercises the group / scalar / conditional confidence paths together."""
    return GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="addr", strategy="most_complete",
                                      columns=["street", "city"])],
        field_rules={
            "name": GoldenFieldRule(strategy="first_non_null"),
            "phone": [
                GoldenFieldRule(strategy="source_priority",
                                source_priority=["crm", "erp", "web"],
                                when="city == 'NY'"),
                GoldenFieldRule(strategy="most_complete"),
            ],
        },
    )


def test_edge_size_one_cluster():
    # A lone row is its own winner; every scalar/group/conditional resolves to
    # that row's value and all-agree confidence (a 1-row cluster trivially
    # agrees -> per-unit 1.0).
    df = pl.DataFrame({
        "__cluster_id__": [1],
        "__row_id__": [10],
        "__source__": ["crm"],
        "street": ["1 A St"],
        "city": ["NY"],
        "name": ["Bob"],
        "phone": ["111"],
    })
    assert survivorship_native_eligible(_mixed_rules(), provenance=False) is True
    assert_parity(df, _mixed_rules())


def test_edge_all_agree_cluster():
    # Every row identical on every column -> each scalar/group short-circuits to
    # the shared value with confidence 1.0; the conditional likewise.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 1],
        "__row_id__": [10, 11, 12],
        "__source__": ["crm", "crm", "crm"],
        "street": ["1 A St", "1 A St", "1 A St"],
        "city": ["NY", "NY", "NY"],
        "name": ["Bob", "Bob", "Bob"],
        "phone": ["111", "111", "111"],
    })
    assert_parity(df, _mixed_rules())


def test_edge_all_null_group_columns_cluster():
    # Every group column null across the cluster -> winner is the lowest
    # __row_id__ row, all pinned group values null, group confidence 0; the
    # scalar (name) is also all-null here.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1],
        "__row_id__": [10, 11],
        "__source__": ["crm", "web"],
        "street": [None, None],
        "city": [None, None],
        "name": [None, None],
        "phone": ["111", "222"],
    }, schema={"__cluster_id__": pl.Int64, "__row_id__": pl.Int64,
               "__source__": pl.Utf8, "street": pl.Utf8, "city": pl.Utf8,
               "name": pl.Utf8, "phone": pl.Utf8})
    assert_parity(df, _mixed_rules())


def test_edge_single_size_one_cluster_group_only():
    # Size-1 cluster, group-only config (no scalars/conditionals) -> the group
    # is the only unit; confidence = winner_populated / len(cols).
    df = pl.DataFrame({
        "__cluster_id__": [1],
        "__row_id__": [10],
        "street": ["1 A St"],
        "city": [None],
        "zip": ["10001"],
    })
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="addr", strategy="most_complete",
                                      columns=["street", "city", "zip"])],
    )
    assert_parity(df, rules)
