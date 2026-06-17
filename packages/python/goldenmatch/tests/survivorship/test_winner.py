from goldenmatch.core.survivorship.winner import group_winner, GroupResult


def _rows():
    return [
        {"__pos__": 0, "street": "1 Main St", "city": "LA", "zip": None},
        {"__pos__": 1, "street": "1 Main", "city": "LA", "zip": "90001"},
    ]


def test_most_complete_picks_row_with_most_populated_group_cells():
    res = group_winner(_rows(), ["street", "city", "zip"], strategy="most_complete")
    assert isinstance(res, GroupResult)
    assert res.winner_pos == 1
    assert res.values == {"street": "1 Main", "city": "LA", "zip": "90001"}
    assert res.confidence == 1.0
    assert res.tie is False


def test_strict_lockstep_pins_winner_null():
    rows = [
        {"__pos__": 0, "street": "1 Main St", "city": "LA", "zip": "90001"},
        {"__pos__": 1, "street": "1 Main Street Apt 4B", "city": "LA", "zip": None},
    ]
    res = group_winner(rows, ["street", "city", "zip"], strategy="most_complete")
    assert res.winner_pos == 0
    assert res.values["zip"] == "90001"


def test_all_null_group_confidence_zero():
    rows = [{"__pos__": 0, "a": None, "b": None}, {"__pos__": 1, "a": None, "b": None}]
    res = group_winner(rows, ["a", "b"], strategy="most_complete")
    assert res.confidence == 0.0
    assert res.values == {"a": None, "b": None}


def test_source_priority():
    rows = [
        {"__pos__": 0, "a": "x", "b": "y", "__source__": "billing"},
        {"__pos__": 1, "a": "p", "b": "q", "__source__": "crm"},
    ]
    res = group_winner(rows, ["a", "b"], strategy="source_priority", source_priority=["crm", "billing"])
    assert res.winner_pos == 1


def test_most_recent():
    rows = [
        {"__pos__": 0, "a": "old", "__dt__": "2020-01-01"},
        {"__pos__": 1, "a": "new", "__dt__": "2024-01-01"},
    ]
    res = group_winner(rows, ["a"], strategy="most_recent", dates=["2020-01-01", "2024-01-01"])
    assert res.winner_pos == 1


def test_tie_applies_0_7_penalty():
    rows = [{"__pos__": 0, "a": "x", "b": "y"}, {"__pos__": 1, "a": "p", "b": "q"}]
    res = group_winner(rows, ["a", "b"], strategy="most_complete")
    assert res.tie is True
    assert res.confidence == 0.7


def test_lockstep_winner_null_not_backfilled_from_nonwinner():
    # Winner (by source_priority) has zip=None; non-winner has zip='90001'.
    # Anti-Frankenstein: result must be None, not the non-winner's value.
    rows = [
        {"__pos__": 0, "__source__": "crm", "name": "Alice", "zip": None},
        {"__pos__": 1, "__source__": "billing", "name": "Alice Smith", "zip": "90001"},
    ]
    res = group_winner(
        rows, ["name", "zip"],
        strategy="source_priority",
        source_priority=["crm", "billing"],
    )
    assert res.winner_pos == 0
    assert res.values["zip"] is None   # winner's null pinned; no back-fill
