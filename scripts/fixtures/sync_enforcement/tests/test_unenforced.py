from lane import orphan_lane


def test_orphan_runs():
    assert orphan_lane() == 1
