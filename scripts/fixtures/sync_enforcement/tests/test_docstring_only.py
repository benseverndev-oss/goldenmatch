from lane import prose_lane


def test_prose_runs():
    """`prose_lane` must behave the same way `slow_lane` does.

    This docstring names both symbols. Nothing in the CODE references
    slow_lane, so nothing compares them -- which is exactly the shape that
    made tests/test_engine.py look like it enforced the 6c89042c7 incident.
    """
    assert prose_lane() == 1
